# FFL examples: fwh_pds

Every block compiles against `src/pdsarchive/ffl/pds.ffl`:

```bash
fw ffl compile --check --primary <block.ffl> --library src/pdsarchive/ffl/pds.ffl
```

## 1. The whole audit

```ffl
namespace example.audit {
    workflow Pilot() => (html: String, failed: Long) andThen {
        run = pds.AuditArchive(per_node = 40, seed = 20261021)
        yield Pilot(html = run.html, failed = run.failed)
    }
}
```

The same seed draws the same sample, so a re-run is a re-check of the same
products, not a new sample.

## 2. The flow spelled out: a larger sample, hashing everything

The same four steps `AuditArchive` runs, written out, with 100 products per
site and a large `max_mb` so nothing falls back to a size-only check.
`Sample` draws from every site `Strata` lists; there is no per-site filter.

```ffl
namespace example.onesite {
    workflow Rings(work_dir: String = "audit-rings") => (failed: Long) andThen {
        strata = pds.audit.Strata(dest = $.work_dir ++ "/strata.json")
        sample = pds.audit.Sample(strata_path = strata.strata_path, per_node = 100,
            seed = 7, batch_size = 10, dest = $.work_dir ++ "/sample.json")
        checks = pds.audit.CheckAll(batches = sample.batches, max_mb = 500.0, width = 2,
            dest = $.work_dir ++ "/results")
        report = pds.audit.Report(strata_path = strata.strata_path, results = checks.results,
            seed = 7, max_mb = 500.0, dest = $.work_dir)
        yield Rings(failed = report.failed)
    }
}
```

`width = 2` because every batch here goes to the archive servers: the cap is
there for them.

## 3. Coverage at a finer grid

```ffl
namespace example.coverage {
    workflow HalfDegree() => (index: String) andThen {
        run = pds.MapCoverage(min_products = 1000, res = 0.5, width = 2)
        yield HalfDegree(index = run.index)
    }
}
```

## 4. One body's map from footprints already downloaded

`Footprints` writes one file per body; `BodyMap` reads one. Re-rendering
needs no registry calls.

```ffl
namespace example.redraw {
    workflow Mars(footprints_path: String, registry_products: Long) => (map: String) andThen {
        m = pds.coverage.BodyMap(body = #{"key": "mars", "name": "Mars",
            "footprints_path": $.footprints_path, "registry_products": $.registry_products}, res = 1.0)
        yield Mars(map = m.map_path)
    }
}
```

## 5. Decide on the result: `when` attached to the step it gates

Inside the cases, `$` is the `AuditArchive` step, so `$.failed` is its return.

```ffl
namespace example.verdict {
    workflow Verdict() => (message: String) andThen {
        run = pds.AuditArchive(per_node = 40, seed = 20261021) andThen when {
            case $.failed == 0 => {
                yield Verdict(message = "every sampled product verified")
            }
            case _ => {
                yield Verdict(message = "some sampled products failed; see the report")
            }
        }
    }
}
```

## 6. Several seeds, one at a time: `foreach … limit 1` and `+=`

Each seed is an independent sample. `limit 1` runs them one after another, so
the archive servers never see two audits at once; `+=` keeps every run's
failure count, in seed order.

```ffl
namespace example.seeds {
    facet AuditSeeds(seeds: Json) => (failed: Json)
        andThen foreach s in $.seeds limit 1 {
            run = pds.AuditArchive(per_node = 40, seed = $.s, work_dir = "audit-seeds")
            yield AuditSeeds(failed += run.failed)
        }

    workflow Repeat() => (failed: Json) andThen {
        all = AuditSeeds(seeds = [1, 2, 3])
        yield Repeat(failed = all.failed)
    }
}
```

⚠️ All three runs share `work_dir`, so each overwrites the last one's report;
only the failure counts are kept separately.

## Syntax cheat sheet

| Construct | Here |
|---|---|
| fan-out with a cap | `andThen foreach b in $.batches limit $.width { … }` |
| branch on a result | `run = AuditArchive(…) andThen when { case $.failed == 0 => { … } case _ => { … } }` |
| aggregate a fan-out | `yield CheckAll(results += c.results_path)` |
| string concatenation | `$.work_dir ++ "/strata.json"` |
| map literal | `#{"key": "mars", "name": "Mars"}` |
| facet metadata | `with Effect(kind = "external") with Cost(tier = "free")` |
