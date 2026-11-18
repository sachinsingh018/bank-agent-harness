import Link from "next/link";

export const metadata = { title: "How it works | Alert desk" };
// Rendered per request so the replay banner follows the server env, like every other page.
export const dynamic = "force-dynamic";

export default function AboutPage() {
  return (
    <>
      <h1>How it works</h1>
      <p className="lede">
        A bank must look into unusual money movements and report the suspicious ones. This desk
        shows how an AI agent can do the first investigation while people keep the final say.
        All data is synthetic.
      </p>

      <ol className="steps">
        <li>
          <strong>Rules raise alerts.</strong> Simple monitoring rules watch the transfers and
          raise an alert when they see a known pattern, such as money fanning out from one account
          to many. Rules are fast but noisy: most alerts turn out to be ordinary activity.
        </li>
        <li>
          <strong>The agent investigates with read-only tools.</strong> For each alert, the agent
          looks up the account&apos;s transfers and its counterparties through tools that can only
          read. It cannot change data, and it cannot see the answer key. It ends with an answer:
          escalate or close, a confidence from 0 to 1, a short narrative and claims that cite real
          transfers.
        </li>
        <li>
          <strong>Fixed checks run on every answer.</strong> Before anyone sees the answer, plain
          code checks it: the shape is right, every cited transfer exists and was looked up, and
          every quoted amount and sum matches the data. If a check fails, the agent gets one more
          try; after that the case goes to a person. Running out of steps, tokens or time also
          sends it to a person. Nothing is ever closed automatically.
        </li>
        <li>
          <strong>A reviewer agent takes a second look.</strong> A separate agent reads the answer
          and the evidence with its own checklist, like a second analyst. If it rejects the
          answer, or does not finish, the case goes to a person.
        </li>
        <li>
          <strong>A person decides, and every step is logged.</strong> A person approves, edits or
          rejects the answer and can save a short lesson for future cases. Every step, from the
          agent&apos;s run to the human decision, is written to an append-only audit log that is
          never changed or removed.
        </li>
      </ol>

      <h2>How much has been measured</h2>
      <div className="prose">
        <p>
          Honestly, not much yet. So far only a few alerts have been run with a free model, in
          small smoke tests of a handful of alerts each. Numbers on sets this small are indicative:
          they show whether the pipeline works and which way things move, not how accurate the
          agent is. Larger runs on a fixed, versioned set of alerts come next, and every result is
          listed on the <Link href="/evals">Evals</Link> page as it is recorded.
        </p>
      </div>
    </>
  );
}
