import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { firstRunId } from "./bundle";

test("guided mode plays on its own and pauses", async ({ page }) => {
  await page.goto(`/watch/${firstRunId()}`);
  const modes = page.getByRole("group", { name: "Replay mode" });
  await expect(modes.getByRole("button", { name: "Guided" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  const counter = page.locator(".counter");
  await expect(counter).toHaveText(/^Step 2 of \d+$/, { timeout: 10_000 });
  await page.getByRole("button", { name: "Pause" }).click();
  const paused = await counter.textContent();
  await page.waitForTimeout(6_000);
  await expect(counter).toHaveText(paused ?? "");
  await expect(page.getByRole("button", { name: "Play", exact: true })).toBeVisible();
});

test("explore mode opens at the step in the link and steps with the keyboard", async ({ page }) => {
  await page.goto(`/watch/${firstRunId()}?step=3`);
  const modes = page.getByRole("group", { name: "Replay mode" });
  await expect(modes.getByRole("button", { name: "Explore" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.locator(".counter")).toHaveText(/^Step 3 of \d+$/);
  await page.locator("body").press("ArrowRight");
  await expect(page.locator(".counter")).toHaveText(/^Step 4 of \d+$/);
  await page.locator("body").press("ArrowLeft");
  await expect(page.locator(".counter")).toHaveText(/^Step 3 of \d+$/);
});

test("the narration is labelled as written by code", async ({ page }) => {
  await page.goto(`/watch/${firstRunId()}?step=1`);
  await expect(page.getByRole("heading", { name: "What the agent did" })).toBeVisible();
  await expect(page.getByText("Agent notes")).toHaveCount(0);
});

test("the case file tab shows the case without a decision form", async ({ page }) => {
  await page.goto(`/watch/${firstRunId()}?step=1`);
  await page.getByRole("tab", { name: "Case file" }).click();
  const panel = page.getByRole("tabpanel", { name: "Case file" });
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("heading", { name: "Checks" })).toBeVisible();
  await expect(panel.getByRole("heading", { name: "Audit trail" })).toBeVisible();
  await expect(page.locator("form")).toHaveCount(0);
});

test("technical details open with the tool, its timing and the model effort", async ({ page }) => {
  await page.goto(`/watch/${firstRunId()}?step=2`);
  const details = page.locator("details", { hasText: "Technical details" });
  await details.locator("summary").click();
  await expect(details.getByText("Tool", { exact: true })).toBeVisible();
  await expect(details.getByText("Tool time")).toBeVisible();
  await expect(details.getByText("Model tokens")).toBeVisible();
});

test("provenance strip and raw download", async ({ page, request }) => {
  const runId = firstRunId();
  await page.goto(`/watch/${runId}?step=1`);
  await expect(page.getByText("Recorded run, replayed step by step")).toBeVisible();
  await expect(page.getByText("The answer key was hidden from the agent.")).toBeVisible();
  const link = page.getByRole("link", { name: "Download raw run (JSON)" });
  await expect(link).toHaveAttribute("href", `/watch/${runId}/raw`);
  const response = await request.get(`/watch/${runId}/raw`);
  expect(response.status()).toBe(200);
  const body = (await response.json()) as { steps: { run_id: string } };
  expect(body.steps.run_id).toBe(runId);
});

test("a transfer chip lights its money link on the board", async ({ page }) => {
  await page.goto(`/watch/${firstRunId()}?step=1`);
  const chip = page.getByRole("list", { name: "Transfers in this step" }).getByRole("button").first();
  await chip.focus();
  await expect(page.locator(".flow-edge.hot").first()).toBeVisible();
  const edge = page.locator("svg [data-edge]").first();
  await edge.focus();
  await expect(page.locator("aside").getByText(/ paid .*US Dollar/)).toBeVisible();
});

test("no WCAG A or AA violations on the replay in explore mode and the case file", async ({
  page,
}) => {
  await page.goto(`/watch/${firstRunId()}?step=3`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  let results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(results.violations).toEqual([]);
  await page.getByRole("tab", { name: "Case file" }).click();
  results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(results.violations).toEqual([]);
});

test("guided mode waits for play under reduced motion", async ({ browser }) => {
  const context = await browser.newContext({ reducedMotion: "reduce" });
  const page = await context.newPage();
  await page.goto(`/watch/${firstRunId()}`);
  const counter = page.locator(".counter");
  await expect(page.getByRole("button", { name: "Play", exact: true })).toBeVisible();
  await page.waitForTimeout(6_000);
  await expect(counter).toHaveText(/^Step 1 of \d+$/);
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await expect(page.getByRole("button", { name: "Pause" })).toBeVisible();
  await expect(counter).toHaveText(/^Step 2 of \d+$/, { timeout: 10_000 });
  await context.close();
});
