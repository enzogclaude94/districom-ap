import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

// The build must not depend on Google Fonts (docs/BRAND.md): the brand fonts are files of the repository.
const src = join(__dirname, "..", "src");
const fonts = join(src, "app", "fonts");

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx|css)$/.test(name) ? [path] : [];
  });
}

describe("brand fonts", () => {
  it("no source file loads fonts from Google", () => {
    const offenders = sources(src).filter((path) =>
      /next\/font\/google|fonts\.googleapis\.com|fonts\.gstatic\.com/.test(readFileSync(path, "utf8")),
    );
    expect(offenders).toEqual([]);
  });

  it("serves Inter and Manrope from local files with the brand's weights and CSS variables", () => {
    const layout = readFileSync(join(src, "app", "layout.tsx"), "utf8");
    expect(layout).toContain('from "next/font/local"');
    for (const weight of ["400", "500", "600"]) {
      expect(layout).toContain(`{ path: "./fonts/Inter-opsz14.woff2", weight: "${weight}", style: "normal" }`);
    }
    expect(layout).toContain('{ path: "./fonts/Manrope-SemiBold.woff2", weight: "600", style: "normal" }');
    expect(layout).toContain('variable: "--font-inter"');
    expect(layout).toContain('variable: "--font-manrope"');
    const tokens = readFileSync(join(src, "app", "globals.css"), "utf8");
    expect(tokens).toContain("--font-sans: var(--font-inter);");
    expect(tokens).toContain("--font-display: var(--font-manrope);");
  });

  it("ships each font file with its license", () => {
    for (const file of ["Inter-opsz14.woff2", "Manrope-SemiBold.woff2"]) {
      const bytes = readFileSync(join(fonts, file));
      expect(bytes.subarray(0, 4).toString("latin1")).toBe("wOF2");
    }
    for (const license of ["OFL-Inter.txt", "OFL-Manrope.txt"]) {
      expect(existsSync(join(fonts, license))).toBe(true);
      expect(readFileSync(join(fonts, license), "utf8")).toContain("SIL OPEN FONT LICENSE Version 1.1");
    }
  });
});
