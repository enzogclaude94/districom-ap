import { defineConfig, devices } from "@playwright/test";

/**
 * Critical-flow E2E tests against the real backend + frontend.
 * Requires PostgreSQL reachable through DATABASE_URL and an admin created with
 * E2E_ADMIN_EMAIL / E2E_ADMIN_PASSWORD (see README "Tests E2E").
 */
const FRONTEND_PORT = 3100;
const BACKEND_PORT = 8100;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://localhost:${FRONTEND_PORT}`,
    trace: "retain-on-failure",
    // Optional: reuse a preinstalled Chromium instead of `playwright install`.
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE }
      : {},
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
  webServer: [
    {
      command: `uv run --directory ../backend uvicorn app.main:app --port ${BACKEND_PORT}`,
      url: `http://localhost:${BACKEND_PORT}/api/v1/health`,
      reuseExistingServer: !process.env.CI,
      env: { COOKIE_SECURE: "false", CORS_ORIGINS: "" },
    },
    {
      // API_INTERNAL_URL is baked into the rewrites at build time, so build with it.
      command: `pnpm build && pnpm start --port ${FRONTEND_PORT}`,
      timeout: 240_000,
      url: `http://localhost:${FRONTEND_PORT}/login`,
      reuseExistingServer: !process.env.CI,
      env: { API_INTERNAL_URL: `http://localhost:${BACKEND_PORT}` },
    },
  ],
});
