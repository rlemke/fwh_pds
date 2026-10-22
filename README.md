# fwh_pds

The NASA **Planetary Data System** archive, audited and mapped, from the
[PDS Registry Search API](https://pds.mcp.nasa.gov/api/search/1/) (90.7 million
products across nine archive sites, October 2026).

Two questions about the archive as a whole:

1. **Does it still match its own checksums?** (`pds.AuditArchive`) A uniform
   random sample per archive site is fetched from the URL the registry records
   and compared with the size and MD5 it records.
2. **What can it place on a map?** (`pds.MapCoverage`) Every product carrying a
   bounding box, gridded per body, beside how many carry none.

> ⚠️ Both describe the archive **through its registry**. A file the registry
> does not list cannot be audited, and a product whose label records no
> coordinates cannot be mapped, however well its own files geolocate it.

## FFL at a glance

```ffl
audit    = pds.AuditArchive(per_node = 40, seed = 20261021)
coverage = pds.MapCoverage(min_products = 500, res = 1.0)
```

More in [docs/ffl-examples.md](docs/ffl-examples.md).

## What the first runs found (2026-10-21)

### The audit: 360 products, 40 per site

| Site | MD5 verified | Size only | Failed | Failure rate (95% CI) | Why |
|---|---:|---:|---:|---|---|
| PSA (ESA) | 40 | 0 | 0 | 0% (0–8.8%) | |
| PDS_GEO | 34 | 6 | 0 | 0% (0–8.8%) | |
| PDS_SBN | 39 | 1 | 0 | 0% (0–8.8%) | |
| PDS_PPI | 37 | 3 | 0 | 0% (0–8.8%) | |
| PDS_NAIF | 11 | 29 | 0 | 0% (0–8.8%) | SPICE kernels are large: mostly size-only |
| PDS_ATM | 39 | 0 | 1 | 2.5% (0.4–12.9%) | one Juno MWR file gone |
| PDS_IMG | 34 | 1 | 5 | 12.5% (5.5–26.1%) | MSL Mastcam paths with a doubled `archive/archive/` |
| PDS_ENG | 26 | 2 | 12 | 30% (18–45%) | registry records paths with no host |
| PDS_RMS | 10 | 0 | 30 | 75% (60–86%) | Cassini ISS/VIMS moved one directory deeper |

**Not one failure was corrupted content.** No data file came back with the
wrong checksum or the wrong length. Every failure is a *locator* problem: the
registry points somewhere the file is not.

- **Rings node**: the Cassini ISS and VIMS files are still served, at
  `bundles/cassini_iss/cassini_iss_saturn/…` where the registry says
  `bundles/cassini_iss_saturn/…`. About 1.8 million registered products
  (`cassini_iss_saturn`, `cassini_vims_saturn`, and the two cruise bundles)
  have URLs that answer 404 for files that exist.
- **Imaging node**: the sampled MSL Mastcam products carry
  `…/archive/archive/msl_mmm/…`; that path redirects to a CDN which answers
  403, and removing the doubled segment gives a 404. Through the registry these
  files cannot be reached at all.
- **Engineering node**: 3,058 of its 4,813 products carry no checksum, and
  sampled file references like `/data/pds4/…` have no host.
- **Labels are a separate story**, and are reported separately: 16 of 40
  atmospheres-node labels (LADEE UVS, Mars 2020 MEDA, Juno JIRAM) no longer
  match their registry record while the data files verify. The labels were
  edited on disk after they were harvested.

### Coverage: 0.48% of the archive can be found by location

436,502 of 90,693,862 products carry a bounding box. By site, the imaging node
(26.3 million products) has **425**; the rings node, none.

| Body | Products | With a box | Surface touched by 10+ products |
|---|---:|---:|---:|
| Mars | 48,123,945 | 340,792 (0.71%) | 93.2% |
| Moon | 4,319,113 | 53,891 (1.25%) | 39.1% |
| Jupiter | 754,033 | 37,429 (4.96%) | 93.1% |
| Venus | 272,288 | 1,359 (0.50%) | 58.8% |
| Mercury | 509,245 | 876 (0.17%) | 0.4% |

**The same body under different names.** Registry search is exact-match. Mars
products are split between `Mars` (26.2 million) and `MARS` (21.9 million), so
a search on either spelling misses a large share. The same holds for the Moon
(`Moon`, `MOON`, `Earth I (Moon)`), Bennu, Jupiter, the Mars Reconnaissance
Orbiter (`MARS RECONNAISSANCE ORBITER`, `>MARS RECONNAISSANCE ORBITER` with a
stray `>`, `Mars Reconnaissance Orbiter`), InSight, Juno and others: 720
bodies and 53 missions are spelled more than one way. The index lists them for
a person to judge; they are never merged into one count.

## Things this gets right on purpose

**A byte count is not a checksum.** Files above `max_mb` are checked for
existence and size only and reported as *size only*, never as verified.

**Missing means missing at the registered URL.** Not lost: the rings files
above exist.

**Data and labels are judged separately.** Otherwise a stale label record reads
as a damaged product.

**Transient answers are retried.** All five timeouts in the first run answered
within half a second minutes later; reported as failures they made two sites
look broken for no reason but load.

**What could not be verified is counted, not dropped.** Products with no
recorded MD5 cannot be sampled by the MD5 method; each site's count of them is
in the report.

**Uniform sampling without a random sort.** The API has none. Sorting by the
file MD5 and starting after a random 128-bit value works because MD5s are
uniform: what follows a random point in hash order is an unbiased draw. The
same seed draws the same sample, so a re-run re-checks the same products.

**Bounding boxes, repaired only where the repair is certain:**

- longitudes in -180..180 are folded to 0..360 east, and west-positive labels
  converted, not mirrored;
- west > east is a crossing of 0/360 *unless* west − east < 180, where the
  edges are simply reversed (CRISM tiles near the pole record west 182.76,
  east 178.44: literally a 356° band round the planet). 2,504 Mars boxes;
- a box spanning 90° of longitude or more away from the poles is a mosaic or
  a broken label and nothing says which (CRISM tiles mixing east- and
  west-positive edges; Mini-RF products named for 84° S with a box at 3–11° N),
  so it is kept off the grid and listed by collection;
- a box over 25% of the body is a global product and is counted, not gridded.

**Bounding boxes overstate coverage.** An oblique image's box is larger than
the ground it saw. The maps answer "which products claim to touch this cell".

## Running it

```bash
fw install domain pds          # or: pip install -e .
fw runner start --domain pds -- --topics 'pds.*'
fw ffl run --primary src/pdsarchive/ffl/pds.ffl --workflow pds.MapCoverage
fw ffl run --primary src/pdsarchive/ffl/pds.ffl --workflow pds.AuditArchive \
    --inputs '{"per_node": 40, "seed": 20261021}'
```

`FW_PDS_REGISTRY_URL` points at a different registry endpoint. Output goes to
the domain's storage (`cache/pds/output`, maps under `cache/pds/maps`), which
the local maps gallery lists.

The audit downloads from public science archives. `width` caps how many
batches run at once, and batches are interleaved across sites, so no single
server sees the whole fan-out.
