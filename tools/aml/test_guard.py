"""Pure tests: the statement guard. No database. Statements are built as SQLAlchemy objects
and compiled with the PostgreSQL dialect, exactly as the listener would see them."""

from typing import Any, cast

import pytest
import sqlalchemy
from sqlalchemy import (
    Column,
    Date,
    Integer,
    MetaData,
    String,
    Table,
    column,
    delete,
    func,
    insert,
    literal,
    literal_column,
    select,
    table,
    text,
    update,
    values,
)
from sqlalchemy import (
    cast as sa_cast,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import with_loader_criteria
from sqlalchemy.schema import CreateTable, DropTable
from sqlalchemy.sql import elements, operators
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.types import UserDefinedType

from harness.db.models import Account, Alert, AlertTransaction, Transaction
from tools.aml.db import (
    ALLOWED_FUNCTIONS,
    ALLOWED_TABLES,
    HOUSEKEEPING,
    SetTransactionReadOnly,
    ToolSecurityError,
    check_compiled_statement,
    check_execution,
)

# Test files are outside the static scan. The name is built from two pieces so the full
# literal never appears in non-test code, which keeps the repo-wide scan auditable.
LABELS = "transaction" + "_labels"
DIALECT = postgresql.dialect()  # type: ignore[no-untyped-call]


def run_guard(statement: Any) -> None:
    """Compile like the listener sees it and run the whole guard."""
    compiled = statement.compile(dialect=DIALECT)
    check_execution(str(compiled), compiled)


def run_raw(sql: str) -> None:
    """A raw string, as exec_driver_sql would send it: no compiled object."""
    check_execution(sql, None)


def refused(statement: Any) -> None:
    with pytest.raises(ToolSecurityError):
        run_guard(statement)


def labels_table() -> Any:
    return table(LABELS, column("txn_id"), column("is_laundering"))


def labels_real_table() -> Table:
    return Table(LABELS, MetaData(), Column("txn_id", Integer), Column("x", String))


# ------------------------------------------------------------------ accepted


def test_plain_reads_of_the_real_tables_are_accepted() -> None:
    run_guard(select(Alert.alert_id).where(Alert.alert_id == "x"))
    run_guard(select(func.count()).select_from(Transaction))
    run_guard(
        select(Transaction.txn_id, func.sum(Transaction.amt_paid), func.min(Transaction.ts))
        .join(AlertTransaction, AlertTransaction.txn_id == Transaction.txn_id)
        .group_by(Transaction.txn_id)
        .order_by(func.count().desc())
        .limit(5)
    )
    run_guard(select(Account.bank).where(Account.bank.in_([1, 2])))
    sub = select(Transaction.from_bank).subquery()
    run_guard(select(func.count()).select_from(sub))
    cte = select(Alert.alert_id).cte("c")
    run_guard(select(cte.c.alert_id))
    run_guard(select(func.max(Alert.txn_count).filter(Alert.txn_count > 1)))


def test_housekeeping_set_is_accepted_exactly() -> None:
    assert HOUSEKEEPING == {"set transaction read only"}
    for sql in ("SET TRANSACTION READ ONLY", "set  transaction\n read only;"):
        run_raw(sql)
    run_guard(SetTransactionReadOnly())


def test_allowed_sets_are_exact() -> None:
    assert ALLOWED_TABLES == {"transactions", "accounts", "alerts", "alert_transactions"}
    assert LABELS not in ALLOWED_TABLES
    assert ALLOWED_FUNCTIONS == {"count", "sum", "min", "max"}


# ------------------------------------------------------------------- refused


def test_text_of_anything_is_refused() -> None:
    refused(text("select 1"))
    refused(text("select a from alerts"))
    refused(select(Alert.alert_id).where(text("1=1")))
    refused(select(Alert.alert_id).order_by(text("1")))
    refused(select(text("1")).select_from(Alert))
    refused(select(text("a")).select_from(text("alerts")))


def test_tables_outside_the_four_are_refused() -> None:
    refused(select(labels_table().c.txn_id))
    refused(select(func.count()).select_from(labels_table()))
    refused(select(labels_real_table().c.txn_id))
    refused(select(func.count()).select_from(labels_real_table()))
    refused(select(literal(1)).select_from(table("users")))
    refused(select(literal(1)).select_from(table("alerts", schema="other")))
    refused(select(literal(1)).select_from(labels_table().alias("alerts")))
    # A look-alike with an allowed name that is not the real table object.
    refused(select(column("alert_id")).select_from(table("alerts", column("alert_id"))))
    fake = Table("alerts", MetaData(), Column("alert_id", String))
    refused(select(fake.c.alert_id))


@pytest.mark.parametrize(
    "name",
    [
        "query_to_xml",
        "table_to_xml",
        "cursor_to_xml",
        "database_to_xml",
        "ts_stat",
        "generate_series",
        "pg_sleep",
        "pg_read_file",
        "lo_import",
        "dblink",
        "set_config",
        "current_setting",
        "version",
        "coalesce",
        "date_trunc",
        "now",
        "avg",
        "some_unlisted_function",
    ],
)
def test_functions_off_the_allowlist_are_refused(name: str) -> None:
    call = getattr(func, name)
    refused(select(call("select 1")).select_from(Alert))
    refused(select(Alert.alert_id).where(call("x") == 1))
    refused(select(call("select 1")).select_from(Alert).limit(1))
    refused(select(literal(1)).select_from(call(1, 2).table_valued("x")))
    refused(select(literal(1)).select_from(Alert).join(call(1, 2).table_valued("x"), literal(1)))
    refused(select(literal(1)).select_from(call(1, 2).table_valued("x").lateral()))
    refused(select(literal_column(f"{name}('select 1')")).select_from(Alert))
    refused(select(Alert.alert_id).where(text(f"{name}('x') is not null")))


def test_function_in_from_or_join_is_refused_even_if_listed() -> None:
    refused(select(literal(1)).select_from(func.count()))
    refused(select(Alert.alert_id).join(func.count(), literal(True)))
    refused(select(func.count().table_valued("x")))
    refused(select(func.count()).select_from(func.count().table_valued("x").lateral()))
    refused(select(func.pg_catalog.count()).select_from(Alert))


def test_values_and_lateral_are_refused() -> None:
    v = values(column("a", Integer), name="v").data([(1,), (2,)])
    refused(select(v.c.a))
    refused(select(Alert.alert_id).select_from(Alert).where(Alert.txn_count.in_(select(v.c.a))))
    sub = select(Alert.alert_id).subquery()
    refused(
        select(sub.c.alert_id)
        .select_from(Alert)
        .join(select(Alert.alert_id).lateral(), literal(True))
    )


def test_ctes_and_subqueries_cannot_hide_a_disallowed_table() -> None:
    hidden = select(labels_table().c.txn_id).cte("alerts")
    refused(select(hidden.c.txn_id))
    refused(select(Alert.alert_id).where(Alert.alert_id.in_(select(hidden.c.txn_id))))
    users_cte = select(table("users", column("a")).c.a).cte("transactions")
    refused(select(users_cte.c.a))
    # A CTE named after an allowed table that selects from a disallowed one.
    alerts_cte = select(labels_table().c.txn_id).cte("alerts")
    refused(select(func.count()).select_from(alerts_cte))
    sub = select(labels_table().c.txn_id).subquery("accounts")
    refused(select(sub.c.txn_id))
    refused(select(func.count()).select_from(sub))
    refused(select(literal(1)).select_from(Alert).where(Alert.alert_id.in_(select(sub.c.txn_id))))
    refused(select(sub.c.txn_id).select_from(sub).join(Alert, literal(True)))


def test_union_with_a_disallowed_table_is_refused() -> None:
    both = select(Alert.alert_id).union(select(labels_table().c.txn_id))
    refused(both)
    refused(select(both.subquery().c.alert_id))
    refused(select(Alert.alert_id).union_all(select(Account.acct)))
    # Even a union of allowed tables is not a plain select at the top level.
    refused(select(Alert.alert_id).union(select(Account.acct)))
    # The queries never use a union, so a union nested in a subquery is refused as well.
    nested = select(Alert.alert_id).union(select(Account.acct)).subquery()
    refused(select(nested.c.alert_id))
    nested_bad = select(Alert.alert_id).union(select(labels_table().c.txn_id)).subquery()
    refused(select(nested_bad.c.alert_id))


def test_scalar_subquery_on_a_disallowed_table_is_refused() -> None:
    bad = select(labels_table().c.txn_id).scalar_subquery()
    refused(select(Alert.alert_id, bad))
    refused(select(Alert.alert_id).where(Alert.txn_count == bad))
    refused(select(Alert.alert_id).where(Alert.txn_count.in_(select(labels_table().c.txn_id))))
    refused(select(Alert.alert_id).where(select(labels_table().c.txn_id).exists()))
    refused(select(Alert.alert_id).where(~Alert.txn_count.in_(select(labels_table().c.txn_id))))


def test_joins_to_a_disallowed_table_are_refused() -> None:
    refused(select(Alert.alert_id).join(labels_table(), literal(True)))
    refused(select(Alert.alert_id).outerjoin(labels_table(), literal(True)))
    refused(select(Alert.alert_id).select_from(Alert).join(labels_real_table(), literal(True)))
    refused(select(Alert.alert_id).select_from(Alert, labels_table()))


def test_non_select_statements_are_refused() -> None:
    refused(insert(Alert).values(alert_id="x"))
    refused(update(Alert).values(summary="x"))
    refused(delete(Alert))
    refused(delete(Alert).where(Alert.alert_id == "x").returning(Alert.alert_id))
    refused(CreateTable(Table("t", MetaData(), Column("a", Integer))))
    refused(DropTable(cast(Table, Alert.__table__)))
    refused(insert(Alert).from_select(["alert_id"], select(Alert.alert_id)))


def test_literal_column_and_raw_text_nodes_are_refused() -> None:
    refused(select(literal_column("pg_sleep(10)")).select_from(Alert))
    refused(select(literal_column("1 from users --")).select_from(Alert))
    refused(select(Alert.alert_id, literal_column("(select 1)")))
    refused(select(Alert.alert_id).where(literal_column("true")))
    refused(select(Alert.alert_id).order_by(literal_column("1")))
    refused(select(column("x")).select_from(Alert))
    refused(select(literal_column("*")).select_from(labels_table()))
    refused(select(Alert.alert_id).with_for_update())


def test_froms_less_selects_are_refused() -> None:
    refused(select(literal(1)))
    refused(select(func.count()))
    refused(select(func.max(literal(1))))


def test_non_ascii_and_label_text_are_refused() -> None:
    compiled = select(Alert.alert_id).compile(dialect=DIALECT)
    with pytest.raises(ToolSecurityError):
        check_execution("select 1 from alerts -- café", compiled)
    with pytest.raises(ToolSecurityError):
        check_execution(f"select 1 from alerts /* {LABELS.upper()} */", compiled)
    refused(select(Alert.alert_id.label(LABELS)))


RAW_REFUSED = [
    "select 1",
    "select 1from users",
    "select a from alerts where x in (table users)",
    "with users as (select * from users) select * from users",
    "select query_to_xml('select 1', true, false, '')",
    "select * from alerts",
    "select count(*) from alerts",
    "table alerts",
    "values (1)",
    "show standard_conforming_strings",
    "select current_schema()",
    "select pg_catalog.version()",
    "set transaction read write",
    "set transaction read only; select 1",
    "set transaction read only; drop table alerts",
    "insert into alerts values (1)",
    "begin",
    "",
    f"select count(*) from {LABELS}",
    f"SELECT 1 FROM {LABELS.upper()}",
]


@pytest.mark.parametrize("sql", RAW_REFUSED)
def test_raw_strings_are_refused_unless_exact_housekeeping(sql: str) -> None:
    with pytest.raises(ToolSecurityError):
        run_raw(sql)


def test_refusal_message_contains_no_statement_text() -> None:
    sql = f"select secret_marker_xyz from alerts, {LABELS} where 'param_marker' = 'x'"
    with pytest.raises(ToolSecurityError) as raw_info:
        run_raw(sql)
    stmt = select(literal("param_marker")).select_from(labels_table())
    with pytest.raises(ToolSecurityError) as tree_info:
        run_guard(stmt)
    for info in (raw_info, tree_info):
        message = str(info.value)
        assert len(message) < 80
        assert message.startswith("statement refused: ")
        for fragment in ("secret_marker_xyz", "param_marker", "alerts", LABELS, "select"):
            assert fragment not in message.lower(), fragment


def test_read_only_construct_compiles_to_the_expected_statement() -> None:
    assert str(SetTransactionReadOnly().compile(dialect=DIALECT)) == "SET TRANSACTION READ ONLY"


# ------------------------------------------------------- bypass attempts, all refused


def test_installed_sqlalchemy_major_version_is_reviewed() -> None:
    # The guard walks SQLAlchemy 2.x internals. A major upgrade must force a review of it.
    assert sqlalchemy.__version__.split(".")[0] == "2"


def test_prefixes_suffixes_and_hints_are_refused() -> None:
    base = select(Alert.alert_id)
    refused(base.prefix_with("/* x */"))
    refused(base.prefix_with("DISTINCT"))
    refused(base.suffix_with("; COMMIT; DELETE FROM alerts"))
    refused(base.suffix_with("FOR UPDATE"))
    refused(base.with_statement_hint("x"))
    refused(base.with_hint(cast(Table, Alert.__table__), "ONLY"))
    inners = [
        select(Alert.alert_id).prefix_with("x"),
        select(Alert.alert_id).suffix_with("; DELETE FROM alerts"),
        select(Alert.alert_id).with_statement_hint("x"),
    ]
    for inner in inners:
        refused(select(inner.subquery().c.alert_id))
    refused(select(select(Alert.alert_id).suffix_with("x").subquery().c.alert_id))
    cte_inner = select(Alert.alert_id)
    refused(select(cte_inner.suffix_with("x").cte("c").c.alert_id))
    refused(select(cte_inner.cte("c").prefix_with("x").c.alert_id))
    refused(select(cte_inner.cte("c").suffix_with("x").c.alert_id))
    refused(select(Alert.alert_id).where(Alert.alert_id.in_(base.suffix_with("x"))))


def test_unmodelled_select_attributes_are_refused() -> None:
    refused(select(Alert.alert_id).with_for_update())
    refused(select(Alert.alert_id).correlate(cast(Table, Alert.__table__)))


def test_custom_operators_are_refused() -> None:
    payload = "= 1 or exists (select 1 from users) or"
    refused(select(Alert.alert_id).where(Alert.alert_id.op(payload)(literal("x"))))
    refused(select(Alert.txn_count.op("+")(1)))
    custom: Any = operators.custom_op("; drop table alerts; --")
    anything: Any = Alert.txn_count
    unary: Any = elements.UnaryExpression(anything, operator=custom)
    refused(select(unary).select_from(Alert))
    modified: Any = elements.UnaryExpression(anything, modifier=custom)
    refused(select(modified).select_from(Alert))
    clauses = elements.ClauseList(Alert.txn_count, Alert.alert_id, operator=custom)
    # A plain ClauseList with a custom operator cannot even be compiled, so test the tree walk.
    with pytest.raises(ToolSecurityError):
        check_compiled_statement(select(clauses).select_from(Alert))


def test_standard_operators_off_the_list_are_refused() -> None:
    refused(select(Alert.alert_id).where(Alert.alert_id.like("a%")))
    refused(select(Alert.alert_id).where(Alert.alert_id.ilike("a%")))
    refused(select(Alert.alert_id).where(Alert.alert_id.regexp_match("a")))
    refused(select(Alert.txn_count + 1).select_from(Alert))
    refused(select(Alert.alert_id).where(Alert.txn_count.between(1, 2)))


def test_casts_to_unlisted_types_are_refused() -> None:
    run_guard(select(sa_cast(Transaction.ts, Date)).select_from(Transaction))

    class Evil(UserDefinedType[str]):
        cache_ok = True

        def get_col_spec(self, **kw: Any) -> str:
            return "text) from users; --"

    refused(select(sa_cast(Alert.alert_id, Evil())))
    refused(select(sa_cast(Alert.alert_id, String(collation="C"))))
    refused(select(sa_cast(Alert.alert_id, String)))
    refused(select(sa_cast(Alert.alert_id, Integer)))


def test_subclasses_and_compiles_overrides_are_refused() -> None:
    class EvilBinary(elements.BinaryExpression[bool]):
        inherit_cache = False

    @compiles(EvilBinary)
    def _compile_binary(element: Any, compiler: Any, **kw: Any) -> str:
        return "1 = 1"

    plain = Alert.txn_count == 1
    evil = EvilBinary(plain.left, plain.right, plain.operator)
    refused(select(Alert.alert_id).where(evil))

    class EvilLabel(elements.Label[Any]):
        inherit_cache = False

    @compiles(EvilLabel)
    def _compile_label(element: Any, compiler: Any, **kw: Any) -> str:
        return "(select 1)"

    refused(select(EvilLabel("x", Alert.alert_id)))

    class EvilColumn(Column[Any]):
        inherit_cache = False

    refused(select(EvilColumn("a", Integer)).select_from(Alert))


def test_function_subclass_named_like_an_allowed_function_is_refused() -> None:
    class count(FunctionElement[int]):  # noqa: N801
        name = "count"
        inherit_cache = False

    @compiles(count)
    def _compile_count(element: Any, compiler: Any, **kw: Any) -> str:
        return "pg_sleep(10)"

    refused(select(count()).select_from(Alert))
    refused(select(Alert.alert_id).where(count() > 1))


def test_loader_criteria_and_options_are_refused() -> None:
    refused(select(Alert).options(with_loader_criteria(Alert, Alert.txn_count > 1)))
    refused(select(Alert.alert_id).options(with_loader_criteria(Alert, Alert.txn_count > 1)))


def test_schema_translate_map_is_refused() -> None:
    stmt = select(Alert.alert_id).execution_options(schema_translate_map={None: "other"})
    refused(stmt)
    compiled = select(Alert.alert_id).compile(dialect=DIALECT, schema_translate_map={None: "other"})
    with pytest.raises(ToolSecurityError):
        check_execution(str(compiled), compiled)


def test_semicolons_and_comments_in_compiled_text_are_refused() -> None:
    compiled = select(Alert.alert_id).compile(dialect=DIALECT)
    for text_ in (
        "select 1 from alerts; delete from alerts",
        "select 1 from alerts -- x",
        "select 1 from alerts /* x */",
    ):
        with pytest.raises(ToolSecurityError):
            check_execution(text_, compiled)
    # A @compiles override that renders a semicolon or a comment is refused, whatever its type.
    refused(select(Alert.alert_id.label("a;b")))
    refused(select(Alert.alert_id.label("a--b")))
    refused(select(Alert.alert_id.label("a/*b")))
