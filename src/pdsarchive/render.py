"""Self-contained HTML: no CDN, no basemap tiles, no external requests.

A planetary basemap would need a tile server per body; the grid itself is the
content, so each map is drawn on a canvas from data embedded in the page.
"""

from __future__ import annotations

import html
import json
from typing import Any

_CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--line:#dedbd2;--card:#ffffff;--accent:#8a3b12;--bad:#a4161a;--ok:#2d6a4f;--empty:#ece9e1}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#15161a;--fg:#e8e6e1;--muted:#9a988f;--line:#2c2e35;--card:#1d1f24;--accent:#e0915c;--bad:#ef6f6c;--ok:#74c69d;--empty:#24262c}}
:root[data-theme="dark"]{--bg:#15161a;--fg:#e8e6e1;--muted:#9a988f;--line:#2c2e35;--card:#1d1f24;--accent:#e0915c;--bad:#ef6f6c;--ok:#74c69d;--empty:#24262c}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.6rem;margin:0 0 4px}
h2{font-size:1.15rem;margin:32px 0 8px}
p.sub{color:var(--muted);margin:0 0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin:12px 0}
.warn{border-left:4px solid var(--accent)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}
.stat b{display:block;font-size:1.35rem}
.stat span{color:var(--muted);font-size:.85rem}
table{border-collapse:collapse;width:100%;font-size:.9rem}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--muted)}
td.n{text-align:right;font-variant-numeric:tabular-nums}
.scroll{overflow-x:auto}
.bad{color:var(--bad)} .ok{color:var(--ok)}
code{font-size:.85em;word-break:break-all}
canvas{width:100%;height:auto;display:block;border:1px solid var(--line);border-radius:6px;background:var(--empty);image-rendering:pixelated}
.bar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:8px 0}
select{font:inherit;padding:4px 6px;background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:4px}
#readout{color:var(--muted);min-height:1.5em;font-variant-numeric:tabular-nums}
.legend{display:flex;align-items:center;gap:8px;font-size:.85rem;color:var(--muted)}
.legend i{display:inline-block;width:180px;height:10px;border-radius:2px}
a{color:var(--accent)}
"""


def _page(title: str, body: str, script: str = "") -> str:
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head>"
        f"<body><main>{body}</main>{('<script>' + script + '</script>') if script else ''}</body></html>"
    )


def _n(x: Any) -> str:
    if x is None:
        return "–"
    if isinstance(x, float):
        return f"{x:,.1f}"
    return f"{x:,}"


def _pct(x: float | None, digits: int = 1) -> str:
    if x is None:
        return "–"
    floor = 10**-digits
    if 0 < 100 * x < floor:
        # A nonzero share must never print as 0: 16 boxes among 926,997 Saturn
        # products is "almost none", not "none".
        return f"<{floor:g}%"
    return f"{100 * x:.{digits}f}%"


# --- coverage map -----------------------------------------------------------

_MAP_JS = r"""
const D = JSON.parse(document.getElementById('data').textContent);
const cv = document.getElementById('map'), ctx = cv.getContext('2d');
cv.width = D.cols; cv.height = D.rows;
const sel = document.getElementById('mission'), out = document.getElementById('readout');
const stat = document.getElementById('selstat');
// viridis-like ramp, 8 stops
const RAMP = [[68,1,84],[70,50,127],[54,92,141],[39,127,142],[31,161,135],[74,193,109],[160,218,57],[253,231,37]];
function ramp(t){t=Math.max(0,Math.min(1,t));const x=t*(RAMP.length-1),i=Math.floor(x),f=x-i;
  const a=RAMP[i],b=RAMP[Math.min(i+1,RAMP.length-1)];return [0,1,2].map(k=>Math.round(a[k]+(b[k]-a[k])*f));}
function cells(m){const arr=new Float64Array(D.cols*D.rows);const src=m?D.missions[m].cells:D.total;
  for(const [i,c] of src) arr[i]=c; return arr;}
function draw(){
  const m = sel.value, a = cells(m);
  let max = 0; for (const v of a) if (v>max) max=v;
  const img = ctx.createImageData(D.cols, D.rows), lm = Math.log10(max+1);
  for (let i=0;i<a.length;i++){ const o=i*4;
    if (!a[i]) { img.data[o+3]=0; continue; }
    const [r,g,b] = ramp(lm? Math.log10(a[i]+1)/lm : 1); img.data[o]=r; img.data[o+1]=g; img.data[o+2]=b; img.data[o+3]=255; }
  ctx.putImageData(img,0,0);
  ctx.strokeStyle='rgba(128,128,128,.45)'; ctx.lineWidth=Math.max(1,D.cols/720);
  for (let lon=0; lon<=360; lon+=30){const x=lon/360*D.cols; ctx.beginPath(); ctx.moveTo(x,0); ctx.lineTo(x,D.rows); ctx.stroke();}
  for (let lat=-60; lat<=60; lat+=30){const y=(90-lat)/180*D.rows; ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(D.cols,y); ctx.stroke();}
  document.getElementById('lmax').textContent = max.toLocaleString();
  const s = m ? D.missions[m] : D.summary;
  stat.textContent = (m? m+': ' : 'All missions: ') + s.gridded.toLocaleString() + ' products in the grid, covering '
    + (100*s.covered).toFixed(1) + '% of the surface (area-weighted, by bounding box).';
  window._cur = a;
}
cv.addEventListener('mousemove', e => {
  const r = cv.getBoundingClientRect(), c = Math.floor((e.clientX-r.left)/r.width*D.cols), w = Math.floor((e.clientY-r.top)/r.height*D.rows);
  if (c<0||w<0||c>=D.cols||w>=D.rows) return;
  const lon = (c+0.5)*D.res, lat = 90-(w+0.5)*D.res, v = window._cur[w*D.cols+c];
  out.textContent = `${lat.toFixed(1)}° lat, ${lon.toFixed(1)}° E: ${v.toLocaleString()} product${v===1?'':'s'}`;
});
cv.addEventListener('mouseleave', () => out.textContent = '');
for (const m of Object.keys(D.missions).sort((a,b)=>D.missions[b].gridded-D.missions[a].gridded)) {
  const o=document.createElement('option'); o.value=m; o.textContent=`${m} (${D.missions[m].gridded.toLocaleString()})`; sel.appendChild(o); }
sel.addEventListener('change', draw); draw();
"""


def render_coverage_map(cov: Any, registry_products: int, generated: str) -> str:
    """One body's coverage map. ``registry_products`` is ALL the body's products,
    with or without a footprint, so the page can say how much it is drawn from."""
    g = cov.grid
    data = {
        "cols": g.cols,
        "rows": g.rows,
        "res": g.res,
        "total": sorted(g.total.items()),
        "summary": {"gridded": cov.gridded, "covered": g.covered_fraction()},
        "missions": {
            m: {
                "cells": sorted(cells.items()),
                "gridded": cov.missions.get(m, 0),
                "covered": g.covered_fraction(cells),
            }
            for m, cells in g.by_mission.items()
        },
    }
    share = cov.products / registry_products if registry_products else None
    rejected = ", ".join(f"{k}: {v:,}" for k, v in sorted((cov.rejected or {}).items())) or "none"
    globals_ = ", ".join(
        f"{html.escape(m)} {n:,}"
        for m, n in sorted((cov.global_by_mission or {}).items(), key=lambda kv: -kv[1])
    )
    wide_rows = "".join(
        f"<tr><td><code>{html.escape(c)}</code></td><td class=n>{_n(w['count'])}</td><td><code>{html.escape(w['example'])}</code></td></tr>"
        for c, w in sorted((cov.wide_by_collection or {}).items(), key=lambda kv: -kv[1]["count"])
    )
    wide_table = (
        f"<div class=scroll><table><tr><th>Collection</th><th>Wide boxes</th><th>Example</th></tr>{wide_rows}</table></div>"
        if wide_rows
        else ""
    )
    t = html.escape(cov.target)
    body = f"""
<h1>{t}: where the PDS archive has data</h1>
<p class=sub>Product counts per {g.res:g}° cell, from the bounding boxes in the PDS Registry. Generated {html.escape(generated)}.</p>
<div class="card warn"><b>Read this first.</b> Of {_n(registry_products)} {t} products in the registry,
only {_n(cov.products)} ({_pct(share, 2)}) carry a bounding box, so this map is drawn from that fraction and nothing else.
A missing cell can mean "never observed" or "observed, but the label records no coordinates"; the registry cannot tell those apart.
A bounding box is also larger than the ground an oblique image actually saw, so coverage is <i>overstated</i>.</div>
<div class="stats card">
 <div class=stat><b>{_n(cov.products)}</b><span>products with a bounding box</span></div>
 <div class=stat><b>{_n(cov.gridded)}</b><span>drawn on the grid</span></div>
 <div class=stat><b>{_n(cov.global_products)}</b><span>global products, kept off the grid</span></div>
 <div class=stat><b>{_pct(g.covered_fraction())}</b><span>of the surface touched by at least one box</span></div>
 <div class=stat><b>{_pct(g.covered_fraction(at_least=10))}</b><span>touched by 10 or more</span></div>
</div>
<div class=bar><label>Mission <select id=mission><option value="">All missions</option></select></label>
<span class=legend>1 <i style="background:linear-gradient(90deg,#440154,#46327e,#365c8d,#277f8e,#1fa187,#4ac16d,#a0da39,#fde725)"></i> <span id=lmax></span> products (log scale)</span></div>
<canvas id=map aria-label="Coverage map of {t}"></canvas>
<div id=readout></div>
<p id=selstat></p>
<h2>What is not on the map</h2>
<table><tr><th>Global products (a box over more than 25% of the body)</th><td>{globals_ or "none"}</td></tr>
<tr><th>Boxes rejected as invalid</th><td>{rejected}</td></tr>
<tr><th>Boxes with west and east reversed, read the narrower way</th><td>{_n(cov.reversed_edges)}</td></tr>
<tr><th>Wide boxes away from the poles (90&deg; of longitude or more): a mosaic or a broken label, kept off the grid</th><td>{_n(cov.wide)}</td></tr></table>
{wide_table}
<p class=sub>Longitude is east-positive, 0° to 360°. Products labelled west-positive were converted; boxes crossing 0°/360° were split.
Each logical product is counted once, at its latest version.</p>
"""
    return _page(
        f"{cov.target} coverage",
        body + f'<script id=data type="application/json">{json.dumps(data, separators=(",", ":"))}</script>',
        _MAP_JS,
    )


def render_coverage_index(summary: dict[str, Any]) -> str:
    rows = []
    for t in summary["targets"]:
        link = f'<a href="{html.escape(t["map"])}">map</a>' if t.get("map") else ""
        rows.append(
            f"<tr><td>{html.escape(t['target'])}</td><td class=n>{_n(t['registry_products'])}</td>"
            f"<td class=n>{_n(t['with_footprint'])}</td><td class=n>{_pct(t['share'], 2)}</td>"
            f"<td class=n>{_pct(t.get('covered'))}</td><td class=n>{_pct(t.get('covered10'))}</td><td>{link}</td></tr>"
        )
    sites = "".join(
        f"<tr><td>{html.escape(s['node'])}</td><td class=n>{_n(s['products'])}</td><td class=n>{_n(s['with_footprint'])}</td>"
        f"<td class=n>{_pct(s['share'], 3)}</td></tr>"
        for s in summary["sites"]
    )

    def _variants(groups: list[dict[str, Any]], what: str) -> str:
        if not groups:
            return f"<p>No {what} spelled more than one way.</p>"
        shown, more = groups[:150], len(groups) - 150
        trs = "".join(
            "<tr><td>"
            + "<br>".join(f"<code>{html.escape(repr(k))}</code>" for k in g["spellings"])
            + "</td><td class=n>"
            + "<br>".join(_n(v) for v in g["spellings"].values())
            + "</td></tr>"
            for g in shown
        )
        tail = f"<p class=sub>…and {more:,} more groups, in census.json.</p>" if more > 0 else ""
        return f"<p class=sub>{len(groups):,} groups.</p><div class=scroll><table><tr><th>Spellings</th><th>Products</th></tr>{trs}</table></div>{tail}"

    body = f"""
<h1>What the PDS archive can place on a map</h1>
<p class=sub>Registry-wide counts from the PDS Registry Search API, {html.escape(summary["generated"])}.
{_n(summary["registry_products"])} products in all; {_n(summary["with_footprint"])} ({_pct(summary["share"], 2)}) carry a bounding box.</p>
<div class="card warn">Coverage below is what the <b>registry's labels</b> record, not what the missions observed.
A product without a bounding box may well have been geolocated in its own files or in a mission-specific index;
it simply cannot be found by location through the registry.</div>
<h2>By body</h2>
<div class=scroll><table><tr><th>Body</th><th>Products</th><th>With a box</th><th>Share</th><th>Surface touched</th><th>By 10+ products</th><th></th></tr>{"".join(rows)}</table></div>
<h2>By archive site</h2>
<div class=scroll><table><tr><th>Site</th><th>Products</th><th>With a box</th><th>Share</th></tr>{sites}</table></div>
<h2>The same body under different names</h2>
<p class=sub>Spellings that differ only in case, punctuation or a catalogue number. Searching for one misses the others.
Grouped for a person to judge, never merged.</p>
{_variants(summary["target_variants"], "bodies")}
<h2>The same mission under different names</h2>
{_variants(summary["investigation_variants"], "missions")}
"""
    return _page("PDS coverage", body)


# --- audit report -------------------------------------------------------------


def render_audit(summary: dict[str, Any], meta: dict[str, Any]) -> str:
    rows = []
    for s in summary["sites"]:
        rate = s["failure_rate"]
        lo, hi = s["ci95"]
        cls = "bad" if s["failed"] else "ok"
        rows.append(
            f"<tr><td>{html.escape(s['node'])}</td><td class=n>{_n(s['products'])}</td>"
            f"<td class=n>{_n(s['without_md5'])}</td><td class=n>{_n(s['sampled'])}</td>"
            f"<td class=n>{_n(s['verified_md5'])}</td><td class=n>{_n(s['size_only'])}</td>"
            f"<td class='n {cls}'>{_n(s['failed'])}</td><td class=n>{_n(s['undetermined'])}</td>"
            f"<td class=n>{_pct(rate)} <span class=sub>({_pct(lo)}–{_pct(hi)})</span></td>"
            f"<td class=n>{_n(s['estimated_failing_products'])}</td>"
            f"<td class=n>{_n(s['labels_failed'])} / {_n(s['labels_judged'])} "
            f"<span class=sub>({_pct(s['label_failure_rate'])})</span></td></tr>"
        )
    fails = []
    for p in summary["failures"][:500]:
        for f in p["files"]:
            if f["verdict"] in meta["failure_verdicts"]:
                fails.append(
                    f"<tr><td>{html.escape(p['node'])}</td><td><code>{html.escape(p['product'])}</code></td>"
                    f"<td>{html.escape(f['role'])}</td><td class=bad>{html.escape(f['verdict'])}</td>"
                    f"<td><code>{html.escape(f['url'])}</code></td><td>{html.escape(str(f.get('http_status') or ''))} "
                    f"{html.escape(f.get('detail') or '')}</td></tr>"
                )
    stale = []
    for p in summary.get("label_failures", [])[:500]:
        for f in p["files"]:
            if f["role"] == "label" and f["verdict"] in meta["failure_verdicts"]:
                stale.append(
                    f"<tr><td>{html.escape(p['node'])}</td><td><code>{html.escape(p['product'])}</code></td>"
                    f"<td class=bad>{html.escape(f['verdict'])}</td><td class=n>{_n(f.get('recorded_size'))}</td>"
                    f"<td class=n>{_n(f.get('observed_size'))}</td><td><code>{html.escape(f['url'])}</code></td></tr>"
                )
    body = f"""
<h1>Does the PDS archive still match its own checksums?</h1>
<p class=sub>A random sample of {_n(summary["sampled"])} products, fetched from the URLs the PDS Registry records and compared
with the size and MD5 it records. Run {html.escape(meta["generated"])}, seed {meta["seed"]}.</p>
<div class="card warn"><b>How to read the rates.</b> A product <i>fails</i> if a data file is missing at the URL the registry records, unreachable, or differs from its
recorded checksum or size. Files larger than {meta["max_mb"]:g} MB were checked for existence and size only ("size only"), which proves
they are not missing or truncated but not that their content is intact. The failure rate is over products that reached a verdict;
the interval is a 95% Wilson interval, and the estimate scales the rate to the products that site holds with a checksum.
Label files are judged separately (last column), so a stale registry entry for a label is not reported as damaged data.
<i>Missing</i> means missing at that URL, not lost: a site that moves its files without the registry being updated produces exactly this.
Unreachable and timeout count as failures <i>from here</i>; a site that blocks one client is not the same as a lost file, so read the detail column.</div>
<div class="stats card">
 <div class=stat><b>{_n(summary["sampled"])}</b><span>products sampled</span></div>
 <div class=stat><b>{_n(sum(s["failed"] for s in summary["sites"]))}</b><span>failed</span></div>
 <div class=stat><b>{_n(summary["estimated_failing_products"])}</b><span>estimated failing products, archive-wide</span></div>
 <div class=stat><b>{_n(sum(s["without_md5"] for s in summary["sites"]))}</b><span>products with no checksum to verify</span></div>
</div>
<h2>By archive site</h2>
<div class=scroll><table><tr><th>Site</th><th>Products</th><th>No checksum</th><th>Sampled</th><th>MD5 verified</th><th>Size only</th>
<th>Failed</th><th>Undetermined</th><th>Failure rate (95% CI)</th><th>Est. failing</th><th>Labels differing</th></tr>{"".join(rows)}</table></div>
<h2>Every failure</h2>
<div class=scroll><table><tr><th>Site</th><th>Product</th><th>File</th><th>Verdict</th><th>URL</th><th>Detail</th></tr>{"".join(fails) or "<tr><td colspan=6>None.</td></tr>"}</table></div>
<h2>Labels that no longer match the registry</h2>
<p class=sub>Products whose data files are fine but whose label (.xml) differs from what the registry recorded:
usually a label edited on disk after harvest, so a search on the registry describes a label that is no longer served.</p>
<div class=scroll><table><tr><th>Site</th><th>Product</th><th>Verdict</th><th>Recorded bytes</th><th>Served bytes</th><th>Label URL</th></tr>{"".join(stale) or "<tr><td colspan=6>None.</td></tr>"}</table></div>
<h2>Method</h2>
<p>Products are drawn per site by sorting on the data file's MD5 and starting at a random 128-bit value: MD5s are uniformly distributed,
so what follows a random point in hash order is an unbiased draw. Products with no recorded MD5 cannot be drawn that way and are counted
in the "No checksum" column instead. Every data file of a sampled product is checked, and its label (.xml) file too.</p>
"""
    return _page("PDS checksum audit", body)
