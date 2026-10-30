import { expect, test } from "@playwright/test";

import { login } from "./helpers";

/**
 * S2-2: a platform admin creates an account, gives it a module, resets its password and deactivates it; the person
 * logs in with the temporary password, must replace it, and loses every session on the reset and the deactivation.
 */

const NEW_PASSWORD = "mi-frase-nueva-y-larga";

test("a platform admin administers a user end to end", async ({ page, browser, isMobile }, testInfo) => {
  const email = `e2e-user-${testInfo.project.name}-${Date.now()}@districom.local`;
  await login(page, "/usuarios");
  await expect(page.getByRole("heading", { name: "Usuarios" })).toBeVisible();

  await page.getByRole("button", { name: "Nuevo usuario" }).click();
  await page.getByLabel("Nombre y apellido").fill("Usuario E2E");
  await page.getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "Crear usuario" }).click();
  const temporary = (await page.getByLabel("Contraseña temporal").textContent())!.trim();
  expect(temporary).toMatch(/^[A-Za-z0-9]{4}(-[A-Za-z0-9]{4}){3}$/);
  await page.getByRole("link", { name: "Asignar accesos" }).click();
  await expect(page.getByRole("heading", { name: "Usuario E2E" })).toBeVisible();
  await expect(page.getByLabel("Empleados")).toBeDisabled(); // an area needs its module
  await page.getByLabel("Inteligencia Comercial").selectOption("viewer");
  await expect(page.getByLabel("Inteligencia Comercial")).toHaveValue("viewer");

  // The person: temporary password, forced change, then the module.
  const person = await browser.newContext();
  const personPage = await person.newPage();
  await personPage.goto("/login");
  await personPage.getByLabel("Email").fill(email);
  await personPage.getByLabel("Contraseña").fill(temporary);
  await personPage.getByRole("button", { name: "Ingresar" }).click();
  await personPage.getByLabel("Contraseña temporal").fill(temporary);
  await personPage.getByLabel("Contraseña nueva", { exact: true }).fill(NEW_PASSWORD);
  await personPage.getByLabel("Repetí la contraseña nueva").fill(NEW_PASSWORD);
  await personPage.getByRole("button", { name: "Cambiar contraseña" }).click();
  await expect(personPage.getByText("Todavía no hay datos comerciales")).toBeVisible();
  // Not a platform admin: no user administration, neither on screen nor through the API.
  expect((await personPage.request.get("/api/v1/users")).status()).toBe(403);

  // The admin sees the session, resets the password: the person's session is closed.
  await page.reload();
  await expect(page.getByRole("table", { name: "Sesiones del usuario" })).toBeVisible();
  await page.getByRole("button", { name: "Resetear contraseña" }).click();
  await page.getByRole("button", { name: "Generar contraseña temporal" }).click();
  await expect(page.getByLabel("Contraseña temporal")).toBeVisible();
  await page.getByRole("button", { name: "Listo" }).click();
  await personPage.reload();
  await expect(personPage).toHaveURL(/\/login/);

  // Deactivated: cannot log in with the password chosen before either.
  await page.getByRole("button", { name: "Desactivar" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Desactivar" }).click();
  await expect(page.getByText("Desactivado", { exact: true })).toBeVisible();
  const refused = await personPage.request.post("/api/v1/auth/login", {
    data: { email, password: NEW_PASSWORD },
    headers: { "X-Requested-With": "e2e" },
  });
  expect(refused.status()).toBe(401);
  await expect(page.getByRole("list", { name: "Actividad de la cuenta" })).toContainText("Usuario desactivado");
  await person.close();

  if (isMobile) await page.getByRole("button", { name: "Abrir menú" }).click();
  await page.getByRole("link", { name: "Usuarios" }).click();
  await expect(page).toHaveURL(/\/usuarios$/);
});

test("on one's own account an admin cannot deactivate nor reset", async ({ page }) => {
  await login(page, "/usuarios");
  await page.getByRole("button", { name: "Todos" }).click();
  await page.getByRole("searchbox", { name: "Buscar por nombre o email" }).fill("e2e-admin@");
  await page.getByRole("link", { name: "Admin E2E" }).click();
  await expect(page.getByText(/Es tu cuenta/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Desactivar" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Resetear contraseña" })).toHaveCount(0);
  const me = page.url().split("/").at(-1);
  const response = await page.request.post(`/api/v1/users/${me}/deactivate`, {
    headers: { "X-Requested-With": "e2e" },
  });
  expect(response.status()).toBe(409);
});
