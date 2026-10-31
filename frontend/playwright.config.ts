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
  // The specs share one ledger (Cajas, settlements): balance checks need them to run one at a time.
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://localhost:${FRONTEND_PORT}`,
    trace: "retain-on-failure",
    // The browser lives where the business does: form defaults ("today") are computed in the browser and
    // the backend validates business dates in BUSINESS_TIMEZONE. A CI runner in UTC would otherwise send
    // tomorrow's date between 21:00 and 24:00 in Buenos Aires, and the backend rightly refuses it.
    timezoneId: "America/Argentina/Buenos_Aires",
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
      // The browser talks to the Next.js server, which forwards /api/* with the backend's Host (S2 Origin check).
      env: { COOKIE_SECURE: "false", CORS_ORIGINS: "", TRUSTED_ORIGINS: `http://localhost:${FRONTEND_PORT}` },
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
