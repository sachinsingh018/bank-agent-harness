import Link from "next/link";

import { getRunSteps, getShowcase, type RunSteps } from "../lib/api";
import { patternLabel, pickFeatured } from "../lib/replay";
import { Preview, type PreviewStep } from "./_ui/preview";

const PREVIEW_STEPS = 9;

async function featuredRun(): Promise<RunSteps | null> {
  try {
    const featured = pickFeatured((await getShowcase(60)).items);
    return featured ? await getRunSteps(featured.run_id) : null;
  } catch {
    return null; // the landing page works without a preview
  }
}

export default async function Home() {
  const run = await featuredRun();
  const steps: PreviewStep[] = (run?.steps ?? []).slice(0, PREVIEW_STEPS).map((s) => ({
    index: s.index,
    actor: s.actor,
    title: s.title,
    explanation: s.explanation,
  }));

  return (
    <div className="landing">
      <section className="hero">
        <div className="hero-text">
          <p className="eyebrow">An AI agent harness for bank alert work, built from scratch</p>
          <h1 className="display hero-title">
            Watch an AI agent investigate a money laundering alert, one step at a time.
          </h1>
          <p className="hero-lede">
            A monitoring rule flags a suspicious pattern of transfers. The agent digs in with
            read-only data tools, every claim it makes is checked against the data, a second
            agent reviews it, and a person has the final say.
          </p>
          <p className="hero-actions">
            <Link href="/watch" className="button-link primary">
              Watch an investigation
            </Link>
            <Link href="/about" className="button-link">
              How it works
            </Link>
          </p>
        </div>
        {run && steps.length > 1 && (
          <figure className="hero-preview">
            <Preview steps={steps} graph={run.graph} />
            <figcaption className="meta">
              Replaying a recorded {patternLabel(run.alert.detector).toLowerCase()} case.{" "}
              <Link href={`/watch/${encodeURIComponent(run.run_id)}`}>Watch this one</Link>
            </figcaption>
          </figure>
        )}
      </section>

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
            every amount must add up, then a reviewer agent reads it.
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
