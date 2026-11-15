"""Versioned prompt files: load, check the version against the file name, and hash."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_VERSION_SUFFIX = re.compile(r"_(v[0-9]+)$")


@dataclass(frozen=True)
class PromptRef:
    """A prompt file and the version it must carry in its name (investigator_v1.md is v1)."""

    path: Path
    version: str


@dataclass(frozen=True)
class LoadedPrompt:
    text: str
    version: str
    path: str  # relative to the repo root when inside it, for the trace
    sha256: str


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


def load_prompt(ref: PromptRef) -> LoadedPrompt:
    path = _resolve(ref.path).resolve()
    match = _VERSION_SUFFIX.search(path.stem)
    if match is None or match.group(1) != ref.version:
        raise ValueError(
            f"prompt version {ref.version!r} does not match the file name {path.name!r}"
        )
    raw = path.read_bytes()
    try:
        shown = str(path.relative_to(REPO_ROOT))
    except ValueError:
        shown = str(path)
    return LoadedPrompt(
        text=raw.decode("utf-8"),
        version=ref.version,
        path=shown,
        sha256=hashlib.sha256(raw).hexdigest(),
    )
