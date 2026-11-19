import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { firstRunId } from "./bundle";

const PAGES = ["/", "/watch", "/results", "/about"];

for (const url of [...PAGES, "replay"]) {
  test(`no WCAG A or AA violations on ${url}`, async ({ page }) => {
    await page.goto(url === "replay" ? `/watch/${firstRunId()}` : url);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
    expect(results.violations).toEqual([]);
  });
}
