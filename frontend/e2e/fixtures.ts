import { expect, test as base, type BrowserContext } from "@playwright/test";

// R9-5-6: the Content Security Policy is enforced. Every E2E test runs with this guard: any violation the browser
// raises in any page of the test's context (blocked under enforce, or reported under Report-Only) fails the test
// with the list, even when the feature it exercises still happened to work. Contexts a test opens itself
// (`browser.newContext()`, a second person logging in) are guarded too. Only csp.spec.ts, which provokes a violation
// on purpose, uses @playwright/test directly (tests/e2e-csp-guard.test.ts checks every other spec).

const BINDING = "__districomCspViolation";

/** Adds to `found` the CSP violations of every page, present and future, of `context`. */
async function guardCsp(context: BrowserContext, found: string[]): Promise<void> {
  await context.exposeBinding(BINDING, ({ page }, violation: string) => {
    found.push(`${new URL(page.url()).pathname}: ${violation}`);
  });
  await context.addInitScript((binding) => {
    document.addEventListener("securitypolicyviolation", (event) => {
      const report = (window as unknown as Record<string, (violation: string) => void>)[binding];
      report?.(`${event.disposition} ${event.effectiveDirective} ${event.blockedURI || "(none)"}`);
    });
  }, BINDING);
}

export const test = base.extend<{ cspViolations: string[] }>({
  cspViolations: [
    async ({ browser, context }, provide) => {
      const found: string[] = [];
      await guardCsp(context, found);
      const newContext = browser.newContext.bind(browser);
      browser.newContext = async (options) => {
        const opened = await newContext(options);
        await guardCsp(opened, found);
        return opened;
      };
      try {
        await provide(found);
      } finally {
        browser.newContext = newContext;
      }
      expect(found, "CSP violations during the test").toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };
