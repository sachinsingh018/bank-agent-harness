"""The verifier interface and the runner that applies every verifier to an output.

Verifiers are deterministic plain code: no model calls. run_verifiers runs all of them, never
stops at the first failure, and turns a crashing verifier into a failed result, so a bug in a
verifier can never let an output through.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict


class VerifierResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    errors: list[str]


@dataclass(frozen=True)
class VerifyContext:
    case_id: str  # the case being run, for example the alert id
    seen_txn_ids: frozenset[int]  # every transfer id that appeared in a tool result this run
    lookup: Any  # read-only access to stored data, defined by the module


class Verifier(Protocol):
    name: str

    def check(self, output: BaseModel, ctx: VerifyContext) -> VerifierResult: ...


def _crashed(name: str, exc: BaseException) -> VerifierResult:
    # Only the exception class: messages can hold data and must never reach the model.
    return VerifierResult(
        name=name, passed=False, errors=[f"verifier error ({type(exc).__name__})"]
    )


def run_verifiers(
    verifiers: Sequence[Verifier], output: BaseModel, ctx: VerifyContext
) -> list[VerifierResult]:
    """Run every verifier in order and return one result each. An exception inside a verifier,
    or a return value that is not a VerifierResult, becomes a failed result."""
    results: list[VerifierResult] = []
    for verifier in verifiers:
        name = str(getattr(verifier, "name", type(verifier).__name__))
        try:
            result = verifier.check(output, ctx)
        except Exception as exc:
            results.append(_crashed(name, exc))
            continue
        if not isinstance(result, VerifierResult):
            results.append(
                VerifierResult(name=name, passed=False, errors=["verifier returned no result"])
            )
            continue
        results.append(result)
    return results
