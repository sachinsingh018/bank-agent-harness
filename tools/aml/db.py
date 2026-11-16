"""The only place that opens a database connection for the AML tools.

Two layers keep the tools read-only and away from the answer key:
1. Every connection runs in a read-only transaction, so writes fail at the database. The
   transaction also gets a statement timeout (SET LOCAL, so it ends with the transaction).
2. A guard inspects every statement before it runs. For statements built with SQLAlchemy it
   walks the expression tree and refuses anything that is not a plain read of the four allowed
   tables using an explicit list of node kinds and functions. Statements that arrive as raw
   strings are refused unless they exactly match a tiny housekeeping set. The guard is
   installed in production code paths and fails closed: whatever it does not recognise is
   refused. It never puts statement text or parameters into an error message.
"""

from collections.abc import Iterator, Sized
from contextlib import contextmanager
from typing import Any, cast

from sqlalchemy import Column, Connection, Engine, Table, create_engine, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import util as orm_util
from sqlalchemy.sql import annotation, elements, functions, operators, selectable, sqltypes
from sqlalchemy.sql.base import Executable
from sqlalchemy.sql.elements import ClauseElement
from sqlalchemy.sql.visitors import iterate

from harness.db.models import Account, Alert, AlertTransaction, Transaction
from harness.db.session import DatabaseConnectionError, get_database_url
from tools.aml.caps import STATEMENT_TIMEOUT_MS

ALLOWED_TABLES = frozenset({"transactions", "accounts", "alerts", "alert_transactions"})
# The one place in tools/ where the answer key table is named: a deny constant. It is only ever
# compared against statement text and is never used as a model, a query or a select. The static
# tests exempt exactly this file by path and check that this is its only use.
FORBIDDEN_TABLE = "transaction_labels"

# The real table objects. A table is accepted only if it IS one of these objects, so a table
# built elsewhere with an allowed name is refused.
_REAL_TABLES: dict[str, Table] = {
    t.name: t
    for t in (
        cast(Table, Transaction.__table__),
        cast(Table, Account.__table__),
        cast(Table, Alert.__table__),
        cast(Table, AlertTransaction.__table__),
    )
}

# SQL functions the six queries emit, and nothing broader. A function node is accepted only if
# its class is exactly one of FUNCTION_TYPES and its name is in this set.
ALLOWED_FUNCTIONS = frozenset({"count", "sum", "min", "max"})
FUNCTION_TYPES: frozenset[type] = frozenset(
    {functions.count, functions.sum, functions.min, functions.max}
)

# Raw (non SQLAlchemy built) statements allowed, after lowercasing and collapsing whitespace.
# Only what open_readonly sends: the read-only switch and the per transaction statement
# timeout, built only from the integer constant in caps.py, never from input. The dialect's
# own first-connect statements (version, current_schema, standard_conforming_strings) run on
# an internal connection that does not carry the engine's listeners, by design, so they never
# reach the guard and are not whitelisted. If a future SQLAlchemy changes that, the guard
# fails closed with a generic error.
_TIMEOUT_SQL = f"SET LOCAL statement_timeout = {int(STATEMENT_TIMEOUT_MS)}"
HOUSEKEEPING = frozenset({"set transaction read only", _TIMEOUT_SQL.lower()})


class ToolSecurityError(Exception):
    """A statement was refused by the guard. The message is generic by design."""


def _refuse(reason: str) -> ToolSecurityError:
    return ToolSecurityError(f"statement refused: {reason}")


# ------------------------------------------------------------------ the guard


class SetTransactionReadOnly(Executable, ClauseElement):
    """SET TRANSACTION READ ONLY, built as a clause so no raw SQL strings are needed."""

    inherit_cache = True


@compiles(SetTransactionReadOnly)
def _compile_read_only(element: SetTransactionReadOnly, compiler: Any, **kw: Any) -> str:
    return "SET TRANSACTION READ ONLY"


class SetLocalStatementTimeout(Executable, ClauseElement):
    """SET LOCAL statement_timeout for the current transaction only. It works through Neon
    poolers, unlike startup options. The value is the caps.py constant and nothing else."""

    inherit_cache = True


@compiles(SetLocalStatementTimeout)
def _compile_statement_timeout(element: SetLocalStatementTimeout, compiler: Any, **kw: Any) -> str:
    return _TIMEOUT_SQL


# Housekeeping clauses are accepted only if their compiled text is also in HOUSEKEEPING.
_HOUSEKEEPING_TYPES: frozenset[type] = frozenset({SetTransactionReadOnly, SetLocalStatementTimeout})


# Node classes a plain read may contain. A node is accepted only if its exact class (type, not
# isinstance) is in this set, so subclasses, @compiles overrides and look-alikes are refused.
# ORM annotated copies are unwrapped to the class they wrap first (see _kind).
# Plain joins and the ORM join that select(...).join(Model, ...) builds.
_JOIN_TYPES: frozenset[type] = frozenset({selectable.Join, orm_util._ORMJoin})
_ALLOWED_NODES: frozenset[type] = frozenset(
    {
        selectable.Select,
        selectable.Subquery,
        selectable.CTE,  # not used by the six queries, a plain CTE is safe and tested
        *_JOIN_TYPES,
        Table,
        Column,  # real table columns and the columns of a subquery or CTE
        elements.ColumnClause,  # only the bare star that func.count() builds
        elements.BindParameter,
        selectable._OffsetLimitParam,  # LIMIT and OFFSET values
        elements.BinaryExpression,
        elements.ClauseList,  # group_by and order_by lists
        elements.BooleanClauseList,  # and_, or_
        elements.Tuple,  # tuple_ for keyset paging and counterparty keys
        elements.Grouping,
        elements.UnaryExpression,  # desc and NOT
        elements.Label,
        elements._label_reference,  # ORDER BY a label by name, it wraps the label itself
        elements.Cast,  # cast(ts, Date)
        elements.TypeClause,
        elements.FunctionFilter,  # count(*) FILTER (WHERE ...)
        elements.Null,
        *FUNCTION_TYPES,
    }
)

# Standard operators only. custom_op and .op("...") are never members. Compared by identity.
ALLOWED_OPERATORS: tuple[Any, ...] = (
    operators.eq,
    operators.ne,
    operators.lt,
    operators.le,
    operators.gt,
    operators.ge,
    operators.and_,
    operators.or_,
    operators.in_op,
    operators.not_in_op,
    operators.is_,
    operators.is_not,
    operators.inv,  # ~ (NOT)
    operators.comma_op,  # lists and tuples
)
ALLOWED_MODIFIERS: tuple[Any, ...] = (operators.desc_op, operators.asc_op)

# Exact standard types a Cast may use. The only cast in the queries is ts to Date.
ALLOWED_CAST_TYPES: frozenset[type] = frozenset({sqltypes.Date})

# For nodes that carry hints, prefixes and the like: the attributes (from _traverse_internals,
# which lists everything the compiler can render) that may be non-empty. Anything else that is
# set makes the guard refuse. Nodes not listed here have no such extras that can reach the text.
_SELECT_MODELLED = frozenset(
    {
        "_raw_columns",
        "_from_obj",
        "_where_criteria",
        "_having_criteria",
        "_order_by_clauses",
        "_group_by_clauses",
        "_setup_joins",
        "_limit_clause",
        "_offset_clause",
        "_distinct",
        "_label_style",
        "_propagate_attrs",
        "_annotations",
    }
)
_CTE_MODELLED = frozenset({"element", "name"})
_SUBQUERY_MODELLED = frozenset({"element", "name"})
_FUNCTION_MODELLED = frozenset(
    {"clause_expr", "_propagate_attrs", "_params", "packagenames", "name", "type"}
)
_MODELLED_BY_NODE: dict[type, frozenset[str]] = {
    selectable.Select: _SELECT_MODELLED,
    selectable.CTE: _CTE_MODELLED,
    selectable.Subquery: _SUBQUERY_MODELLED,
    **{t: _FUNCTION_MODELLED for t in FUNCTION_TYPES},
}


def _kind(node: Any) -> type:
    """The exact class of a node. ORM annotated copies (AnnotatedColumn and so on) are generated
    classes, so they count as the exact class they wrap, but only if built by SQLAlchemy's own
    annotation module over that exact class."""
    cls = type(node)
    if isinstance(node, annotation.Annotated):
        wrapped = type(node._Annotated__element)  # type: ignore[attr-defined]
        if cls.__module__ == annotation.__name__ and cls.__bases__[-1] is wrapped:
            return wrapped
    return cls


def _is_empty(value: Any) -> bool:
    return value is None or value is False or (isinstance(value, Sized) and len(value) == 0)


def _check_unmodelled(node: Any, kind: type) -> None:
    """Refuse a node that carries any set attribute the guard does not model (prefixes,
    suffixes, hints, options, FOR UPDATE, DISTINCT ON, correlation and so on)."""
    modelled = _MODELLED_BY_NODE.get(kind)
    if modelled is None:
        return
    for name, _ in kind._traverse_internals:  # type: ignore[attr-defined]
        if name not in modelled and not _is_empty(getattr(node, name, None)):
            raise _refuse("statement option not allowed")


def _check_operator(op: Any) -> None:
    if op is not None and not any(op is allowed for allowed in ALLOWED_OPERATORS):
        raise _refuse("operator not allowed")


def _check_type(sql_type: Any) -> None:
    if type(sql_type) not in ALLOWED_CAST_TYPES or getattr(sql_type, "collation", None):
        raise _refuse("type not allowed")


def _is_real_table(node: Any) -> bool:
    if _kind(node) is not Table:
        return False
    real = _REAL_TABLES.get(node.name)
    if real is None or node.schema is not None:
        return False
    return node is real or node._deannotate() is real


def _is_allowed_column(node: elements.ColumnClause[Any], kind: type) -> bool:
    if kind is elements.ColumnClause:
        # func.count() with no argument builds the bare star column. Nothing else may be built
        # from raw text, so only this exact one is accepted.
        return node.is_literal and node.name == "*" and node.table is None
    if node.is_literal:
        return False
    if _kind(node.table) in (selectable.Subquery, selectable.CTE):
        # A named column of a subquery or CTE. Its source is walked on its own, and the name is
        # always quoted by the compiler because it is not literal.
        return True
    return _is_real_table(node.table)


def _check_node(node: Any) -> None:
    kind = _kind(node)
    if kind not in _ALLOWED_NODES:
        raise _refuse("construct not recognised")
    if kind is Table:
        if not _is_real_table(node):
            raise _refuse("table outside the allowed set")
        return
    if kind in (Column, elements.ColumnClause) and not _is_allowed_column(node, kind):
        raise _refuse("column outside the allowed set")
    _check_unmodelled(node, kind)
    if kind in FUNCTION_TYPES and (node.packagenames or node.name not in ALLOWED_FUNCTIONS):
        raise _refuse("function not allowed")
    if kind in (
        elements.BinaryExpression,
        elements.UnaryExpression,
        elements.ClauseList,
        elements.BooleanClauseList,
        elements.Tuple,
    ):
        _check_operator(node.operator)
    if kind is elements.UnaryExpression and node.modifier is not None:
        if not any(node.modifier is m for m in ALLOWED_MODIFIERS):
            raise _refuse("operator not allowed")
    if kind is elements.BinaryExpression and node.modifiers:
        raise _refuse("operator not allowed")
    if kind is elements.Cast:
        _check_type(node.type)
    if kind is elements.TypeClause:
        _check_type(node.type)
    if kind is selectable.Select:
        froms = node.get_final_froms()
        if not froms:
            raise _refuse("select form not allowed")
        for source in froms:
            if _kind(source) in FUNCTION_TYPES:
                raise _refuse("function source not allowed")
            if _kind(source) in _JOIN_TYPES:
                # ORM joins are only built when the final FROM list is made, so walk them too.
                for part in iterate(source):
                    _check_node(part)
    if kind in _JOIN_TYPES:
        if _kind(node.left) in FUNCTION_TYPES or _kind(node.right) in FUNCTION_TYPES:
            raise _refuse("function source not allowed")


def check_compiled_statement(statement: Any) -> None:
    """Raise ToolSecurityError unless this statement object is a plain read of allowed tables."""
    if type(statement) in _HOUSEKEEPING_TYPES:
        return
    if type(statement) is not selectable.Select:
        raise _refuse("not a read")
    if "schema_translate_map" in statement._execution_options:
        raise _refuse("execution option not allowed")
    for node in iterate(statement):
        _check_node(node)


def check_raw_statement(sql: str) -> None:
    """Raw strings are refused unless they exactly match the housekeeping set."""
    if " ".join(sql.strip().rstrip(";").split()).lower() not in HOUSEKEEPING:
        raise _refuse("raw statement")


def check_execution(statement: str, compiled: Any) -> None:
    """The whole guard: cheap text checks first, then the tree walk or the raw-string rule."""
    if FORBIDDEN_TABLE in statement.lower():
        raise _refuse("forbidden table")
    if not statement.isascii():
        raise _refuse("unusual character")
    if compiled is None:
        check_raw_statement(statement)
        return
    # Values are bound parameters, so the text of a legitimate statement never has these.
    if ";" in statement or "--" in statement or "/*" in statement:
        raise _refuse("unexpected syntax")
    if getattr(compiled, "schema_translate_map", None):
        raise _refuse("execution option not allowed")
    if type(getattr(compiled, "statement", None)) in _HOUSEKEEPING_TYPES:
        check_raw_statement(statement)
    check_compiled_statement(getattr(compiled, "statement", None))


def _guard(
    conn: Connection,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> None:
    check_execution(statement, getattr(context, "compiled", None))


def install_guard(target: Engine | Connection) -> None:
    """Refuse every unsafe statement run on this engine or connection."""
    event.listen(target, "before_cursor_execute", _guard)


class StatementRecorder:
    """Records the text of every statement, for tests."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def _record(
        self,
        conn: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        self.statements.append(statement)

    def install(self, target: Engine | Connection) -> None:
        event.listen(target, "before_cursor_execute", self._record)


# ----------------------------------------------------------------- connections


def build_tool_engine(url: str) -> Engine:
    """Guarded engine for a given URL. Errors are generic so a URL can never leak."""
    try:
        engine = create_engine(
            url,
            echo=False,
            hide_parameters=True,
            pool_size=2,
            max_overflow=0,
        )
    except Exception:
        raise DatabaseConnectionError("Invalid database URL (details hidden)") from None
    install_guard(engine)
    return engine


def make_tool_engine() -> Engine:
    return build_tool_engine(get_database_url())


@contextmanager
def open_readonly(engine: Engine) -> Iterator[Connection]:
    """A connection inside a read-only transaction that is always rolled back. A statement
    that runs longer than STATEMENT_TIMEOUT_MS is cancelled by Postgres and surfaces as the
    generic DatabaseConnectionError below."""
    try:
        conn = engine.connect()
    except (SQLAlchemyError, OSError):
        raise DatabaseConnectionError("Database connection failed (details hidden)") from None
    try:
        conn.begin()
        conn.execute(SetTransactionReadOnly())
        conn.execute(SetLocalStatementTimeout())
        yield conn
    except (SQLAlchemyError, OSError):
        raise DatabaseConnectionError("Database operation failed (details hidden)") from None
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()
