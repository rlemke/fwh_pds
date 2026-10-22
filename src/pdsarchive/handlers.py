"""Handlers for the ``pds`` facets: the checksum audit and the coverage maps."""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
from typing import Any

from . import _audit, _coverage
from . import _registry as reg
from . import storage as store

log = logging.getLogger(__name__)


def _log(params: dict[str, Any]):
    cb = params.get("_step_log")
    return cb if callable(cb) else (lambda *a, **k: None)


def _now() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------------------
# 1. checksum audit
# ---------------------------------------------------------------------------

AUDIT_FIELDS = [
    reg.NODE,
    reg.INVESTIGATION,
    reg.TARGET,
    reg.FILE_REF,
    reg.FILE_SIZE,
    reg.FILE_MD5,
    reg.LABEL_REF,
    reg.LABEL_SIZE,
    reg.LABEL_MD5,
    reg.PRODUCT_CLASS,
]


def handle_strata(params: dict[str, Any]) -> dict[str, Any]:
    """Every archive site, with how many products it holds and how many carry an MD5."""
    say = _log(params)
    dest = store.resolve(str(params["dest"]))
    sites = reg.facets("", [reg.NODE], limit=200)[reg.NODE]
    strata: dict[str, dict[str, int]] = {}
    for node, n in sites.items():
        with_md5 = reg.count(reg.all_of(reg.eq(reg.NODE, node), f'{reg.FILE_MD5} ge "0"'))
        strata[node] = {"products": int(n), "with_md5": with_md5}
        say(f"{node}: {n:,} products, {n - with_md5:,} without an MD5")
    total = reg.count("")
    store.write_text(
        dest, json.dumps({"registry_products": total, "strata": strata, "generated": _now()}, indent=1)
    )
    return {"strata_path": dest, "nodes": sorted(strata), "products": total}


def handle_sample(params: dict[str, Any]) -> dict[str, Any]:
    """A uniform random sample per site, cut into batches for the fan-out.

    Batches are interleaved across sites, so a capped fan-out spreads its load
    over every archive server instead of working through one site at a time.
    """
    say = _log(params)
    strata = json.loads(store.read_text(str(params["strata_path"])))["strata"]
    per_node = int(params.get("per_node") or 40)
    seed = int(params.get("seed") or 1)
    batch_size = max(1, int(params.get("batch_size") or 10))
    dest = store.resolve(str(params["dest"]))
    sample: dict[str, list[dict[str, Any]]] = {}
    for i, node in enumerate(sorted(strata)):
        if not strata[node]["with_md5"]:
            say(f"{node}: no product carries an MD5; nothing can be sampled", level="warning")
            continue
        got = reg.random_sample(reg.eq(reg.NODE, node), per_node, seed + i, AUDIT_FIELDS)
        sample[node] = got
        say(f"{node}: sampled {len(got)} of {strata[node]['with_md5']:,}")
    store.write_text(dest, json.dumps(sample))
    per_site = {
        n: [rows[i : i + batch_size] for i in range(0, len(rows), batch_size)] for n, rows in sample.items()
    }
    batches = []
    for k in range(max((len(v) for v in per_site.values()), default=0)):
        for node in sorted(per_site):
            if k < len(per_site[node]):
                batches.append(
                    {
                        "sample_path": dest,
                        "node": node,
                        "start": k * batch_size,
                        "count": len(per_site[node][k]),
                    }
                )
    return {"sample_path": dest, "batches": batches, "sampled": sum(len(v) for v in sample.values())}


def handle_check_batch(params: dict[str, Any]) -> dict[str, Any]:
    say = _log(params)
    b = params["batch"]
    if isinstance(b, str):
        b = json.loads(b)
    rows = json.loads(store.read_text(b["sample_path"]))[b["node"]][b["start"] : b["start"] + b["count"]]
    max_bytes = int(float(params.get("max_mb") or 20) * 1024 * 1024)
    results = []
    for p in rows:
        pc = _audit.check_product(p, max_bytes)
        results.append(pc.as_dict())
        if pc.verdict in _audit.FAILURES:
            bad = next(f for f in pc.files if f.verdict in _audit.FAILURES)
            say(f"{b['node']} {pc.product}: {bad.verdict} {bad.url} {bad.detail}", level="warning")
    dest = store.resolve(str(params["dest"]))
    out = f"{dest.rstrip('/')}/{b['node']}-{b['start']:05d}.json"
    store.write_text(out, json.dumps(results))
    failed = sum(1 for r in results if r["verdict"] in _audit.FAILURES)
    return {"results_path": out, "checked": len(results), "failed": failed}


def handle_audit_report(params: dict[str, Any]) -> dict[str, Any]:
    from .render import render_audit

    say = _log(params)
    strata_doc = json.loads(store.read_text(str(params["strata_path"])))
    paths = params.get("results") or []
    if isinstance(paths, str):
        paths = [paths]
    products: list[dict[str, Any]] = []
    for path in paths:
        products.extend(json.loads(store.read_text(str(path))))
    summary = _audit.summarise(products, strata_doc["strata"])
    dest = store.resolve(str(params["dest"]))
    meta = {
        "generated": _now(),
        "seed": int(params.get("seed") or 1),
        "max_mb": float(params.get("max_mb") or 20),
        "failure_verdicts": sorted(_audit.FAILURES),
    }
    store.write_text(f"{dest.rstrip('/')}/audit.json", json.dumps({"meta": meta, **summary}, indent=1))
    html_path = store.maps("audit", "index.html")
    store.write_text(html_path, render_audit(summary, meta))
    failed = sum(s["failed"] for s in summary["sites"])
    say(
        f"{len(products)} products checked, {failed} failed; "
        f"~{summary['estimated_failing_products']:,} failing archive-wide (estimate)"
    )
    return {
        "report_path": f"{dest.rstrip('/')}/audit.json",
        "html_path": html_path,
        "checked": len(products),
        "failed": failed,
        "estimated_failing": summary["estimated_failing_products"],
    }


# ---------------------------------------------------------------------------
# 2. coverage
# ---------------------------------------------------------------------------

FOOTPRINT_FIELDS = [
    reg.WEST,
    reg.EAST,
    reg.NORTH,
    reg.SOUTH,
    reg.LON_DIRECTION,
    reg.TARGET,
    reg.INVESTIGATION,
]


def handle_census(params: dict[str, Any]) -> dict[str, Any]:
    """Registry-wide: how much of the archive carries a bounding box, per body and per site."""
    say = _log(params)
    dest = store.resolve(str(params["dest"]))
    total = reg.count("")
    boxed = reg.count(reg.HAS_FOOTPRINT)
    all_f = reg.facets("", [reg.TARGET, reg.INVESTIGATION, reg.NODE], limit=20000)
    box_f = reg.facets(reg.HAS_FOOTPRINT, [reg.TARGET, reg.NODE], limit=20000)
    # Registry product counts per BODY: spellings sharing a name key are summed,
    # because the footprints step groups them the same way.
    bodies: dict[str, dict[str, Any]] = {}
    for name, n in all_f[reg.TARGET].items():
        k = _coverage.name_key(name)
        b = bodies.setdefault(k, {"key": k, "spellings": {}, "registry_products": 0, "with_footprint": 0})
        b["spellings"][name] = n
        b["registry_products"] += n
    for name, n in box_f[reg.TARGET].items():
        k = _coverage.name_key(name)
        b = bodies.setdefault(k, {"key": k, "spellings": {}, "registry_products": 0, "with_footprint": 0})
        b["with_footprint"] += n
    sites = [
        {
            "node": node,
            "products": n,
            "with_footprint": box_f[reg.NODE].get(node, 0),
            "share": box_f[reg.NODE].get(node, 0) / n if n else None,
        }
        for node, n in sorted(all_f[reg.NODE].items(), key=lambda kv: -kv[1])
    ]
    doc = {
        "generated": _now(),
        "registry_products": total,
        "with_footprint": boxed,
        "share": boxed / total if total else None,
        "bodies": bodies,
        "sites": sites,
        "target_variants": _coverage.variants(all_f[reg.TARGET]),
        "investigation_variants": _coverage.variants(all_f[reg.INVESTIGATION]),
        "distinct_target_names": len(all_f[reg.TARGET]),
    }
    store.write_text(dest, json.dumps(doc, indent=1))
    say(
        f"{boxed:,} of {total:,} products carry a bounding box; "
        f"{len(doc['target_variants'])} bodies and {len(doc['investigation_variants'])} missions spelled more than one way"
    )
    return {"census_path": dest, "registry_products": total, "with_footprint": boxed}


def handle_footprints(params: dict[str, Any]) -> dict[str, Any]:
    """Every bounding box in the registry, split into one compact file per body."""
    say = _log(params)
    dest = store.resolve(str(params["dest"])).rstrip("/")
    census = json.loads(store.read_text(str(params["census_path"])))
    min_products = int(params.get("min_products") or 500)
    per_body: dict[str, list[list[Any]]] = {}
    seen = 0
    for page in reg.pages(reg.HAS_FOOTPRINT, FOOTPRINT_FIELDS):
        for p in page:
            seen += 1
            key = _coverage.name_key(reg.first(p, reg.TARGET)) or "(no target recorded)"
            per_body.setdefault(key, []).append(_coverage.compact(p))
        if seen % 50000 < len(page):
            say(f"{seen:,} footprints read")
    bodies = []
    for key, rows in sorted(per_body.items(), key=lambda kv: -len(kv[1])):
        if len(rows) < min_products:
            continue
        info = census["bodies"].get(key) or {}
        name = _coverage.display_name(info.get("spellings") or {}) or key
        path = f"{dest}/{_slug(key)}.json"
        store.write_text(path, json.dumps(rows, separators=(",", ":")))
        bodies.append(
            {
                "key": key,
                "name": name,
                "footprints_path": path,
                "footprints": len(rows),
                "registry_products": int(info.get("registry_products") or 0),
            }
        )
    say(f"{seen:,} footprints over {len(per_body)} bodies; {len(bodies)} have at least {min_products}")
    return {"bodies": bodies, "footprints": seen}


def handle_body_map(params: dict[str, Any]) -> dict[str, Any]:
    from .render import render_coverage_map

    say = _log(params)
    body = params["body"]
    if isinstance(body, str):
        body = json.loads(body)
    rows = json.loads(store.read_text(body["footprints_path"]))
    cov = _coverage.build([_coverage.expand(r) for r in rows], body["name"], float(params.get("res") or 1.0))
    # One folder per body, each with its own index.html: that is the layout the
    # local maps gallery lists, so every body appears there as a map of its own.
    html_path = store.maps("coverage", _slug(body["key"]), "index.html")
    store.write_text(html_path, render_coverage_map(cov, body["registry_products"], _now()))
    covered = cov.grid.covered_fraction()
    say(
        f"{body['name']}: {cov.gridded:,} boxes gridded, {cov.global_products:,} global, "
        f"{100 * covered:.1f}% of the surface touched"
    )
    return {
        "map_path": html_path,
        "summary": {
            "key": body["key"],
            "name": body["name"],
            "map": f"{_slug(body['key'])}/index.html",
            "products": cov.products,
            "gridded": cov.gridded,
            "global": cov.global_products,
            "covered": covered,
            "covered10": cov.grid.covered_fraction(at_least=10),
            "rejected": cov.rejected,
        },
    }


def handle_coverage_index(params: dict[str, Any]) -> dict[str, Any]:
    from .render import render_coverage_index

    census = json.loads(store.read_text(str(params["census_path"])))
    maps = params.get("maps") or []
    if isinstance(maps, dict):
        maps = [maps]
    by_key = {m["key"]: m for m in maps}
    targets = []
    for key, b in sorted(census["bodies"].items(), key=lambda kv: -kv[1]["registry_products"]):
        if not b["with_footprint"] and b["registry_products"] < 100000:
            continue
        m = by_key.get(key) or {}
        targets.append(
            {
                "target": _coverage.display_name(b.get("spellings") or {}) or key,
                "registry_products": b["registry_products"],
                "with_footprint": b["with_footprint"],
                "share": b["with_footprint"] / b["registry_products"] if b["registry_products"] else None,
                "covered": m.get("covered"),
                "covered10": m.get("covered10"),
                "map": m.get("map"),
            }
        )
    summary = {**census, "targets": targets}
    html_path = store.maps("coverage", "index.html")
    store.write_text(html_path, render_coverage_index(summary))
    return {"index_path": html_path, "bodies_listed": len(targets), "maps": len(maps)}


def _slug(key: str) -> str:
    s = "".join(c if c.isalnum() else "-" for c in key.lower()).strip("-")
    while "--" in s:
        s = s.replace("--", "-")
    return s or "unnamed"


_DISPATCH = {
    "pds.audit.Strata": handle_strata,
    "pds.audit.Sample": handle_sample,
    "pds.audit.CheckBatch": handle_check_batch,
    "pds.audit.Report": handle_audit_report,
    "pds.coverage.Census": handle_census,
    "pds.coverage.Footprints": handle_footprints,
    "pds.coverage.BodyMap": handle_body_map,
    "pds.coverage.Index": handle_coverage_index,
}


def handle(payload: dict) -> dict:
    facet = payload["_facet_name"]
    fn = _DISPATCH.get(facet)
    if fn is None:
        raise ValueError(f"Unknown facet: {facet}")
    return fn(payload)


def register_handlers(runner) -> None:
    """RegistryRunner registration — one entrypoint per facet."""
    for facet_name in _DISPATCH:
        runner.register_handler(
            facet_name=facet_name,
            module_uri=f"file://{os.path.abspath(__file__)}",
            entrypoint="handle",
        )


def register_poller(poller) -> None:
    for facet_name, fn in _DISPATCH.items():
        poller.register(facet_name, fn)


def register_all_registry_handlers(runner) -> None:
    register_handlers(runner)
