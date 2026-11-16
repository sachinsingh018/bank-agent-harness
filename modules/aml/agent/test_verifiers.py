"""AML verifiers with an in-memory fake lookup: one test per failure kind, plus passes."""

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel

from harness.verifiers.base import VerifierResult, VerifyContext, run_verifiers
from modules.aml.agent.lookup import TransferView
from modules.aml.agent.models import AmlFindings, CitedAmount, Claim, Figure, Typology
from modules.aml.agent.spec import AML_SPEC
from modules.aml.agent.verifiers import (
    AlertMembershipVerifier,
    ArithmeticVerifier,
    CitationVerifier,
)

ALERT = "AL-0123456789ab"


def _view(
    txn_id: int, paid: str, received: str, pc: str = "US Dollar", rc: str = ""
) -> TransferView:
    return TransferView(
        txn_id=txn_id,
        paid=Decimal(paid),
        paid_currency=pc,
        received=Decimal(received),
        received_currency=rc or pc,
    )


STORE = {
    1: _view(1, "100.00", "100.00"),
    2: _view(2, "250.50", "230.10", "US Dollar", "Euro"),
    3: _view(3, "0.25", "0.25"),
    4: _view(4, "10", "9", "Euro"),
}


class FakeLookup:
    def __init__(
        self, store: dict[int, TransferView], alerts: dict[str, frozenset[int]] | None = None
    ) -> None:
        self.store = store
        self.alerts = alerts if alerts is not None else {ALERT: frozenset({1, 2})}

    def transfers(self, txn_ids: Iterable[int]) -> dict[int, TransferView]:
        return {i: self.store[i] for i in set(txn_ids) if i in self.store}

    def alert_txn_ids(self, alert_id: str) -> frozenset[int] | None:
        return self.alerts.get(alert_id)


def _ctx(
    seen: Iterable[int] = (1, 2, 3, 4), case_id: str = ALERT, lookup: Any = None
) -> VerifyContext:
    return VerifyContext(
        case_id=case_id,
        seen_txn_ids=frozenset(seen),
        lookup=lookup if lookup is not None else FakeLookup(STORE),
    )


def _amount(
    txn_id: int, value: str, side: str = "paid", currency: str = "US Dollar"
) -> CitedAmount:
    return CitedAmount.model_validate(
        {"txn_id": txn_id, "side": side, "value": value, "currency": currency}
    )


def _findings(
    claims: list[Claim] | None = None,
    figures: list[Figure] | None = None,
    recommendation: str = "escalate",
    alert_id: str = ALERT,
) -> AmlFindings:
    escalate = recommendation == "escalate"
    return AmlFindings.model_validate(
        {
            "alert_id": alert_id,
            "recommendation": recommendation,
            "confidence": 0.8 if escalate else 0.2,
            "typology": "FAN-OUT" if escalate else Typology.NONE,
            "narrative": "Test narrative.",
            "claims": claims
            if claims is not None
            else [Claim(statement="s", txn_ids=[1], amounts=[_amount(1, "100.00")])],
            "figures": figures or [],
        }
    )


def _check(verifier: Any, findings: BaseModel, ctx: VerifyContext | None = None) -> VerifierResult:
    result = verifier.check(findings, ctx or _ctx())
    assert isinstance(result, VerifierResult)
    assert result.passed == (not result.errors)
    return result


# ---- citations --------------------------------------------------------------------------


def test_citations_pass() -> None:
    claims = [
        Claim(
            statement="s",
            txn_ids=[1, 2],
            amounts=[_amount(1, "100.00"), _amount(2, "230.10", "received", "Euro")],
        )
    ]
    assert _check(CitationVerifier(), _findings(claims)).passed


def test_citations_decimal_scale_does_not_matter() -> None:
    claims = [Claim(statement="s", txn_ids=[1], amounts=[_amount(1, "100.0")])]
    assert _check(CitationVerifier(), _findings(claims)).passed
    claims = [Claim(statement="s", txn_ids=[1], amounts=[_amount(1, "100")])]
    assert _check(CitationVerifier(), _findings(claims)).passed


def test_citations_missing_txn() -> None:
    claims = [Claim(statement="s", txn_ids=[1, 99])]
    result = _check(CitationVerifier(), _findings(claims), _ctx(seen=(1, 99)))
    assert result.errors == ["txn 99: does not exist"]


def test_citations_not_seen() -> None:
    result = _check(CitationVerifier(), _findings(), _ctx(seen=()))
    assert result.errors == ["txn 1: did not appear in any tool result in this run"]


def test_citations_figure_ids_are_checked_too() -> None:
    figures = [Figure(kind="count", value="1", txn_ids=[77])]
    result = _check(CitationVerifier(), _findings(figures=figures))
    assert "txn 77: does not exist" in result.errors
    assert "txn 77: did not appear in any tool result in this run" in result.errors


def test_citations_wrong_amount() -> None:
    claims = [Claim(statement="s", txn_ids=[1], amounts=[_amount(1, "100.01")])]
    result = _check(CitationVerifier(), _findings(claims))
    assert result.errors == ["txn 1: cited paid amount does not match the stored amount"]
    assert "100.00" not in " ".join(result.errors)


def test_citations_wrong_side() -> None:
    claims = [Claim(statement="s", txn_ids=[2], amounts=[_amount(2, "250.50", "received")])]
    result = _check(CitationVerifier(), _findings(claims))
    assert "txn 2: cited received amount does not match the stored amount" in result.errors
    assert "txn 2: cited received currency does not match the stored currency" in result.errors


def test_citations_wrong_currency() -> None:
    claims = [Claim(statement="s", txn_ids=[1], amounts=[_amount(1, "100.00", currency="Euro")])]
    result = _check(CitationVerifier(), _findings(claims))
    assert result.errors == ["txn 1: cited paid currency does not match the stored currency"]


def test_citations_amount_txn_not_in_claim() -> None:
    claims = [Claim(statement="s", txn_ids=[1], amounts=[_amount(3, "0.25")])]
    result = _check(CitationVerifier(), _findings(claims))
    assert result.errors == [
        "claim 0: txn 3: amount cited but the txn is not in the claim's txn_ids"
    ]


# ---- arithmetic -------------------------------------------------------------------------


def _sum(value: str, ids: list[int], side: str = "paid", currency: str = "US Dollar") -> Figure:
    return Figure.model_validate(
        {"kind": "sum", "value": value, "currency": currency, "side": side, "txn_ids": ids}
    )


def test_arithmetic_pass() -> None:
    figures = [
        _sum("350.75", [1, 2, 3]),
        _sum("100.25", [1, 3, 3], side="received"),  # duplicates count once
        Figure(kind="count", value="3", txn_ids=[1, 2, 3, 2]),
    ]
    assert _check(ArithmeticVerifier(), _findings(figures=figures)).passed


def test_arithmetic_sum_scale_does_not_matter() -> None:
    assert _check(ArithmeticVerifier(), _findings(figures=[_sum("100.0", [1])])).passed


def test_arithmetic_wrong_sum() -> None:
    result = _check(ArithmeticVerifier(), _findings(figures=[_sum("350.76", [1, 2, 3])]))
    assert result.errors == [
        "figure 0: sum does not equal the exact sum of the cited transfers on the paid side"
    ]


def test_arithmetic_cross_currency_is_refused() -> None:
    result = _check(ArithmeticVerifier(), _findings(figures=[_sum("109.00", [1, 4])]))
    assert len(result.errors) == 1
    assert result.errors[0].startswith("figure 0: txn 4: paid currency differs")


def test_arithmetic_currency_on_the_other_side() -> None:
    result = _check(ArithmeticVerifier(), _findings(figures=[_sum("330.10", [1, 2], "received")]))
    assert len(result.errors) == 1
    assert result.errors[0].startswith("figure 0: txn 2: received currency differs")


def test_arithmetic_missing_txn() -> None:
    result = _check(ArithmeticVerifier(), _findings(figures=[_sum("100.00", [1, 99])]))
    assert result.errors == ["figure 0: txn 99: does not exist"]


def test_arithmetic_wrong_count() -> None:
    figures = [Figure(kind="count", value="4", txn_ids=[1, 2, 2, 3])]
    result = _check(ArithmeticVerifier(), _findings(figures=figures))
    assert result.errors == [
        "figure 0: count does not equal the number of distinct cited transfers"
    ]


# ---- alert membership -------------------------------------------------------------------


def test_membership_escalate_pass() -> None:
    assert _check(AlertMembershipVerifier(), _findings()).passed


def test_membership_close_needs_no_alert_transfer() -> None:
    claims = [Claim(statement="s", txn_ids=[3])]
    findings = _findings(claims, recommendation="close")
    assert _check(AlertMembershipVerifier(), findings).passed


def test_membership_escalate_without_alert_transfer() -> None:
    claims = [Claim(statement="s", txn_ids=[3]), Claim(statement="t", txn_ids=[4])]
    result = _check(AlertMembershipVerifier(), _findings(claims))
    assert result.errors == ["escalate needs at least one claim citing a transfer in the alert"]


def test_membership_alert_not_found() -> None:
    ctx = _ctx(lookup=FakeLookup(STORE, alerts={}))
    result = _check(AlertMembershipVerifier(), _findings(), ctx)
    assert result.errors == ["alert not found"]


def test_membership_close_alert_not_found() -> None:
    claims = [Claim(statement="s", txn_ids=[3])]
    findings = _findings(claims, recommendation="close")
    ctx = _ctx(lookup=FakeLookup(STORE, alerts={}))
    result = _check(AlertMembershipVerifier(), findings, ctx)
    assert not result.passed and result.errors == ["alert not found"]


def test_membership_wrong_alert_id() -> None:
    findings = _findings(alert_id="AL-ffffffffffff", recommendation="close")
    result = _check(AlertMembershipVerifier(), findings)
    assert result.errors == ["alert_id does not match the alert being investigated"]


# ---- together ---------------------------------------------------------------------------


def test_spec_verifiers_all_pass_on_good_findings() -> None:
    figures = [_sum("100.00", [1]), Figure(kind="count", value="1", txn_ids=[1])]
    results = run_verifiers(AML_SPEC.verifiers, _findings(figures=figures), _ctx())
    assert [r.passed for r in results] == [True, True, True]


def test_lookup_crash_is_a_failure() -> None:
    class Broken(FakeLookup):
        def transfers(self, txn_ids: Iterable[int]) -> dict[int, TransferView]:
            raise RuntimeError("db down")

    results = run_verifiers(AML_SPEC.verifiers, _findings(), _ctx(lookup=Broken(STORE)))
    assert [r.passed for r in results] == [False, True, True]
    assert results[0].errors == ["verifier error (RuntimeError)"]


def test_wrong_output_type_is_a_failure() -> None:
    class Other(BaseModel):
        x: int = 1

    results = run_verifiers(AML_SPEC.verifiers, Other(), _ctx())
    assert all(not r.passed for r in results)


@pytest.mark.parametrize("verifier", AML_SPEC.verifiers)
def test_verifiers_have_names(verifier: Any) -> None:
    assert verifier.name
