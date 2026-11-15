"""Structural guard: the rules and the transaction reader must never touch the answer key."""

import ast
import re
from pathlib import Path

AML = Path(__file__).parent
RULE_FILES = sorted((AML / "rules").glob("*.py"))
SCANNED = [*RULE_FILES, AML / "load_txns.py", AML / "run_rules.py"]
FORBIDDEN_WORDS = ("TransactionLabel", "transaction_labels")
# The ONE documented exception: the SQL guard names the table once, as its deny constant.
# tools/aml/test_guard_static.py checks that this is the only use in that file.
LABEL_EXEMPT = AML.parent.parent / "tools" / "aml" / "db.py"


PATTERNS = AML.parent.parent / "tools" / "aml" / "patterns.py"
RULES_PREFIX = "modules.aml.rules"
RULES_REFUSED = (
    "modules.aml.rules.registry",
    "modules.aml.rules.ids",
)


def _import_names(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
            names.extend(f"{node.module}.{a.name}" for a in node.names)
    return names


def test_files_are_found() -> None:
    assert len(RULE_FILES) >= 4
    assert (AML / "load_txns.py").exists()
    assert (AML / "run_rules.py").exists()
    names = {p.name for p in RULE_FILES}
    for expected in ("cycle.py", "scatter_gather.py", "gather_scatter.py", "registry.py"):
        assert expected in names


def test_no_label_mentions_as_text() -> None:
    for path in SCANNED:
        text = path.read_text(encoding="utf-8")
        for word in FORBIDDEN_WORDS:
            assert word not in text, f"{path.name} mentions {word}"


def test_no_label_names_in_ast() -> None:
    for path in SCANNED:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            idents: list[str] = []
            if isinstance(node, ast.Name):
                idents.append(node.id)
            elif isinstance(node, ast.Attribute):
                idents.append(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                idents.append(node.value)
            for ident in idents:
                for word in FORBIDDEN_WORDS:
                    assert word not in ident, f"{path.name} uses {word}"
        for name in _import_names(tree):
            assert "TransactionLabel" not in name


def test_rules_have_no_database_imports() -> None:
    for path in RULE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name in _import_names(tree):
            assert not name.startswith("sqlalchemy"), f"{path.name} imports {name}"
            assert not name.startswith("harness.db"), f"{path.name} imports {name}"


def test_no_label_mentions_in_modules_apps_or_tools() -> None:
    root = AML.parent.parent
    checked = 0
    for top in ("modules", "apps", "tools"):
        for path in sorted((root / top).rglob("*.py")):
            if ".venv" in path.parts or "node_modules" in path.parts:
                continue
            if path.name.startswith("test_") or path.name == "conftest.py":
                continue
            if path == LABEL_EXEMPT:
                continue
            checked += 1
            text = path.read_text(encoding="utf-8")
            for word in FORBIDDEN_WORDS:
                assert word not in text, f"{path} mentions {word}"
    assert checked > 5


def test_label_exemption_is_exactly_one_file() -> None:
    root = AML.parent.parent
    mentioning = [
        p
        for top in ("modules", "apps", "tools")
        for p in sorted((root / top).rglob("*.py"))
        if ".venv" not in p.parts
        and "node_modules" not in p.parts
        and not p.name.startswith("test_")
        and p.name != "conftest.py"
        and "transaction_labels" in p.read_text(encoding="utf-8")
    ]
    assert mentioning == [LABEL_EXEMPT]


DRIVER_BYPASS_WORDS = (
    "raw_connection",
    "driver_connection",
    ".cursor(",
    "exec_driver_sql",
    "literal_column",
)


def test_tools_code_cannot_reach_the_driver_or_run_raw_sql() -> None:
    root = AML.parent.parent / "tools"
    checked = 0
    for path in sorted(root.rglob("*.py")):
        if ".venv" in path.parts or path.name.startswith("test_") or path.name == "conftest.py":
            continue
        checked += 1
        text = path.read_text(encoding="utf-8")
        for word in DRIVER_BYPASS_WORDS:
            assert word not in text, f"{path} uses {word}"
        assert not re.search(r"(?<![A-Za-z0-9_])text\(", text), f"{path} uses text("
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "execute":
                first = node.args[0] if node.args else None
                assert not isinstance(first, ast.JoinedStr | ast.Constant), f"{path} raw execute"
    assert checked > 5


def test_tools_code_other_than_the_door_cannot_open_connections_or_import_writers() -> None:
    root = AML.parent.parent / "tools"
    door = root / "aml" / "db.py"
    banned_prefixes = (
        "psycopg",
        "sqlalchemy.event",
        "sqlalchemy.ext.compiler",
        "harness.db.session",
        "modules",
    )
    checked = 0
    for path in sorted(root.rglob("*.py")):
        if ".venv" in path.parts or path.name.startswith("test_") or path.name == "conftest.py":
            continue
        if path == door:
            continue
        checked += 1
        for name in _import_names(ast.parse(path.read_text(encoding="utf-8"))):
            if path == PATTERNS and name.startswith(RULES_PREFIX):
                # The one narrow exception: the pure detectors, never the loader or the store.
                assert not name.startswith(RULES_REFUSED), f"{path} imports {name}"
                continue
            assert not name.startswith(banned_prefixes), f"{path} imports {name}"
            assert name not in ("sqlalchemy.create_engine", "sqlalchemy.event"), f"{path} {name}"
    assert checked > 4


def test_only_patterns_py_imports_modules_in_tools() -> None:
    root = AML.parent.parent / "tools"
    importers = []
    for path in sorted(root.rglob("*.py")):
        if ".venv" in path.parts or path.name.startswith("test_") or path.name == "conftest.py":
            continue
        names = _import_names(ast.parse(path.read_text(encoding="utf-8")))
        if any(n == "modules" or n.startswith("modules.") for n in names):
            importers.append(path)
            for name in names:
                if name.startswith("modules"):
                    assert name.startswith(RULES_PREFIX), f"{path} imports {name}"
                    for loader in ("load_txns", "store", "run_rules"):
                        assert f"modules.aml.{loader}" not in name, f"{path} imports {name}"
    assert importers == [PATTERNS]


def test_rules_import_nothing_from_tools_or_the_apps() -> None:
    for path in RULE_FILES:
        for name in _import_names(ast.parse(path.read_text(encoding="utf-8"))):
            assert not name.startswith("tools"), f"{path.name} imports {name}"
            assert not name.startswith("apps"), f"{path.name} imports {name}"


AGENT_DIR = AML / "agent"
PROMPTS_DIR = AML / "prompts"
AGENT_FORBIDDEN_WORDS = (
    *FORBIDDEN_WORDS,
    "is_laundering",
    "attempt_type",
    "attempt_id",
)


def _agent_code() -> list[Path]:
    return [
        p
        for p in sorted(AGENT_DIR.rglob("*.py"))
        if not p.name.startswith("test_") and p.name != "conftest.py"
    ]


def test_agent_code_and_prompts_never_name_the_labels() -> None:
    files = [*_agent_code(), *sorted(p for p in PROMPTS_DIR.rglob("*") if p.is_file())]
    assert any(p.name == "models.py" for p in files)
    assert any(p.name == "investigator_v1.md" for p in files)
    for path in files:
        text = path.read_text(encoding="utf-8")
        for word in AGENT_FORBIDDEN_WORDS:
            assert word not in text, f"{path} mentions {word}"


def test_agent_and_harness_import_nothing_from_evals() -> None:
    harness_dir = AML.parent.parent / "harness"
    harness_code = [
        p
        for p in sorted(harness_dir.rglob("*.py"))
        if not p.name.startswith("test_") and p.name != "conftest.py"
    ]
    files = [*_agent_code(), *harness_code]
    assert len(files) > 5
    for path in files:
        for name in _import_names(ast.parse(path.read_text(encoding="utf-8"))):
            assert name != "evals" and not name.startswith("evals."), f"{path} imports {name}"


def test_harness_code_never_names_the_labels() -> None:
    """Plan section 9: all of harness/ except harness/db/, which defines the tables."""
    harness_dir = AML.parent.parent / "harness"
    db_dir = harness_dir / "db"
    files = [
        p
        for p in sorted(harness_dir.rglob("*.py"))
        if not p.name.startswith("test_") and p.name != "conftest.py" and db_dir not in p.parents
    ]
    assert any(p.name == "mcp_client.py" for p in files)
    for path in files:
        text = path.read_text(encoding="utf-8")
        for word in AGENT_FORBIDDEN_WORDS:
            assert word not in text, f"{path} mentions {word}"
