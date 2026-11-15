"""The verifier interface. Step 5 adds the framework and the module checks."""

from __future__ import annotations

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
    seen_txn_ids: frozenset[int]  # every transfer id that appeared in a tool result this run
    lookup: Any  # read-only access to stored data, defined by the module


class Verifier(Protocol):
    name: str

    def check(self, output: BaseModel, ctx: VerifyContext) -> VerifierResult: ...
