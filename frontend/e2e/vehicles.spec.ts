import { expect, test } from "@playwright/test";

import { login } from "./helpers";

const operatorEmail = process.env.E2E_OPERATOR_EMAIL ?? "e2e-operator@districom.local";
const operatorPassword = process.env.E2E_OPERATOR_PASSWORD ?? "operator-password-123";

test("a platform admin registers, corrects and deactivates a vehicle; an operator only consults", async ({
  page,
}, testInfo) => {
  const plate = `ZZ${String(Date.now()).slice(-6)}${testInfo.project.name === "mobile" ? "M" : "D"}`;
  await login(page, "/vehiculos");
  await expect(page.getByRole("heading", { name: "Vehículos" })).toBeVisible();

  await page.getByRole("button", { name: "Nuevo vehículo" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Patente").fill(`${plate.slice(0, 2)}-${plate.slice(2).toLowerCase()}`);
  await dialog.getByLabel("Descripción").fill("Camión E2E");
  await dialog.getByRole("button", { name: "Crear vehículo" }).click();
  await expect(page.getByRole("status")).toHaveText(`Vehículo ${plate} registrado.`);

  await page.getByRole("button", { name: `Corregir ${plate}` }).click();
  await dialog.getByLabel("Descripción").fill("Camión E2E corregido");
  await dialog.getByRole("button", { name: "Guardar" }).click();
  await expect(page.getByRole("cell", { name: "Camión E2E corregido" })).toBeVisible();

  await page.getByRole("button", { name: `Desactivar ${plate}` }).click();
  await expect(page.getByRole("status")).toContainText("quedó desactivado");
  await expect(page.getByRole("cell", { name: plate, exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Todos" }).click();
  await page.getByRole("button", { name: `Historia de ${plate}` }).click();
  await expect(dialog.getByText("Descripción corregida")).toBeVisible();
  await expect(dialog.getByText("Desactivado")).toBeVisible();

  await page.context().clearCookies();
  await login(page, "/vehiculos", operatorEmail, operatorPassword);
  await page.getByRole("button", { name: "Todos" }).click();
  await expect(page.getByRole("cell", { name: plate, exact: true })).toBeVisible();
  await expect(page.getByText("Solo un administrador de DistriCom los modifica.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Nuevo vehículo" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: `Activar ${plate}` })).toHaveCount(0);
});
