# Architecture

Plain-words summary of the shared harness. Full detail is in docs/brief.md.

## One engine, three plug-ins

The harness is built once. Each module (Monitor, Onboard, Lend) supplies a ModuleSpec with five things: tools, prompts, an output schema, verifiers and an eval dataset. The harness never contains module-specific logic.

## Parts

| Part | Job |
|---|---|
| Agent loop | Send case and tools to the model, run tool calls, append results, repeat until a final structured answer or a limit (steps, tokens, time). A hit limit goes to human review. |
| Model adapter | One interface, two backends (Anthropic SDK, OpenAI SDK via OpenRouter). Model IDs in config. Logs model, tokens, cost, latency per call. |
| Tools | Read-only MCP servers. One per module plus a shared screening server. |
| Verifiers | Deterministic checks: schema, citations, arithmetic, module rules. One retry, then human review. |
| Maker-checker | A reviewer agent with a different prompt approves or rejects the preparer's output. |
| Approval queue | The agent only proposes. A human approves in the UI. |
| Audit log | Append-only record of every decision, approval, rejection and edit. |
| Corrections memory | Owner corrections stored as rules and injected into future cases. |
| Eval runner | CLI that scores runs against fixed datasets, stored with commit, prompt versions and models. |
| Tracing | Langfuse traces of every step. |

## Case flow

Rules alert, then agent loop with tools, then schema-valid answer, then verifiers, then reviewer agent, then proposal in the approval queue, then human decision, with everything audited and traced.

## Plug-in interface (to be designed in code)

A ModuleSpec bundles: `tools` (MCP server config), `prompt_version`, `output_model` (Pydantic), `verifiers` (list of checks), `eval_dataset` (manifest). The loop, adapter, approval flow, audit log and eval runner take a ModuleSpec and need no changes for a new module.

## Layout

apps/api (FastAPI), apps/web (Next.js), harness (core), tools (MCP servers), modules/{aml,kyb,credit}, evals, data (scripts only), docs.
