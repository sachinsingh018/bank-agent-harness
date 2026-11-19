import { notFound, redirect } from "next/navigation";

import { isBundleMode } from "../../lib/source";

// The page is called Lessons for users; old /corrections links still work.
export default async function CorrectionsRedirect({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  // The public site has no API: these pages exist only in API mode.
  if (isBundleMode()) notFound();
  const raw = (await searchParams).cursor;
  const cursor = Array.isArray(raw) ? raw[0] : raw;
  redirect(cursor ? `/lessons?cursor=${encodeURIComponent(cursor)}` : "/lessons");
}
