"""Prompt loader: version check against the file name, hashing and repo relative paths."""

import hashlib
from pathlib import Path

import pytest

from harness.prompts import REPO_ROOT, PromptRef, load_prompt


def test_loads_text_version_and_hash(tmp_path: Path) -> None:
    path = tmp_path / "helper_v3.md"
    path.write_text("Hello prompt\n", encoding="utf-8")
    loaded = load_prompt(PromptRef(path, "v3"))
    assert loaded.text == "Hello prompt\n"
    assert loaded.version == "v3"
    assert loaded.sha256 == hashlib.sha256(b"Hello prompt\n").hexdigest()


def test_version_mismatch_raises(tmp_path: Path) -> None:
    path = tmp_path / "helper_v3.md"
    path.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        load_prompt(PromptRef(path, "v2"))
    unversioned = tmp_path / "helper.md"
    unversioned.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        load_prompt(PromptRef(unversioned, "v1"))


def test_repo_prompt_path_is_relative_to_repo_root() -> None:
    ref = PromptRef(Path("modules/aml/prompts/investigator_v1.md"), "v1")
    loaded = load_prompt(ref)
    assert loaded.path == "modules/aml/prompts/investigator_v1.md"
    raw = (REPO_ROOT / loaded.path).read_bytes()
    assert loaded.sha256 == hashlib.sha256(raw).hexdigest()
