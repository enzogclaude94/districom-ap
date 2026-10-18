import { expect, test } from "@playwright/test";

import { login } from "./helpers";

const viewerEmail = process.env.E2E_VIEWER_EMAIL ?? "e2e-viewer@districom.local";
const viewerPassword = process.env.E2E_VIEWER_PASSWORD ?? "viewer-password-123";

test("platform admin picks Rendiciones and switches back to Comercial", async ({ page, isMobile }) => {
  await login(page, "/");
  await expect(page.getByRole("heading", { name: "Módulos" })).toBeVisible();
  await page.getByRole("link", { name: "Entrar a Rendiciones" }).click();

  await expect(page).toHaveURL(/\/rendiciones$/);
  const logo = page.getByRole("img", { name: "DistriCom Rendiciones" });
  await expect(logo).toBeVisible();
  expect(await logo.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
  await expect(page.getByText("El módulo está habilitado")).toBeVisible();

  if (isMobile) await page.getByRole("button", { name: "Abrir menú" }).click();
  // The E2E admin holds the protected employee area (README, CI): R8 is no longer a placeholder.
  await expect(
    page.getByRole("link", { name: "Cuentas internas" }).and(page.locator('[href="/rendiciones/empleados"]')),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "Liquidaciones internas" })).toHaveAttribute(
    "href",
    "/rendiciones/empleados/liquidaciones",
  );
  await expect(page.getByRole("link", { name: "Bocas", exact: true })).toHaveAttribute("href", "/rendiciones/boca");
  await expect(page.getByRole("link", { name: "Combustible" })).toHaveAttribute("href", "/rendiciones/combustible");
  await page.getByRole("button", { name: /Cambiar de módulo/ }).click();
  await page.getByRole("navigation", { name: "Módulos" }).getByRole("link", { name: "Inteligencia Comercial" }).click();
  await expect(page).toHaveURL(/\/comercial$/);
  await expect(page.getByText("Todavía no hay datos comerciales")).toBeVisible();
});

test("a user without a role in Rendiciones cannot open it", async ({ page }) => {
  await login(page, "/comercial", viewerEmail, viewerPassword);
  // Only one module: the platform home goes straight to it.
  await page.goto("/");
  await expect(page).toHaveURL(/\/comercial$/);

  await page.goto("/rendiciones");
  await expect(page.getByText("No tenés acceso a Rendiciones")).toBeVisible();
  await expect(page.getByRole("img", { name: "DistriCom Rendiciones" })).toHaveCount(0);

  const response = await page.request.get("/api/v1/settlements/status", {
    headers: { "X-Requested-With": "e2e" },
  });
  expect(response.status()).toBe(403);
});
