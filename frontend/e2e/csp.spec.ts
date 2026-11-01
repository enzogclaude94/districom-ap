import { expect, test, type Page } from "@playwright/test";

import { login } from "./helpers";

// R9-5-5 (O5): the Content Security Policy in Report-Only. Every screen loads without a violation (what enforce
// would block), and a real violation reaches the backend's report receiver.

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
      seen.push(`${event.effectiveDirective} ${event.blockedURI || "(none)"}`);
    });
  });
}

async function violations(page: Page) {
  return page.evaluate(() => (window as unknown as { __cspViolations: string[] }).__cspViolations);
}

test("the policy is sent in Report-Only with its directives", async ({ page }) => {
  const response = await page.goto("/login");
  const headers = response!.headers();
  expect(headers["content-security-policy"]).toBeUndefined(); // not enforced yet (R9-5-6)
  const policy = headers["content-security-policy-report-only"];
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

test("no screen would be blocked by the policy", async ({ page, baseURL }) => {
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

test("a violation reaches the report receiver", async ({ page }) => {
  await watchViolations(page);
  await page.goto("/login");
  const report = page.waitForRequest((request) => request.url().endsWith("/api/v1/csp-report"));
  await page.evaluate(() => {
    const image = document.createElement("img");
    image.src = "https://example.invalid/pixel.png?cuit=30712345671";
    document.body.appendChild(image);
  });
  const request = await report;
  expect(request.method()).toBe("POST");
  const response = await request.response();
  expect(response?.status()).toBe(204);
  expect(await violations(page)).toEqual(["img-src https://example.invalid/pixel.png?cuit=30712345671"]);
});
