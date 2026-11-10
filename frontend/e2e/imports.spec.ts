import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { expect, test } from "./fixtures";
import { login } from "./helpers";

/**
 * F1.3b: the Imports screen of Comercial with synthetic STORE SYS exports (store_sys_files.py). A master is checked
 * and loaded, a full sales export loads its days, a filtered one over the same day waits for an admin who replaces
 * it with a reason, the same file again is recognised, and a file that is no report says so without loading.
 */
test("imports a STORE SYS master and sales, with an admin's decision on an overlap", async ({ page }) => {
  test.setTimeout(180_000);
  const dir = mkdtempSync(path.join(tmpdir(), "districom-e2e-imports-"));
  const seed = Date.now() % 1_000_000_000;
  execFileSync(
    "uv",
    ["run", "--directory", "../backend", "python", "../frontend/e2e/store_sys_files.py", String(seed), dir],
    {
      stdio: ["ignore", "pipe", "inherit"],
    },
  );
  const file = (name: string) => path.join(dir, name);
  const upload = async (name: string, scope?: "full" | "filtered") => {
    await page.goto("/comercial/datos/importaciones");
    await page.getByLabel("Archivo").setInputFiles(file(name));
    if (scope === "full") await page.getByLabel("Completo, sin filtros").check();
    if (scope === "filtered") {
      await page.getByLabel("Filtrado (por vendedor, zona…)").check();
      await page.getByLabel("Qué filtros usaste").fill("Solo VENDEDOR E2E");
    }
    await page.getByRole("button", { name: "Subir y revisar" }).click();
    await expect(page).toHaveURL(/\/comercial\/datos\/importaciones\/\d+$/, { timeout: 60_000 });
  };

  await login(page, "/comercial/datos/importaciones");
  await expect(page.getByRole("heading", { name: "Importaciones" })).toBeVisible();

  // The customers master: detected by its columns, previewed, then loaded.
  await upload("clientes.xlsx");
  await expect(page.getByText("Clientes", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Clientes nuevos")).toBeVisible();
  await page.getByRole("button", { name: "Confirmar y cargar" }).click();
  await expect(page.getByRole("heading", { name: "Resultado" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Cargada con avisos").or(page.getByText("Cargada", { exact: true }))).toBeVisible();

  // A full sales export: new days, an article that is not in the master, every customer by code.
  await upload("ventas_completo.xlsx", "full");
  await expect(page.getByText("Completo, sin filtros")).toBeVisible();
  await expect(page.getByText(/Carga ventas de 3 días que todavía no estaban cargados/)).toBeVisible();
  await expect(page.getByText("Clientes que no están en el maestro").locator("..")).toContainText("Ninguno");
  await page.getByRole("button", { name: "Ver filas" }).first().click();
  await expect(page.getByRole("table")).toBeVisible();
  const confirm = page.getByRole("button", { name: "Confirmar y cargar" });
  await confirm.click();
  await expect(page.getByRole("heading", { name: "Resultado" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("button", { name: /Confirmar y cargar|Cargando/ })).toHaveCount(0);

  // A filtered export of one of those days: it waits for an admin, who replaces it with a reason.
  await upload("ventas_filtrado.xlsx", "filtered");
  await expect(page.getByText(/no puede probar qué reemplaza/)).toBeVisible();
  await expect(page.getByText("Hace falta tu decisión como administrador.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Confirmar y cargar" })).toHaveCount(0);
  await page.getByRole("button", { name: "Aplicar decisión y cargar" }).click();
  await expect(page.getByText("Elegí qué hacer con las ventas ya cargadas.")).toBeVisible();
  await page.getByLabel(/Reemplazarlas/).check();
  await page.getByRole("button", { name: "Aplicar decisión y cargar" }).click();
  await expect(page.getByText(/Escribí el motivo de la decisión/)).toBeVisible();
  await page.getByLabel("Motivo (obligatorio)").fill("El export filtrado corrige ese día");
  await page.getByRole("button", { name: "Aplicar decisión y cargar" }).click();
  await expect(page.getByRole("heading", { name: "Resultado" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Ventas anteriores reemplazadas").locator("..")).toContainText("3");

  // The same file again is recognised; a file that is no report is refused without loading anything.
  await upload("ventas_filtrado.xlsx", "filtered");
  await expect(page.getByText("Este mismo archivo ya se importó con los mismos datos declarados.")).toBeVisible();
  await upload("no_es_un_reporte.xlsx");
  await expect(page.getByText("No se pudo leer", { exact: true })).toBeVisible();
  await expect(page.getByText(/No se cargó nada/)).toBeVisible();

  // The history lists them, newest first, and opens each one.
  await page.goto("/comercial/datos/importaciones");
  const history = page.getByRole("table");
  await expect(history.getByRole("link", { name: "ventas_completo.xlsx" }).first()).toBeVisible();
  await history.getByRole("link", { name: "ventas_completo.xlsx" }).first().click();
  await expect(page.getByRole("heading", { name: "ventas_completo.xlsx" })).toBeVisible();
});
