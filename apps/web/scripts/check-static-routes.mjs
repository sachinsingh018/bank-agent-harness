// Fails unless every public route is static (○) or prerendered (●) in a saved next build
// output. Usage: node scripts/check-static-routes.mjs [build-output.txt]
//
// Route table lines look like "├ ○ /about". A dynamic route with prerendered pages prints its
// own line without a mark, followed by indented child lines such as "│ ├ ● /watch/abc" and
// "│ └ ● [+5 more paths]"; then every child must be ○ or ●.

import { readFileSync } from "node:fs";

const PUBLIC_ROUTES = ["/", "/watch", "/watch/[runId]", "/results", "/about"];
const STATIC_MARKS = new Set(["○", "●"]);
const LINE = /^([\s┌├└│]*?)([○●ƒ◐λ])?\s*(\/\S*|\[\+\d+ more paths?\])\s*$/;

const file = process.argv[2] ?? "build-output.txt";
const text = readFileSync(file, "utf8").replace(/\u001b\[[0-9;]*m/g, "");

/** Route path to the marks that decide it: its own, or those of its child lines. */
const marks = new Map();
let parent = null;
for (const raw of text.split("\n")) {
  const match = LINE.exec(raw.trimEnd());
  if (!match) {
    parent = null;
    continue;
  }
  const [, prefix, mark, route] = match;
  const depth = prefix.replace(/\s+$/, "").length;
  if (parent && depth > parent.depth) {
    if (mark) marks.get(parent.route).push(mark);
    continue;
  }
  parent = null;
  if (!route.startsWith("/") || marks.has(route)) continue;
  marks.set(route, mark ? [mark] : []);
  if (!mark) parent = { route, depth };
}

const problems = PUBLIC_ROUTES.flatMap((route) => {
  const found = marks.get(route);
  if (found === undefined || found.length === 0) return [`${route}: not found in the build output`];
  const bad = found.filter((mark) => !STATIC_MARKS.has(mark));
  return bad.length === 0 ? [] : [`${route}: rendered per request (${bad.join(" ")})`];
});

if (problems.length > 0) {
  console.error("These public routes must be static (○) or prerendered (●):");
  for (const problem of problems) console.error(`  ${problem}`);
  process.exit(1);
}
console.log(`Static route check passed for ${PUBLIC_ROUTES.length} routes.`);
