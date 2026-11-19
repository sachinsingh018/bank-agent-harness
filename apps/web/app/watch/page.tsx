import type { Metadata } from "next";
import { Suspense } from "react";

import type { ShowcaseItem } from "../../lib/api";
import { getShowcase } from "../../lib/data";
import { loadErrorText } from "../../lib/errors";
import { pageMeta } from "../../lib/meta";
import { isBundleMode } from "../../lib/source";
import { Gallery, GalleryFromUrl } from "./gallery";

export const metadata: Metadata = pageMeta(
  "Case library | AML Alert Desk",
  "Recorded money laundering alerts, each with the agent investigation replayed step by step.",
);

export default async function WatchGallery() {
  let items: ShowcaseItem[] = [];
  let error: string | null = null;
  try {
    items = (await getShowcase()).items;
  } catch (err) {
    error = loadErrorText(err, "recorded cases");
  }

  const recorded = isBundleMode();

  return (
    <>
      <p className="eyebrow">Recorded investigations</p>
      <h1 className="display">Case library</h1>
      <p className="lede">
        Each card is an alert raised by a monitoring rule, drawn as its money flow: dots are bank
        accounts, arrows are transfers. Open one to follow, step by step, how the agent
        investigated it and how the checks and the reviewer agent assessed its answer.
      </p>

      {error && <p className="notice error">{error}</p>}

      {!error && (
        <Suspense
          fallback={<Gallery items={items} pattern={null} outcome={null} recorded={recorded} />}
        >
          <GalleryFromUrl items={items} recorded={recorded} />
        </Suspense>
      )}
    </>
  );
}
