import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";
import { login } from "./helpers";

const HEADERS = { "X-Requested-With": "e2e" };

/** Synthetic files with a new AXUM salesperson code per run: its import opens «Preventista AXUM sin vincular». */
async function importNewSalesperson(page: Page) {
  const dir = mkdtempSync(path.join(tmpdir(), "districom-e2e-quality-"));
  const seed = Date.now() % 1_000_000_000;
  const run = (script: string) =>
    execFileSync("uv", ["run", "--directory", "../backend", "python", `../frontend/e2e/${script}`, String(seed), dir], {
      stdio: ["ignore", "pipe", "inherit"],
    })
      .toString()
      .trim();
  run("store_sys_files.py");
  const code = run("axum_files.py");
  const upload = async (name: string) => {
    await page.goto("/comercial/datos/importaciones");
    await page.getByLabel("Archivo").setInputFiles(path.join(dir, name));
    await page.getByRole("button", { name: "Subir y revisar" }).click();
    await expect(page).toHaveURL(/\/comercial\/datos\/importaciones\/\d+$/, { timeout: 60_000 });
    await page.getByRole("button", { name: "Confirmar y cargar" }).click();
    await expect(page.getByRole("heading", { name: "Resultado" })).toBeVisible({ timeout: 60_000 });
  };
  await login(page, "/comercial/datos/importaciones");
  await upload("clientes.xlsx");
  await upload("compro_no_compro.xlsx");
  const job = Number(new URL(page.url()).pathname.split("/").at(-1));
  return { code, seed, job };
}

/** From the import just loaded to its problems in Calidad, and to the new salesperson's problem. */
async function openSalespersonIssue(page: Page, code: string) {
  await page.getByRole("link", { name: "Ver sus problemas en Calidad" }).click();
  await expect(page.getByRole("heading", { name: "Calidad" })).toBeVisible();
  await expect(page.getByText(/Solo los de la importación #\d+/)).toBeVisible();
  const row = page.getByRole("row").filter({ hasText: code });
  await expect(row).toContainText("Advertencia");
  await expect(row).toContainText("Abierto");
  await row.getByRole("link", { name: "Preventista AXUM sin vincular" }).click();
  await expect(page.getByRole("heading", { name: "Preventista AXUM sin vincular" })).toBeVisible();
  await expect(page.getByText(new RegExp(`Preventista AXUM ${code}`))).toBeVisible();
  return page.url();
}

/**
 * F1.6: a synthetic import opens a problem; Calidad shows it, its detail leads to the exact mapping, the cause is
 * corrected there and, back in Calidad, the problem is resolved by itself.
 */
test("corrects the cause in Mapeos and the problem resolves by itself", async ({ page }) => {
  test.setTimeout(180_000);
  const { code, seed, job } = await importNewSalesperson(page);
  const issueUrl = await openSalespersonIssue(page, code);

  const employee = `Preventista Calidad ${seed}`;
  const created = await page.request.post("/api/v1/employees", { headers: HEADERS, data: { full_name: employee } });
  expect(created.status()).toBe(201);
  await page.getByRole("link", { name: "Corregir en Mapeos" }).click();
  await expect(page.getByRole("heading", { name: `Preventista AXUM ${code}` })).toBeVisible();
  await page.getByRole("button", { name: "Vincular" }).click();
  await page.getByLabel("Buscar empleado").fill(employee);
  await page.getByRole("button", { name: "Buscar" }).click();
  await page.getByRole("list", { name: "Resultados" }).getByRole("button", { name: "Elegir" }).click();
  await page.getByRole("button", { name: "Confirmar vínculo" }).click();
  await expect(page.getByText("Confirmado", { exact: true })).toBeVisible();

  await page.goto(issueUrl);
  await expect(page.getByText("Resuelto", { exact: true })).toBeVisible();
  await expect(page.getByText(/Se resolvió solo .*: la causa se corrigió/)).toBeVisible();

  await page.goto(`/comercial/datos/calidad?job_id=${job}`);
  await expect(page.getByText(/^\d+ problemas? abiertos? · /)).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: code })).toHaveCount(0); // no longer open
  await page.getByRole("button", { name: /^Resueltos/ }).click();
  await expect(page.getByRole("row").filter({ hasText: code })).toContainText("Resuelto");
});

/** F1.6: an open problem ignored with a note leaves the open ones and shows among the ignored. */
test("ignores a problem with a note", async ({ page }) => {
  test.setTimeout(180_000);
  const { code, job } = await importNewSalesperson(page);
  await openSalespersonIssue(page, code);

  await page.getByRole("button", { name: "Ignorar" }).click();
  await page.getByRole("button", { name: "Confirmar ignorar" }).click();
  await expect(page.getByText("Escribí una nota que explique por qué.")).toBeVisible();
  await page.getByLabel("Nota (obligatoria)").fill("Código de prueba E2E, no es un preventista real");
  await page.getByRole("button", { name: "Confirmar ignorar" }).click();
  await expect(page.getByText(/Ignorado a mano por/)).toBeVisible();
  await expect(page.getByText("Código de prueba E2E, no es un preventista real")).toBeVisible();

  await page.goto(`/comercial/datos/calidad?job_id=${job}`);
  await expect(page.getByRole("heading", { name: "Calidad" })).toBeVisible();
  await expect(page.getByText(/^\d+ problemas? abiertos? · /)).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: code })).toHaveCount(0);
  await page.getByRole("button", { name: /^Ignorados/ }).click();
  await expect(page.getByRole("row").filter({ hasText: code })).toContainText("Ignorado");
});
