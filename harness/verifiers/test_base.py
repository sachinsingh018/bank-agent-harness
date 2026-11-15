"""run_verifiers: all verifiers run, and a crash is a failure."""

from typing import Any

from pydantic import BaseModel

from harness.verifiers.base import VerifierResult, VerifyContext, run_verifiers

SECRET = "secret-value-in-exception"


class Out(BaseModel):
    x: int


class Passing:
    name = "pass"

    def check(self, output: BaseModel, ctx: VerifyContext) -> VerifierResult:
        return VerifierResult(name=self.name, passed=True, errors=[])


class Failing:
    name = "fail"

    def check(self, output: BaseModel, ctx: VerifyContext) -> VerifierResult:
        return VerifierResult(name=self.name, passed=False, errors=["bad"])


class Crashing:
    name = "crash"

    def check(self, output: BaseModel, ctx: VerifyContext) -> VerifierResult:
        raise KeyError(SECRET)


class NoResult:
    name = "none"

    def check(self, output: BaseModel, ctx: VerifyContext) -> Any:
        return True


CTX = VerifyContext(case_id="C-1", seen_txn_ids=frozenset(), lookup=None)


def test_all_verifiers_run_without_short_circuit() -> None:
    results = run_verifiers([Failing(), Crashing(), Passing()], Out(x=1), CTX)
    assert [(r.name, r.passed) for r in results] == [
        ("fail", False),
        ("crash", False),
        ("pass", True),
    ]


def test_exception_becomes_generic_failure() -> None:
    [result] = run_verifiers([Crashing()], Out(x=1), CTX)
    assert not result.passed
    assert result.errors == ["verifier error (KeyError)"]
    assert SECRET not in result.model_dump_json()


def test_non_result_return_is_a_failure() -> None:
    [result] = run_verifiers([NoResult()], Out(x=1), CTX)
    assert result.name == "none" and not result.passed


def test_empty_list_gives_no_results() -> None:
    assert run_verifiers([], Out(x=1), CTX) == []
