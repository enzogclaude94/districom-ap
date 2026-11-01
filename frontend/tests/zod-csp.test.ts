import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { z } from "@/lib/zod";

// R9-5-5: Zod's `new Function` probe would be a CSP violation; it is off as long as every schema imports `z`
// from @/lib/zod, which sets `jitless` before any schema exists.
const SRC = join(__dirname, "..", "src");

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sources(path);
    return /\.(ts|tsx)$/.test(entry.name) ? [path] : [];
  });
}

describe("zod under the CSP", () => {
  it("validates without generating code", () => {
    expect(z.config().jitless).toBe(true);
  });

  it("is imported only through @/lib/zod", () => {
    const direct = sources(SRC).filter(
      (file) => !file.endsWith(join("lib", "zod.ts")) && /from ["']zod(\/[^"']*)?["']/.test(readFileSync(file, "utf8")),
    );
    expect(direct).toEqual([]);
  });
});
