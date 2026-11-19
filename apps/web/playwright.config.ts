import { defineConfig, devices } from "@playwright/test";

// End to end checks of the public site in bundle mode. The API URL points at a closed port on
// purpose: every public page must render from the committed bundle alone. Run npm run build
// first; the web server serves that build.
const PORT = 3100;

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npm run start -- -p ${PORT}`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    env: {
      SHOWCASE_SOURCE: "bundle",
      API_BASE_URL: "http://127.0.0.1:9",
    },
  },
});
