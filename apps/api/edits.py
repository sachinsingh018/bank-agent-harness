"""Approve with edits: build the reviewer's edited findings and check them like the agent's.

Only four fields are editable (recommendation, confidence, typology, narrative); claims and
figures stay as the agent wrote them. The edited findings are validated with the module's
output model and then run through the module's deterministic verifiers, reading stored data
through the same guarded read-only door as the agent. The module-specific parts live here so
the queue endpoints stay generic. Plan: docs/plans/edits-and-corrections.md section 1.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Engine

from harness.verifiers.base import VerifierResult, VerifyContext, run_verifiers_detailed
from modules.aml.agent.lookup import AmlLookup
from modules.aml.agent.models import AmlFindings
from modules.aml.agent.spec import AML_SPEC
from modules.aml.agent.verifiers import cited_txn_ids

EDITABLE_FIELDS = ("recommendation", "confidence", "typology", "narrative")
NO_FINDINGS = "this run has no findings to edit"
UNSUPPORTED = "edits are not supported for this module"


class FindingsEdits(BaseModel):
    """The reviewer's values for the editable fields. The module's model checks the values."""

    model_config = ConfigDict(extra="forbid")

    recommendation: Annotated[str, Field(min_length=1, max_length=40)]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    typology: Annotated[str, Field(min_length=1, max_length=40)]
    narrative: Annotated[str, Field(min_length=1, max_length=1200)]


class EditCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    errors: list[str]  # empty when the edited findings pass every check
    edited: dict[str, Any]
    original: dict[str, Any]
    verifier_results: list[VerifierResult]
    infrastructure_error: bool  # a verifier raised, for example a database error


def _validation_errors(exc: ValidationError) -> list[str]:
    errors: list[str] = []
    for e in exc.errors():
        where = ".".join(str(part) for part in e["loc"])
        message = str(e["msg"]).removeprefix("Value error, ")
        errors.append(f"{where}: {message}" if where else message)
    return errors


def check_edits(
    module: str,
    case_id: str,
    findings: dict[str, Any] | None,
    edits: FindingsEdits,
    tool_engine: Engine,
) -> EditCheck:
    """Edited findings = the original findings with the four fields replaced. Errors list why
    they fail; the caller writes nothing unless errors is empty."""
    edited = edits.model_dump()
    if module != AML_SPEC.name:
        return EditCheck(
            errors=[UNSUPPORTED],
            edited=edited,
            original={},
            verifier_results=[],
            infrastructure_error=False,
        )
    if not isinstance(findings, dict):
        return EditCheck(
            errors=[NO_FINDINGS],
            edited=edited,
            original={},
            verifier_results=[],
            infrastructure_error=False,
        )
    original = {k: findings.get(k) for k in EDITABLE_FIELDS}
    try:
        output = AmlFindings.model_validate({**findings, **edited})
    except ValidationError as exc:
        return EditCheck(
            errors=_validation_errors(exc),
            edited=edited,
            original=original,
            verifier_results=[],
            infrastructure_error=False,
        )
    # Claims are unchanged, so the transfers the agent cited are exactly the ones it saw.
    ctx = VerifyContext(
        case_id=case_id,
        seen_txn_ids=frozenset(cited_txn_ids(output)),
        lookup=AmlLookup(tool_engine),
    )
    run = run_verifiers_detailed(AML_SPEC.verifiers, output, ctx)
    errors = [f"{r.name}: {e}" for r in run.results if not r.passed for e in r.errors]
    return EditCheck(
        errors=errors,
        edited=edited,
        original=original,
        verifier_results=run.results,
        infrastructure_error=run.error_class is not None,
    )
