"""fwh_pds — the checksum audit and the coverage grid.

⚠️ The tests that matter are about what gets REPORTED: a stale label must not
read as damaged data, a byte count must not read as a verified checksum, a
404 must say "missing at the registered URL" rather than "lost", and a
mislabelled bounding box must not paint a stripe round a planet. Every case
here was measured in the live registry first.

No test touches the network: the registry and the archive servers are stubbed.
"""

from __future__ import annotations

import hashlib
import io
import urllib.error

import pytest

from pdsarchive import _audit, _coverage
from pdsarchive import _registry as reg

# --- a stub archive server ---------------------------------------------------


class _Resp(io.BytesIO):
    def __init__(self, body: bytes, length: bool = True, status: int = 200):
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Length": str(len(body))} if length else {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _server(files: dict[str, object]):
    """``url -> bytes | int (HTTP error code) | Exception``."""

    def opener(req, timeout=None):
        got = files[req.full_url]
        if isinstance(got, int):
            raise urllib.error.HTTPError(req.full_url, got, "err", {}, None)
        if isinstance(got, Exception):
            raise got
        if isinstance(got, tuple):  # (body, send_length)
            return _Resp(got[0], length=got[1])
        return _Resp(got)

    return opener


def _md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def _product(
    data: dict[str, bytes],
    label: tuple[str, bytes] | None = None,
    *,
    node="PDS_X",
    recorded: dict[str, tuple[int, str]] | None = None,
    label_recorded=None,
) -> dict:
    """A registry record. ``recorded`` overrides what the registry says about a file."""
    recorded = recorded or {}
    props = {
        reg.NODE: [node],
        reg.INVESTIGATION: ["M"],
        reg.TARGET: ["Mars"],
        reg.FILE_REF: [],
        reg.FILE_SIZE: [],
        reg.FILE_MD5: [],
    }
    for url, body in data.items():
        size, md5 = recorded.get(url, (len(body), _md5(body)))
        props[reg.FILE_REF].append(url)
        props[reg.FILE_SIZE].append(str(size))
        props[reg.FILE_MD5].append(md5)
    if label:
        url, body = label
        size, md5 = label_recorded or (len(body), _md5(body))
        props[reg.LABEL_REF] = [url]
        props[reg.LABEL_SIZE] = [str(size)]
        props[reg.LABEL_MD5] = [md5]
    return {"id": "urn:nasa:pds:b:c:p::1.0", "properties": props}


D = "https://a.example/d.img"
L = "https://a.example/d.xml"


# --- verdicts ------------------------------------------------------------------


def test_a_file_that_matches_is_verified():
    p = _product({D: b"x" * 100}, (L, b"<label/>"))
    pc = _audit.check_product(p, 1 << 20, opener=_server({D: b"x" * 100, L: b"<label/>"}))
    assert pc.verdict == "verified" and pc.label_verdict == "verified"


def test_a_stale_label_does_not_fail_the_data():
    """⚠️ Measured on the atmospheres node: a label edited on disk in 2024
    (11,961 recorded vs 12,856 served bytes) beside a data file that verified.
    Judged together that reads as a damaged product; it is a stale registry
    entry for the label."""
    p = _product({D: b"data"}, (L, b"<new label/>"), label_recorded=(5, _md5(b"<old>")))
    pc = _audit.check_product(p, 1 << 20, opener=_server({D: b"data", L: b"<new label/>"}))
    assert pc.verdict == "verified"
    assert pc.label_verdict == "size_mismatch"
    s = _audit.summarise([pc.as_dict()], {"PDS_X": {"products": 10, "with_md5": 10}})
    site = s["sites"][0]
    assert site["failed"] == 0 and site["labels_failed"] == 1
    assert s["failures"] == [] and len(s["label_failures"]) == 1


def test_same_size_different_bytes_is_an_md5_mismatch():
    p = _product({D: b"aaaa"})
    pc = _audit.check_product(p, 1 << 20, check_labels=False, opener=_server({D: b"aaab"}))
    assert pc.verdict == "md5_mismatch"


def test_a_404_is_missing_at_the_registered_url():
    """⚠️ Not "lost": on the rings node the Cassini files are served one
    directory deeper than the registry says."""
    p = _product({D: b"x"})
    pc = _audit.check_product(p, 1 << 20, check_labels=False, opener=_server({D: 404}))
    assert pc.verdict == "missing"
    assert "REGISTRY records" in _audit.__doc__


def test_an_html_error_page_with_status_200_is_a_soft_404():
    page = b"<!DOCTYPE html><html><body>Not here</body></html>"
    p = _product({D: b"real binary"})
    pc = _audit.check_product(p, 1 << 20, check_labels=False, opener=_server({D: page}))
    assert pc.verdict == "soft_404"


def test_a_large_file_is_size_only_never_verified():
    """A byte count proves the file exists and is whole, not that its content is intact."""
    body = b"y" * 5000
    p = _product({D: body})
    pc = _audit.check_product(p, 1000, check_labels=False, opener=_server({D: body}))
    assert pc.verdict == "size_only_match"
    assert pc.verdict not in ("verified",)
    assert "size_only_match" in _audit.PASSES and "verified" in _audit.PASSES


def test_a_large_file_of_the_wrong_size_fails():
    p = _product({D: b"z" * 5000}, recorded={D: (4000, "0" * 32)})
    pc = _audit.check_product(p, 1000, check_labels=False, opener=_server({D: b"z" * 5000}))
    assert pc.verdict == "size_only_mismatch"


def test_too_large_with_no_content_length_is_undetermined():
    p = _product({D: b"w" * 5000})
    pc = _audit.check_product(p, 1000, check_labels=False, opener=_server({D: (b"w" * 5000, False)}))
    assert pc.verdict == "unverified_large"
    assert pc.verdict in _audit.UNDETERMINED


def test_a_path_with_no_host_is_reported_not_crashed_on():
    """⚠️ Measured on the engineering node: file refs like /data/pds4/... ."""
    p = _product({"/data/pds4/x.sch": b"x"})
    pc = _audit.check_product(p, 1 << 20, check_labels=False, opener=_server({}))
    assert pc.verdict == "relative_ref" and pc.verdict in _audit.FAILURES


def test_one_bad_file_makes_a_bad_product():
    a, b = "https://a.example/1", "https://a.example/2"
    p = _product({a: b"1", b: b"2"})
    pc = _audit.check_product(p, 1 << 20, check_labels=False, opener=_server({a: b"1", b: 410}))
    assert pc.verdict == "missing"


def test_a_transient_timeout_is_retried_before_it_becomes_a_verdict():
    """⚠️ Measured: all 5 timeouts in the first audit answered within half a
    second minutes later."""
    calls = {"n": 0}

    def flaky(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError(TimeoutError("timed out"))
        return _Resp(b"x")

    p = _product({D: b"x"})
    pc = _audit.check_product(p, 1 << 20, check_labels=False, opener=flaky, backoff=0)
    assert pc.verdict == "verified" and calls["n"] == 2
    assert "first: timeout" in pc.files[0].detail


def test_a_persistent_timeout_is_still_reported():
    p = _product({D: b"x"})
    pc = _audit.check_product(
        p,
        1 << 20,
        check_labels=False,
        backoff=0,
        opener=_server({D: urllib.error.URLError(TimeoutError("timed out"))}),
    )
    assert pc.verdict == "timeout"


def test_a_404_is_not_retried():
    calls = {"n": 0}

    def gone(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)

    pc = _audit.check_product(_product({D: b"x"}), 1 << 20, check_labels=False, opener=gone, backoff=0)
    assert pc.verdict == "missing" and calls["n"] == 1


def test_unreachable_is_its_own_verdict():
    p = _product({D: b"x"})
    pc = _audit.check_product(
        p,
        1 << 20,
        check_labels=False,
        backoff=0,
        opener=_server({D: urllib.error.URLError(ConnectionRefusedError("refused"))}),
    )
    assert pc.verdict == "unreachable"


# --- statistics ---------------------------------------------------------------


def test_wilson_interval_is_not_zero_width_at_zero_failures():
    lo, hi = _audit.wilson(0, 40)
    assert lo == 0.0 and 0.05 < hi < 0.1


def test_undetermined_products_are_outside_the_rate():
    rows = [
        {"node": "N", "verdict": "verified", "label_verdict": "", "files": []},
        {
            "node": "N",
            "verdict": "missing",
            "label_verdict": "",
            "files": [{"verdict": "missing", "host": "h"}],
        },
        {"node": "N", "verdict": "unverified_large", "label_verdict": "", "files": []},
    ]
    site = _audit.summarise(rows, {"N": {"products": 1000, "with_md5": 900}})["sites"][0]
    assert site["judged"] == 2 and site["failed"] == 1 and site["undetermined"] == 1
    assert site["failure_rate"] == 0.5 and site["estimated_failing_products"] == 450
    assert site["without_md5"] == 100


# --- registry queries -------------------------------------------------------------


def test_compound_queries_are_parenthesised():
    """The API answers 400 to a compound query without the outer parentheses."""
    q = reg.all_of(reg.eq(reg.NODE, "PDS_IMG"), f'{reg.FILE_MD5} ge "0"')
    assert q.startswith("((") and q.endswith("))") and " and " in q
    assert reg.all_of("a eq 1") == "(a eq 1)"
    assert reg.all_of("(a eq 1)") == "(a eq 1)"


def test_random_sample_dedupes_versions_of_one_product(monkeypatch):
    pages = iter(
        [
            {"data": [{"id": "urn:a::1.0"}, {"id": "urn:a::2.0"}, {"id": "urn:b::1.0"}]},
            {"data": [{"id": "urn:c::1.0"}]},
        ]
    )
    monkeypatch.setattr(reg, "_get", lambda params, **k: next(pages))
    got = reg.random_sample('n eq "x"', 3, seed=1, fields=[])
    assert [p["id"] for p in got] == ["urn:a::1.0", "urn:b::1.0", "urn:c::1.0"]


# --- footprints ---------------------------------------------------------------------


def _fp(w, e, n, s, direction="Positive East", mission="M", pid="urn:nasa:pds:b:c:p::1.0"):
    return {
        "id": pid,
        "properties": {
            reg.WEST: [str(w)],
            reg.EAST: [str(e)],
            reg.NORTH: [str(n)],
            reg.SOUTH: [str(s)],
            reg.LON_DIRECTION: [direction],
            reg.INVESTIGATION: [mission],
        },
    }


def test_negative_longitudes_fold_to_0_360():
    box, _ = _coverage.to_box(_fp(-10, 10, 5, -5))
    assert box.lon_spans() == [(350.0, 360.0), (0.0, 10.0)]


def test_a_real_crossing_is_split_not_reversed():
    box, note = _coverage.to_box(_fp(355, 5, 1, 0))
    assert note == "" and box.lon_spans() == [(355.0, 360.0), (0.0, 5.0)]


def test_reversed_edges_are_read_the_narrow_way():
    """⚠️ Measured: CRISM tiles near the pole record west 182.76, east 178.44.
    Read literally that is a 356-degree band round the planet."""
    box, note = _coverage.to_box(_fp(182.75777, 178.43676, 85.59, 85.22))
    assert note == "reversed"
    assert box.lon_spans() == [(178.43676, 182.75777)]


def test_positive_west_is_converted_not_mirrored():
    # west-positive 30..20 W (west edge 30 W) is 330..340 E
    box, _ = _coverage.to_box(_fp(30, 20, 1, 0, direction="Positive West"))
    assert box.lon_spans() == [(330.0, 340.0)]


def test_an_east_edge_of_360_does_not_wrap_into_the_first_column():
    g = _coverage.Grid(1.0)
    box, _ = _coverage.to_box(_fp(358, 360, 0.5, 0.2))
    g.add(box, "M")
    cols = {i % g.cols for i in g.total}
    assert cols == {358, 359}


def test_a_global_mosaic_is_kept_off_the_grid():
    cov = _coverage.build([_fp(0, 360, 90, -90)], "Moon")
    assert cov.global_products == 1 and cov.gridded == 0 and not cov.grid.total


def test_a_wide_non_polar_box_is_kept_off_the_grid_and_listed():
    """⚠️ Measured: CRISM tiles mixing conventions (126.67 to -120 for a tile at
    233-240 E) and Mini-RF products named for 84 S with a box at 3-11 N."""
    cov = _coverage.build(
        [_fp(126.67, -120, 62.5, 57.5, pid="urn:nasa:pds:crism:data_browse:t1::1.0")], "Mars"
    )
    assert cov.wide == 1 and cov.gridded == 0
    assert cov.wide_by_collection == {
        "crism:data_browse": {"count": 1, "example": "urn:nasa:pds:crism:data_browse:t1::1.0"}
    }


def test_a_wide_box_near_a_pole_is_normal():
    cov = _coverage.build([_fp(0, 120, 89, 86)], "Mars")
    assert cov.wide == 0 and cov.gridded == 1


def test_each_product_counts_once_at_its_latest_version():
    old = _fp(10, 11, 1, 0, pid="urn:nasa:pds:b:c:p::1.0")
    new = _fp(20, 21, 1, 0, pid="urn:nasa:pds:b:c:p::1.10")
    cov = _coverage.build([old, new], "Mars")
    assert cov.products == 1
    assert {i % cov.grid.cols for i in cov.grid.total} == {20}


def test_an_edge_on_a_cell_boundary_does_not_claim_the_next_cell():
    g = _coverage.Grid(1.0)
    g.add(_coverage.Box(10.0, 12.0, 1.0, 0.0), "M")
    assert sorted(i % g.cols for i in g.total) == [10, 11]
    assert sorted(i // g.cols for i in g.total) == [89, 89]


def test_covered_fraction_is_area_weighted():
    g = _coverage.Grid(1.0)
    g.add(_coverage.Box(0.0, 360.0, 90.0, 80.0), "M")  # polar cap
    assert g.covered_fraction() == pytest.approx((1 - 0.98480775) / 2, rel=1e-6)


# --- names --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "a, b",
    [
        ("1 Ceres", "(1) Ceres"),
        ("MESSENGER", "Messenger"),
        ("Cassini-Huygens", "Cassini_Huygens Mission"),
        ("Earth I (Moon)", "MOON"),
        ("SOLAR_WIND", "Solar Wind"),
    ],
)
def test_spellings_of_one_thing_share_a_key(a, b):
    assert _coverage.name_key(a) == _coverage.name_key(b)


def test_different_bodies_do_not_collide():
    assert _coverage.name_key("Mars") != _coverage.name_key("Mars Express")
    assert _coverage.name_key("(4) Vesta") != _coverage.name_key("(1) Ceres")


def test_variants_are_reported_not_merged():
    v = _coverage.variants({"Mars": 26_243_810, "MARS": 21_880_135, "Venus": 5})
    assert v == [
        {"key": "mars", "spellings": {"Mars": 26_243_810, "MARS": 21_880_135}, "products": 48_123_945}
    ]


def test_display_name_prefers_mixed_case():
    assert _coverage.display_name({"JUPITER": 634_865, "Jupiter": 119_168}) == "Jupiter"
    assert _coverage.display_name({"CERES": 3}) == "Ceres"


def test_a_stray_leading_character_is_stripped_for_display():
    assert _coverage.canonical_name(">MARS RECONNAISSANCE ORBITER") == "MARS RECONNAISSANCE ORBITER"


# --- the domain wires up -----------------------------------------------------------------


def test_every_event_facet_has_a_handler():
    import re
    from pathlib import Path

    from pdsarchive import handlers

    src = (Path(handlers.__file__).parent / "ffl" / "pds.ffl").read_text()
    events: set[str] = set()
    ns = ""
    for line in src.splitlines():
        m = re.match(r"\s*namespace\s+([\w.]+)", line)
        if m:
            ns = m.group(1)
        m = re.match(r"\s*event facet\s+(\w+)", line)
        if m:
            events.add(f"{ns}.{m.group(1)}")
    assert events == set(handlers._DISPATCH)
