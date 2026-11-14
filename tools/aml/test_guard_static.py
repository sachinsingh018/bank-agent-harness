"""Static guard: no tool code may mention the answer key, use raw SQL or reflect the schema."""

import ast
import re
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
ALLOWED_MODEL_IMPORTS = {"Transaction", "Account", "Alert", "AlertTransaction"}
# Test files are outside the static scan. The name is built from two pieces so the full
# literal never appears in non-test code, which keeps the repo-wide scan auditable.
LABELS_TABLE = "transaction" + "_labels"
FORBIDDEN_TEXT = ("TransactionLabel", LABELS_TABLE)
# The ONE documented exception: the guard module names the table once, as its deny constant.
LABEL_EXEMPT = TOOLS / "aml" / "db.py"
DENY_CONSTANT = "FORBIDDEN_TABLE"
FORBIDDEN_PATTERNS = {
    "text(": re.compile(r"(?<![A-Za-z0-9_])text\("),
    "exec_driver_sql": re.compile(r"exec_driver_sql"),
    "raw_connection": re.compile(r"raw_connection"),
    "driver_connection": re.compile(r"driver_connection"),
    ".cursor(": re.compile(r"\.cursor\("),
    "literal_column": re.compile(r"literal_column"),
    "select(*": re.compile(r"select\(\s*\*"),
    "MetaData": re.compile(r"MetaData"),
    "inspect(": re.compile(r"(?<![A-Za-z0-9_])inspect\("),
    "reflect": re.compile(r"reflect"),
    "autoload": re.compile(r"autoload"),
}


BANNED_NAMES = (
    "MetaData",
    "exec_driver_sql",
    "raw_connection",
    "driver_connection",
    "literal_column",
)


def _is_plain_string(node: ast.expr) -> bool:
    """A string literal, f-string, concatenation or format call: not a SQLAlchemy object."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp):
        return _is_plain_string(node.left) or _is_plain_string(node.right)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr in ("format", "join") and _is_plain_string(node.func.value)
    return False


def _tool_files() -> list[Path]:
    return sorted(
        p
        for p in TOOLS.rglob("*.py")
        if not p.name.startswith("test_")
        and p.name != "conftest.py"
        and ".venv" not in p.parts
        and "__pycache__" not in p.parts
    )


def test_files_are_found() -> None:
    names = {p.name for p in _tool_files()}
    assert {"db.py", "queries.py", "models.py", "caps.py", "paging.py"} <= names
    assert {"patterns.py", "detector_docs.py"} <= names


def _checked_for_labels() -> list[Path]:
    return [p for p in _tool_files() if p != LABEL_EXEMPT]


def test_no_label_mentions_as_text() -> None:
    for path in _checked_for_labels():
        text = path.read_text(encoding="utf-8")
        for word in FORBIDDEN_TEXT:
            assert word not in text, f"{path.name} mentions {word}"


def test_label_exemption_is_exactly_one_file() -> None:
    assert LABEL_EXEMPT.exists()
    assert LABEL_EXEMPT.relative_to(TOOLS).as_posix() == "aml/db.py"
    files = _tool_files()
    assert len(files) - len(_checked_for_labels()) == 1
    mentioning = [p for p in files if LABELS_TABLE in p.read_text(encoding="utf-8")]
    assert mentioning == [LABEL_EXEMPT]


def test_exempt_file_uses_the_name_only_as_the_deny_constant() -> None:
    text = LABEL_EXEMPT.read_text(encoding="utf-8")
    assert "TransactionLabel" not in text
    assert text.count(LABELS_TABLE) == 1
    tree = ast.parse(text)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    literals = [
        n for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value == LABELS_TABLE
    ]
    assert len(literals) == 1
    assign = parents[literals[0]]
    assert isinstance(assign, ast.Assign)
    assert [t.id for t in assign.targets if isinstance(t, ast.Name)] == [DENY_CONSTANT]
    assert isinstance(assign.value, ast.Constant)
    uses = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == DENY_CONSTANT]
    loads = [n for n in uses if isinstance(n.ctx, ast.Load)]
    assert loads, "the deny constant should be used by the guard"
    for use in loads:
        compare = parents[use]
        assert isinstance(compare, ast.Compare)
        assert compare.left is use
        assert all(isinstance(op, ast.In) for op in compare.ops)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "harness.db.models":
            assert all(a.name in ALLOWED_MODEL_IMPORTS for a in node.names)


def test_no_forbidden_constructs_as_text() -> None:
    for path in _tool_files():
        text = path.read_text(encoding="utf-8")
        for label, pattern in FORBIDDEN_PATTERNS.items():
            assert not pattern.search(text), f"{path.name} uses {label}"


def test_no_forbidden_names_in_ast() -> None:
    for path in _tool_files():
        words = () if path == LABEL_EXEMPT else FORBIDDEN_TEXT
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            idents: list[str] = []
            if isinstance(node, ast.Name):
                idents.append(node.id)
            elif isinstance(node, ast.Attribute):
                idents.append(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                idents.append(node.value)
            elif isinstance(node, ast.alias):
                idents.append(node.name)
            for ident in idents:
                for word in words:
                    assert word not in ident, f"{path.name} uses {word}"
                assert ident not in BANNED_NAMES, f"{path.name} uses {ident}"
            if isinstance(node, ast.Call):
                func = node.func
                called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                assert called not in ("text", "inspect", "cursor"), f"{path.name} calls {called}"
            if isinstance(node, ast.ImportFrom):
                assert all(a.name not in ("text", "inspect") for a in node.names), path.name


def test_model_imports_are_limited() -> None:
    for path in _tool_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "harness.db.models":
                for alias in node.names:
                    assert alias.name in ALLOWED_MODEL_IMPORTS, f"{path.name} imports {alias.name}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "harness.db.models", f"{path.name} imports the module"
            elif isinstance(node, ast.ImportFrom) and node.module == "harness.db":
                assert all(a.name != "models" for a in node.names), f"{path.name} imports models"


def test_no_plain_string_is_passed_to_execute() -> None:
    for path in _tool_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if called in ("execute", "exec_driver_sql", "executemany"):
                assert node.args, f"{path.name} calls {called} with no statement object"
                assert not _is_plain_string(node.args[0]), f"{path.name} runs a plain string"


def test_plain_string_detector_works() -> None:
    def first_arg(code: str) -> ast.expr:
        call = ast.parse(code).body[0]
        assert isinstance(call, ast.Expr) and isinstance(call.value, ast.Call)
        return call.value.args[0]

    assert _is_plain_string(first_arg("c.execute('select 1')"))
    assert _is_plain_string(first_arg("c.execute(f'select {x}')"))
    assert _is_plain_string(first_arg("c.execute('a' + x)"))
    assert _is_plain_string(first_arg("c.execute('a{}'.format(x))"))
    assert not _is_plain_string(first_arg("c.execute(select(x))"))
    assert not _is_plain_string(first_arg("c.execute(stmt)"))


# ---------------------------------------------------------------- import allowlist
# Non-test tools code may import only these. Plain "import x" is allowed for the standard
# library, pydantic and tools.*. "from x import y" is allowed for the standard library,
# pydantic and tools.* too, plus the explicit entries below. Anything else fails.
FREE_ROOTS = {"pydantic", "tools"}
# Standard library modules that could reach the driver, the process or the import system.
DENIED_STDLIB = {
    "importlib",
    "ctypes",
    "subprocess",
    "socket",
    "os",
    "sys",
    "builtins",
    "pickle",
    "runpy",
    "sqlite3",
}
# The select-side names the six queries use, and the four models.
COMMON_FROM_IMPORTS: dict[str, set[str]] = {
    "sqlalchemy": {"ColumnElement", "Connection", "Date", "Row", "Select"}
    | {"and_", "cast", "func", "or_", "select", "tuple_"},
    "harness.db.models": ALLOWED_MODEL_IMPORTS,
}
# Files that legitimately need more, spelled out per file. Nothing else gets these.
PER_FILE_FROM_IMPORTS: dict[str, dict[str, set[str]]] = {
    "aml/db.py": {
        "sqlalchemy": {"Column", "Connection", "Engine", "Table", "create_engine", "event"},
        "sqlalchemy.exc": {"SQLAlchemyError"},
        "sqlalchemy.ext.compiler": {"compiles"},
        "sqlalchemy.orm": {"util"},
        "sqlalchemy.sql": {
            "annotation",
            "elements",
            "functions",
            "operators",
            "selectable",
            "sqltypes",
        },
        "sqlalchemy.sql.base": {"Executable"},
        "sqlalchemy.sql.elements": {"ClauseElement"},
        "sqlalchemy.sql.visitors": {"iterate"},
        "harness.db.session": {"DatabaseConnectionError", "get_database_url"},
    },
    # The ONE file that may use the rules layer: the pure detectors, never the loader, the
    # store or the runner. run_pattern_check converts rows to Txn and runs one detector.
    "aml/patterns.py": {
        "modules.aml.rules.types": {"AlertDraft", "Txn"},
        "modules.aml.rules.ruleset": {"RULESET_VERSION", "V1", "RulesetConfig"},
        "modules.aml.rules.fan": {"fan_in", "fan_out"},
        "modules.aml.rules.cycle": {"cycle"},
        "modules.aml.rules.scatter_gather": {"scatter_gather"},
        "modules.aml.rules.gather_scatter": {"gather_scatter"},
    },
}


def _is_free(module: str) -> bool:
    root = module.split(".")[0]
    return (root in sys.stdlib_module_names and root not in DENIED_STDLIB) or root in FREE_ROOTS


def import_violations(source: str, rel: str) -> list[str]:
    """Imports that are not on the allowlist, for a tools file at this path under tools/."""
    allowed = {k: set(v) for k, v in COMMON_FROM_IMPORTS.items()}
    for module, names in PER_FILE_FROM_IMPORTS.get(rel, {}).items():
        allowed.setdefault(module, set()).update(names)
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend(f"import {a.name}" for a in node.names if not _is_free(a.name))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level or not module:
                found.append("relative import")
            elif _is_free(module):
                continue
            else:
                ok = allowed.get(module, set())
                found.extend(
                    f"from {module} import {a.name}" for a in node.names if a.name not in ok
                )
    return found


def test_imports_are_on_the_allowlist() -> None:
    for path in _tool_files():
        rel = path.relative_to(TOOLS).as_posix()
        found = import_violations(path.read_text(encoding="utf-8"), rel)
        assert not found, f"{rel}: {found}"


def test_only_patterns_py_may_import_the_rules() -> None:
    owners = [
        rel
        for rel, mods in PER_FILE_FROM_IMPORTS.items()
        if any(m.startswith("modules") for m in mods)
    ]
    assert owners == ["aml/patterns.py"]
    for path in _tool_files():
        rel = path.relative_to(TOOLS).as_posix()
        text = path.read_text(encoding="utf-8")
        if rel != "aml/patterns.py":
            assert "modules." not in text, rel
    for mod in PER_FILE_FROM_IMPORTS["aml/patterns.py"]:
        assert mod.startswith("modules.aml.rules.")


RULES_IMPORT = (
    "from modules.aml.rules.fan import fan_out\nfrom modules.aml.rules.types import Txn\n"
)


@pytest.mark.parametrize(
    "rel", ["aml/queries.py", "aml/db.py", "aml/detector_docs.py", "aml/models.py"]
)
def test_other_files_may_not_import_the_rules(rel: str) -> None:
    assert import_violations(RULES_IMPORT, rel)
    assert import_violations("from modules.aml.rules.ruleset import V1", rel)


def test_patterns_py_may_import_the_detectors() -> None:
    assert not import_violations(RULES_IMPORT, "aml/patterns.py")
    assert not import_violations("from modules.aml.rules.ruleset import V1", "aml/patterns.py")


@pytest.mark.parametrize(
    "source",
    [
        "from modules.aml.load_txns import load_txns",
        "from modules.aml.store import write_alerts",
        "from modules.aml.run_rules import main",
        "from modules.aml import store",
        "from modules.aml.rules import registry",
        "from modules.aml.rules.registry import run_all",
        "from modules.aml.rules.ids import make_alert_id",
        "from modules.aml.rules.fan import _fan",
        "from modules.aml.rules.types import Account",
        "import modules.aml.rules.fan",
        "import modules.aml.store",
        "from modules import aml",
    ],
)
def test_patterns_py_still_refuses_everything_else_in_modules(source: str) -> None:
    assert import_violations(source, "aml/patterns.py")


def test_per_file_import_allowlist_names_only_existing_files() -> None:
    for rel in PER_FILE_FROM_IMPORTS:
        assert (TOOLS / rel).exists()


@pytest.mark.parametrize(
    "source",
    [
        "from sqlalchemy import create_engine",
        "from sqlalchemy import event",
        "from sqlalchemy import text",
        "import sqlalchemy",
        "import psycopg",
        "from psycopg import connect",
        "from sqlalchemy.event import listen",
        "from sqlalchemy.ext.compiler import compiles",
        "from sqlalchemy.orm import Session",
        "from harness.db.session import get_engine",
        "from harness.db.models import TransactionLabel",
        "import harness.db.models",
        "from modules.aml.store import write_alerts",
        "from modules.aml import store",
        "from . import db",
        "import importlib",
        "import os",
        "import subprocess",
        "import requests",
    ],
)
def test_import_allowlist_flags_bad_imports(source: str) -> None:
    assert import_violations(source, "aml/queries.py")


def test_import_allowlist_accepts_good_imports_and_is_per_file() -> None:
    good = "from sqlalchemy import select\nfrom tools.aml.caps import X\nimport json\n"
    assert not import_violations(good, "aml/queries.py")
    event_import = "from sqlalchemy import event"
    assert import_violations(event_import, "aml/queries.py")
    assert not import_violations(event_import, "aml/db.py")
    assert import_violations("from sqlalchemy import text", "aml/db.py")
    assert import_violations("from harness.db.session import get_engine", "aml/db.py")


# --------------------------------------------------------------------------- AST bans
# Applied to every non-test tools file except db.py, the one door. Tools code gets its
# connection only through db.open_readonly or db.make_tool_engine.
BANNED_ATTRS = {
    "dispatch": "event dispatch",
    "op": ".op(",
    "prefix_with": "prefix_with",
    "suffix_with": "suffix_with",
    "with_statement_hint": "with_statement_hint",
    "with_hint": "with_hint",
    "options": ".options(",
    "pool": "engine.pool",
    "connection": "driver access via .connection",
    "connect": "Engine.connect",
    "begin": "Engine.begin",
    "execution_options": "execution_options",
}
BANNED_AST_NAMES = {
    "compiles": "compiles",
    "Session": "Session",
    "sessionmaker": "sessionmaker",
    "scoped_session": "scoped_session",
    "create_engine": "create_engine",
    "get_engine": "get_engine",
    "__import__": "__import__",
    "eval": "eval",
    "exec": "exec",
}
BANNED_BASES = {
    "ClauseElement",
    "ColumnElement",
    "FunctionElement",
    "Executable",
    "TypeDecorator",
    "UserDefinedType",
    "ClauseList",
    "BinaryExpression",
}


def _ident(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def ast_violations(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute):
            if node.attr in BANNED_ATTRS:
                found.append(BANNED_ATTRS[node.attr])
            if node.attr == "remove" and _ident(node.value) == "event":
                found.append("event.remove")
        elif isinstance(node, ast.Name) and node.id in BANNED_AST_NAMES:
            found.append(BANNED_AST_NAMES[node.id])
        elif isinstance(node, ast.alias) and node.name in BANNED_AST_NAMES:
            found.append(BANNED_AST_NAMES[node.name])
        elif isinstance(node, ast.ClassDef):
            found.extend(
                f"subclass of {_ident(b)}" for b in node.bases if _ident(b) in BANNED_BASES
            )
    return found


def test_no_banned_ast_constructs_outside_db_py() -> None:
    checked = 0
    for path in _tool_files():
        if path == LABEL_EXEMPT:
            continue
        checked += 1
        found = ast_violations(path.read_text(encoding="utf-8"))
        assert not found, f"{path.name}: {found}"
    assert checked >= 4


@pytest.mark.parametrize(
    ("source", "flag"),
    [
        ("event.remove(engine, 'x', fn)", "event.remove"),
        ("target.dispatch.before_cursor_execute", "event dispatch"),
        ("col.op('= 1 or')(x)", ".op("),
        ("select(a).prefix_with('x')", "prefix_with"),
        ("select(a).suffix_with('x')", "suffix_with"),
        ("select(a).with_statement_hint('x')", "with_statement_hint"),
        ("select(a).with_hint(t, 'x')", "with_hint"),
        ("select(a).options(opt)", ".options("),
        ("from sqlalchemy.ext.compiler import compiles", "compiles"),
        ("@compiles(Thing)\ndef f(e, c, **kw): pass", "compiles"),
        ("class Mine(ClauseElement): pass", "subclass of ClauseElement"),
        ("class Mine(elements.ColumnElement): pass", "subclass of ColumnElement"),
        ("engine.pool.dispose()", "engine.pool"),
        ("conn.connection.execute(sql_var)", "driver access via .connection"),
        ("engine.connect()", "Engine.connect"),
        ("engine.begin()", "Engine.begin"),
        ("conn.execution_options(schema_translate_map={})", "execution_options"),
        ("s = Session(engine)", "Session"),
        ("from sqlalchemy.orm import Session", "Session"),
        ("e = create_engine(url)", "create_engine"),
        ("e = get_engine()", "get_engine"),
        ("__import__('os')", "__import__"),
    ],
)
def test_ast_scanner_flags_each_ban(source: str, flag: str) -> None:
    assert flag in ast_violations(source)


def test_ast_scanner_accepts_clean_code() -> None:
    clean = "def f(conn, inp):\n    return conn.execute(select(T.a).where(T.b == inp)).all()\n"
    assert ast_violations(clean) == []


def test_text_scanner_flags_get_raw_connection() -> None:
    # "raw_connection" is matched as a substring, so get_raw_connection is covered too.
    assert FORBIDDEN_PATTERNS["raw_connection"].search("conn.get_raw_connection()")
