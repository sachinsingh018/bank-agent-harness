"""The agent side of the harness never touches the database directly (plan section 10)."""

import ast
from collections.abc import Iterator
from pathlib import Path

HARNESS = Path(__file__).resolve().parent
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
