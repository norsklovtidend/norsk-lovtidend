"""Simple checkpoint helpers to resume scraping without starting over."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


class CheckpointError(RuntimeError):
    """Raised when a checkpoint file cannot be interpreted."""


@dataclass(slots=True)
class CheckpointState:
    resume_url: str
    offset: int | None
    resume_index: int
    total_documents: int
    total_files: int
    updated_at: str


def load_checkpoint(path: Path) -> CheckpointState | None:
    """Return the checkpoint stored at *path*, if any."""

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:  # pragma: no cover - user action needed
        raise CheckpointError(f"Checkpoint file {path} contains invalid JSON: {exc}") from exc

    resume_url = payload.get("resume_url")
    if not resume_url:
        return None

    resume_index = payload.get("resume_index", 0)
    try:
        parsed_index = int(resume_index)
    except (TypeError, ValueError):  # pragma: no cover - invalid user data
        parsed_index = 0
    if parsed_index < 0:
        parsed_index = 0

    return CheckpointState(
        resume_url=resume_url,
        offset=payload.get("offset"),
        resume_index=parsed_index,
        total_documents=int(payload.get("total_documents", 0)),
        total_files=int(payload.get("total_files", 0)),
        updated_at=payload.get("updated_at", ""),
    )


def save_checkpoint(
    path: Path,
    *,
    resume_url: str | None,
    resume_index: int = 0,
    total_documents: int,
    total_files: int,
) -> None:
    """Persist the resume URL along with some basic stats."""

    if resume_url is None:
        if path.exists():
            path.unlink()
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    offset = extract_offset(resume_url)
    index = resume_index if resume_index >= 0 else 0
    payload: dict[str, Any] = {
        "resume_url": resume_url,
        "offset": offset,
        "resume_index": index,
        "total_documents": total_documents,
        "total_files": total_files,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "version": 1,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def extract_offset(url: str | None) -> int | None:
    if not url:
        return None
    parsed = urlparse(url)
    values = parse_qs(parsed.query).get("offset")
    if not values:
        return None
    try:
        return int(values[0])
    except (TypeError, ValueError):  # pragma: no cover - invalid server data
        return None


def describe_resume_point(state: CheckpointState) -> str:
    """Return a human-readable description of where scraping will resume."""

    fragments: list[str] = []
    if state.offset is not None:
        suffix = f" (+{state.resume_index})" if state.resume_index else ""
        fragments.append(f"offset {state.offset}{suffix}")
    elif state.resume_index:
        fragments.append(f"+{state.resume_index} docs into page")
    if state.resume_url:
        fragments.append(state.resume_url)
    return " @ ".join(fragments) if fragments else "beginning"


def update_checkpoint_file(
    path: Path,
    *,
    resume_url: str | None,
    resume_index: int = 0,
    total_documents: int,
    total_files: int,
) -> None:
    """Persist the checkpoint using :func:`save_checkpoint`."""

    save_checkpoint(
        path,
        resume_url=resume_url,
        resume_index=resume_index,
        total_documents=total_documents,
        total_files=total_files,
    )


__all__ = [
    "CheckpointState",
    "CheckpointError",
    "describe_resume_point",
    "extract_offset",
    "load_checkpoint",
    "save_checkpoint",
    "update_checkpoint_file",
]
