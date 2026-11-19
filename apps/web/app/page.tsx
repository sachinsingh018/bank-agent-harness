import Link from "next/link";

import type { RunSteps } from "../lib/api";
import { getManifest, getResults, getRunSteps, getShowcase } from "../lib/data";
import { patternLabel, pickFeatured } from "../lib/replay";
import { splitView } from "../lib/results-view";
import { CountBar } from "./_ui/count-bar";
import { Preview, type PreviewStep } from "./_ui/preview";
import styles from "./page.module.css";

// The mini-replay plays the whole featured run, up to this many steps.
const PREVIEW_STEPS = 16;

async function featuredRun(): Promise<RunSteps | null> {
  try {
    const chosen = (await getManifest())?.featured;
    if (chosen) {
      const run = await getRunSteps(chosen);
      if (run) return run;
    }
    const featured = pickFeatured((await getShowcase(60)).items);
    return featured ? await getRunSteps(featured.run_id) : null;
  } catch {
    return null; // the landing page works without a preview
  }
}

/** One sentence that says what the preview shows, from the run's own outcome step. */
function previewSummary(run: RunSteps): string {
  const pattern = patternLabel(run.alert.detector).toLowerCase();
  const outcome = [...run.steps].reverse().find((s) => s.kind === "outcome");
  const tools = `The agent made ${run.tool_steps} tool calls`;
  if (outcome?.recommendation && typeof outcome.confidence === "number") {
    return (
      `A recorded ${pattern} case replays on a loop. ${tools} and proposed to ` +
      `${outcome.recommendation} at confidence ${outcome.confidence}.`
    );
  }
  return `A recorded ${pattern} case replays on a loop. ${tools}.`;
}

export default async function Home() {
  const [run, results] = await Promise.all([featuredRun(), getResults().catch(() => null)]);
  const steps: PreviewStep[] = (run?.steps ?? []).slice(0, PREVIEW_STEPS).map((s) => ({
    index: s.index,
    actor: s.actor,
    title: s.title,
    explanation: s.explanation,
  }));
  const early = splitView(results?.splits?.early)?.agent ?? null;
  const freeModel = results?.model?.endsWith(":free") ?? false;

  return (
    <div className="landing">
      <section className="hero">
        <div className="hero-text">
          <p className="eyebrow">An AI agent harness for bank alert work, built from scratch</p>
          <h1 className="display hero-title">
            Watch an AI agent investigate a money laundering alert, one step at a time.
          </h1>
          <p className="hero-lede">
            It reads the transfers behind a suspicious alert with read-only tools, code checks
            every claim it makes, and a person approves every decision.
          </p>
          <p className="hero-actions">
            <Link href="/watch" className="button-link primary">
              Watch an investigation
            </Link>
            <Link href="/about" className={styles.quiet}>
              How it works
            </Link>
          </p>
        </div>
        {run && steps.length > 1 && (
          <figure className="hero-preview">
            <Preview steps={steps} graph={run.graph} summary={previewSummary(run)} />
            <figcaption className="meta">
              Replaying a recorded {patternLabel(run.alert.detector).toLowerCase()} case.{" "}
              <Link href={`/watch/${encodeURIComponent(run.run_id)}`}>Watch this one</Link>
            </figcaption>
          </figure>
        )}
      </section>

      {early && (
        <section className={styles.headline} aria-labelledby="headline-title">
          <h2 id="headline-title" className={styles.headlineTitle}>
            Of the {early.falseTotal + early.realTotal} early development alerts, the agent
            would propose to close:
          </h2>
          <div className={styles.pair}>
            <div className={styles.count}>
              <p className={styles.figure}>
                <span className={`${styles.big} condensed`}>{early.falseClosed}</span> of{" "}
                {early.falseTotal} false alarms
              </p>
              <CountBar part={early.falseClosed} total={early.falseTotal} tone="good" />
              <p className="meta">Harmless alerts a person would not have to dig into.</p>
            </div>
            <div className={styles.count}>
              <p className={styles.figure}>
                <span className={`${styles.big} ${styles.missText} condensed`}>
                  {early.realClosed}
                </span>{" "}
                of {early.realTotal} real cases
              </p>
              <CountBar part={early.realClosed} total={early.realTotal} tone="miss" />
              <p className="meta">Real laundering it would have let go.</p>
            </div>
          </div>
          <p className={styles.why}>That is why a person approves every close.</p>
          <p className="meta">
            Early alerts only, strict truth{freeModel ? ", free model" : ""}.{" "}
            <Link href="/results">How these numbers were measured</Link>
          </p>
        </section>
      )}

      <ol className="pillars">
        <li>
          <span className="pillar-n condensed">1</span>
          <span>
            <strong>Investigates</strong> with read-only tools: it can look at accounts and
            transfers, never change them.
          </span>
        </li>
        <li>
          <span className="pillar-n condensed">2</span>
          <span>
            <strong>Gets checked</strong> by code first: every cited transfer must exist and
            every amount must add up. A reviewer agent is meant to read it next.
          </span>
        </li>
        <li>
          <span className="pillar-n condensed">3</span>
          <span>
            <strong>A person decides.</strong> The agent only proposes. Anything unclear goes
            to a human, and nothing closes on its own.
          </span>
        </li>
      </ol>
    </div>
  );
}
