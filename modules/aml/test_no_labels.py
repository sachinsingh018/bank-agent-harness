"""Structural guard: the rules and the transaction reader must never touch the answer key."""

import ast
from pathlib import Path

AML = Path(__file__).parent
RULE_FILES = sorted((AML / "rules").glob("*.py"))
SCANNED = [*RULE_FILES, AML / "load_txns.py"]
FORBIDDEN_WORDS = ("TransactionLabel", "transaction_labels")


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
