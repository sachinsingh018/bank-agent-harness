// Server env flags. Import only from server code.

/** Decisions from the web app are off unless WEB_DECISIONS_ENABLED is exactly "1". Local only
 * until a real reviewer login exists. */
export function webDecisionsEnabled(): boolean {
  return process.env.WEB_DECISIONS_ENABLED === "1";
}

const RUN_ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;

/** Run ids are uuid4 strings; anything else is refused before a URL is built. */
export function isValidRunId(runId: string): boolean {
  return RUN_ID_PATTERN.test(runId);
}
