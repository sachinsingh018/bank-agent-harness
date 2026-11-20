import { expect, test } from "@playwright/test";

import { devPromptVersion, earlyCounts, firstRunId, heldout } from "./bundle";

const REPO = "https://github.com/sachinsingh018/bank-agent-harness";

test("landing renders", async ({ page }) => {
  await page.goto("/");
  const title = page.getByRole("heading", { level: 1 });
  await expect(title).toBeVisible();
  await expect(title).toContainText("most turn out to be false");
  await expect(title).toContainText("a person makes the final call");
  const primary = page.getByRole("link", { name: "Watch an investigation" });
  await expect(primary).toBeVisible();
  await expect(primary).toHaveAttribute("href", "/watch");
  await expect(page.getByRole("link", { name: "See the results" })).toHaveAttribute(
    "href",
    "/results",
  );
});

test("landing names the builder and links the source", async ({ page }) => {
  await page.goto("/");
  const hero = page.getByRole("region", { name: /Banks flag huge numbers/ });
  await expect(hero).toContainText("Built by Sachin Singh");
  await expect(hero.getByRole("link", { name: "GitHub" })).toHaveAttribute("href", REPO);
  await expect(hero.getByRole("link", { name: "LinkedIn" })).toHaveAttribute(
    "href",
    "https://www.linkedin.com/in/sac108",
  );
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link", { name: "Source code on GitHub" })).toHaveAttribute(
    "href",
    REPO,
  );
});

test("landing skills link to the code on GitHub", async ({ page }) => {
  await page.goto("/");
  const skills = page.getByRole("region", { name: "What this project shows" });
  const cards = skills.getByRole("listitem");
  await expect(cards).toHaveCount(6);
  const hrefs = await skills
    .getByRole("link")
    .evaluateAll((links) => links.map((a) => a.getAttribute("href") ?? ""));
  const external = hrefs.filter((href) => !href.startsWith("/"));
  expect(external.length).toBe(6);
  for (const href of external) expect(href.startsWith(`${REPO}/`)).toBe(true);
});

test("landing definitions open on tap and close on Escape", async ({ page }) => {
  await page.goto("/");
  const term = page.getByRole("button", { name: "AML", exact: true });
  await term.click();
  await expect(term).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("tooltip").filter({ hasText: "Anti-money laundering" })).toBeVisible();
  await term.press("Escape");
  await expect(term).toHaveAttribute("aria-expanded", "false");
});

test("landing shows the false positives closed next to the true positives closed", async ({ page }) => {
  const held = heldout();
  const early = held.splits.early.counts;
  await page.goto("/");
  const headline = page.getByRole("region", {
    name: `Of ${held.splits.early.n} alerts the agent had never seen (early held-out set), it would recommend closing:`,
  });
  await expect(headline).toContainText(`${early.false_closed} of ${early.false_total} false positives`);
  await expect(headline).toContainText(`${early.real_closed} of ${early.real_total} true positives`);
  await expect(headline).toContainText("That is why an analyst approves every close.");
  await expect(headline).toContainText(
    `Held-out alerts, strict truth, free model, prompt ${held.prompt_version}.`,
  );
  await expect(headline.getByRole("link", { name: "How these numbers were measured" })).toHaveAttribute(
    "href",
    "/results",
  );
});

test("landing preview can be paused", async ({ page }) => {
  await page.goto("/");
  const toggle = page.getByRole("button", { name: "Pause preview" });
  await toggle.click();
  await expect(page.getByRole("button", { name: "Play preview" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
});

test("landing preview is still under reduced motion", async ({ browser }) => {
  const context = await browser.newContext({ reducedMotion: "reduce" });
  const page = await context.newPage();
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Pause preview" })).toHaveCount(0);
  await context.close();
});

test("gallery renders the recorded cases", async ({ page }) => {
  await page.goto("/watch");
  await expect(page.getByRole("heading", { level: 1, name: "Case library" })).toBeVisible();
  await expect(page.locator(".case-card").first()).toBeVisible();
});

test("replay opens at the first step", async ({ page }) => {
  await page.goto(`/watch/${firstRunId()}`);
  await expect(page.getByRole("toolbar", { name: "Replay controls" })).toBeVisible();
  await expect(page.locator(".counter")).toHaveText(/^Step 1 of \d+$/);
});

test("replay opens at the step in the link", async ({ page }) => {
  await page.goto(`/watch/${firstRunId()}?step=3`);
  await expect(page.locator(".counter")).toHaveText(/^Step 3 of \d+$/);
});

test("results renders", async ({ page }) => {
  const early = earlyCounts();
  await page.goto("/results");
  await expect(page.getByRole("heading", { level: 1, name: "Performance" })).toBeVisible();
  const dev = page.getByRole("region", { name: /^Development alerts:/ });
  await expect(dev.getByRole("table").first()).toContainText(
    `${early.real_closed} of ${early.real_total}`,
  );
  await expect(page.getByRole("heading", { name: /Context: the rules on all/ })).toBeVisible();
  await expect(page.getByRole("link", { name: "CDLA Sharing 1.0" })).toBeVisible();
});

test("results leads with the held-out section and its caveats", async ({ page }) => {
  const held = heldout();
  const early = held.splits.early.counts;
  await page.goto("/results");
  const section = page.getByRole("region", { name: /early held-out alerts the agent had never seen/ });
  await expect(section.getByRole("heading", { level: 2 })).toHaveText(
    `The headline: ${held.splits.early.n} early held-out alerts the agent had never seen`,
  );
  await expect(page.getByRole("heading", { level: 2 }).first()).toHaveText(/held-out/);
  await expect(section.getByRole("table").first()).toContainText(
    `${early.false_closed} of ${early.false_total}`,
  );
  await expect(section.getByRole("table").first()).toContainText(
    `${early.real_closed} of ${early.real_total}`,
  );
  await expect(section).toContainText(`All ${held.splits.all.n} held-out alerts`);
  await expect(section).toContainText(`routed ${held.human_review.count} of the ${held.splits.all.n}`);
  await expect(section).toContainText(
    `Of the ${held.grounding.claims} claims the agent made on the held-out alerts, ${held.grounding.grounded} passed`,
  );
  await expect(section).toContainText(`Eval run ${held.eval_run_id}, prompt ${held.prompt_version}.`);
  await expect(page.getByRole("note")).toContainText(
    `The held-out run used prompt ${held.prompt_version} and the development run used prompt ${devPromptVersion()}.`,
  );
  await expect(page.getByRole("note")).toContainText("not a head-to-head comparison");
  await expect(page.locator("body")).not.toContainText(/no run on the held-out|no held-out run/i);
});

test("about renders", async ({ page }) => {
  await page.goto("/about");
  await expect(page.getByRole("heading", { level: 1, name: "Methodology" })).toBeVisible();
  await expect(page.getByRole("img", { name: /How a case moves through the harness/ })).toBeVisible();
  await expect(page.getByRole("link", { name: "CDLA Sharing 1.0" })).toBeVisible();
});

test("nav hides the API only pages", async ({ page }) => {
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link", { name: "Case library" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Queue" })).toHaveCount(0);
});

test("queue is not found in bundle mode", async ({ page }) => {
  const response = await page.goto("/queue");
  expect(response?.status()).toBe(404);
});

test("an unknown run is not found", async ({ page }) => {
  const response = await page.goto("/watch/not-a-recorded-run");
  expect(response?.status()).toBe(404);
});
