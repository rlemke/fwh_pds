"""Backend-aware paths for the pds domain (a shim over ``facetwork.domains.storage``)."""

from __future__ import annotations

from facetwork.domains.storage import domain_storage, is_remote, join  # noqa: F401

_S = domain_storage("pds", path_name="pds", local_name="pds")


def output(*parts: str) -> str:
    return _S.output(*parts)


def maps(*parts: str) -> str:
    return join(_S.maps_root(), *parts)


def resolve(path: str) -> str:
    """An absolute path or URI is used as given; anything else lands under the domain's output root."""
    if path.startswith("/") or "://" in path:
        return path
    return output(path)


def read_text(path: str) -> str:
    return _S.read_text(path)


def write_text(path: str, body: str) -> None:
    _S.write_text(path, body)


def list_dir(path: str) -> list[str]:
    return _S.list_dir(path)
