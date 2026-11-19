import type { Metadata } from "next";
import { Suspense } from "react";

import type { ShowcaseItem } from "../../lib/api";
import { getShowcase } from "../../lib/data";
import { loadErrorText } from "../../lib/errors";
import { pageMeta } from "../../lib/meta";
import { isBundleMode } from "../../lib/source";
import { Gallery, GalleryFromUrl } from "./gallery";

export const metadata: Metadata = pageMeta(
  "Watch an investigation | Alert desk",
  "Pick a recorded money laundering alert and watch the agent investigate it, step by step.",
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
      <p className="eyebrow">Watch an investigation</p>
      <h1 className="display">Pick a case</h1>
      <p className="lede">
        Each card is a real alert from a monitoring rule, drawn as its money flow: dots are bank
        accounts, arrows are transfers. Open one to watch, step by step, how the agent
        investigated it and what the checks and the reviewer made of its answer.
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
