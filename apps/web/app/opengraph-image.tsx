// The site-wide share image. Static: drawn once at build time.

import { OG_CONTENT_TYPE, OG_SIZE, ogCard } from "./_ui/og-card";

export const alt = "Alert desk: watch an AI agent investigate a money laundering alert";
export const size = OG_SIZE;
export const contentType = OG_CONTENT_TYPE;

export default function Image() {
  return ogCard({
    eyebrow: "An AI agent harness for bank alert work, built from scratch",
    title: "Watch an AI agent investigate a money laundering alert, one step at a time.",
    detail: "Read-only tools, checks in code, and a person approves every decision.",
  });
}
