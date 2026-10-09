import { expect, test } from "@playwright/test";

import { login } from "./helpers";

const operatorEmail = process.env.E2E_OPERATOR_EMAIL ?? "e2e-operator@districom.local";
const operatorPassword = process.env.E2E_OPERATOR_PASSWORD ?? "operator-password-123";

test("a platform admin registers, corrects and deactivates a third party; an operator only consults", async ({
  page,
}, testInfo) => {
  const name = `Tercero E2E ${Date.now()}${testInfo.project.name === "mobile" ? "M" : "D"}`;
  await login(page, "/terceros");
  await expect(page.getByRole("heading", { name: "Terceros" })).toBeVisible();

  await page.getByRole("button", { name: "Nuevo tercero" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Nombre o razón social").fill(name);
  await dialog.getByRole("button", { name: "Registrar tercero" }).click();
  await expect(page.getByRole("status")).toHaveText(`Tercero ${name} registrado.`);

  // The same name again (neither has a CUIT): a warning, never a silent duplicate.
  await page.getByRole("button", { name: "Nuevo tercero" }).click();
  await dialog.getByLabel("Nombre o razón social").fill(name.toUpperCase());
  await dialog.getByRole("button", { name: "Registrar tercero" }).click();
  await expect(dialog.getByText(/Ya hay un tercero con el nombre/)).toBeVisible();
  await dialog.getByRole("button", { name: "Cerrar" }).click();

  await page.getByLabel("Buscar").fill(name);
  await page.getByRole("button", { name: `Corregir ${name}` }).click();
  await dialog.getByLabel("Notas (opcional)").fill("Proveedor de prueba");
  await dialog.getByRole("button", { name: "Guardar" }).click();
  await expect(page.getByRole("status")).toHaveText(`${name} actualizado.`);

  await page.getByRole("button", { name: `Desactivar ${name}` }).click();
  await expect(page.getByRole("status")).toContainText("quedó desactivado");
  await page.getByRole("button", { name: "Todos" }).click();
  await page.getByRole("link", { name: `Ficha de ${name}` }).click();
  await expect(page.getByRole("heading", { name })).toBeVisible();
  await expect(page.getByText("Notas corregidas")).toBeVisible();
  await expect(page.getByText("Desactivado", { exact: true }).first()).toBeVisible();

  await page.context().clearCookies();
  await login(page, "/terceros", operatorEmail, operatorPassword);
  await page.getByRole("button", { name: "Todos" }).click();
  await page.getByLabel("Buscar").fill(name);
  await expect(page.getByRole("link", { name: `Ficha de ${name}` })).toBeVisible();
  await expect(page.getByText("Solo un administrador de DistriCom los modifica.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Nuevo tercero" })).toHaveCount(0);
});
