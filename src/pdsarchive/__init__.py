"""fwh_pds — the NASA Planetary Data System archive, audited and mapped.

Two questions about the archive as a whole, both answered from the PDS
Registry Search API:

1. **Does it still match its own checksums?** A random sample per archive site
   is fetched from the URL the registry records and compared with the size and
   MD5 it records.
2. **What can it place on a map?** Every product carrying a bounding box,
   gridded per body, beside how many products carry none.

Discovered by the Facetwork runner via the ``facetwork.domains`` entry point::

    [project.entry-points."facetwork.domains"]
    pds = "pdsarchive:domain"
"""

from __future__ import annotations

from pathlib import Path

from facetwork.domains import DomainPackage

from .handlers import register_all_registry_handlers

domain = DomainPackage(
    name="pds",
    ffl_dir=Path(__file__).parent / "ffl",
    register_handlers=register_all_registry_handlers,
)
