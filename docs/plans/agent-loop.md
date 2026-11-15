# Plan: agent loop, model adapter, MCP client, verifiers and eval runner (M2 core)

Status: drafted 2026-11-15, verified by an independent reviewer pass (10 issues found, all addressed below, see section 13), plus one data leak found during verification (section 12). The owner asked to proceed to building after verification.

## Goal

Run one AML alert end to end: the harness gives the alert to a model, the model investigates with the nine read-only tools, returns a typed finding, deterministic verifiers check it, and the result is either a proposal (waiting for human approval) or a case for human review. Then score many alerts against the answer key. That is the M2 milestone "first scored AML run".

## Plain-English background

- **Agent loop.** A `while` loop: send the conversation to the model, run any tools it asks for, add the results, repeat. It stops when the model hands in its answer or a limit is hit.
- **Model adapter.** One small interface in front of different model providers, so the loop does not care whether it talks to Claude or to an open-weight model through OpenRouter.
- **MCP client.** The harness side of the tools server we already built. It starts the server as a child process and calls tools over stdio.
- **Verifiers.** Plain code that checks the answer: does every cited transfer exist, does every quoted amount match the database, does the arithmetic add up. Cheap, exact, no LLM.
- **SAR.** A Suspicious Activity Report: what a bank files with the regulator when an alert looks like real laundering. "Escalate" in this project means "recommend a SAR review"; a human makes the final call.
- **Typology.** The pattern a laundering scheme follows (fan-out, cycle and so on).
- **Calibration.** Whether a stated confidence of 0.8 is right about 80% of the time.

## 1. Shape of one case run

```
alert_id
  -> CaseRunner (harness; the only part that may write run records, never the agent)
       -> start tools server (MCP client, stdio)
       -> AgentLoop(model adapter, tool session, ModuleSpec, limits)
            model <-> tools, up to the limits
            model calls submit_findings(...)  -> Pydantic validation
       -> verifiers (citations, arithmetic, module checks)
            any failure (schema or verifier) -> errors go back once -> check again
            fail again -> human_review
       -> CaseResult: proposed | human_review, with the full trace
```

A hit limit, a refusal, a dead tools server, a crash or a second failure always ends as `human_review` with the partial trace. Nothing is ever auto-closed.

## 2. Model adapter (`harness/models/`)

One async interface:

```python
class ModelAdapter(Protocol):
    async def complete(self, req: ModelRequest) -> ModelResponse: ...
```

Neutral types (Pydantic): `ModelRequest` (system text, messages, tool specs, max output tokens), `ModelResponse` (blocks, stop reason `end_turn | tool_use | max_tokens | refusal | other`, usage, latency, model, provider, request id). Blocks are `text`, `tool_call` (id, name, arguments as a dict) and `tool_result` (id, JSON text, is_error).

Assistant turns also keep the provider's raw content, so the same provider gets them back unchanged (needed for Claude thinking blocks).

Backends:
- `anthropic_adapter.py`: Anthropic SDK (`anthropic==2.3.0`), async client, our own manual loop (no SDK tool runner: hard rule, own loop). Prompt caching on the system prompt and tool list.
- `openrouter_adapter.py`: OpenAI SDK (`openai==3.42.0`) pointed at OpenRouter's OpenAI compatible endpoint, `chat.completions` with tools.
- `scripted_adapter.py`: no network. Replays a scripted list of responses. Used by tests and by the CI fast eval, so CI needs no API key.

**To confirm against the installed SDK in step 2, not assumed:** that current Opus runs adaptive thinking by default and rejects disabling it; the shape of `output_config.effort`; that forced `tool_choice` (`any` or a named tool) is rejected on current models, so we use `auto` and the prompt tells the model to finish with `submit_findings`; that thinking blocks must be passed back unchanged; how a refusal stop reason arrives; OpenRouter's tool call and provider routing fields.

Config (`config/models.toml`, no secrets): named profiles, each with provider, model id, max output tokens, effort (Anthropic only), `provider_routing` (OpenRouter only: preferred or allowed hosting providers, so a benchmark can pin a US or EU host), and price per million input, output, cache read and cache write tokens. Profiles at the start: one Claude profile, one free OpenRouter model for development (never depend on one specific free model, so the free profile is easy to swap), and one US or EU hosted open-weight profile for later benchmarks. Model ids live only here. API keys come only from env vars (`ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`) set as Codespaces secrets.

Every call is recorded in the trace: provider, model, prompt version, input, output and cache tokens, cost (from config prices), latency and request id.

## 3. MCP client (`harness/mcp_client.py`)

- `ToolSession` is an async context manager. It starts the module's server with the ModuleSpec's launch parameters (for AML, `tools.aml.launch.server_params`, which passes only `DATABASE_URL` and `PYTHONPATH`), lists the tools and turns each into a neutral `ToolSpec` (name, description, JSON schema).
- `call(name, arguments)` uses the 10 s read timeout from `tools/aml/caps.py`. It returns a `ToolOutcome`: ok or error code, the JSON text the model sees, duration, size and the truncated flag. A timeout becomes a typed `internal` error.
- No client-side schema check: the server already validates arguments with Pydantic and returns `invalid_argument`. That avoids a second validator and a new dependency.
- If the server process has died (the session cannot be used), the case ends as human_review `tool_failure`.
- The exact `mcp` 2.3.0 client calls are copied from `tools/aml/test_e2e_stdio.py`, which already works.

## 4. ModuleSpec (`harness/module_spec.py`)

Matches docs/architecture.md (tools, prompt version, output model, verifiers, eval dataset) plus one helper:

```python
@dataclass(frozen=True)
class ModuleSpec:
    name: str  # "aml"
    server_params: Callable[[str], StdioServerParameters]  # the tools
    prompt: PromptRef  # versioned file, loaded and hashed at start
    output_model: type[BaseModel]  # AmlFindings
    verifiers: Sequence[Verifier]
    eval_dataset: Path  # dataset manifest
    build_case_message: Callable[[str], str]  # alert id -> first user message
```

The loop, adapter, MCP client and runner take a ModuleSpec and hold no AML logic. The AML spec lives in `modules/aml/agent/spec.py`. Prompts are versioned files (`modules/aml/prompts/investigator_v1.md`), never inline strings; the trace records file name and sha256.

## 5. The agent loop (`harness/loop.py`)

The final answer is a tool, `submit_findings`, whose input schema is the module's output model. This works the same on every provider. The harness validates the arguments with Pydantic.

Limits (`config/harness.toml`): max 12 tool steps, a token budget per case counting every processed token, cached ones included (start 600,000), a wall clock limit (start 300 s), max output tokens per call.

Rules:
- A step is one call to an investigation tool. `submit_findings` is not a step.
- Parallel calls in one turn each count. If a turn asks for more calls than steps remain, the calls past the limit are not run and the case ends as human_review `limit_steps`.
- Repeated identical calls (same tool, same arguments) are not run again: the cached result is returned with a note that it is a repeat. They still count as a step.
- A tool error (`invalid_argument`, `not_found`, `too_large`, `internal`, timeout) goes back to the model as an error result and counts as a step.
- **Retry budget is one, in total.** The single correction turn is used by whichever comes first: the model ended its turn without `submit_findings`, the submission failed Pydantic validation, or the verifiers failed. The second failure of any kind ends in human_review.

Stop reasons:

| Model stop | What the loop does |
|---|---|
| `tool_use` | run the calls (investigation tools and or `submit_findings`) |
| `end_turn` without `submit_findings` | use the one retry with a reminder, else human_review `no_answer` |
| `max_tokens` | human_review `limit_output` |
| `refusal` | human_review `refusal` |
| `other` | human_review `model_error` |
| API error after SDK retries | human_review `model_error` |

Every model call and tool call goes into a `RunTrace` (Pydantic), written by the runner.

## 6. AML output model (`modules/aml/agent/models.py`)

```python
class Typology(StrEnum):  # values match ATTEMPT_TYPES in harness/db/models.py
    FAN_OUT = "FAN-OUT"
    FAN_IN = "FAN-IN"
    CYCLE = "CYCLE"
    SCATTER_GATHER = "SCATTER-GATHER"
    GATHER_SCATTER = "GATHER-SCATTER"
    STACK = "STACK"
    BIPARTITE = "BIPARTITE"
    RANDOM = "RANDOM"
    NONE = "NONE"  # no laundering pattern; only with close


class AmlFindings(BaseModel):
    alert_id: str
    recommendation: Literal["escalate", "close"]
    confidence: float  # 0 to 1: probability the alert is real laundering
    typology: Typology
    narrative: str  # plain words, max 1,200 characters
    claims: list[Claim]  # 1 to 10
    figures: list[Figure]  # numbers the narrative states, 0 to 10


class Claim(BaseModel):
    statement: str
    txn_ids: list[int]  # at least one
    amounts: list[CitedAmount]  # txn_id, side ("paid" | "received"), value, currency


class Figure(BaseModel):
    kind: Literal["sum", "count"]
    value: str  # exact decimal string, or an integer for counts
    currency: str | None  # required for sum, never mixed
    side: Literal["paid", "received"] | None
    txn_ids: list[int]
```

Detector names (structuring, volume spike) are not typologies and are not in the enum. The prompt explains each typology in plain words (from `describe_detector` where they overlap).

## 7. Verifiers (`harness/verifiers/` framework, `modules/aml/agent/verifiers.py` checks)

A `Verifier` takes the output, the trace and a read-only lookup, and returns `VerifierResult(name, passed, errors)`. All run on every output. No LLM judge in M2.

1. **Schema.** Pydantic validation of the submission (in the loop; shares the one retry).
2. **Citations.** Every cited txn_id exists. Every cited amount equals the stored amount and currency on that side, compared as `Decimal` (so "100.0" equals "100.00"). Every cited txn_id must have appeared in a tool result during this run.
3. **Arithmetic.** Every `sum` figure equals the exact `Decimal` sum of the cited transfers on that side and currency; every `count` equals the number of distinct cited transfers.
4. **Module checks.** `escalate` needs at least one claim citing a transfer that belongs to the alert (needs the database, so it is a verifier). The two rules that need no data, `NONE` only with `close`, and `escalate` needs confidence of at least 0.5 while `close` needs below 0.5, live in the `AmlFindings` model validator, so they fail as schema errors and share the one retry.

**Lookup path.** In process, read-only, through the existing guarded door: `build_tool_engine(url)` and `open_readonly` from `tools/aml/db.py`, then the existing query functions (`get_transaction`, and `get_alert` plus `get_alert_transactions` paged to collect the alert's full transfer set). The URL is passed in explicitly by the runner (`DATABASE_URL` in normal runs, `TEST_DATABASE_URL` in tests), so tests never read `DATABASE_URL`. The verifiers never see labels.

## 8. Eval datasets, runner and scorer (`evals/`)

**Datasets** come from the existing scheme split (`SPLIT_SEED = 42`, 4 development schemes per family) so prompts are tuned on development alerts only and the headline comes from held-out alerts:
- Every v1 alert is assigned once by `evals/make_aml_datasets.py`: an alert whose laundering transfers mostly belong to development schemes goes to `dev`, mostly held-out goes to `heldout`; alerts with no laundering are split half and half by sha256 of the alert id.
- `evals/datasets/aml_alerts_dev_v1.json`, `aml_alerts_heldout_v1.json` and `aml_alerts_smoke_v1.json` (10 dev alerts for quick checks). Ids only, no labels in the files. The script is the only dataset code that reads labels and it joins the reader allowlist.
- The script prints the number of truly suspicious (strict) alerts in each set. With 39 strict positives among 171 alerts, each full set has about 20; that is the minimum we accept, and anything less is reported.

**Runner** `evals/run_aml_agent.py`: runs a dataset with a named model profile, writes one JSONL record per alert under `evals/runs/<run_id>/` (added to `.gitignore`) plus a manifest: git commit, prompt version and hash, model profile and ids, harness config, dataset version. The database tables come in step 8.

**Scorer** `evals/aml_agent_score.py`, on exactly the alerts in the run:
- Ground truth per alert: the strict rule from `classify_alert` (at least half of its transfers are laundering). Loose numbers are reported next to it.
- **Baseline on the same alerts:** rules only means every alert is escalated, so baseline recall is 1.0 and baseline precision is the share of strict positives in the set.
- **Escalation precision and recall** of the agent (`escalate` versus strict truth). Human review cases are counted as escalated (a human looks at them), and also reported separately.
- **False positive reduction at a fixed recall** (brief section 5): rank alerts by agent confidence (human review cases at the top), take the lowest threshold that keeps recall at or above the target (default 0.9, configurable), report `1 - agent false positives / baseline false positives` at that threshold, plus the same at the agent's own escalate decisions.
- **Scheme recall** like the rules baseline: feed the agent's escalated alerts into the existing `score()` from `evals/aml_rules_score.py` to get schemes caught and touched, so the numbers sit side by side with the v1 baseline.
- **Typology accuracy** only on strict positive alerts, against `_alert_family` (the main family of the alert's laundering transfers). On negatives, the rate of `NONE` is reported separately.
- **Grounding rate** (claims passing the citation check), **calibration** (expected calibration error and 5 reliability bins, marked indicative because the sets are small), **human review rate** with reasons, **cost, latency and tool steps per alert**.
- Every metric is also split by the late window flag (section 12).

**CI fast eval:** CI already loads the slice and labels. A fixture runs the rules into a test ruleset version (as the existing tool tests do), the runner runs 3 of those alerts with the scripted adapter, and the scorer runs on the result. Plumbing only, no API key, no cost. Real model runs are on demand.

## 9. Label guard changes

- `harness/` is scanned for label words except `harness/db/` (the models file defines the table and a test there names it).
- `modules/aml/agent/` and the new AML prompt files are scanned.
- New `evals/test_label_readers.py` with an explicit reader list: `evals/aml_rules_score.py`, `evals/aml_agent_score.py`, `evals/make_aml_datasets.py` and the loader `data/load_aml_slice.py` (the only writer). Any other non-test file in `evals/` or `data/` that names the labels fails.
- `harness/` and `modules/aml/agent/` must not import anything from `evals/`.
- Decision note 0005 records this.

## 10. Database tables and the write path (last step of M2)

Names follow brief section 9:
- `case_events`: the append-only audit log. A trigger refuses UPDATE and DELETE. Columns: id, at, actor (`agent-runner`, later `human:<name>`), event (`proposal_created`, `human_review_created`, later `approved`, `rejected`, `edited`), case id, payload JSONB, prev hash and hash (a hash chain, so tampering shows).
- `agent_runs` (one row per case run) and `model_calls` (tokens, cost, latency per call).
- `eval_runs` and `eval_results`, so any two eval runs can be compared in Postgres (brief section 5).

Write path:
- Only the CaseRunner and the eval runner write, through their own connection from a separate env var, `HARNESS_WRITE_DATABASE_URL`. It is never passed to `server_params` (which passes only `DATABASE_URL`). Locally both URLs may point at the same role for now; a separate read-only role for the tools stays future hardening (docs/plans/mcp-tools.md).
- Static test: no file under `harness/` or `modules/aml/agent/` imports `harness.db.session` or opens an engine, except `harness/runner.py` and the write module `harness/records.py`. The verifiers read only through `tools.aml.db`.

## 11. Where things live

```
config/models.toml            model profiles and prices (no secrets)
config/harness.toml           limits
harness/models/types.py       neutral request, response, block and usage types
harness/models/config.py      loads profiles, computes cost
harness/models/anthropic_adapter.py
harness/models/openrouter_adapter.py
harness/models/scripted_adapter.py
harness/mcp_client.py
harness/module_spec.py
harness/prompts.py
harness/loop.py
harness/trace.py              RunTrace and CaseResult
harness/verifiers/base.py
harness/runner.py             CaseRunner
harness/records.py            database writes (step 8)
modules/aml/agent/models.py   AmlFindings
modules/aml/agent/verifiers.py
modules/aml/agent/spec.py
modules/aml/prompts/investigator_v1.md
evals/make_aml_datasets.py
evals/datasets/aml_alerts_{dev,heldout,smoke}_v1.json
evals/run_aml_agent.py
evals/aml_agent_score.py
evals/test_label_readers.py
```

New dependencies, pinned to releases at least two weeks old: `anthropic==2.3.0`, `openai==3.42.0`.

## 12. Data leak found during verification: late dates

Transfer ids follow time order. Normal traffic in the slice almost stops after 2022-09-10: of the 203 transfers dated 11 to 17 September, 126 are laundering (62%), against 396 of 25,290 (about 1.6%) before. The IBM generator lets laundering schemes run past the end of the normal sample. An agent or a tuned prompt could learn "late date means laundering" and score well for the wrong reason.

Handling:
- The prompt never mentions the date range or this effect, and no prompt tuning may use it.
- Every agent metric is reported twice: alerts whose window ends on or before 2022-09-10, and later ones. The early set is the honest headline.
- Corrections memory (M3) must not store date-based rules.
- Decision note 0006 records it, and docs/glossary.md and data/README.md get a short note.

## 13. Build order (builder, tester, reviewer, commit for each)

1. Neutral types, config loader with cost, scripted adapter. Pure tests.
2. Anthropic and OpenRouter adapters: confirm the API facts in section 2 against the installed SDKs, conversion to and from each provider's format, unit tested with constructed response objects, no network.
3. MCP client, ModuleSpec, prompt loader, AML output model and prompt v1. Test: the client starts the real server on the local test database, lists nine tools and calls one.
4. Agent loop with limits, repeats, the single retry and the trace. Tests with the scripted adapter against the real tools server.
5. Verifier framework and AML verifiers, one test per failure kind.
6. Label guard changes, dataset script and files, runner, scorer, CI fast eval, decision notes 0005 and 0006.
7. First real scored run on the local database (needs an API key in Codespaces secrets and owner approval for spend; free OpenRouter profile first).
8. `case_events`, `agent_runs`, `model_calls`, `eval_runs`, `eval_results` with migration, append-only and hash chain tests, and the write path.

## 14. Verification

| Piece | Verified by |
|---|---|
| Plan | Independent reviewer pass (done, 10 issues fixed: guard scope, typology mapping, metric definitions and splits, verifier lookup path, ModuleSpec and table names, separate write URL, stop reasons and retry budget, gitignore and CI data, unconfirmed API claims, duplicate schema check) |
| Adapters | Round trip tests with tool calls, tool results, unchanged thinking blocks, every stop reason; cost equals hand computed numbers |
| No hard coded model ids | Static test: no model id strings in Python outside `config/` and tests |
| No inline prompts | The loop takes prompts only through the loader; hash in the trace |
| Read-only agent | The agent's only reach is the tools server (already proven read-only); static import test from section 10 |
| Limits and retry | One test per stop reason and limit, each ending in human_review with the partial trace, never `close`; retry used at most once across all failure kinds |
| Labels never reach the agent | Guard tests from section 9; tool results identical with labels emptied (existing invariance test) |
| Date leak | Metrics split by late window; prompt scanned for date range mentions |
| Scorer | Unit tests on hand built alerts, labels and findings with known precision, recall, FP reduction at recall and ECE; baseline numbers on the full alert set match `evals/baselines/aml_rules_v1.json` |
| End to end | CI fast eval with the scripted adapter on 3 alerts; then the first real run, 3 cases read by hand |
| Conventions | ruff, mypy strict on all Python paths, pytest with database tests on the local test database, no em or en dashes |

## Notes and deferred items

- `screen_name` (sanctions screening) stays deferred until sanctions lists are loaded. The other AML tools are complete.
- Maker-checker reviewer agent, approval queue UI, corrections memory and Langfuse tracing are M3.
- The Neon smoke test is not needed for this work: the local database holds the same slice and the same 171 v1 alerts. Do it once before the hosted demo (M4).
