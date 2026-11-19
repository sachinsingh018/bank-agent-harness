import { expect, test } from "@playwright/test";

import { earlyCounts, firstRunId } from "./bundle";

test("landing renders", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Watch an AI agent");
  await expect(page.getByRole("link", { name: "Watch an investigation" })).toBeVisible();
});

test("landing shows the false alarms closed next to the real cases closed", async ({ page }) => {
  const early = earlyCounts();
  await page.goto("/");
  const headline = page.getByRole("region", { name: /early development alerts/ });
  await expect(headline).toContainText(`${early.false_closed} of ${early.false_total} false alarms`);
  await expect(headline).toContainText(`${early.real_closed} of ${early.real_total} real cases`);
  await expect(headline).toContainText("That is why a person approves every close.");
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
  await expect(page.getByRole("heading", { level: 1, name: "Pick a case" })).toBeVisible();
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
  await expect(page.getByRole("heading", { level: 1, name: "Results" })).toBeVisible();
  await expect(page.getByRole("table").first()).toContainText(
    `${early.real_closed} of ${early.real_total}`,
  );
  await expect(page.getByRole("heading", { name: /Context: the rules on all/ })).toBeVisible();
  await expect(page.getByRole("link", { name: "CDLA Sharing 1.0" })).toBeVisible();
});

test("about renders", async ({ page }) => {
  await page.goto("/about");
  await expect(page.getByRole("heading", { level: 1, name: "How it works" })).toBeVisible();
  await expect(page.getByRole("img", { name: /How a case moves through the harness/ })).toBeVisible();
  await expect(page.getByRole("link", { name: "CDLA Sharing 1.0" })).toBeVisible();
});

test("nav hides the API only pages", async ({ page }) => {
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link", { name: "Watch" })).toBeVisible();
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
