import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import type { RunSteps } from "../lib/api";
import { getManifest, getResults, getRunSteps, getShowcase } from "../lib/data";
import { pageMeta, SITE_DESCRIPTION } from "../lib/meta";
import { patternLabel, pickFeatured } from "../lib/replay";
import { splitView } from "../lib/results-view";
import { Contact } from "./_ui/contact";
import { CountBar } from "./_ui/count-bar";
import { ApproveIcon, CheckIcon, FlagIcon, GitHubIcon, LensIcon } from "./_ui/icons";
import { Preview, type PreviewStep } from "./_ui/preview";
import { Term } from "./_ui/term";
import styles from "./page.module.css";

export const metadata: Metadata = pageMeta(
  "AML Alert Desk: an AI agent does the first investigation of suspicious transfers, and a person makes the final call",
  SITE_DESCRIPTION,
);

// The mini-replay plays the whole featured run, up to this many steps.
const PREVIEW_STEPS = 16;

const REPO = "https://github.com/sachinsingh018/bank-agent-harness";
const LINKEDIN = "https://www.linkedin.com/in/sac108";

/** A file or folder in the repo on GitHub. */
function repo(path: string, folder = false): string {
  return `${REPO}/${folder ? "tree" : "blob"}/main/${path}`;
}

const DEFINITIONS = {
  aml:
    "Anti-money laundering: the checks a bank runs to spot money from crime being moved " +
    "through ordinary accounts.",
  alert:
    "A case a bank's rules open when transfers match a suspicious pattern. Someone has to look " +
    "at it before it can be closed.",
  falsePositive:
    "An alert that turns out to be nothing: normal transfers that only looked suspicious.",
};

const STEPS: { icon: ReactNode; title: string; body: ReactNode }[] = [
  {
    icon: <FlagIcon />,
    title: "A rule flags a pattern",
    body: (
      <>
        Simple rules watch the transfers and raise an{" "}
        <Term definition={DEFINITIONS.alert}>alert</Term> when money moves in a suspicious shape,
        for example one account paying many others at once.
      </>
    ),
  },
  {
    icon: <LensIcon />,
    title: "The agent investigates",
    body: (
      <>
        An AI agent reads the accounts and transfers behind the alert with read-only tools. It can
        look, but it has no way to change anything.
      </>
    ),
  },
  {
    icon: <CheckIcon />,
    title: "Code checks its work",
    body: (
      <>
        Before anyone reads the answer, plain code checks it: every transfer it cites must exist
        and every amount must add up. If not, it gets one retry, then a person takes over. If
        the checks cannot run at all, the case goes straight to a person.
      </>
    ),
  },
  {
    icon: <ApproveIcon />,
    title: "A person decides",
    body: (
      <>
        The agent only recommends. An analyst approves or rejects, and the decision is written to
        an append-only, hash-chained log, so a later edit shows up.
      </>
    ),
  },
];

const SKILLS: { title: string; body: string; links: { href: string; label: string }[] }[] = [
  {
    title: "An agent loop written from scratch",
    body: "Model calls, tool calls, step, token and time limits and one retry, in plain Python with no agent framework. Any limit hit sends the case to a person.",
    links: [{ href: repo("harness/loop.py"), label: "harness/loop.py" }],
  },
  {
    title: "Read-only tools over MCP",
    body: "The agent's tools are an MCP server that only runs checked, read-only queries, so the agent cannot write to the database.",
    links: [{ href: repo("tools/aml", true), label: "tools/aml" }],
  },
  {
    title: "Code checks before any model judge",
    body: "Deterministic verifiers check every cited transfer and every figure against the stored data, with no model calls.",
    links: [
      { href: repo("modules/aml/agent/verifiers.py"), label: "modules/aml/agent/verifiers.py" },
    ],
  },
  {
    title: "Evals with honest metrics",
    body: "Scored on a fixed, versioned set of alerts with strict truth. The real cases the agent would miss are shown next to the false alarms it clears.",
    links: [
      { href: repo("evals/aml_agent_score.py"), label: "evals/aml_agent_score.py" },
      { href: "/results", label: "Results page" },
    ],
  },
  {
    title: "Append-only audit trail",
    body: "Human decisions go to an append-only, hash-chained log (tamper-evident), written outside the agent. A verify command recomputes the chain.",
    links: [
      {
        href: repo("docs/decisions/0007-audit-log-and-run-records.md"),
        label: "Decision note 0007",
      },
    ],
  },
  {
    title: "Static site, tested in CI",
    body: "This site is static Next.js built from recorded runs, with Playwright and axe accessibility tests on every pull request.",
    links: [{ href: repo(".github/workflows/ci.yml"), label: ".github/workflows/ci.yml" }],
  },
];

const STACK = [
  "Python 3.12",
  "FastAPI",
  "Pydantic",
  "Postgres",
  "SQLAlchemy",
  "MCP",
  "Next.js",
  "TypeScript",
  "Playwright",
  "GitHub Actions",
  "Vercel",
];

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
      `A recorded ${pattern} case replays on a loop. ${tools} and recommended ` +
      `${outcome.recommendation === "escalate" ? "escalation" : "closure"} at confidence ${outcome.confidence}.`
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
    <div className={styles.page}>
      <section className={styles.hero} aria-labelledby="hero-title">
        <div className={styles.heroText}>
          <p className="eyebrow">Portfolio project</p>
          <h1 id="hero-title" className={styles.title}>
            Banks flag huge numbers of suspicious transfers, and most turn out to be false
            alarms.{" "}
            <span className={styles.titleAccent}>
              This AI agent does the first investigation, and a person makes the final call.
            </span>
          </h1>
          <p className={styles.lede}>
            Recorded runs on synthetic bank data, replayed step by step. Built from scratch with
            no agent framework. Every transfer and figure the agent cites is checked against the
            stored data by code before a person sees it.
          </p>
          <p className={styles.actions}>
            <Link href="/watch" className="button-link primary">
              Watch an investigation
            </Link>
            <Link href="/results" className={styles.secondary}>
              See the results
            </Link>
          </p>
          <p className={styles.byline}>
            <span>
              Built by <strong>Sachin Singh</strong>
            </span>
            <a href={REPO} className={styles.bylineLink} rel="noopener noreferrer">
              <GitHubIcon size={18} />
              GitHub
            </a>
            <a href={LINKEDIN} className={styles.bylineLink} rel="noopener noreferrer">
              LinkedIn
            </a>
          </p>
        </div>
        {run && steps.length > 1 && (
          <figure className={`hero-preview ${styles.preview}`}>
            <Preview steps={steps} graph={run.graph} summary={previewSummary(run)} />
            <figcaption className="meta">
              Replaying a recorded {patternLabel(run.alert.detector).toLowerCase()} case.{" "}
              <Link href={`/watch/${encodeURIComponent(run.run_id)}`}>Open this case</Link>
            </figcaption>
          </figure>
        )}
      </section>

      {early && (
        <section className={styles.proof} aria-labelledby="headline-title">
          <h2 id="headline-title" className={styles.proofTitle}>
            Of the {early.falseTotal + early.realTotal} early development alerts, the agent
            would recommend closing:
          </h2>
          <div className={styles.pair}>
            <div className={styles.count}>
              <p className={styles.figure}>
                <span className={`${styles.big} condensed`}>{early.falseClosed}</span> of{" "}
                {early.falseTotal}{" "}
                <Term definition={DEFINITIONS.falsePositive}>false positives</Term>
              </p>
              <CountBar part={early.falseClosed} total={early.falseTotal} tone="good" />
              <p className="meta">False positives an analyst would not need to work.</p>
            </div>
            <div className={styles.count}>
              <p className={styles.figure}>
                <span className={`${styles.big} ${styles.missText} condensed`}>
                  {early.realClosed}
                </span>{" "}
                of {early.realTotal} true positives
              </p>
              <CountBar part={early.realClosed} total={early.realTotal} tone="miss" />
              <p className="meta">True positives it would have closed.</p>
            </div>
          </div>
          <p className={styles.plain}>
            A false positive is an alert about normal transfers that only looked suspicious. A
            true positive is real money laundering.
          </p>
          <p className={styles.why}>That is why an analyst approves every close.</p>
          <p className="meta">
            Early alerts only, strict truth{freeModel ? ", free model" : ""}.{" "}
            <Link href="/results">How these numbers were measured</Link>
          </p>
        </section>
      )}

      <section className={styles.section} aria-labelledby="how-title">
        <h2 id="how-title" className={styles.sectionTitle}>
          How it works
        </h2>
        <p className={styles.sectionLede}>
          The agent does the first pass on{" "}
          <Term definition={DEFINITIONS.aml}>AML</Term> (anti-money laundering) alerts. Here is
          what happens to each one.
        </p>
        <ol className={styles.steps}>
          {STEPS.map((step, i) => (
            <li key={step.title} className={styles.step}>
              <span className={styles.stepIcon}>{step.icon}</span>
              <h3 className={styles.stepTitle}>
                <span className={`${styles.stepN} condensed`}>{i + 1}</span> {step.title}
              </h3>
              <p className={styles.stepBody}>{step.body}</p>
            </li>
          ))}
        </ol>
        <p className="meta">
          <Link href="/about">Read the full methodology</Link>
        </p>
      </section>

      <section className={styles.section} aria-labelledby="skills-title">
        <h2 id="skills-title" className={styles.sectionTitle}>
          What this project shows
        </h2>
        <p className={styles.sectionLede}>
          For engineers: each card links to the code or note that backs it up.
        </p>
        <ul className={styles.skills}>
          {SKILLS.map((skill) => (
            <li key={skill.title} className={styles.skill}>
              <h3 className={styles.skillTitle}>{skill.title}</h3>
              <p className={styles.skillBody}>{skill.body}</p>
              <p className={styles.skillLinks}>
                {skill.links.map((link) =>
                  link.href.startsWith("/") ? (
                    <Link key={link.href} href={link.href}>
                      {link.label}
                    </Link>
                  ) : (
                    <a
                      key={link.href}
                      href={link.href}
                      className={styles.code}
                      rel="noopener noreferrer"
                    >
                      {link.label}
                    </a>
                  ),
                )}
              </p>
            </li>
          ))}
        </ul>
      </section>

      <section className={styles.section} aria-labelledby="stack-title">
        <h2 id="stack-title" className={styles.sectionTitle}>
          Built with
        </h2>
        <ul className={styles.chips}>
          {STACK.map((name) => (
            <li key={name}>{name}</li>
          ))}
        </ul>
      </section>

      <Contact />
    </div>
  );
}
