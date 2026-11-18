"""A deterministic AML policy used only to test the eval plumbing (CI fast eval, no API key).

It is a ModelAdapter that reads only the request messages. Turn 1 asks for get_alert with the
alert id taken from the case message. Turn 2 submits a valid close finding with confidence 0.1
and typology NONE, citing the first transfer of the get_alert result with its exact paid
amount and currency. It never reads the database or the answer key, and it is not a baseline:
its scores mean nothing.

As the checker (the request offers the submit_review tool) it approves at once with a fixed
review, so the fast eval runs the whole maker-checker path with no API key.
"""

from __future__ import annotations

import json
import re
from typing import Any

from harness.models.config import ModelProfile
from harness.models.scripted_adapter import (
    default_scripted_profile,
    text_response,
    tool_call_response,
)
from harness.models.types import ModelRequest, ModelResponse, TextBlock, ToolResultBlock
from modules.aml.agent.models import (
    REVIEW_TOOL_NAME,
    SUBMIT_TOOL_NAME,
    AmlFindings,
    AmlReview,
    CitedAmount,
    Claim,
    Typology,
)

ALERT_ID_PATTERN = re.compile(r"AL-[0-9a-f]{12}")
NO_ALERT_TEXT = "Scripted policy: no alert result to work from."


def _case_alert_id(req: ModelRequest) -> str | None:
    for message in req.messages:
        if message.role != "user":
            continue
        for block in message.blocks:
            if isinstance(block, TextBlock):
                match = ALERT_ID_PATTERN.search(block.text)
                if match:
                    return match.group(0)
    return None


def _alert_result(req: ModelRequest) -> dict[str, Any] | None:
    """The first successful get_alert result in the conversation, if any."""
    for message in req.messages:
        for block in message.blocks:
            if not isinstance(block, ToolResultBlock) or block.is_error:
                continue
            try:
                body = json.loads(block.content)
            except ValueError:
                continue
            if isinstance(body, dict) and "transfers" in body and "alert_id" in body:
                return body
    return None


def _findings(alert: dict[str, Any]) -> dict[str, Any]:
    first = alert["transfers"]["items"][0]
    paid = first["amount_paid"]
    findings = AmlFindings(
        alert_id=alert["alert_id"],
        recommendation="close",
        confidence=0.1,
        typology=Typology.NONE,
        narrative="Scripted plumbing finding. Not an investigation.",
        claims=[
            Claim(
                statement="The first transfer of the alert.",
                txn_ids=[first["txn_id"]],
                amounts=[
                    CitedAmount(
                        txn_id=first["txn_id"],
                        side="paid",
                        value=paid["value"],
                        currency=paid["currency"],
                    )
                ],
            )
        ],
    )
    return findings.model_dump(mode="json")


def scripted_review() -> dict[str, Any]:
    review = AmlReview(
        verdict="approve",
        reasons=["Scripted plumbing review. Not a review."],
        confidence=0.5,
    )
    return review.model_dump(mode="json")


class ScriptedAmlPolicy:
    """ModelAdapter for the scripted profile. Stateless: each call looks at the messages."""

    def __init__(self, profile: ModelProfile | None = None) -> None:
        self.profile: ModelProfile = profile or default_scripted_profile()

    async def complete(self, req: ModelRequest) -> ModelResponse:
        if any(t.name == REVIEW_TOOL_NAME for t in req.tools):
            return tool_call_response([(REVIEW_TOOL_NAME, scripted_review())], id_prefix="r")
        alert = _alert_result(req)
        if alert is not None and alert["transfers"]["items"]:
            return tool_call_response([(SUBMIT_TOOL_NAME, _findings(alert))], id_prefix="s")
        if alert is None and not any(
            isinstance(b, ToolResultBlock) for m in req.messages for b in m.blocks
        ):
            alert_id = _case_alert_id(req)
            if alert_id is not None:
                return tool_call_response([("get_alert", {"alert_id": alert_id})], id_prefix="a")
        return text_response(NO_ALERT_TEXT)
