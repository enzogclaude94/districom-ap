import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";
import { login } from "./helpers";

const HEADERS = { "X-Requested-With": "e2e" };

/** New synthetic masters per run: a zone no rule classifies, an unlinked seller code, an article without category. */
async function importMasters(page: Page) {
  const dir = mkdtempSync(path.join(tmpdir(), "districom-e2e-masters-"));
  const seed = Date.now();
  const [article, ...rest] = execFileSync(
    "uv",
    ["run", "--directory", "../backend", "python", "../frontend/e2e/masters_files.py", String(seed), dir],
    { stdio: ["ignore", "pipe", "inherit"] },
  )
    .toString()
    .trim()
    .split(" ");
  const [zone, seller] = [rest.slice(0, 3).join(" "), rest.slice(3).join(" ")];
  const jobs: Record<string, number> = {};
  await login(page, "/comercial/datos/importaciones");
  for (const name of ["clientes.xlsx", "articulos.xlsx"]) {
    await page.goto("/comercial/datos/importaciones");
    await page.getByLabel("Archivo").setInputFiles(path.join(dir, name));
    await page.getByRole("button", { name: "Subir y revisar" }).click();
    await expect(page).toHaveURL(/\/comercial\/datos\/importaciones\/\d+$/, { timeout: 60_000 });
    await page.getByRole("button", { name: "Confirmar y cargar" }).click();
    await expect(page.getByRole("heading", { name: "Resultado" })).toBeVisible({ timeout: 60_000 });
    jobs[name] = Number(new URL(page.url()).pathname.split("/").at(-1));
  }
  return { article, zone, seller, jobs };
}

/** From an import's problems in Calidad to one problem's detail, and from there to Maestros. */
async function openIssue(page: Page, job: number, label: string, subject: string) {
  await page.goto(`/comercial/datos/calidad?job_id=${job}`);
  const row = page.getByRole("row").filter({ hasText: subject }).filter({ hasText: label });
  await expect(row).toContainText("Abierto");
  await row.getByRole("link", { name: label }).click();
  await expect(page.getByRole("heading", { name: label })).toBeVisible();
  const issueUrl = page.url();
  await page.getByRole("link", { name: "Corregir en Maestros" }).click();
  return issueUrl;
}

async function expectResolved(page: Page, issueUrl: string) {
  await page.goto(issueUrl);
  await expect(page.getByText("Resuelto", { exact: true })).toBeVisible();
  await expect(page.getByText(/Se resolvió solo .*: la causa se corrigió/)).toBeVisible();
}

/** F1.7 flow A: Calidad «Producto sin categoría» → Maestros → the product → a category → resolved in Calidad. */
test("assigns a category in Maestros and the Calidad problem resolves", async ({ page }) => {
  test.setTimeout(180_000);
  const { article, jobs } = await importMasters(page);
  const issueUrl = await openIssue(page, jobs["articulos.xlsx"], "Producto sin categoría", article);
  await expect(page).toHaveURL(/\/comercial\/datos\/maestros\/productos\/\d+$/);
  await expect(page.getByRole("heading", { name: new RegExp(`^${article} · ARTICULO E2E`) })).toBeVisible();
  await page.getByRole("button", { name: "Clasificar" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Categoría").selectOption({ index: 1 });
  await dialog.getByLabel("Motivo del cambio").fill("Clasificado en la prueba E2E");
  await dialog.getByRole("button", { name: "Guardar" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText("Sin categoría", { exact: true })).toHaveCount(0);
  await expectResolved(page, issueUrl);
});

/** F1.7 flow B: Calidad «Zona sin configurar» → Maestros → the zone → type and days → resolved in Calidad. */
test("configures a zone in Maestros and the Calidad problem resolves", async ({ page }) => {
  test.setTimeout(180_000);
  const { zone, jobs } = await importMasters(page);
  const issueUrl = await openIssue(page, jobs["clientes.xlsx"], "Zona sin configurar", zone);
  await expect(page).toHaveURL(/\/comercial\/datos\/maestros\/zonas\/\d+$/);
  await expect(page.getByText("Ninguna regla clasifica esta zona", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Configurar zona" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Tipo de zona", { exact: true }).selectOption("PRESALE");
  await dialog.getByLabel(/Se visita/).check();
  await dialog.getByLabel("martes").check();
  await dialog.getByLabel("viernes").check();
  await dialog.getByLabel("Motivo del cambio").fill("Configurada en la prueba E2E");
  await dialog.getByRole("button", { name: "Guardar" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText("Ma · Vi").first()).toBeVisible();
  await expect(page.getByText("Manual", { exact: true })).toBeVisible();
  await expectResolved(page, issueUrl);
});

/** F1.7 flow C: a seller code nobody linked shows in Maestros, links to Mapeos, and once decided shows linked. */
test("shows an unlinked seller, resolves it in Mapeos and then shows it linked", async ({ page }) => {
  test.setTimeout(180_000);
  const { seller } = await importMasters(page);
  await page.goto("/comercial/datos/maestros/vendedores");
  const unlinked = page.getByRole("row").filter({ hasText: seller });
  await expect(unlinked).toContainText("Pendiente");
  const employee = `Preventista Maestros ${seller.split(" ").at(-1)}`;
  const created = await page.request.post("/api/v1/employees", { headers: HEADERS, data: { full_name: employee } });
  expect(created.status()).toBe(201);
  await unlinked.getByRole("link", { name: seller }).click();
  await expect(page).toHaveURL(/\/comercial\/datos\/mapeos\/\d+$/);
  await page.getByRole("button", { name: "Vincular" }).click();
  await page.getByLabel("Buscar empleado").fill(employee);
  await page.getByRole("button", { name: "Buscar" }).click();
  await page.getByRole("list", { name: "Resultados" }).getByRole("button", { name: "Elegir" }).click();
  await page.getByRole("button", { name: "Confirmar vínculo" }).click();
  await expect(page.getByText("Confirmado", { exact: true })).toBeVisible();

  await page.goto("/comercial/datos/maestros/vendedores");
  const linked = page.getByRole("row").filter({ hasText: employee });
  await expect(linked).toContainText(seller);
  await expect(linked).toContainText("1"); // its customer, assigned by the master
  await expect(page.getByRole("row").filter({ hasText: seller }).filter({ hasText: "Pendiente" })).toHaveCount(0);
});
