import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { firstRunId } from "./bundle";

// Every public page at a 390 px phone width: no sideways scrolling and tap targets of at least
// 24 px (axe's target-size rule, WCAG 2.2 AA).
test.use({ viewport: { width: 390, height: 844 } });

for (const url of ["/", "/watch", "/results", "/about", "replay"]) {
  test(`${url} fits a 390 px screen`, async ({ page }) => {
    await page.goto(url === "replay" ? `/watch/${firstRunId()}?step=3` : url);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
    const results = await new AxeBuilder({ page }).withRules(["target-size"]).analyze();
    expect(results.violations).toEqual([]);
  });
}
