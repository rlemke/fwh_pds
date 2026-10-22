"""Does an archived file still match the checksum its registry entry records?

One verdict per file, chosen so that what was NOT checked is never reported as
a pass:

========================  ====================================================
``verified``              downloaded; size and MD5 both match the registry
``md5_mismatch``          downloaded; size matches, content does not
``size_mismatch``         downloaded; a different number of bytes
``soft_404``              HTTP 200, but the body is an HTML error page
``missing``               404 / 410 at the URL the REGISTRY records. Not the
                          same as lost: measured on the rings node, the Cassini
                          ISS/VIMS files are still served, one directory deeper
                          than the registry says, so about 1.8M registered URLs
                          answer 404 for files that exist.
``http_error``            any other non-200 answer
``unreachable``           DNS, refused connection, TLS failure
``timeout``               no answer, or the transfer stalled
``size_only_match``       too large to hash here; Content-Length agrees
``size_only_mismatch``    too large to hash here; Content-Length disagrees
``unverified_large``      too large to hash, and no Content-Length to compare
``no_checksum``           the registry records no MD5 for this file
``no_file_ref``           the registry records no URL for this file
``relative_ref``          the registry records a path with no host, which no
                          client can fetch (measured: the engineering node)
========================  ====================================================

Data files and label files are judged SEPARATELY. A label (.xml) that differs
from its registry record usually means the label was edited on disk after it
was harvested, with the data file untouched (measured: an atmospheres-node
label modified in 2024, 11,961 recorded vs 12,856 served bytes, beside a data
file that verified). Folding the two together would report a stale registry
entry as a damaged product.

``size_only_*`` and ``unverified_large`` are deliberately separate from
``verified``: a byte count proves the file exists and was not truncated, not
that its content is intact.
"""

from __future__ import annotations

import hashlib
import math
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

from . import _registry as reg

FAILURES = {
    "md5_mismatch",
    "size_mismatch",
    "soft_404",
    "missing",
    "http_error",
    "unreachable",
    "timeout",
    "size_only_mismatch",
    "relative_ref",
}
#: Could not reach a verdict about the content; neither pass nor fail.
UNDETERMINED = {"unverified_large", "no_checksum", "no_file_ref"}
PASSES = {"verified", "size_only_match"}

_CHUNK = 1 << 16


@dataclass
class FileCheck:
    product: str
    role: str  # "data" or "label"
    url: str
    host: str
    verdict: str
    recorded_size: int | None = None
    recorded_md5: str = ""
    observed_size: int | None = None
    observed_md5: str = ""
    http_status: int | None = None
    detail: str = ""
    seconds: float = 0.0


@dataclass
class ProductCheck:
    product: str
    node: str
    investigation: str
    target: str
    files: list[FileCheck] = field(default_factory=list)

    _ORDER = (
        "missing",
        "soft_404",
        "relative_ref",
        "md5_mismatch",
        "size_mismatch",
        "size_only_mismatch",
        "http_error",
        "unreachable",
        "timeout",
        "no_file_ref",
        "no_checksum",
        "unverified_large",
        "size_only_match",
        "verified",
    )

    def _worst(self, role: str) -> str:
        got = {f.verdict for f in self.files if f.role == role}
        return next((v for v in self._ORDER if v in got), "")

    @property
    def verdict(self) -> str:
        """The product's verdict is its worst DATA file's: one bad file is a bad product."""
        return self._worst("data") or "no_file_ref"

    @property
    def label_verdict(self) -> str:
        """The worst label file's verdict ('' when the registry lists no label)."""
        return self._worst("label")

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["verdict"] = self.verdict
        d["label_verdict"] = self.label_verdict
        return d


def _int(v: str) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def check_file(
    product: str,
    role: str,
    url: str,
    size: int | None,
    md5: str,
    max_bytes: int,
    timeout: int = 60,
    opener: Any = None,
) -> FileCheck:
    """Fetch one file and compare it with what the registry says it should be."""
    host = urlparse(url).hostname or ""
    fc = FileCheck(product, role, url, host, "", recorded_size=size, recorded_md5=md5.lower())
    if not url:
        fc.verdict = "no_file_ref"
        return fc
    if urlparse(url).scheme not in ("http", "https"):
        fc.verdict, fc.detail = "relative_ref", "not an http(s) URL"
        return fc
    if not md5:
        # Still worth knowing whether it exists, but the content cannot be judged.
        fc.verdict = "no_checksum"
    started = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": reg.USER_AGENT})
    open_ = opener or urllib.request.urlopen
    try:
        with open_(req, timeout=timeout) as resp:
            fc.http_status = getattr(resp, "status", 200)
            length = _int(resp.headers.get("Content-Length", ""))
            if length is not None and length > max_bytes:
                fc.observed_size = length
                if fc.verdict != "no_checksum":
                    fc.verdict = "size_only_match" if size is None or length == size else "size_only_mismatch"
                    if size is None:
                        fc.verdict = "unverified_large"
                return _done(fc, started)
            h = hashlib.md5()
            n = 0
            head = b""
            while True:
                chunk = resp.read(_CHUNK)
                if not chunk:
                    break
                if len(head) < 512:
                    head += chunk[: 512 - len(head)]
                h.update(chunk)
                n += len(chunk)
                if n > max_bytes:
                    fc.observed_size = None
                    if fc.verdict != "no_checksum":
                        fc.verdict = "unverified_large"
                    fc.detail = f"no Content-Length and over {max_bytes} bytes"
                    return _done(fc, started)
            fc.observed_size = n
            fc.observed_md5 = h.hexdigest()
    except urllib.error.HTTPError as exc:
        fc.http_status = exc.code
        fc.verdict = "missing" if exc.code in (404, 410) else "http_error"
        fc.detail = str(exc.reason)[:200]
        return _done(fc, started)
    except TimeoutError as exc:
        fc.verdict, fc.detail = "timeout", str(exc)[:200]
        return _done(fc, started)
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            fc.verdict = "timeout"
        else:
            fc.verdict = "unreachable"
        fc.detail = f"{type(reason).__name__}: {reason}"[:200]
        return _done(fc, started)
    except (ConnectionError, ssl.SSLError, OSError) as exc:
        fc.verdict, fc.detail = "unreachable", f"{type(exc).__name__}: {exc}"[:200]
        return _done(fc, started)

    if fc.verdict == "no_checksum":
        return _done(fc, started)
    if fc.observed_md5 == fc.recorded_md5 and (size is None or fc.observed_size == size):
        fc.verdict = "verified"
    elif _looks_like_html(head) and not _expects_html(url):
        # A server that answers 200 with an error page would otherwise read as
        # "corrupted content" — the file is not corrupt, it is not there.
        fc.verdict = "soft_404"
    elif size is not None and fc.observed_size != size:
        fc.verdict = "size_mismatch"
    else:
        fc.verdict = "md5_mismatch"
    return _done(fc, started)


def _done(fc: FileCheck, started: float) -> FileCheck:
    fc.seconds = round(time.monotonic() - started, 3)
    return fc


def _looks_like_html(head: bytes) -> bool:
    h = head.lstrip()[:64].lower()
    return h.startswith((b"<!doctype html", b"<html"))


def _expects_html(url: str) -> bool:
    return url.lower().endswith((".htm", ".html"))


#: Verdicts that say more about the moment than about the file.
TRANSIENT = {"timeout", "unreachable"}


def _transient(fc: FileCheck) -> bool:
    return fc.verdict in TRANSIENT or (
        fc.verdict == "http_error"
        and fc.http_status is not None
        and (fc.http_status >= 500 or fc.http_status == 429)
    )


def check_file_retrying(*args: Any, attempts: int = 3, backoff: float = 5.0, **kw: Any) -> FileCheck:
    """:func:`check_file`, retried on a transient answer before it becomes a verdict.

    ⚠️ Measured: all 5 timeouts in the first 360-product audit answered within
    half a second when re-tried minutes later. Reported as failures they made
    two sites look 2.5% and 7.5% broken for no reason but load at that moment.
    """
    fc = check_file(*args, **kw)
    for attempt in range(1, attempts):
        if not _transient(fc):
            break
        first = f"{fc.verdict} {fc.http_status or ''} {fc.detail}".strip()
        time.sleep(backoff * attempt)
        fc = check_file(*args, **kw)
        fc.detail = f"attempt {attempt + 1} (first: {first[:80]}) {fc.detail}".strip()
    return fc


def check_product(
    p: dict[str, Any], max_bytes: int, check_labels: bool = True, opener: Any = None, backoff: float = 5.0
) -> ProductCheck:
    """Check every data file of a product (and its label), as the registry lists them."""
    pc = ProductCheck(
        product=p["id"],
        node=reg.first(p, reg.NODE),
        investigation=reg.first(p, reg.INVESTIGATION),
        target=reg.first(p, reg.TARGET),
    )
    refs = reg.values(p, reg.FILE_REF)
    sizes = reg.values(p, reg.FILE_SIZE)
    md5s = reg.values(p, reg.FILE_MD5)
    if not refs:
        pc.files.append(FileCheck(p["id"], "data", "", "", "no_file_ref"))
    for i, url in enumerate(refs):
        pc.files.append(
            check_file_retrying(
                p["id"],
                "data",
                url,
                _int(sizes[i]) if i < len(sizes) else None,
                md5s[i] if i < len(md5s) else "",
                max_bytes,
                opener=opener,
                backoff=backoff,
            )
        )
    if check_labels:
        for url, size, md5 in zip(
            reg.values(p, reg.LABEL_REF), reg.values(p, reg.LABEL_SIZE), reg.values(p, reg.LABEL_MD5)
        ):
            pc.files.append(
                check_file_retrying(
                    p["id"], "label", url, _int(size), md5, max_bytes, opener=opener, backoff=backoff
                )
            )
    return pc


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval for a proportion. Sound at k=0, where the normal
    approximation claims an interval of exactly zero width."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def summarise(products: list[dict[str, Any]], strata: dict[str, dict[str, int]]) -> dict[str, Any]:
    """Per-site failure rates with intervals, and the archive-wide estimate they imply.

    ``strata`` is ``{node: {"products": N, "with_md5": M}}`` from the registry.
    A site's failure rate is over products that reached a verdict either way;
    undetermined products are reported beside it, not folded into either side.
    """
    by_node: dict[str, list[dict[str, Any]]] = {}
    for p in products:
        by_node.setdefault(p["node"], []).append(p)
    sites = []
    est_fail = 0.0
    for node in sorted(set(by_node) | set(strata)):
        rows = by_node.get(node, [])
        verdicts: dict[str, int] = {}
        for r in rows:
            verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
        failed = sum(v for k, v in verdicts.items() if k in FAILURES)
        passed = sum(v for k, v in verdicts.items() if k in PASSES)
        judged = failed + passed
        lo, hi = wilson(failed, judged)
        population = (strata.get(node) or {}).get("with_md5", 0)
        est = (failed / judged) * population if judged else None
        if est:
            est_fail += est
        labels_judged = sum(1 for r in rows if r.get("label_verdict") in FAILURES | PASSES)
        labels_failed = sum(1 for r in rows if r.get("label_verdict") in FAILURES)
        llo, lhi = wilson(labels_failed, labels_judged)
        hosts: dict[str, int] = {}
        for r in rows:
            for f in r["files"]:
                if f["verdict"] in FAILURES:
                    hosts[f["host"]] = hosts.get(f["host"], 0) + 1
        sites.append(
            {
                "node": node,
                "products": (strata.get(node) or {}).get("products", 0),
                "with_md5": population,
                "without_md5": (strata.get(node) or {}).get("products", 0) - population,
                "sampled": len(rows),
                "judged": judged,
                "failed": failed,
                "verified_md5": verdicts.get("verified", 0),
                "size_only": verdicts.get("size_only_match", 0),
                "undetermined": sum(v for k, v in verdicts.items() if k in UNDETERMINED),
                "verdicts": verdicts,
                "failure_rate": (failed / judged) if judged else None,
                "ci95": [lo, hi],
                "estimated_failing_products": round(est) if est is not None else None,
                "labels_judged": labels_judged,
                "labels_failed": labels_failed,
                "label_failure_rate": (labels_failed / labels_judged) if labels_judged else None,
                "label_ci95": [llo, lhi],
                "failing_hosts": hosts,
            }
        )
    return {
        "sites": sites,
        "sampled": len(products),
        "estimated_failing_products": round(est_fail),
        "failures": [p for p in products if p["verdict"] in FAILURES],
        "label_failures": [
            p for p in products if p.get("label_verdict") in FAILURES and p["verdict"] not in FAILURES
        ],
    }
