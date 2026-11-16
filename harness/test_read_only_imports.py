"""The agent side of the harness never touches the database directly (plan section 10).

Only harness/records.py, harness/db/ and the eval scripts (evals/run_aml_agent.py,
evals/aml_agent_score.py) touch the write path, plus the approval queue API (apps/api/queue.py
and apps/api/deps.py), which is the human decision path outside the agent. harness/runner.py gets a
recorder callable from the eval runner and imports no database code (decision 0007)."""

import ast
import re
from collections.abc import Iterator
from pathlib import Path

HARNESS = Path(__file__).resolve().parent
ROOT = HARNESS.parent
WRITE_PATH_FORBIDDEN = ("harness.db.session", "harness.records", "psycopg")
WRITE_PATH_EXEMPT = (HARNESS / "records.py",)
RECORDS_IMPORTERS = {
    ROOT / "evals" / "run_aml_agent.py",
    ROOT / "evals" / "aml_agent_score.py",
    # The human approval flow: appends decision events, never agent code.
    ROOT / "apps" / "api" / "queue.py",
    ROOT / "apps" / "api" / "deps.py",
}
FORBIDDEN = ("harness.db", "sqlalchemy", "psycopg")
CHECKED = (
    "loop.py",
    "runner.py",
    "mcp_client.py",
    "module_spec.py",
    "prompts.py",
    "trace.py",
)


def _files() -> Iterator[Path]:
    for name in CHECKED:
        yield HARNESS / name
    for path in sorted((HARNESS / "models").glob("*.py")):
        if not path.name.startswith("test_"):
            yield path


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_agent_files_import_no_database_code() -> None:
    offenders: list[str] = []
    for path in _files():
        assert path.exists(), path
        for name in _imports(path):
            if any(name == f or name.startswith(f + ".") for f in FORBIDDEN):
                offenders.append(f"{path.relative_to(HARNESS.parent)}: {name}")
    assert not offenders, "\n".join(offenders)


def _imports_with_names(path: Path) -> list[str]:
    """Like _imports, plus module.name for each name in a from import."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _is_test(path: Path) -> bool:
    return path.name.startswith("test_") or path.name == "conftest.py"


def _code(top: Path) -> list[Path]:
    return [
        p
        for p in sorted(top.rglob("*.py"))
        if not _is_test(p) and "__pycache__" not in p.parts and ".venv" not in p.parts
    ]


def _write_path_offenders(path: Path) -> list[str]:
    found: list[str] = []
    for name in _imports_with_names(path):
        if any(name == f or name.startswith(f + ".") for f in WRITE_PATH_FORBIDDEN):
            found.append(name)
        elif name == "sqlalchemy.create_engine":
            found.append(name)
    return found


def test_agent_side_cannot_reach_the_write_path() -> None:
    """No harness file outside harness/db/ and harness/records.py (runner.py included), and
    no file in modules/aml/agent/, imports the session helpers, the records module,
    create_engine or the driver."""
    db_dir = HARNESS / "db"
    files = [p for p in _code(HARNESS) if db_dir not in p.parents and p not in WRITE_PATH_EXEMPT]
    files += _code(ROOT / "modules" / "aml" / "agent")
    assert HARNESS / "runner.py" in files and HARNESS / "loop.py" in files
    assert any(p.parent.name == "agent" for p in files)
    offenders = [
        f"{p.relative_to(ROOT)}: {name}" for p in files for name in _write_path_offenders(p)
    ]
    assert not offenders, "\n".join(offenders)


def test_only_the_eval_scripts_and_the_queue_api_import_the_records_module() -> None:
    importers = set()
    for top in ("harness", "modules", "evals", "tools", "apps", "data", "migrations"):
        for path in _code(ROOT / top):
            names = _imports_with_names(path)
            if any(n == "harness.records" or n.startswith("harness.records.") for n in names):
                importers.add(path)
    assert importers == RECORDS_IMPORTERS


def test_only_tests_import_tests_support() -> None:
    importers = []
    for top in ("harness", "modules", "evals", "tools", "apps", "data", "migrations"):
        for path in _code(ROOT / top):
            names = _imports_with_names(path)
            if any(n == "tests_support" or n.startswith("tests_support.") for n in names):
                importers.append(str(path.relative_to(ROOT)))
    assert not importers, importers


CORRECTIONS_WORD = re.compile(r"\bcorrections?\b", re.IGNORECASE)


def test_agent_side_never_mentions_the_corrections_table() -> None:
    """Corrections are stored but not used by the agent yet (docs/plans/edits-and-corrections.md):
    no harness file outside harness/db/, and no file in modules/aml/agent/, imports the
    Correction model or mentions the corrections table."""
    db_dir = HARNESS / "db"
    files = [p for p in _code(HARNESS) if db_dir not in p.parents]
    files += _code(ROOT / "modules" / "aml" / "agent")
    assert HARNESS / "loop.py" in files and HARNESS / "records.py" in files
    offenders: list[str] = []
    for path in files:
        if CORRECTIONS_WORD.search(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(ROOT)}: mentions corrections")
        for name in _imports_with_names(path):
            if name.endswith(".Correction"):
                offenders.append(f"{path.relative_to(ROOT)}: {name}")
    assert not offenders, "\n".join(offenders)
