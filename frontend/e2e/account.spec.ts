import { execFileSync } from "node:child_process";

import { expect, test, type Browser } from "@playwright/test";

/**
 * S2-1: a temporary password only lets its holder choose a new one; «Mi cuenta» lists the open sessions with their
 * network and closes them; a change from another origin is refused by the backend. A fresh user per run, created
 * and reset with the CLI (the administration screens come in S2-2).
 */

const NEW_PASSWORD = "mi-frase-nueva-y-larga";

function cli(args: string[], input?: string): string {
  return execFileSync("uv", ["run", "--directory", "../backend", "python", "-m", "app.cli", ...args], {
    input,
    stdio: ["pipe", "pipe", "inherit"],
  }).toString();
}

function userWithTemporaryPassword(email: string): string {
  cli(["create-viewer", "--email", email, "--name", "Cuenta E2E"], "contraseña-inicial-123\n");
  cli(["grant-module-role", "--email", email, "--module", "commercial", "--role", "viewer"]);
  const printed = cli(["reset-password", "--email", email]);
  const temporary = printed.trim().split(" ").at(-1);
  expect(temporary).toMatch(/^[A-Za-z0-9]{4}(-[A-Za-z0-9]{4}){3}$/);
  return temporary!;
}

async function loginIn(browser: Browser, email: string, password: string) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Contraseña").fill(password);
  await page.getByRole("button", { name: "Ingresar" }).click();
  return { context, page };
}

test("temporary password: forced change on first login, then the app", async ({ page }, testInfo) => {
  const email = `e2e-temp-${testInfo.project.name}-${Date.now()}@districom.local`;
  const temporary = userWithTemporaryPassword(email);

  await page.goto("/login?next=/configuracion");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Contraseña").fill(temporary);
  await page.getByRole("button", { name: "Ingresar" }).click();
  await expect(page.getByRole("heading", { name: "Elegí tu contraseña" })).toBeVisible();

  // Nothing else is reachable: not through the screens, not through the API.
  await page.goto("/comercial");
  await expect(page.getByRole("heading", { name: "Elegí tu contraseña" })).toBeVisible();
  const blocked = await page.request.get("/api/v1/employees");
  expect(blocked.status()).toBe(403);
  expect((await blocked.json()).code).toBe("password_change_required");

  await page.getByLabel("Contraseña temporal").fill(temporary);
  await page.getByLabel("Contraseña nueva", { exact: true }).fill(NEW_PASSWORD);
  await page.getByLabel("Repetí la contraseña nueva").fill(NEW_PASSWORD);
  await page.getByRole("button", { name: "Cambiar contraseña" }).click();
  await expect(page.getByText("Todavía no hay datos comerciales")).toBeVisible();

  // The temporary password no longer works.
  const old = await page.request.post("/api/v1/auth/login", {
    data: { email, password: temporary },
    headers: { "X-Requested-With": "e2e" },
  });
  expect(old.status()).toBe(401);
});

test("Mi cuenta: sessions with their network, closing another one", async ({ browser }, testInfo) => {
  const email = `e2e-sessions-${testInfo.project.name}-${Date.now()}@districom.local`;
  const temporary = userWithTemporaryPassword(email);
  const first = await loginIn(browser, email, temporary);
  await first.page.getByLabel("Contraseña temporal").fill(temporary);
  await first.page.getByLabel("Contraseña nueva", { exact: true }).fill(NEW_PASSWORD);
  await first.page.getByLabel("Repetí la contraseña nueva").fill(NEW_PASSWORD);
  await first.page.getByRole("button", { name: "Cambiar contraseña" }).click();
  await expect(first.page.getByText("Todavía no hay datos comerciales")).toBeVisible();

  const second = await loginIn(browser, email, NEW_PASSWORD);
  await expect(second.page.getByText("Todavía no hay datos comerciales")).toBeVisible();

  await first.page.getByRole("link", { name: "Mi cuenta" }).click();
  await expect(first.page.getByRole("heading", { name: "Mi cuenta" })).toBeVisible();
  const table = first.page.getByRole("table", { name: "Sesiones abiertas" });
  // One close button per session (on a phone the stacked table hides its header row).
  const closeButtons = table.getByRole("button", { name: /^Cerrar/ });
  await expect(closeButtons).toHaveCount(2);
  await expect(table.getByText("Esta sesión", { exact: true })).toBeVisible();
  // Through the Next.js server the backend sees a local address: «El servidor».
  await expect(table.getByText("El servidor").first()).toBeVisible();

  await first.page.getByRole("button", { name: "Cerrar la otra sesión" }).click();
  await expect(closeButtons).toHaveCount(1);
  await second.page.reload();
  await expect(second.page).toHaveURL(/\/login/);

  await first.context.close();
  await second.context.close();
});

test("a change sent from another origin is refused", async ({ page }) => {
  const response = await page.request.post("/api/v1/auth/login", {
    data: { email: "nadie@districom.local", password: "cualquier-cosa-123" },
    headers: { "X-Requested-With": "e2e", Origin: "https://evil.example.com" },
  });
  expect(response.status()).toBe(403);
  expect((await response.json()).code).toBe("origin_not_allowed");
});
