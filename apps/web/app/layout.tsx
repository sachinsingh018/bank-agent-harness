import type { Metadata } from "next";
import { Archivo } from "next/font/google";
import Link from "next/link";
import type { ReactNode } from "react";

import { webDecisionsEnabled } from "../lib/flags";
import "./globals.css";

// One family with the variable width axis: normal width for body, condensed for headings.
const archivo = Archivo({
  subsets: ["latin"],
  axes: ["wdth"],
  variable: "--font-archivo",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Alert desk",
  description:
    "An AML alert desk: an AI agent investigates, a second agent reviews, a person decides.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={archivo.variable}>
      <body>
        <a href="#content" className="skip-link">
          Skip to content
        </a>
        <header className="site-header">
          <div className="bar">
            <Link href="/" className="brand">
              Alert desk
            </Link>
            <nav aria-label="Main">
              <Link href="/watch">Watch</Link>
              <Link href="/queue">Queue</Link>
              <Link href="/evals">Evals</Link>
              <Link href="/lessons">Lessons</Link>
              <Link href="/about">How it works</Link>
            </nav>
          </div>
          {!webDecisionsEnabled() && (
            <div className="replay-banner" role="status">
              <p>
                Replay mode: you can browse recorded cases, checks and eval results. Decisions are
                off, so nothing here changes any data.
              </p>
            </div>
          )}
        </header>
        <main id="content">{children}</main>
      </body>
    </html>
  );
}
