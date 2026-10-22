"""Client for the PDS Registry Search API (``/api/search/1``).

Three operations, each matched to what the API is good at:

- :func:`count` and :func:`facets` are server-side aggregations. A count over
  90 million products returns in under a second, so totals and per-site
  breakdowns never page through records.
- :func:`pages` walks a result set with ``sort`` + ``search-after``. The final
  sort field is ``lidvid`` because ``harvest_date_time`` alone ties, and a tie
  that straddles a page boundary would silently skip records.
- :func:`random_sample` draws a uniform sample WITHOUT a random-sort feature:
  sort by the file's MD5 and start after a random 128-bit value. MD5s are
  uniformly distributed, so the products that follow a random point in hash
  order are an unbiased draw, and neighbours in that order are unrelated.
  Products with no recorded MD5 cannot be drawn this way; they are counted
  separately (:func:`count_without_checksum`) and reported, never ignored.

Queries use the API's own grammar: ``(a eq "x" and b ge 0)``. A compound query
needs the outer parentheses or the API answers 400.
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

DEFAULT_BASE = "https://pds.mcp.nasa.gov/api/search/1"
USER_AGENT = "facetwork-pds/0.1 (+https://github.com/rlemke/facetwork)"

# Field names, spelled once.
NODE = "ops:Harvest_Info.ops:node_name"
HARVEST_TIME = "ops:Harvest_Info.ops:harvest_date_time"
FILE_REF = "ops:Data_File_Info.ops:file_ref"
FILE_SIZE = "ops:Data_File_Info.ops:file_size"
FILE_MD5 = "ops:Data_File_Info.ops:md5_checksum"
FILE_NAME = "ops:Data_File_Info.ops:file_name"
LABEL_REF = "ops:Label_File_Info.ops:file_ref"
LABEL_SIZE = "ops:Label_File_Info.ops:file_size"
LABEL_MD5 = "ops:Label_File_Info.ops:md5_checksum"
TARGET = "pds:Target_Identification.pds:name"
INVESTIGATION = "pds:Investigation_Area.pds:name"
INSTRUMENT_REF = "ref_lid_instrument"
PRODUCT_CLASS = "product_class"
SUPERSEDED_BY = "ops:Provenance.ops:superseded_by"
ARCHIVE_STATUS = "ops:Tracking_Meta.ops:archive_status"
START_TIME = "pds:Time_Coordinates.pds:start_date_time"
_BC = "cart:Bounding_Coordinates.cart:"
WEST, EAST = _BC + "west_bounding_coordinate", _BC + "east_bounding_coordinate"
NORTH, SOUTH = _BC + "north_bounding_coordinate", _BC + "south_bounding_coordinate"
LON_DIRECTION = "cart:Geodetic_Model.cart:longitude_direction"

#: Anything that has a bounding box.
HAS_FOOTPRINT = f"({NORTH} ge -1000)"


class RegistryError(RuntimeError):
    """The registry refused the request (4xx): a bad query, not a transient fault."""


def base_url() -> str:
    return os.environ.get("FW_PDS_REGISTRY_URL", DEFAULT_BASE).rstrip("/")


def _get(params: list[tuple[str, str]], attempts: int = 5, timeout: int = 120) -> dict[str, Any]:
    """GET /products with retry on transient faults (5xx, timeouts, resets).

    A 4xx is raised at once as :class:`RegistryError`: retrying a malformed query
    only spends the budget on a request that cannot succeed.
    """
    url = f"{base_url()}/products?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    delay = 2.0
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as exc:
            body = exc.read()[:300].decode("utf-8", "replace")
            if 400 <= exc.code < 500 and exc.code != 429:
                raise RegistryError(f"HTTP {exc.code} for {url}: {body}") from exc
            if attempt == attempts:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == attempts:
                raise
        time.sleep(delay)
        delay = min(delay * 2, 60)
    raise AssertionError("unreachable")


def all_of(*clauses: str) -> str:
    """AND the clauses together. Each is parenthesised, so a clause may itself
    be compound; the API needs the outer parentheses on any compound query."""
    parts = [c.strip() for c in clauses if c and c.strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0] if parts[0].startswith("(") else f"({parts[0]})"
    return "(" + " and ".join(f"({p})" for p in parts) + ")"


def eq(field: str, value: str) -> str:
    return f'{field} eq "{value}"'


def count(q: str = "") -> int:
    params = [("limit", "0")]
    if q:
        params.append(("q", q))
    return int(_get(params)["summary"]["hits"])


def facets(q: str, fields: list[str], limit: int = 100) -> dict[str, dict[str, int]]:
    """Server-side term counts: ``{field: {value: count}}``, most populous first."""
    params = [("limit", "0"), ("facet-limit", str(limit))]
    if q:
        params.append(("q", q))
    params += [("facet-fields", f) for f in fields]
    out: dict[str, dict[str, int]] = {f: {} for f in fields}
    for f in _get(params)["summary"].get("facets") or []:
        out[f["property"]] = dict(f.get("counts") or {})
    return out


def count_without_checksum(q: str) -> int:
    """Products matching ``q`` that carry no data-file MD5 (so cannot be verified)."""
    return count(q) - count(all_of(q, f'{FILE_MD5} ge "0"'))


def pages(q: str, fields: list[str], page_size: int = 5000) -> Iterator[list[dict[str, Any]]]:
    """Every product matching ``q``, a page at a time, in a stable order."""
    sort = [HARVEST_TIME, "lidvid"]
    after: list[str] | None = None
    want = sorted(set(fields) | set(sort))
    while True:
        params = [("limit", str(page_size)), ("sort", ",".join(sort)), ("fields", ",".join(want))]
        if q:
            params.append(("q", q))
        if after:
            params.append(("search-after", ",".join(after)))
        data = _get(params).get("data") or []
        if not data:
            return
        yield data
        last = data[-1]
        after = [first(last, HARVEST_TIME), last["id"]]
        if len(data) < page_size:
            return


def random_sample(q: str, n: int, seed: int, fields: list[str], draw: int = 20) -> list[dict[str, Any]]:
    """``n`` products drawn uniformly from those matching ``q`` that have an MD5.

    Each draw starts at a random point in MD5 order and takes ``draw`` products.
    Several versions of one product can share a file and so sit next to each
    other in hash order; those are de-duplicated by logical id so one product
    is never counted twice.
    """
    rng = random.Random(seed)
    want = sorted(set(fields) | {FILE_MD5})
    q_md5 = all_of(q, f'{FILE_MD5} ge "0"')
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    stalls = 0
    while len(out) < n and stalls < 10:
        start = f"{rng.getrandbits(128):032x}"
        params = [
            ("limit", str(min(draw, n - len(out)))),
            ("sort", FILE_MD5),
            ("search-after", start),
            ("fields", ",".join(want)),
            ("q", q_md5),
        ]
        data = _get(params).get("data") or []
        added = 0
        for p in data:
            lid = p["id"].split("::")[0]
            if lid in seen:
                continue
            seen.add(lid)
            out.append(p)
            added += 1
        stalls = stalls + 1 if added == 0 else 0
    return out[:n]


def first(product: dict[str, Any], field: str) -> str:
    """The first value of a property ('' when absent). Registry values are lists."""
    v = (product.get("properties") or {}).get(field)
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return "" if v is None else str(v)


def values(product: dict[str, Any], field: str) -> list[str]:
    v = (product.get("properties") or {}).get(field)
    if v is None:
        return []
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]
