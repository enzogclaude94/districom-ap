import { expect, type Page } from "@playwright/test";

export const adminEmail = process.env.E2E_ADMIN_EMAIL ?? "e2e-admin@districom.local";
export const adminPassword = process.env.E2E_ADMIN_PASSWORD ?? "e2e-password-123";

export async function login(page: Page, next: string, email = adminEmail, password = adminPassword) {
  await page.goto(`/login?next=${encodeURIComponent(next)}`);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Contraseña").fill(password);
  await page.getByRole("button", { name: "Ingresar" }).click();
  await expect(page).toHaveURL(new RegExp(`${next.replace(/[/]/g, "\\/")}$`));
}
