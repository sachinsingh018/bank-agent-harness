import { expect, test } from "@playwright/test";

import { firstRunId } from "./bundle";

test("landing renders", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Watch an AI agent");
  await expect(page.getByRole("link", { name: "Watch an investigation" })).toBeVisible();
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
  await page.goto("/results");
  await expect(page.getByRole("heading", { level: 1, name: "Results" })).toBeVisible();
});

test("about renders", async ({ page }) => {
  await page.goto("/about");
  await expect(page.getByRole("heading", { level: 1, name: "How it works" })).toBeVisible();
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
