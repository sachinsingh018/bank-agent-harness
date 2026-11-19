import type { Metadata } from "next";
import { Archivo } from "next/font/google";
import Link from "next/link";
import type { ReactNode } from "react";

import { webDecisionsEnabled } from "../lib/flags";
import { pageMeta, SITE_DESCRIPTION, SITE_NAME, siteUrl } from "../lib/meta";
import { isBundleMode } from "../lib/source";
import "./globals.css";

// One family with the variable width axis: normal width for body, condensed for headings.
const archivo = Archivo({
  subsets: ["latin"],
  axes: ["wdth"],
  variable: "--font-archivo",
  display: "swap",
});

export const metadata: Metadata = {
  ...pageMeta(SITE_NAME, SITE_DESCRIPTION),
  metadataBase: siteUrl(),
};

export default function RootLayout({ children }: { children: ReactNode }) {
  // The public site (bundle mode) links only to pages that work without the API.
  const live = !isBundleMode();
  return (
    <html lang="en" className={archivo.variable}>
      <body>
        <a href="#content" className="skip-link">
          Skip to content
        </a>
        <header className="site-header">
          <div className="bar">
            <Link href="/" className="brand">
              AML Alert Desk
            </Link>
            <nav aria-label="Main">
              <Link href="/watch">Case library</Link>
              <Link href="/results">Performance</Link>
              {live && <Link href="/queue">Queue</Link>}
              {live && <Link href="/evals">Evals</Link>}
              {live && <Link href="/lessons">Lessons</Link>}
              <Link href="/about">Methodology</Link>
            </nav>
          </div>
          {!webDecisionsEnabled() && (
            <div className="replay-banner" role="status">
              <p>
                Replay mode: you can browse recorded cases, checks and evaluation results. Decisions
                are disabled, so nothing here changes any data.
              </p>
            </div>
          )}
        </header>
        <main id="content">{children}</main>
      </body>
    </html>
  );
}
