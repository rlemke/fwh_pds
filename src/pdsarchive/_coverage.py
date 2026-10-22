"""Where on each body the archive has data: a grid built from bounding boxes.

⚠️ A bounding box is not a footprint. An oblique image of a thin strip has a
box much larger than the ground it saw, so these maps OVERSTATE coverage. They
answer "which products claim to touch this cell", which is the right question
for finding data and the wrong one for claiming a surface was imaged.

Conventions the registry mixes, all normalised here:

- Longitude is 0..360 for most products and -180..180 for some; both are
  folded to 0..360 east.
- 83 products (2026-10) declare ``longitude_direction = "Positive West"``; their
  longitudes are converted, otherwise they land mirrored across the prime
  meridian.
- ``west > east`` means the box crosses the 0/360 line, and it is split in
  two -- UNLESS the edges are merely reversed. Measured on Mars: CRISM map
  tiles near the pole record west 182.76, east 178.44, a ~4 degree image that
  read literally becomes a 356 degree band and paints a stripe round the
  planet. A real crossing has west - east > 180 (west 355, east 5); when it is
  under 180 the narrower reading is taken and the box is counted as repaired.
- A box spanning 90 degrees of longitude or more, centred away from the poles,
  is a mosaic or a broken label, and nothing in the record says which.
  Measured: CRISM map tiles that write one edge east-positive and the other
  west-positive (tile "60n237" recorded as 126.67 to -120, i.e. 233.33 and
  240 E, read together as a 113 degree strip), and Chandrayaan-1 Mini-RF
  products named for 84 S whose box sits at 3 to 11 N. Either way it is not a
  local observation, so it is kept off the grid and listed by collection.
  Near a pole a wide box is normal (a 4 km image at 85 N spans degrees of
  longitude), so the rule does not apply there.
- A box spanning most of the body is a global mosaic. Counting it in every
  cell would make every map read "covered everywhere", so global products are
  counted separately and kept out of the grid.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from . import _registry as reg

#: A box covering more than this fraction of the sphere is a global product.
GLOBAL_FRACTION = 0.25
#: Longitude span, in degrees, beyond which a non-polar box is not a local observation.
WIDE_DEGREES = 90.0
#: Centre latitude, in degrees, poleward of which wide boxes are expected.
POLAR_LATITUDE = 75.0


@dataclass
class Box:
    west: float  # 0..360 east
    east: float  # 0..360 east; may be < west when crossing 0/360
    north: float
    south: float

    def lon_spans(self) -> list[tuple[float, float]]:
        if self.east >= self.west:
            return [(self.west, self.east)]
        return [(self.west, 360.0), (0.0, self.east)]

    def sphere_fraction(self) -> float:
        """Fraction of a sphere's area inside the box (exact for a lat/lon box)."""
        import math

        lon = sum(e - w for w, e in self.lon_spans()) / 360.0
        band = (math.sin(math.radians(self.north)) - math.sin(math.radians(self.south))) / 2.0
        return max(0.0, lon * band)


def _f(v: str) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def to_box(p: dict[str, Any]) -> tuple[Box | None, str]:
    """``(box, note)``. With no box, ``note`` names why it was rejected; with a
    box, ``note`` is ``"reversed"`` when its edges were swapped back, else ''."""
    w, e = _f(reg.first(p, reg.WEST)), _f(reg.first(p, reg.EAST))
    n, s = _f(reg.first(p, reg.NORTH)), _f(reg.first(p, reg.SOUTH))
    if None in (w, e, n, s):
        return None, "unparseable"
    if not (-90 <= s <= 90 and -90 <= n <= 90) or s > n:
        return None, "latitude out of range"
    if not (-180 <= w <= 360 and -180 <= e <= 360):
        return None, "longitude out of range"
    full = abs(e - w) >= 359.999
    if reg.first(p, reg.LON_DIRECTION).strip().lower() == "positive west":
        # Each edge keeps its meaning (the western edge is still the western
        # edge); only the numbering flips. Measured: 49 of 59 such boxes have
        # west > east in west-positive numbers, as the definition requires.
        w, e = 360.0 - w, 360.0 - e
    # 360 is the east edge of the last cell, not 0: folding it would wrap a box
    # ending at 360 round to claim the first column too.
    w = w % 360.0
    e = e if e == 360.0 else e % 360.0
    if full:
        return Box(0.0, 360.0, n, s), ""
    if w > e and (w - e) < 180.0:
        return Box(e, w, n, s), "reversed"
    return Box(w, e, n, s), ""


class Grid:
    """Per-cell product counts at ``res`` degrees, overall and per mission."""

    def __init__(self, res: float = 1.0) -> None:
        self.res = res
        self.cols = round(360 / res)
        self.rows = round(180 / res)
        self.total: dict[int, int] = defaultdict(int)
        self.by_mission: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))

    def add(self, box: Box, mission: str) -> None:
        r0, r1 = self._span(90 - box.north, 90 - box.south, self.rows)
        for w, e in box.lon_spans():
            c0, c1 = self._span(w, e, self.cols)
            for r in range(r0, r1 + 1):
                base = r * self.cols
                for c in range(c0, c1 + 1):
                    self.total[base + c] += 1
                    self.by_mission[mission][base + c] += 1

    def _span(self, lo: float, hi: float, size: int) -> tuple[int, int]:
        """Cells touched by [lo, hi] degrees. An edge exactly on a cell boundary
        does not claim the next cell; a zero-width span still claims one."""
        import math

        a = math.floor(lo / self.res)
        b = math.ceil(hi / self.res) - 1
        a = min(max(a, 0), size - 1)
        b = min(max(b, a), size - 1)
        return a, b

    def covered_fraction(self, cells: dict[int, int] | None = None, at_least: int = 1) -> float:
        """Area-weighted fraction of the body with at least ``at_least`` products."""
        import math

        cells = self.total if cells is None else cells
        covered = whole = 0.0
        for r in range(self.rows):
            lat_top = 90 - r * self.res
            wgt = math.sin(math.radians(lat_top)) - math.sin(math.radians(lat_top - self.res))
            whole += wgt * self.cols
            base = r * self.cols
            covered += wgt * sum(1 for c in range(self.cols) if cells.get(base + c, 0) >= at_least)
        return covered / whole if whole else 0.0


@dataclass
class TargetCoverage:
    target: str
    grid: Grid
    products: int = 0
    gridded: int = 0
    global_products: int = 0
    reversed_edges: int = 0
    wide: int = 0
    wide_by_collection: dict[str, dict[str, Any]] | None = None
    rejected: dict[str, int] | None = None
    missions: dict[str, int] | None = None
    global_by_mission: dict[str, int] | None = None


def build(products: list[dict[str, Any]], target: str, res: float = 1.0) -> TargetCoverage:
    """Grid one body's footprints. Each logical product counts once (latest version)."""
    latest: dict[str, dict[str, Any]] = {}
    for p in products:
        lid, _, vid = p["id"].partition("::")
        if lid not in latest or _vkey(vid) > _vkey(latest[lid]["id"].partition("::")[2]):
            latest[lid] = p
    cov = TargetCoverage(
        target, Grid(res), rejected={}, missions={}, global_by_mission={}, wide_by_collection={}
    )
    for p in latest.values():
        cov.products += 1
        mission = canonical_name(reg.first(p, reg.INVESTIGATION)) or "(no mission recorded)"
        box, note = to_box(p)
        if box is None:
            cov.rejected[note] = cov.rejected.get(note, 0) + 1
            continue
        if note == "reversed":
            cov.reversed_edges += 1
        if box.sphere_fraction() > GLOBAL_FRACTION:
            cov.global_products += 1
            cov.global_by_mission[mission] = cov.global_by_mission.get(mission, 0) + 1
            continue
        lon_span = sum(e - w for w, e in box.lon_spans())
        if lon_span >= WIDE_DEGREES and abs((box.north + box.south) / 2) < POLAR_LATITUDE:
            cov.wide += 1
            coll = _collection(p["id"])
            w = cov.wide_by_collection.setdefault(coll, {"count": 0, "example": p["id"]})
            w["count"] += 1
            continue
        cov.grid.add(box, mission)
        cov.gridded += 1
        cov.missions[mission] = cov.missions.get(mission, 0) + 1
    return cov


def _collection(lidvid: str) -> str:
    """``urn:nasa:pds:<bundle>:<collection>:<product>`` -> ``<bundle>:<collection>``."""
    parts = lidvid.split("::")[0].split(":")
    return ":".join(parts[3:5]) if len(parts) >= 5 else lidvid


def _vkey(vid: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in vid.split("."))
    except ValueError:
        return (0,)


# --- names ------------------------------------------------------------------

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_SPACE = re.compile(r"\s+")
_NUMBERED = re.compile(r"^\(?\d+\)?\s+")  # "1 Ceres", "(1) Ceres"
_ROMAN = re.compile(r"^[a-z]+ [ivxlc]+ \((.+)\)$")  # "saturn ix (phoebe)"
_MISSION_SUFFIX = re.compile(r"\b(mission|spacecraft|project)\b")


def name_key(name: str) -> str:
    """A key under which spellings of the same thing collide.

    Case, punctuation, a leading catalogue number ("1 Ceres", "(1) Ceres") and
    a trailing "mission" are not identity. Deliberately conservative: two
    names sharing a key are reported as variants for a person to judge, never
    merged silently into one count.
    """
    n = name.strip().lower()
    m = _ROMAN.match(_SPACE.sub(" ", n))
    if m:
        n = m.group(1)
    n = _NUMBERED.sub("", n)
    n = _PUNCT.sub(" ", n)
    n = _MISSION_SUFFIX.sub(" ", n)
    return _SPACE.sub(" ", n).strip()


def canonical_name(name: str) -> str:
    """Display form used for grouping: the stray-punctuation-stripped name.

    Only obvious defects are repaired here (a leading ``>``, surrounding
    whitespace). Case is preserved: "MESSENGER" and "Messenger" are reported as
    variants by :func:`variants`, not quietly unified.
    """
    return name.strip().lstrip(">").strip()


def display_name(spellings: dict[str, int]) -> str:
    """The name to show for a body: its most-used mixed-case spelling.

    The most-used spelling overall is often the all-capitals one ("JUPITER",
    634,865 products, beside "Jupiter", 119,168), which is a labelling habit,
    not the body's name.
    """
    if not spellings:
        return ""
    mixed = {k: v for k, v in spellings.items() if not k.isupper()}
    if mixed:
        return canonical_name(max(mixed, key=mixed.get))
    return canonical_name(max(spellings, key=spellings.get)).title()


def variants(counts: dict[str, int]) -> list[dict[str, Any]]:
    """Groups of distinct spellings that share a :func:`name_key`, largest first."""
    groups: dict[str, dict[str, int]] = defaultdict(dict)
    for name, n in counts.items():
        k = name_key(name)
        if k:
            groups[k][name] = n
    out = [
        {"key": k, "spellings": dict(sorted(g.items(), key=lambda kv: -kv[1])), "products": sum(g.values())}
        for k, g in groups.items()
        if len(g) > 1
    ]
    return sorted(out, key=lambda g: -g["products"])


# --- compact storage ---------------------------------------------------------
#
# 436,502 footprints as full registry records is several hundred MB in memory.
# A row keeps only what gridding reads.

_COMPACT = (reg.WEST, reg.EAST, reg.NORTH, reg.SOUTH, reg.LON_DIRECTION, reg.INVESTIGATION)


def compact(p: dict[str, Any]) -> list[str]:
    return [p["id"], *(reg.first(p, f) for f in _COMPACT)]


def expand(row: list[str]) -> dict[str, Any]:
    return {"id": row[0], "properties": {f: [v] for f, v in zip(_COMPACT, row[1:]) if v != ""}}
