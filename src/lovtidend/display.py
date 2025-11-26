"""Helper functions for presenting scraper output."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:  # Avoid circular imports at runtime
    from .scraper import DocumentListing


def display_paths(files: Sequence[Path], root: Path) -> str:
    fragments: list[str] = []
    for path in files:
        try:
            fragments.append(str(path.relative_to(root)))
        except ValueError:
            fragments.append(str(path))
    return ", ".join(fragments)


def describe_document(document: "DocumentListing") -> str:
    if document.identifier:
        return document.identifier
    return document.title or document.document_url
