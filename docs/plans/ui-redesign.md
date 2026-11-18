# Plan: UI redesign and evals page

Status: drafted 2026-11-18, owner asked for a redesign while waiting for model credit. No model requests needed.

## Subject, audience, job

- Subject: an AML alert desk. An AI agent investigates alerts raised by monitoring rules; a second agent reviews; a human decides.
- Audience: people evaluating the project (hiring managers, engineers, compliance folks) and, in the story, an analyst working cases.
- Primary job: show that every agent conclusion is grounded in real transfers and checked, and show the measured results honestly.

## Tokens

Colour (drawn from banknote intaglio printing and compliance paperwork, not from generic dashboards):

| Name | Light | Dark | Use |
|---|---|---|---|
| paper | #F3F5F2 (cool grey white) | #172128 | page ground |
| ink | #1C2830 (blue black) | #E4E9E6 | text |
| intaglio | #2D6A5C (banknote green) | #6FB3A2 | primary, links, passed checks |
| rule | #D3D9D4 | #2C3A43 | lines, table rules |
| signal | #A86A12 (amber) | #E0A44A | escalate |
| brick | #A3393C | #E07A7C | failed check, reject, human review |
| steel | #56656F | #9AA8B1 | close, secondary text |

Type: one family, Archivo (Google Fonts via next/font, variable width axis). Body at normal width, headings and the verdict line in a condensed width with tight tracking, numbers always tabular. No monospace labels, no all caps labels. Scale (rem): 0.8125 meta, 0.9375 body, 1.125 subhead, 1.5 page title, 2.75 verdict.

Shape: radius 3px on panels, 2px on small marks; separation by rules and space, not shadows. No gradients.

## Layout

```
+--------------------------------------------------------------+
| Alert desk        Queue   Evals   Lessons   How it works     |
| Replay mode: recorded cases only. Decisions are off.          |
+--------------------------------------------------------------+
Case page (desktop)
+--------------------------------------+-----------------------+
| AL-3332102d5f16                      | Checks                |
| Escalate                     0.85    |  citations   passed   |
| [0----------|0.5------####----1]     |  arithmetic  failed   |
| GATHER-SCATTER  fan-in detector      | Reviewer              |
|                                      |  approved, 3 reasons  |
| Narrative                            | Run                   |
| ...                                  |  model, prompt v3,    |
| Claims                               |  tokens, cost, time   |
|  1 statement                         | Audit trail           |
|    7675/8093BA -> 1120/80F1  82.10 USD paid   (evidence strip) |
|  2 statement ...                     | Decision panel        |
| Figures                              |                       |
+--------------------------------------+-----------------------+
```

- Left aligned everywhere. Main column max about 72 characters for prose.
- The one bold element: the verdict line on the case page. The recommendation in large condensed type, the confidence as a number, and a meter from 0 to 1 with the 0.5 decision line marked, so a reader sees at once where the agent landed relative to the threshold. Everything else stays quiet.
- Claims show their evidence as compact transfer strips: sender, receiver, amount with currency and side, format. Clicking a strip reveals the transfer details.
- Queue is a dense table: alert, agent says (coloured word plus a small shape so it is not colour only), confidence with a tiny version of the same meter, typology, reviewer, outcome, recorded time. Waiting, decided and all as plain links, not pills.
- Mobile: the rail stacks under the main column; tables scroll inside their own container.

## Pages

- Queue (restyled), Case (redesigned as above), Lessons (the corrections page, renamed for users), Evals (new), How it works (new, short).
- Evals: one row per recorded eval run: when, dataset, model, prompt version, cases, headline (early window) precision and recall against the rules baseline, human review rate, reviewer reject rate, cost. Expand a row for the full score blocks (early, late, all) and a note that small sets are indicative. Needs `GET /evals` and `GET /evals/{eval_run_id}` in the API (read only, from eval_runs and eval_results; counts and rates only, never per alert truth).
- How it works: five short paragraphs in plain words: rules raise alerts, the agent investigates with read-only tools, deterministic checks, the reviewer agent, the human decides and every step is in the audit log. Plus an honest note on how much has been measured so far.

## Copy

Plain verbs, sentence case, user words: "Lessons" not "corrections", "Checks" not "verifiers", "Agent says" not "recommendation". Errors say what happened and what to do. The replay banner says exactly what visitors can and cannot do.

## Review against the defaults

Checked against the generic looks: no cream and terracotta, no acid accent on black, no broadsheet zero radius columns, no identical shadow cards, no all caps eyebrows, middle dots, arrows or monospace labels. The green, amber and brick come from banknotes and compliance stamps, and the condensed verdict with the threshold meter is specific to a decision with a 0.5 cut line.

## Quality floor

Responsive to 360px, visible keyboard focus, prefers-reduced-motion respected (there is almost no motion: only the strip expanding on click), contrast at least AA in both schemes, colour never the only signal.
