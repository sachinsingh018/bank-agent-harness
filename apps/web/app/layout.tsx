import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "Bank Agent Harness",
  description: "Case UI for the bank client lifecycle agent harness",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="site-header">
          <Link href="/queue" className="brand">
            Bank Agent Harness
          </Link>
          <nav>
            <Link href="/queue">Approval queue</Link>
          </nav>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
