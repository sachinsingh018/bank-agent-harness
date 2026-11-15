"""Structural guard: only an explicit list of files may read the labels (decision 0005)."""

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABEL_WORDS = (
    "TransactionLabel",
    "transaction_labels",
    "is_laundering",
    "attempt_type",
    "attempt_id",
)
READERS = frozenset(
    {
        ROOT / "evals" / "aml_rules_score.py",
        ROOT / "evals" / "aml_agent_score.py",  # step 6b, allowed before it exists
        ROOT / "evals" / "make_aml_datasets.py",
        ROOT / "data" / "load_aml_slice.py",  # the loader, the only writer
    }
)
DATASET_KEYS = frozenset(
    {"dataset", "version", "ruleset_version", "alert_ids", "created_by", "split_seed"}
)


def _is_test(path: Path) -> bool:
    return path.name.startswith("test_") or path.name == "conftest.py"


def _py_files(top: Path) -> list[Path]:
    return [
        p
        for p in sorted(top.rglob("*.py"))
        if ".venv" not in p.parts and "__pycache__" not in p.parts
    ]


def _import_names(path: Path) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_only_listed_readers_name_the_labels() -> None:
    checked = 0
    offenders: list[str] = []
    for top in ("evals", "data"):
        for path in _py_files(ROOT / top):
            if _is_test(path):
                continue
            checked += 1
            text = path.read_text(encoding="utf-8")
            if path not in READERS and any(w in text for w in LABEL_WORDS):
                offenders.append(str(path.relative_to(ROOT)))
    assert checked >= 3
    assert not offenders, offenders


def test_listed_readers_that_exist_are_the_known_ones() -> None:
    existing = {p for p in READERS if p.exists()}
    assert ROOT / "evals" / "aml_rules_score.py" in existing
    assert ROOT / "evals" / "make_aml_datasets.py" in existing
    assert ROOT / "data" / "load_aml_slice.py" in existing


def _agent_side_files() -> list[Path]:
    harness = ROOT / "harness"
    db_dir = harness / "db"
    files = [p for p in _py_files(harness) if db_dir not in p.parents]
    files += _py_files(ROOT / "modules" / "aml" / "agent")
    files += _py_files(ROOT / "tools")
    return files


def test_agent_side_imports_nothing_from_evals() -> None:
    files = _agent_side_files()
    assert any(p.name == "loop.py" for p in files)
    assert any(p.name == "models.py" and "agent" in p.parts for p in files)
    assert any("tools" in p.relative_to(ROOT).parts for p in files)
    offenders = [
        f"{p.relative_to(ROOT)}: {name}"
        for p in files
        for name in _import_names(p)
        if name == "evals" or name.startswith("evals.")
    ]
    assert not offenders, offenders


def test_prompts_do_not_reference_evals() -> None:
    prompts = [p for p in sorted((ROOT / "modules" / "aml" / "prompts").rglob("*")) if p.is_file()]
    assert prompts
    for path in prompts:
        text = path.read_text(encoding="utf-8")
        assert "evals/" not in text and "evals." not in text, path


def test_dataset_files_hold_only_ids_and_metadata() -> None:
    files = sorted((ROOT / "evals" / "datasets").glob("*.json"))
    assert {p.name for p in files} >= {
        "aml_alerts_dev_v1.json",
        "aml_alerts_heldout_v1.json",
        "aml_alerts_smoke_v1.json",
    }
    for path in files:
        body = json.loads(path.read_text(encoding="utf-8"))
        assert set(body) == DATASET_KEYS, path.name
        assert all(isinstance(a, str) for a in body["alert_ids"]), path.name
        assert body["alert_ids"] == sorted(body["alert_ids"]), path.name
        text = path.read_text(encoding="utf-8")
        for word in (*LABEL_WORDS, "strict", "loose", "positive", "label"):
            assert word not in text, f"{path.name} mentions {word}"


def test_dev_and_heldout_are_disjoint_and_smoke_is_dev() -> None:
    d = ROOT / "evals" / "datasets"

    def ids(name: str) -> set[str]:
        return set(json.loads((d / f"aml_alerts_{name}_v1.json").read_text())["alert_ids"])

    dev, held, smoke = ids("dev"), ids("heldout"), ids("smoke")
    assert not dev & held
    assert smoke <= dev and len(smoke) == 10
