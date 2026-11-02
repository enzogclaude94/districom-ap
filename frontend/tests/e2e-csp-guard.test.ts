import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// R9-5-6: with the CSP enforced, every E2E spec runs under the violation guard of e2e/fixtures.ts. Only csp.spec.ts
// provokes a violation on purpose and takes `test` from Playwright directly.
const E2E = join(__dirname, "..", "e2e");
const UNGUARDED = new Set(["csp.spec.ts"]);

const specs = readdirSync(E2E).filter((name) => name.endsWith(".spec.ts"));

describe("E2E specs under the CSP guard", () => {
  it("finds the specs", () => {
    expect(specs.length).toBeGreaterThan(20);
  });

  it.each(specs.filter((name) => !UNGUARDED.has(name)))("%s takes test from ./fixtures", (name) => {
    const source = readFileSync(join(E2E, name), "utf8");
    expect(source).toMatch(/import \{[^}]*\btest\b[^}]*\} from "\.\/fixtures";/);
    expect(source).not.toMatch(/import \{[^}]*\btest\b[^}]*\} from "@playwright\/test";/);
  });
});
