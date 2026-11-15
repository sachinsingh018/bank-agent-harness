"""Deterministic checks on AmlFindings against stored data. Plain code, no model calls.

Each verifier reads only through the read-only lookup in VerifyContext (see lookup.py). Error
messages go back to the model on the one retry, so they name the transfer id and what is wrong
and never contain stored values. Plan: docs/plans/agent-loop.md section 7.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from harness.verifiers.base import VerifierResult, VerifyContext
from modules.aml.agent.lookup import AmlLookupLike
from modules.aml.agent.models import AmlFindings


def _findings(output: BaseModel) -> AmlFindings:
    if not isinstance(output, AmlFindings):
        raise TypeError("expected AmlFindings")
    return output


def _result(name: str, errors: list[str]) -> VerifierResult:
    return VerifierResult(name=name, passed=not errors, errors=errors)


def cited_txn_ids(findings: AmlFindings) -> list[int]:
    """Every transfer id cited anywhere in the findings, sorted and distinct."""
    ids: set[int] = set()
    for claim in findings.claims:
        ids.update(claim.txn_ids)
        ids.update(a.txn_id for a in claim.amounts)
    for figure in findings.figures:
        ids.update(figure.txn_ids)
    return sorted(ids)


class CitationVerifier:
    """Cited transfers exist and were seen this run; cited amounts match the stored ones."""

    name = "citations"

    def check(self, output: BaseModel, ctx: VerifyContext) -> VerifierResult:
        findings = _findings(output)
        lookup: AmlLookupLike = ctx.lookup
        cited = cited_txn_ids(findings)
        stored = lookup.transfers(cited)
        errors: list[str] = []
        for txn_id in cited:
            if txn_id not in stored:
                errors.append(f"txn {txn_id}: does not exist")
            if txn_id not in ctx.seen_txn_ids:
                errors.append(f"txn {txn_id}: did not appear in any tool result in this run")
        for c_index, claim in enumerate(findings.claims):
            for amount in claim.amounts:
                txn_id = amount.txn_id
                if txn_id not in claim.txn_ids:
                    errors.append(
                        f"claim {c_index}: txn {txn_id}: amount cited but the txn is not in "
                        "the claim's txn_ids"
                    )
                view = stored.get(txn_id)
                if view is None:
                    continue
                value, currency = view.amount(amount.side)
                if Decimal(amount.value) != value:
                    errors.append(
                        f"txn {txn_id}: cited {amount.side} amount does not match the stored amount"
                    )
                if amount.currency != currency:
                    errors.append(
                        f"txn {txn_id}: cited {amount.side} currency does not match the stored "
                        "currency"
                    )
        return _result(self.name, errors)


class ArithmeticVerifier:
    """Sums equal the exact sum of the cited transfers; counts equal the distinct ids."""

    name = "arithmetic"

    def check(self, output: BaseModel, ctx: VerifyContext) -> VerifierResult:
        findings = _findings(output)
        lookup: AmlLookupLike = ctx.lookup
        errors: list[str] = []
        for f_index, figure in enumerate(findings.figures):
            distinct = sorted(set(figure.txn_ids))
            if figure.kind == "count":
                if int(figure.value) != len(distinct):
                    errors.append(
                        f"figure {f_index}: count does not equal the number of distinct cited "
                        "transfers"
                    )
                continue
            side = figure.side
            if side is None:  # the model validator already requires it for a sum
                errors.append(f"figure {f_index}: a sum needs a side")
                continue
            stored = lookup.transfers(distinct)
            figure_errors: list[str] = []
            total = Decimal(0)
            for txn_id in distinct:
                view = stored.get(txn_id)
                if view is None:
                    figure_errors.append(f"figure {f_index}: txn {txn_id}: does not exist")
                    continue
                value, currency = view.amount(side)
                if currency != figure.currency:
                    figure_errors.append(
                        f"figure {f_index}: txn {txn_id}: {side} currency differs from the "
                        "figure currency (no cross-currency sums)"
                    )
                    continue
                total += value
            if not figure_errors and Decimal(figure.value) != total:
                figure_errors.append(
                    f"figure {f_index}: sum does not equal the exact sum of the cited "
                    f"transfers on the {side} side"
                )
            errors.extend(figure_errors)
        return _result(self.name, errors)


class AlertMembershipVerifier:
    """The findings are about this case's alert, and escalate cites one of its transfers."""

    name = "alert_membership"

    def check(self, output: BaseModel, ctx: VerifyContext) -> VerifierResult:
        findings = _findings(output)
        lookup: AmlLookupLike = ctx.lookup
        errors: list[str] = []
        if findings.alert_id != ctx.case_id:
            errors.append("alert_id does not match the alert being investigated")
        if findings.recommendation == "escalate":
            members = lookup.alert_txn_ids(ctx.case_id)
            if members is None:
                errors.append("alert not found")
            elif not any(set(claim.txn_ids) & members for claim in findings.claims):
                errors.append("escalate needs at least one claim citing a transfer in the alert")
        return _result(self.name, errors)
