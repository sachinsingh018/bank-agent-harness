import type { Metadata } from "next";
import Link from "next/link";

import { pageMeta } from "../../lib/meta";
import { Attribution } from "../_ui/attribution";
import { Contact } from "../_ui/contact";
import { Guilloche } from "../_ui/guilloche";
import { HarnessDiagram } from "../_ui/harness-diagram";
import styles from "./about.module.css";

export const metadata: Metadata = pageMeta(
  "Methodology | AML Alert Desk",
  "The harness around the agent: read-only tools, checks in code, a reviewer agent, human approval, an audit trail, tracing and evals.",
);

export default function AboutPage() {
  return (
    <>
      <h1>Methodology</h1>
      <p className="lede">
        A bank must investigate unusual money movements and report the suspicious ones. This desk
        shows how an AI agent can carry out the first-level investigation while analysts keep
        the final decision. All data is synthetic.
      </p>

      <div className={styles.layout}>
        <figure className={styles.figure}>
          <HarnessDiagram describedBy="harness-steps" />
          <figcaption className="meta">
            The harness around the agent. Solid boxes act on the case; dotted boxes watch and
            measure. The numbered steps say the same in words.
          </figcaption>
        </figure>

        <ol className="steps" id="harness-steps">
          <li>
            <strong>Scenarios raise an alert.</strong> Simple monitoring rules (scenarios) screen
            the transfers and raise an alert when they match a known pattern, such as money
            fanning out from one account to many. Rules are fast but produce many false
            positives: most alerts turn out to be ordinary activity.
          </li>
          <li>
            <strong>The agent loop investigates with read-only tools.</strong> The agent asks for
            the alert, the account&apos;s transfers and its counterparties through tools that
            can only read. It cannot change data, and it cannot see the answer key. It ends with
            an answer: escalate or close, a confidence from 0 to 1, a short narrative and claims
            that cite recorded transfers. The loop is written by hand, with no agent framework.
          </li>
          <li>
            <strong>Verifiers check every answer in code.</strong> Before anyone sees the
            answer, plain code checks it: the shape is right, every cited transfer exists and
            was looked up, and every quoted amount and sum matches the data. If a check fails,
            the agent gets one retry; after that the case is routed to analyst review. Hitting a
            step, token or time limit also routes it to an analyst. Nothing is ever closed
            automatically.
          </li>
          <li>
            <strong>A reviewer agent takes a second look.</strong> A separate agent reads the
            answer and the evidence with its own checklist, like a second analyst. If it rejects
            the answer, or does not finish, the case is routed to an analyst.{" "}
            <span className={styles.honest}>
              It is built, but it has not completed a review on the free models so far, because
              they hit time and model limits.
            </span>
          </li>
          <li>
            <strong>An analyst approves, and the audit trail records it.</strong> An analyst
            approves, edits or rejects the answer. Every step, from the agent&apos;s run to the human
            decision, is written to an append-only audit log that is never changed or removed.
          </li>
          <li>
            <strong>Tracing records every call.</strong> Each model call, tool call and check is
            recorded with its timing and tokens. The replays on this site are built from these
            records.
          </li>
          <li>
            <strong>Evals score every change.</strong> Any change to prompts, tools, models or
            the harness is run against a fixed, versioned set of alerts, and the results are
            compared with the rules alone. See <Link href="/results">Performance</Link>.
          </li>
        </ol>
      </div>

      <h2>What you can do on this site</h2>
      <div className="prose">
        <p>
          This public site only replays recorded runs. No model is called while you watch, and
          nothing you do here changes any data. <Link href="/watch">Watch an investigation</Link>{" "}
          to step through one case, from the alert to the recommendation an analyst has to approve.
        </p>
      </div>

      <Guilloche />
      <Attribution />
      <Contact />
    </>
  );
}
