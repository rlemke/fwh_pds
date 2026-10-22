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

## 2. One site only, hashing everything

`Sample` reads every site from `Strata`; to audit one, give `CheckAll` only
that site's batches. Here the whole flow is spelled out, with a large
`max_mb` so nothing falls back to a size-only check.

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

## Syntax cheat sheet

| Construct | Here |
|---|---|
| fan-out with a cap | `andThen foreach b in $.batches limit $.width { … }` |
| aggregate a fan-out | `yield CheckAll(results += c.results_path)` |
| string concatenation | `$.work_dir ++ "/strata.json"` |
| map literal | `#{"key": "mars", "name": "Mars"}` |
| facet metadata | `with Effect(kind = "external") with Cost(tier = "free")` |
