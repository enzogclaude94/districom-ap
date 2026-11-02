import { expect, test, type Page } from "@playwright/test";

import { login } from "./helpers";

// R9-5-5/R9-5-6 (O5): the Content Security Policy, enforced. Every screen loads without a violation, and a real
// violation is blocked and reaches the backend's report receiver. The rest of the suite runs under the violation
// guard of fixtures.ts; this spec provokes one on purpose, so it takes `test` from Playwright directly.

const SCREENS = [
  "/",
  "/rendiciones",
  "/rendiciones/cajas",
  "/rendiciones/cajas/gastos",
  "/rendiciones/repartos",
  "/rendiciones/boca",
  "/rendiciones/cheques",
  "/rendiciones/pendientes",
  "/rendiciones/pagos",
  "/rendiciones/combustible",
  "/rendiciones/cierre-del-dia",
  "/rendiciones/empleados",
  "/rendiciones/empleados/liquidaciones",
  "/rendiciones/empleados/pagos",
  "/rendiciones/integracion-store-sys",
  "/rendiciones/configuracion",
  "/comercial",
  "/empleados",
  "/terceros",
  "/bocas",
  "/vehiculos",
  "/usuarios",
  "/configuracion",
  "/cuenta",
];

/** Records every CSP violation the page reports, enforce or report-only, from before any script of the app runs. */
async function watchViolations(page: Page) {
  await page.addInitScript(() => {
    const seen: string[] = [];
    (window as unknown as { __cspViolations: string[] }).__cspViolations = seen;
    document.addEventListener("securitypolicyviolation", (event) => {
      seen.push(`${event.disposition} ${event.effectiveDirective} ${event.blockedURI || "(none)"}`);
    });
  });
}

async function violations(page: Page) {
  return page.evaluate(() => (window as unknown as { __cspViolations: string[] }).__cspViolations);
}

test("the policy is enforced with its directives", async ({ page }) => {
  const response = await page.goto("/login");
  const headers = response!.headers();
  expect(headers["content-security-policy-report-only"]).toBeUndefined(); // enforced since R9-5-6, never both
  const policy = headers["content-security-policy"];
  for (const directive of [
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
    "report-uri /api/v1/csp-report",
  ]) {
    expect(policy).toContain(directive);
  }
  expect(policy).not.toContain("unsafe-eval");
});

test("no screen is blocked by the policy", async ({ page, baseURL }) => {
  test.setTimeout(120_000);
  await watchViolations(page);
  const external: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.protocol.startsWith("http") && url.origin !== new URL(baseURL!).origin) external.push(url.host);
  });
  await login(page, "/rendiciones");
  const found: string[] = [];
  for (const screen of SCREENS) {
    await page.goto(screen);
    await page.waitForLoadState("networkidle");
    found.push(...(await violations(page)).map((v) => `${screen}: ${v}`));
  }
  expect(found).toEqual([]);
  expect(external).toEqual([]); // fonts and everything else served by the app itself
});

/** Adds an element that loads `url` from the page and tells whether it loaded or the browser refused it. */
async function load(page: Page, tag: "img" | "script", url: string) {
  return page.evaluate(
    ([tagName, src]) =>
      new Promise<string>((resolve) => {
        const element = document.createElement(tagName) as HTMLImageElement | HTMLScriptElement;
        element.onload = () => resolve("loaded");
        element.onerror = () => resolve("refused");
        element.src = src;
        document.body.appendChild(element);
      }),
    [tag, url] as const,
  );
}

test("a violation is blocked and reaches the report receiver", async ({ page }) => {
  await watchViolations(page);
  const failures: string[] = [];
  page.on("requestfailed", (request) => failures.push(`${request.failure()?.errorText} ${request.url()}`));
  await page.goto("/login");
  const report = page.waitForRequest((request) => request.url().endsWith("/api/v1/csp-report"));
  expect(await load(page, "img", "https://example.invalid/pixel.png?cuit=30712345671")).toBe("refused");
  const request = await report;
  expect(request.method()).toBe("POST");
  expect(request.postDataJSON()["csp-report"].disposition).toBe("enforce");
  const response = await request.response();
  expect(response?.status()).toBe(204);
  // Refused by the policy itself (Chromium's reason is "csp"), not by the network: the request never left.
  expect(failures).toEqual(["csp https://example.invalid/pixel.png?cuit=30712345671"]);
  expect(await violations(page)).toEqual(["enforce img-src https://example.invalid/pixel.png?cuit=30712345671"]);
});

test("a script from another origin is blocked", async ({ page }) => {
  await watchViolations(page);
  const failures: string[] = [];
  page.on("requestfailed", (request) => failures.push(`${request.failure()?.errorText} ${request.url()}`));
  await page.goto("/login");
  expect(await load(page, "script", "https://example.invalid/x.js")).toBe("refused");
  expect(failures).toEqual(["csp https://example.invalid/x.js"]);
  expect(await violations(page)).toEqual(["enforce script-src-elem https://example.invalid/x.js"]);
});
