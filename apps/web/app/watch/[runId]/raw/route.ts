// The recorded run as a JSON download: the same allowlisted file the replay reads from the
// committed bundle. Bundle mode only, prerendered for every run; API mode has no raw file.

import { getBundleRun, listBundleRunIds } from "../../../../lib/bundle";
import { isBundleMode } from "../../../../lib/source";

export const dynamic = "force-static";

async function bundleParams(): Promise<{ runId: string }[]> {
  return (await listBundleRunIds()).map((runId) => ({ runId }));
}

export const generateStaticParams = isBundleMode() ? bundleParams : undefined;

function notFound(): Response {
  return new Response("Not found\n", { status: 404 });
}

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ runId: string }> },
): Promise<Response> {
  if (!isBundleMode()) return notFound();
  const { runId } = await params;
  const run = await getBundleRun(runId);
  if (!run) return notFound();
  return new Response(`${JSON.stringify(run, null, 2)}\n`, {
    headers: {
      "content-type": "application/json; charset=utf-8",
      "content-disposition": `attachment; filename="run-${runId}.json"`,
    },
  });
}
