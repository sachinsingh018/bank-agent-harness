// Which data source the showcase pages read. Server side only.

/** True unless SHOWCASE_SOURCE is exactly "api". The public site reads the committed bundle;
 * local development sets SHOWCASE_SOURCE=api in .env.local to read the live API. */
export function isBundleMode(): boolean {
  return process.env.SHOWCASE_SOURCE !== "api";
}
