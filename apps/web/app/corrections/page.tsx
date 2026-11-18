import { redirect } from "next/navigation";

// The page is called Lessons for users; old /corrections links still work.
export default async function CorrectionsRedirect({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const raw = (await searchParams).cursor;
  const cursor = Array.isArray(raw) ? raw[0] : raw;
  redirect(cursor ? `/lessons?cursor=${encodeURIComponent(cursor)}` : "/lessons");
}
