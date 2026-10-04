import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Module boundaries (ARCHITECTURE.md §5). Only app routes and the module registry compose modules.
  {
    files: ["src/**/*.{ts,tsx}"],
    ignores: ["src/app/**", "src/modules/**"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["@/modules", "@/modules/*", "**/modules/*"],
              message: "Shared code (components, lib, features, platform) must not import from a module.",
            },
          ],
        },
      ],
    },
  },
  {
    files: ["src/modules/commercial/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["@/modules/*", "!@/modules/commercial"],
              message: "A module must not import another module (or the registry).",
            },
          ],
        },
      ],
    },
  },
  {
    files: ["src/modules/settlements/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["@/modules/*", "!@/modules/settlements"],
              message: "A module must not import another module (or the registry).",
            },
          ],
        },
      ],
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
