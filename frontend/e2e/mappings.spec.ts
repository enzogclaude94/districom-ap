import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { expect, test } from "./fixtures";
import { login } from "./helpers";

const HEADERS = { "X-Requested-With": "e2e" };

/**
 * F1.5: a new AXUM salesperson code arrives pending with its no-purchase events; in Datos › Mapeos the admin finds
 * it, links it with an active employee after seeing how many events it completes, and the code shows confirmed.
 */
test("links a pending AXUM salesperson and completes its no-purchase events", async ({ page }) => {
  test.setTimeout(180_000);
  const dir = mkdtempSync(path.join(tmpdir(), "districom-e2e-mappings-"));
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
  const employee = `Preventista E2E ${seed}`;
  const created = await page.request.post("/api/v1/employees", { headers: HEADERS, data: { full_name: employee } });
  expect(created.status()).toBe(201);

  await page.goto("/comercial/datos/mapeos");
  await expect(page.getByRole("heading", { name: "Mapeos" })).toBeVisible();
  await page.getByLabel("Buscar código o nombre").fill(code);
  await page.getByLabel("Buscar código o nombre").press("Enter");
  const row = page.getByRole("row").filter({ hasText: code });
  await expect(row).toContainText("Preventista AXUM");
  await expect(row).toContainText("2 eventos de no compra");
  await row.getByRole("link", { name: code }).click();

  await expect(page.getByRole("heading", { name: `Preventista AXUM ${code}` })).toBeVisible();
  await page.getByRole("button", { name: "Vincular" }).click();
  await page.getByLabel("Buscar empleado").fill(employee);
  await page.getByRole("button", { name: "Buscar" }).click();
  await page.getByRole("list", { name: "Resultados" }).getByRole("button", { name: "Elegir" }).click();
  await expect(
    page.getByText("2 eventos de no compra históricos quedarán asociados a este preventista."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Confirmar vínculo" }).click();
  await expect(page.getByText(/2 datos históricos se actualizaron/)).toBeVisible();
  await expect(page.getByText("Confirmado", { exact: true })).toBeVisible();
  await expect(page.getByText(employee).first()).toBeVisible();
});
