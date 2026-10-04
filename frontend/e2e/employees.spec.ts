import { expect, test } from "@playwright/test";

import { login } from "./helpers";

test("admin creates an employee, assigns a function and deactivates them", async ({ page }, testInfo) => {
  const name = `E2E ${testInfo.project.name} ${Date.now()}`;
  await login(page, "/empleados");
  await expect(page.getByRole("heading", { name: "Empleados" })).toBeVisible();

  await page.getByRole("link", { name: "Nuevo empleado" }).first().click();
  await expect(page).toHaveURL(/\/empleados\/nuevo$/);
  await page.getByLabel("Nombre y apellido").fill(name);
  await page.getByRole("button", { name: "Crear empleado" }).click();

  await expect(page).toHaveURL(/\/empleados\/\d+$/);
  await expect(page.getByRole("heading", { name })).toBeVisible();
  await page.getByLabel("Asignar función").selectOption({ label: "Preventista" });
  await page.getByLabel("Desde").fill("2026-01-01");
  await page.getByRole("button", { name: "Asignar" }).click();
  await expect(page.getByText("Vigente", { exact: true })).toBeVisible();

  await page.getByLabel("Último día de trabajo").fill("2026-09-30");
  await page.getByRole("button", { name: "Dar de baja" }).click();
  await expect(page.getByText("Dado de baja", { exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: "30/09/2026" })).toBeVisible();

  await page.goto("/empleados");
  await page.getByRole("button", { name: "Dados de baja" }).click();
  await page.getByLabel("Buscar por nombre").fill(name);
  await expect(page.getByRole("link", { name })).toBeVisible();
});
