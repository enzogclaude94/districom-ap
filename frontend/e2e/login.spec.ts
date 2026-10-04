import { expect, test } from "@playwright/test";

const email = process.env.E2E_ADMIN_EMAIL ?? "e2e-admin@districom.local";
const password = process.env.E2E_ADMIN_PASSWORD ?? "e2e-password-123";

test("unauthenticated visitor is sent to login", async ({ page }) => {
  await page.goto("/configuracion");
  await expect(page).toHaveURL(/\/login\?next=%2Fconfiguracion/);
});

test("wrong password shows a clear error", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Contraseña").fill("wrong-password-123");
  await page.getByRole("button", { name: "Ingresar" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Email o contraseña incorrectos." })).toBeVisible();
});

test("admin logs in, navigates and logs out", async ({ page, isMobile }) => {
  await page.goto("/login?next=/configuracion");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Contraseña").fill(password);
  await page.getByRole("button", { name: "Ingresar" }).click();

  await expect(page).toHaveURL(/\/configuracion$/);
  await expect(page.getByRole("heading", { name: "Configuración" })).toBeVisible();
  await expect(page.getByRole("cell", { name: /Moneda/ })).toBeVisible();
  // One section per settings scope: general (platform) and the commercial module.
  await expect(page.getByText("General", { exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: /commercial\.active_window_days/ })).toBeVisible();

  if (isMobile) await page.getByRole("button", { name: "Abrir menú" }).click();
  await page.getByRole("link", { name: "Inicio" }).click();
  await expect(page).toHaveURL(/\/comercial$/);
  await expect(page.getByText("Todavía no hay datos comerciales")).toBeVisible();

  // With more than one module, the platform home is the module selector.
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Módulos" })).toBeVisible();

  await page.getByRole("button", { name: "Cerrar sesión" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
});

test("brand assets are public (login page renders the wordmark)", async ({ page, request }) => {
  for (const path of [
    "/brand/districom-wordmark.svg",
    "/brand/districom-icon-96.png",
    "/brand/districom-rendiciones.png",
    "/favicon.ico",
  ]) {
    const response = await request.get(path, { maxRedirects: 0 });
    expect(response.status(), path).toBe(200);
  }
  await page.goto("/login");
  const wordmark = page.getByRole("img", { name: "DistriCom" });
  await expect(wordmark).toBeVisible();
  expect(await wordmark.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
});
