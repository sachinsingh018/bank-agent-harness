import { expect, test } from "@playwright/test";

import { firstRunId } from "./bundle";

const EMAIL =
  "mailto:sachinsingh018@gmail.com?subject=Bank%20agent%20harness%20demo%20request";
const LINKEDIN = "https://www.linkedin.com/in/sac108";

for (const url of ["/", "/about"]) {
  test(`contact links on ${url}`, async ({ page }) => {
    await page.goto(url);
    const contact = page.getByRole("region", { name: "Want to try it with your own data?" });
    await expect(contact.getByRole("link", { name: "Email me" })).toHaveAttribute("href", EMAIL);
    await expect(contact.getByRole("link", { name: /LinkedIn/ })).toHaveAttribute(
      "href",
      LINKEDIN,
    );
  });
}

/** The path of the page's og:image, which Next writes as an absolute URL. */
async function ogImagePath(page: import("@playwright/test").Page): Promise<string> {
  const content = await page.locator('meta[property="og:image"]').getAttribute("content");
  expect(content).toBeTruthy();
  const url = new URL(content ?? "");
  return `${url.pathname}${url.search}`;
}

for (const target of ["site", "run"]) {
  test(`the ${target} Open Graph image route returns a PNG`, async ({ page, request }) => {
    await page.goto(target === "site" ? "/" : `/watch/${firstRunId()}`);
    const path = await ogImagePath(page);
    if (target === "run") expect(path).toContain(`/watch/${firstRunId()}/opengraph-image`);
    const response = await request.get(path);
    expect(response.status()).toBe(200);
    expect(response.headers()["content-type"]).toBe("image/png");
    expect((await response.body()).byteLength).toBeGreaterThan(1000);
  });
}

test("a run page names its alert pattern and outcome in the title", async ({ page }) => {
  await page.goto(`/watch/${firstRunId()}`);
  await expect(page).toHaveTitle(/alert, (recommended: (escalate|close)|routed to analyst review) \| AML Alert Desk$/);
  const description = await page.locator('meta[name="description"]').getAttribute("content");
  expect(description).toContain("replayed step by step");
});

test("the favicon is served", async ({ page, request }) => {
  await page.goto("/");
  const href = await page.locator('link[rel="icon"]').first().getAttribute("href");
  const response = await request.get(href ?? "/icon.svg");
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toContain("image/svg+xml");
});
