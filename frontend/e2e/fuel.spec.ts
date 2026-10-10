import { expect, test, type APIRequestContext } from "@playwright/test";

import { adminEmail, adminPassword, businessDay, login } from "./helpers";

const operatorEmail = process.env.E2E_OPERATOR_EMAIL ?? "e2e-operator@districom.local";
const operatorPassword = process.env.E2E_OPERATOR_PASSWORD ?? "operator-password-123";

const HEADERS = { "X-Requested-With": "e2e" };

async function post<T>(request: APIRequestContext, path: string, data: unknown) {
  const response = await request.post(`/api/v1${path}`, { headers: HEADERS, data });
  expect(response.ok(), `POST ${path}: ${await response.text()}`).toBeTruthy();
  return (await response.json()) as T;
}

/** Yesterday in the business calendar, resolved when used: never a future date for the backend. */
const yesterday = () => businessDay(-1);

test("an operator loads fuel in a settlement; the backend computes the amount and a correction follows", async ({
  page,
}, testInfo) => {
  const run = `${Date.now()}${testInfo.project.name === "mobile" ? "M" : "D"}`;
  const plate = `FU${run.slice(-7)}`;
  const driverName = `Repartidor combustible ${run}`;

  // Admin: an active vehicle and a driver.
  await login(page, "/rendiciones/repartos", adminEmail, adminPassword);
  await post(page.request, "/vehicles", { plate, description: "Camión combustible E2E" });
  const driver = await post<{ id: number }>(page.request, "/employees", {
    full_name: driverName,
    hire_date: null,
    phone: null,
  });
  await post(page.request, `/employees/${driver.id}/functions`, {
    function_code: "settlements.delivery_driver",
    valid_from: "2026-01-01",
  });

  // Operator: a settlement and a fuel expense through the screen.
  await page.context().clearCookies();
  await login(page, "/rendiciones/repartos", operatorEmail, operatorPassword);
  await page.getByRole("button", { name: "Nueva rendición" }).click();
  const create = page.getByRole("dialog");
  await create.getByLabel("Fecha").fill(yesterday());
  await create.getByLabel("Repartidor").selectOption({ label: driverName });
  await create.getByLabel("Importe esperado").fill("100.000");
  await create.getByRole("button", { name: "Crear rendición" }).click();
  await expect(page).toHaveURL(/\/rendiciones\/repartos\/\d+$/);

  await page.getByRole("button", { name: "Registrar gasto" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Categoría").selectOption({ label: "Combustible" });
  await expect(dialog.getByLabel("Importe")).toHaveCount(0);
  await dialog.getByLabel("Vehículo").selectOption({ label: `${plate} · Camión combustible E2E` });
  await expect(dialog.getByLabel("Quién cargó")).toHaveValue(String(driver.id));
  await dialog.getByLabel("Litros").fill("45,678");
  await dialog.getByLabel("Precio por litro").fill("1.234,5678");
  await expect(dialog.getByText("Total estimado $ 56.392,59")).toBeVisible();
  await dialog.getByRole("button", { name: "Registrar gasto", exact: true }).click();
  await expect(dialog).toBeHidden();

  const row = page.getByRole("row", { name: new RegExp(plate) });
  await expect(row).toContainText("$ 56.392,59");
  await expect(row).toContainText(driverName);

  // A correction of the liters: the amount follows.
  await row.getByRole("button", { name: "Corregir carga" }).click();
  await dialog.getByLabel("Litros").fill("10");
  await expect(dialog.getByText("Total estimado $ 12.345,68")).toBeVisible();
  await dialog.getByRole("button", { name: "Guardar", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(row).toContainText("$ 12.345,68");
});
