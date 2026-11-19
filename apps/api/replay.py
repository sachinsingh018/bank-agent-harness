"""Turn a stored run trace into display steps and a money-flow graph, for the replay pages.

Pure code, no database (docs/plans/investigation-replay.md). Every step carries a one sentence
explanation written here by deterministic code, never by a model. The graph holds the accounts
and transfers the investigation saw, each tagged with the step that first showed it, so the
page can grow the evidence board as the replay moves on. Positions come from a deterministic
layout in a square from -100 to 100: the alert's accounts in the middle (shaped by the
detector), accounts found later on an outer ring next to the account that led to them.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from harness.models.types import EMPTY_TEXT_PLACEHOLDER, TextBlock, ToolCallBlock, ToolResultBlock
from harness.run_traces import StoredTrace
from harness.trace import RunTrace

StepKind = Literal[
    "alert", "thinking", "tool_call", "tool_result", "retry", "checks", "review", "outcome"
]
Actor = Literal["rule", "agent", "tools", "harness", "reviewer"]
NodeRole = Literal["anchor", "alert", "found"]

SUBMIT_TOOLS = ("submit_findings", "submit_review")
MAX_NODES = 60
MAX_EDGE_TXNS = 200
MAX_RAW_CHARS = 6_000
MAX_THINKING_CHARS = 1_200
INNER_RADIUS = 56.0
OUTER_RADIUS = 90.0
ALONE_RADIUS = 78.0
MAX_RING_GAP = 0.24  # radians between neighbours on the outer ring, at most

DETECTOR_LABELS: dict[str, str] = {
    "fan-out": "One account paid many different accounts in a short time",
    "fan-in": "Many different accounts paid one account in a short time",
    "cycle": "Money went round in a circle and came back to where it started",
    "scatter-gather": "Money was split across several accounts, then gathered back together",
    "gather-scatter": "Money pooled in one account, then was spread out again",
}

REASON_TEXT: dict[str, str] = {
    "limit_steps": "the agent used up its budget of tool calls",
    "limit_tokens": "the agent used up its token budget",
    "limit_time": "the agent ran out of time",
    "limit_output": "the agent's answer was too long",
    "refusal": "the model refused to answer",
    "model_error": "the model service failed",
    "no_answer": "the agent never handed in an answer",
    "schema_failed": "the answer did not fit the required format, even after one retry",
    "verifier_failed": "the answer failed the deterministic checks twice",
    "verifier_error": "a deterministic check crashed",
    "tool_failure": "a data tool failed",
    "internal_error": "the harness hit an internal error",
    "reviewer_rejected": "the reviewer agent rejected the findings",
    "reviewer_failed": "the reviewer agent did not finish its review",
}

CHECK_TEXT: dict[str, str] = {
    "citations": "every cited transfer exists and was seen by the agent",
    "arithmetic": "every amount and total matches the data",
    "alert_id": "the findings are about the right alert",
    "consistency": "the recommendation and the confidence agree",
}


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckOut(_Out):
    name: str
    passed: bool
    errors: list[str]


class ClaimOut(_Out):
    statement: str
    txn_ids: list[int]


class ReplayStep(_Out):
    index: int
    kind: StepKind
    actor: Actor
    title: str  # a few words for the timeline
    explanation: str  # one plain sentence, written by code
    tool: str | None = None
    arguments: dict[str, Any] | None = None
    ok: bool | None = None
    summary: str | None = None  # what came back, in plain words
    raw: str | None = None  # the raw tool call or result, pretty printed and capped
    text: str | None = None  # the model's visible text, for thinking steps
    checks: list[CheckOut] = []
    errors: list[str] = []
    reasons: list[str] = []
    claims: list[ClaimOut] = []
    verdict: str | None = None
    status: str | None = None
    reason: str | None = None
    recommendation: str | None = None
    confidence: float | None = None
    typology: str | None = None
    narrative: str | None = None
    txn_ids: list[int] = []  # transfers to highlight on the board at this step


class FlowNode(_Out):
    id: str
    bank: int
    acct: str
    role: NodeRole
    x: float
    y: float
    step: int  # the step that first showed this account


class FlowEdge(_Out):
    id: str
    source: str
    target: str
    count: int
    txn_ids: list[int]
    step: int


class FlowGraph(_Out):
    nodes: list[FlowNode]
    edges: list[FlowEdge]
    hidden_accounts: int  # seen but not drawn, over the node cap


class AlertCard(_Out):
    alert_id: str
    detector: str | None
    detector_label: str | None
    summary: str | None
    anchor: str | None
    txn_count: int | None
    account_count: int | None
    window_start: str | None
    window_end: str | None


# --------------------------------------------------------------------------- small helpers


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _parse(content: str) -> Any:
    try:
        return json.loads(content)
    except ValueError:
        return None


def account_id(value: Any) -> str | None:
    """'bank/acct' for an AccountRef-like dict, else None."""
    ref = _dict(value)
    bank, acct = _int(ref.get("bank")), _str(ref.get("acct"))
    return None if bank is None or acct is None else f"{bank}/{acct}"


def _account_words(args: Mapping[str, Any]) -> str:
    bank, acct = _int(args.get("bank")), _str(args.get("acct"))
    return f"account {bank}/{acct}" if bank is not None and acct else "an account"


def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def _money(value: Any) -> str:
    money = _dict(value)
    amount, currency = _str(money.get("value")), _str(money.get("currency"))
    if amount is None:
        return "an amount"
    try:
        shown = f"{float(amount):,.2f}"
    except ValueError:
        shown = amount
    return f"{shown} {currency}" if currency else shown


def _day(value: Any) -> str:
    text = _str(value)
    return text[:10] if text else "an unknown day"


def pretty_raw(content: str) -> str:
    """JSON pretty printed when it parses, capped for display."""
    data = _parse(content)
    text = content if data is None else json.dumps(data, indent=2, ensure_ascii=False)
    if len(text) > MAX_RAW_CHARS:
        return text[:MAX_RAW_CHARS] + "\n... (cut for display)"
    return text


def _first_sentence(text: str) -> str:
    cut = text.find(". ")
    return text if cut < 0 else text[: cut + 1]


# --------------------------------------------------------------------------- the alert


def alert_card(card: Mapping[str, Any] | None, case_id: str) -> AlertCard:
    data = _dict(card)
    detector = _str(data.get("detector"))
    return AlertCard(
        alert_id=_str(data.get("alert_id")) or case_id,
        detector=detector,
        detector_label=DETECTOR_LABELS.get(detector or ""),
        summary=_str(data.get("summary")),
        anchor=account_id(data.get("anchor")),
        txn_count=_int(data.get("txn_count")),
        account_count=_int(data.get("account_count")),
        window_start=_str(data.get("window_start")),
        window_end=_str(data.get("window_end")),
    )


# --------------------------------------------------------------------------- explanations


def call_title(name: str, args: Mapping[str, Any]) -> str:
    titles = {
        "get_alert": "Read the alert",
        "describe_detector": "Looked up the rule",
        "get_account": "Profiled an account",
        "get_account_history": "Listed an account's transfers",
        "get_counterparties": "Asked who an account deals with",
        "get_transaction_graph": "Mapped the money around an account",
        "get_alert_transactions": "Listed the alert's transfers",
        "get_transaction": "Looked up one transfer",
        "sum_transfers": "Added up transfers",
        "run_pattern_check": "Ran a pattern check",
        "submit_findings": "Handed in its findings",
        "submit_review": "Handed in its review",
    }
    return titles.get(name, f"Called {name.replace('_', ' ')}")


def result_title(name: str) -> str:
    titles = {
        "get_alert": "The alert's details",
        "describe_detector": "What the rule means",
        "get_account": "Account profile",
        "get_account_history": "Transfer history",
        "get_counterparties": "Counterparties",
        "get_transaction_graph": "Money map",
        "get_alert_transactions": "The alert's transfers",
        "get_transaction": "Transfer details",
        "sum_transfers": "Exact totals",
        "run_pattern_check": "Pattern check result",
    }
    return titles.get(name, "Tool answer")


def call_explanation(name: str, args: Mapping[str, Any]) -> str:
    who = _account_words(args)
    if name == "get_alert":
        return "Asked for the alert itself: which rule fired, on which account, and the transfers."
    if name == "describe_detector":
        detector = _str(args.get("detector")) or "this"
        return f"Asked what the {detector} rule means and why banks watch for it."
    if name == "get_account":
        return f"Asked for a profile of {who}: when it was active and how busy it was."
    if name == "get_account_history":
        direction = _str(args.get("direction")) or "both"
        what = {"out": "sent", "in": "received"}.get(direction, "sent and received")
        return f"Asked for the transfers {who} {what}."
    if name == "get_counterparties":
        direction = _str(args.get("direction")) or "both"
        if direction == "out":
            return f"Asked who {who} pays."
        if direction == "in":
            return f"Asked who pays {who}."
        return f"Asked who {who} pays and who pays it."
    if name == "get_transaction_graph":
        hops = _int(args.get("hops"))
        reach = f"up to {_plural(hops, 'step')} away" if hops else "nearby"
        return f"Asked to map where money around {who} came from and went, {reach}."
    if name == "get_alert_transactions":
        return "Asked for the full list of transfers in the alert."
    if name == "get_transaction":
        txn = _int(args.get("txn_id"))
        return f"Asked for the details of transfer {txn}." if txn else "Asked for one transfer."
    if name == "sum_transfers":
        n = len(_list(args.get("txn_ids")))
        side = _str(args.get("side")) or "paid"
        return f"Asked the tools to add up {_plural(n, 'transfer')} exactly ({side} side)."
    if name == "run_pattern_check":
        pattern = _str(args.get("pattern")) or "a"
        return f"Asked whether {who} matches the {pattern} pattern."
    if name == "submit_findings":
        rec = _str(args.get("recommendation")) or "no recommendation"
        conf = args.get("confidence")
        sure = f" with confidence {conf:.2f}" if isinstance(conf, int | float) else ""
        return f"Handed in its answer: recommends {rec}{sure}, backed by cited transfers."
    if name == "submit_review":
        verdict = _str(args.get("verdict")) or "a verdict"
        return f"The reviewer handed in its verdict: {verdict}."
    return f"Called the {name.replace('_', ' ')} tool."


def _error_summary(data: Mapping[str, Any]) -> str:
    message = _str(data.get("message"))
    errors = [e for e in _list(data.get("errors")) if isinstance(e, str)]
    if message:
        return f"The tool refused: {message}"
    if errors:
        return f"The tool refused: {errors[0]}"
    return "The tool returned an error."


def result_summary(name: str, content: str, ok: bool) -> str:
    """What came back, in plain words. Never raises."""
    data = _parse(content)
    if not isinstance(data, dict):
        return "The tool answered with text." if ok else "The tool returned an error."
    if not ok or ("code" in data and ("message" in data or "errors" in data)):
        return _error_summary(data)
    if name == "get_alert":
        return _str(data.get("summary")) or "The alert came back."
    if name == "describe_detector":
        meaning = _str(data.get("meaning"))
        return _first_sentence(meaning) if meaning else "The rule's description came back."
    if name == "get_account":
        tin, tout = _int(data.get("transfers_in")) or 0, _int(data.get("transfers_out")) or 0
        cin = _int(data.get("distinct_counterparties_in")) or 0
        cout = _int(data.get("distinct_counterparties_out")) or 0
        span = f" between {_day(data.get('first_seen'))} and {_day(data.get('last_seen'))}"
        if data.get("first_seen") is None:
            span = ""
        return (
            f"Received {_plural(tin, 'transfer')} from {_plural(cin, 'account')} and sent "
            f"{_plural(tout, 'transfer')} to {_plural(cout, 'account')}{span}."
        )
    if name in ("get_account_history", "get_alert_transactions"):
        returned = _int(data.get("returned")) or len(_list(data.get("items")))
        total = _int(data.get("total_count"))
        more = f" of {total}" if total is not None and total != returned else ""
        return f"{returned}{more} transfers came back."
    if name == "get_counterparties":
        items = [_dict(i) for i in _list(data.get("items"))]
        total = _int(data.get("total_count")) or len(items)
        if not items:
            return "No counterparties came back."
        top = items[0]
        who = account_id(top.get("account")) or "one account"
        n = _int(top.get("transfers")) or 0
        return (
            f"{_plural(total, 'counterparty', 'counterparties')}; the busiest is {who} "
            f"with {_plural(n, 'transfer')}."
        )
    if name == "get_transaction_graph":
        nodes = _int(data.get("total_nodes_found")) or len(_list(data.get("nodes")))
        edges = _int(data.get("total_edges_found")) or len(_list(data.get("edges")))
        return f"Found {_plural(nodes, 'account')} linked by {_plural(edges, 'transfer')}."
    if name == "get_transaction":
        src = account_id(data.get("from_account")) or "an account"
        dst = account_id(data.get("to_account")) or "an account"
        return f"{src} paid {_money(data.get('amount_paid'))} to {dst} on {_day(data.get('ts'))}."
    if name == "sum_transfers":
        totals = [_dict(t) for t in _list(data.get("totals"))]
        parts = [
            f"{_money({'value': t.get('total'), 'currency': t.get('currency')})} over "
            f"{_plural(_int(t.get('count')) or 0, 'transfer')}"
            for t in totals[:3]
        ]
        missing = len(_list(data.get("missing_txn_ids")))
        text = "Total: " + "; ".join(parts) + "." if parts else "Nothing to add up."
        return text + (f" {_plural(missing, 'id')} not found." if missing else "")
    if name == "run_pattern_check":
        matches = _int(data.get("total_matches")) or 0
        pattern = _str(data.get("pattern")) or "the"
        if matches:
            return f"The {pattern} pattern matched {_plural(matches, 'time')}."
        return f"The {pattern} pattern did not match."
    return "The tool answered."


# --------------------------------------------------------------------------- transfers in results


def transfers_in(data: Any) -> Iterator[tuple[str, str, int | None]]:
    """(from, to, txn_id) for every transfer-like object anywhere in data."""
    stack: list[Any] = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            src, dst = account_id(item.get("from_account")), account_id(item.get("to_account"))
            if src and dst:
                yield src, dst, _int(item.get("txn_id"))
                continue
            stack.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            stack.extend(reversed(item))


def links_in(name: str, data: Any) -> Iterator[tuple[str, str, int | None, int]]:
    """(from, to, txn_id or None, count) the result shows. Counterparty lists give links
    without transfer ids."""
    for src, dst, txn in transfers_in(data):
        yield src, dst, txn, 1
    if name == "get_counterparties":
        root = account_id(_dict(data).get("account"))
        for item in _list(_dict(data).get("items")):
            other = account_id(_dict(item).get("account"))
            if root is None or other is None:
                continue
            sent, received = _int(item.get("sent_transfers")), _int(item.get("received_transfers"))
            if sent:
                yield root, other, None, sent
            if received:
                yield other, root, None, received
    if name == "get_transaction_graph":
        for node in _list(_dict(data).get("nodes")):
            acct = account_id(_dict(node).get("account"))
            if acct:
                yield acct, acct, None, 0  # a node with no drawn edge still shows


def accounts_in(name: str, args: Mapping[str, Any], data: Any) -> list[str]:
    """The account a call was about, so it shows on the board even with no transfers."""
    if name in ("get_account", "get_counterparties", "get_transaction_graph"):
        acct = account_id({"bank": args.get("bank"), "acct": args.get("acct")})
        return [acct] if acct else []
    return []


class GraphBuilder:
    """Collects accounts and transfers in the order the replay shows them."""

    def __init__(self) -> None:
        self.order: list[str] = []
        self.first_step: dict[str, int] = {}
        self.hidden: set[str] = set()
        self.edges: dict[tuple[str, str], dict[str, Any]] = {}
        self.parent: dict[str, str] = {}

    def add_node(self, node: str, step: int, parent: str | None = None) -> bool:
        if node in self.first_step:
            return True
        if len(self.order) >= MAX_NODES:
            self.hidden.add(node)
            return False
        self.order.append(node)
        self.first_step[node] = step
        if parent is not None and parent in self.first_step:
            self.parent[node] = parent
        return True

    def add_link(self, src: str, dst: str, txn: int | None, count: int, step: int) -> None:
        if src == dst:
            self.add_node(src, step)
            return
        # A new account hangs off the known account that led to it (for the layout).
        if not self.add_node(src, step, dst):
            return
        if not self.add_node(dst, step, src):
            return
        edge = self.edges.setdefault((src, dst), {"txn_ids": set(), "links": 0, "step": step})
        if txn is not None:
            edge["txn_ids"].add(txn)
        edge["links"] = max(edge["links"], count)

    def graph(self, anchor: str | None, detector: str | None, core_step: int = 0) -> FlowGraph:
        core = [n for n in self.order if self.first_step[n] <= core_step]
        if not core:
            core = self.order[:1]
        core_set = set(core)
        extras = [n for n in self.order if n not in core_set]
        edges = [(s, d) for (s, d) in self.edges]
        positions = layout(core, extras, edges, anchor, detector, self.parent)
        nodes = [
            FlowNode(
                id=n,
                bank=int(n.split("/", 1)[0]),
                acct=n.split("/", 1)[1],
                role="anchor" if n == anchor else ("alert" if n in core_set else "found"),
                x=round(positions[n][0], 2),
                y=round(positions[n][1], 2),
                step=self.first_step[n],
            )
            for n in self.order
        ]
        flow_edges = [
            FlowEdge(
                id=f"{s}>{d}",
                source=s,
                target=d,
                count=max(len(e["txn_ids"]), e["links"], 1),
                txn_ids=sorted(e["txn_ids"])[:MAX_EDGE_TXNS],
                step=e["step"],
            )
            for (s, d), e in self.edges.items()
        ]
        return FlowGraph(nodes=nodes, edges=flow_edges, hidden_accounts=len(self.hidden))


# --------------------------------------------------------------------------- layout


def _ring(
    nodes: list[str], radius: float, start: float = -math.pi / 2
) -> dict[str, tuple[float, float]]:
    n = len(nodes)
    return {
        node: (
            radius * math.cos(start + 2 * math.pi * i / n),
            radius * math.sin(start + 2 * math.pi * i / n),
        )
        for i, node in enumerate(nodes)
    }


def _cycle_order(nodes: list[str], edges: list[tuple[str, str]], start: str | None) -> list[str]:
    succ: dict[str, list[str]] = {}
    for s, d in edges:
        succ.setdefault(s, []).append(d)
    members = set(nodes)
    current = start if start in members else nodes[0]
    order = [current]
    while True:
        nxt = next((d for d in succ.get(current, []) if d in members and d not in order), None)
        if nxt is None:
            break
        order.append(nxt)
        current = nxt
    return order + [n for n in nodes if n not in order]


def _layers(nodes: list[str], edges: list[tuple[str, str]]) -> list[list[str]] | None:
    """Longest-path layers from the sources, or None when the core has a cycle."""
    members = set(nodes)
    inner = [(s, d) for s, d in edges if s in members and d in members and s != d]
    indeg = {n: 0 for n in nodes}
    for _, d in inner:
        indeg[d] += 1
    layer = {n: 0 for n in nodes}
    ready = [n for n in nodes if indeg[n] == 0]
    done = 0
    while ready:
        node = ready.pop(0)
        done += 1
        for s, d in inner:
            if s == node:
                layer[d] = max(layer[d], layer[s] + 1)
                indeg[d] -= 1
                if indeg[d] == 0:
                    ready.append(d)
    if done < len(nodes):
        return None
    out: list[list[str]] = [[] for _ in range(max(layer.values()) + 1)]
    for n in nodes:
        out[layer[n]].append(n)
    return out


def _layered(layers: list[list[str]], radius: float) -> dict[str, tuple[float, float]]:
    pos: dict[str, tuple[float, float]] = {}
    width = len(layers)
    for i, members in enumerate(layers):
        x = -radius + 2 * radius * i / (width - 1)
        step = min(2 * radius / max(1, len(members) - 1), 34.0)
        top = -step * (len(members) - 1) / 2
        for j, node in enumerate(members):
            pos[node] = (x, top + j * step)
    return pos


def _core_layout(
    core: list[str],
    edges: list[tuple[str, str]],
    anchor: str | None,
    detector: str | None,
    radius: float,
) -> dict[str, tuple[float, float]]:
    if len(core) == 1:
        return {core[0]: (0.0, 0.0)}
    if detector in ("fan-out", "fan-in") and anchor in core:
        others = [n for n in core if n != anchor]
        return {anchor: (0.0, 0.0), **_ring(others, radius)}
    if detector == "cycle":
        return _ring(_cycle_order(core, edges, anchor), radius)
    layers = _layers(core, edges)
    if layers is None or len(layers) < 2:
        return _ring(_cycle_order(core, edges, anchor), radius)
    return _layered(layers, radius)


def _spread(desired: list[float]) -> list[float]:
    """Angles in the same order, at least a gap apart, as close to the wishes as possible."""
    n = len(desired)
    gap = min(2 * math.pi / n, MAX_RING_GAP)
    order = sorted(range(n), key=lambda i: desired[i])
    out = [0.0] * n
    prev: float | None = None
    for i in order:
        angle = desired[i] if prev is None else max(desired[i], prev + gap)
        out[i] = angle
        prev = angle
    span = out[order[-1]] - out[order[0]]
    if span > 2 * math.pi - gap:
        first = out[order[0]]
        for k, i in enumerate(order):
            out[i] = first + 2 * math.pi * k / n
    else:
        # Centre each run on its wishes: shift the whole sweep back by half its drift.
        drift = sum(out[i] - desired[i] for i in order) / n
        out = [a - drift / 2 for a in out]
    return out


def layout(
    core: list[str],
    extras: list[str],
    edges: list[tuple[str, str]],
    anchor: str | None,
    detector: str | None,
    parent: Mapping[str, str],
) -> dict[str, tuple[float, float]]:
    """Positions in a square from -100 to 100. Same input, same output."""
    if not core:
        return {}
    pos = _core_layout(core, edges, anchor, detector, INNER_RADIUS if extras else ALONE_RADIUS)
    if not extras:
        return pos
    desired: list[float] = []
    fallback = 0
    for i, node in enumerate(extras):
        par = parent.get(node)
        if par is not None and par in pos and math.hypot(*pos[par]) > 1:
            desired.append(math.atan2(pos[par][1], pos[par][0]))
        elif par is not None and par in extras[:i]:
            desired.append(desired[extras.index(par)])
        else:
            desired.append(-math.pi / 2 + fallback * 2 * math.pi / max(1, len(extras)))
            fallback += 1
    for node, angle in zip(extras, _spread(desired), strict=True):
        pos[node] = (OUTER_RADIUS * math.cos(angle), OUTER_RADIUS * math.sin(angle))
    return pos


def card_graph(card: Mapping[str, Any] | None) -> FlowGraph:
    """The alert's own money flow, for a gallery card."""
    data = _dict(card)
    builder = GraphBuilder()
    anchor = account_id(data.get("anchor"))
    if anchor:
        builder.add_node(anchor, 0)
    for src, dst, txn in transfers_in(data.get("transfers")):
        builder.add_link(src, dst, txn, 1, 0)
    graph = builder.graph(anchor, _str(data.get("detector")))
    # Cards only draw the flow, so the transfer ids are left out to keep the list small.
    edges = [e.model_copy(update={"txn_ids": []}) for e in graph.edges]
    return graph.model_copy(update={"edges": edges})


# --------------------------------------------------------------------------- steps


def _checks(results: list[Any]) -> list[CheckOut]:
    out: list[CheckOut] = []
    for r in results:
        d = _dict(r)
        name = _str(d.get("name"))
        if name is None:
            continue
        errors = [e for e in _list(d.get("errors")) if isinstance(e, str)]
        out.append(CheckOut(name=name, passed=bool(d.get("passed")), errors=errors))
    return out


def _checks_explanation(checks: list[CheckOut]) -> str:
    failed = [c for c in checks if not c.passed]
    if not failed:
        said = [CHECK_TEXT[c.name] for c in checks if c.name in CHECK_TEXT][:2]
        detail = f": {' and '.join(said)}" if said else ""
        return f"All {_plural(len(checks), 'check')} passed{detail}."
    return (
        f"{len(failed)} of {_plural(len(checks), 'check')} failed, so the case is routed to "
        "analyst review."
    )


def claims_of(findings: Mapping[str, Any] | None) -> list[ClaimOut]:
    out: list[ClaimOut] = []
    for claim in _list(_dict(findings).get("claims")):
        c = _dict(claim)
        statement = _str(c.get("statement"))
        if statement:
            ids = [i for i in (_int(t) for t in _list(c.get("txn_ids"))) if i is not None]
            out.append(ClaimOut(statement=statement, txn_ids=ids))
    return out


def _cited(claims: list[ClaimOut]) -> list[int]:
    return sorted({t for c in claims for t in c.txn_ids})


def _float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, int | float) else None


def _submit_errors(content: str) -> list[str]:
    data = _dict(_parse(content))
    errors = [e for e in _list(data.get("errors")) if isinstance(e, str)]
    return errors or ["The answer was refused."]


class _Steps:
    def __init__(self) -> None:
        self.items: list[ReplayStep] = []

    def add(self, **fields: Any) -> ReplayStep:
        step = ReplayStep(index=len(self.items), **fields)
        self.items.append(step)
        return step


def _alert_step(steps: _Steps, alert: AlertCard, lesson_count: int) -> None:
    rule = alert.detector_label or "A transaction monitoring rule raised this alert"
    summary = alert.summary or f"Alert {alert.alert_id} was raised for review."
    lessons = (
        f" The agent was also given {_plural(lesson_count, 'saved lesson')} from past reviews."
        if lesson_count
        else ""
    )
    steps.add(
        kind="alert",
        actor="rule",
        title="The alert",
        explanation=f"{rule}. {summary}{lessons}",
        summary=summary,
    )


def _walk_messages(steps: _Steps, trace: RunTrace, graph: GraphBuilder) -> None:
    """Thinking, tool call, tool result and retry steps, in the order they happened."""
    calls: dict[str, ToolCallBlock] = {}
    for m_index, message in enumerate(trace.messages):
        if message.role == "assistant":
            text = message.text().strip()
            if text and text != EMPTY_TEXT_PLACEHOLDER:
                cut = text[:MAX_THINKING_CHARS] + ("..." if len(text) > MAX_THINKING_CHARS else "")
                steps.add(
                    kind="thinking",
                    actor="agent",
                    title="Thought out loud",
                    explanation="The model wrote down its reasoning before its next move.",
                    text=cut,
                )
            for call in message.tool_calls():
                calls[call.id] = call
                args = call.arguments
                txns = [i for i in (_int(t) for t in _list(args.get("txn_ids"))) if i is not None]
                if _int(args.get("txn_id")) is not None:
                    txns.append(int(args["txn_id"]))
                submit = call.name in SUBMIT_TOOLS
                claims = claims_of(args) if submit else []
                steps.add(
                    kind="tool_call",
                    actor="agent",
                    title=call_title(call.name, args),
                    explanation=call_explanation(call.name, args),
                    tool=call.name,
                    arguments=args,
                    raw=pretty_raw(json.dumps(args, ensure_ascii=False)),
                    claims=claims,
                    recommendation=_str(args.get("recommendation")) if submit else None,
                    confidence=_float(args.get("confidence")) if submit else None,
                    typology=_str(args.get("typology")) if submit else None,
                    txn_ids=sorted(set(txns)) if not submit else _cited(claims),
                )
            continue
        if m_index == 0:
            continue  # the task message
        for block in message.blocks:
            if isinstance(block, TextBlock) and block.text.strip():
                steps.add(
                    kind="retry",
                    actor="harness",
                    title="Reminded to answer",
                    explanation=(
                        "The agent stopped without an answer, so the harness reminded it once "
                        "to hand in its findings."
                    ),
                )
            elif isinstance(block, ToolResultBlock):
                asked = calls.get(block.tool_call_id)
                name = asked.name if asked else "tool"
                asked_args = asked.arguments if asked else {}
                if name in SUBMIT_TOOLS:
                    steps.add(
                        kind="retry",
                        actor="harness",
                        title="Sent back once",
                        explanation=(
                            "The answer did not pass, so the harness sent the problems back "
                            "and allowed one retry."
                        ),
                        errors=_submit_errors(block.content),
                        raw=pretty_raw(block.content),
                        ok=False,
                    )
                    continue
                ok = not block.is_error
                step = steps.add(
                    kind="tool_result",
                    actor="tools",
                    title=result_title(name) if not block.is_error else "The tool refused",
                    explanation="",
                    tool=name,
                    ok=ok,
                    summary=result_summary(name, block.content, ok),
                    raw=pretty_raw(block.content),
                )
                step.explanation = step.summary or ""
                data = _parse(block.content) if ok else None
                new_txns: set[int] = set()
                for acct in accounts_in(name, asked_args, data):
                    graph.add_node(acct, step.index)
                if data is not None:
                    for src, dst, txn, count in links_in(name, data):
                        graph.add_link(src, dst, txn, count, step.index)
                        if txn is not None:
                            new_txns.add(txn)
                step.txn_ids = sorted(new_txns)[:MAX_EDGE_TXNS]


def _review_step(steps: _Steps, review: Mapping[str, Any] | None, checker: RunTrace | None) -> None:
    if review is None:
        return
    verdict = _str(review.get("verdict"))
    body = _dict(review.get("review"))
    reasons = [r for r in _list(body.get("reasons")) if isinstance(r, str)]
    concerns = [_str(_dict(c).get("issue")) or "" for c in _list(body.get("concerns"))]
    used = [c.name for c in (checker.tool_calls if checker else []) if c.name not in SUBMIT_TOOLS]
    looked = (
        f" It made {_plural(len(used), 'tool call')} of its own to double check."
        if used
        else " It judged from the findings and the evidence they cite."
    )
    if verdict == "approve":
        title, said = "Reviewer approved", "A second agent, the reviewer, approved the findings."
    elif verdict == "reject":
        title, said = "Reviewer rejected", "A second agent, the reviewer, rejected the findings."
    else:
        title = "Reviewer did not finish"
        failure = _str(review.get("failure_reason"))
        why = REASON_TEXT.get(failure or "", "it stopped early").replace("the agent", "it")
        said = f"A second agent, the reviewer, did not finish because {why}."
    steps.add(
        kind="review",
        actor="reviewer",
        title=title,
        explanation=said + looked,
        verdict=verdict or "failed",
        reasons=reasons,
        errors=[c for c in concerns if c],
        confidence=_float(body.get("confidence")),
        checks=_checks(_list(review.get("verifier_results"))),
    )


def _outcome_step(steps: _Steps, run: Mapping[str, Any]) -> None:
    status = _str(run.get("status")) or "human_review"
    reason = _str(run.get("reason"))
    findings = _dict(run.get("findings")) or None
    claims = claims_of(findings)
    rec = _str(_dict(findings).get("recommendation"))
    if status == "proposed":
        verb = {"escalate": "escalating this alert", "close": "closing this alert"}.get(
            rec or "", "an action on this alert"
        )
        explanation = (
            f"The harness recommends {verb}. An analyst still has to approve it before "
            "anything happens (four-eyes)."
        )
        title = "Recommendation sent to an analyst"
    else:
        why = REASON_TEXT.get(reason or "", "it needs an analyst's judgement")
        explanation = (
            f"The case is routed to analyst review because {why}. Nothing is closed automatically."
        )
        title = "Routed to analyst review"
    steps.add(
        kind="outcome",
        actor="harness",
        title=title,
        explanation=explanation,
        status=status,
        reason=reason,
        recommendation=rec,
        confidence=_float(_dict(findings).get("confidence")),
        typology=_str(_dict(findings).get("typology")),
        narrative=_str(_dict(findings).get("narrative")),
        claims=claims,
        txn_ids=_cited(claims),
    )


def build_replay(
    run: Mapping[str, Any],
    stored: StoredTrace,
    card: Mapping[str, Any] | None,
    review: Mapping[str, Any] | None,
) -> tuple[AlertCard, list[ReplayStep], FlowGraph]:
    """The alert, the ordered display steps and the money-flow graph of one run.

    run holds the agent_runs fields status, reason, findings and verifier_results; review the
    agent_reviews fields verdict, failure_reason, review and verifier_results, or None."""
    case_id = _str(run.get("case_id")) or stored.maker.case_id
    alert = alert_card(card, case_id)
    steps = _Steps()
    graph = GraphBuilder()
    _alert_step(steps, alert, len(stored.maker.lesson_ids))
    if alert.anchor:
        graph.add_node(alert.anchor, 0)
    for src, dst, txn in transfers_in(_dict(card).get("transfers")):
        graph.add_link(src, dst, txn, 1, 0)
    steps.items[0].txn_ids = sorted({t for e in graph.edges.values() for t in e["txn_ids"]})[
        :MAX_EDGE_TXNS
    ]
    _walk_messages(steps, stored.maker, graph)
    checks = _checks(_list(run.get("verifier_results")))
    if checks:
        steps.add(
            kind="checks",
            actor="harness",
            title="Deterministic checks",
            explanation=_checks_explanation(checks),
            checks=checks,
            ok=all(c.passed for c in checks),
            txn_ids=_cited(claims_of(_dict(run.get("findings")) or None)),
        )
    _review_step(steps, review, stored.checker)
    _outcome_step(steps, run)
    return alert, steps.items, graph.graph(alert.anchor, alert.detector)
