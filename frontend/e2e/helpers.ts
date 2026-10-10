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

/**
 * A day of the business calendar (Argentina) as `YYYY-MM-DD`, `offsetDays` from today. The backend and the
 * browser (`timezoneId`) use that calendar; the machine running the tests may not (UTC is a day ahead from
 * 21:00 in Buenos Aires), so dates sent through the API never come from the machine's clock.
 */
export function businessDay(offsetDays = 0) {
  const date = new Date(Date.now() + offsetDays * 24 * 60 * 60 * 1000);
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Argentina/Buenos_Aires" }).format(date);
}
