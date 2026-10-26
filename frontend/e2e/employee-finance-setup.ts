import { execFileSync } from "node:child_process";

import { expect, type APIRequestContext, type Page } from "@playwright/test";

import { login } from "./helpers";
import { api, driver, openSettlement } from "./settlement-setup";

/**
 * Setup shared by the R8 end-to-end specs (payrolls and payouts). A fresh platform admin, created with the CLI, holds
 * the protected area (ADR 010) only for these tests.
 */

export const AREA = "settlements.employee_finance";
const PASSWORD = "root-password-123";

function createPlatformAdmin(email: string) {
  execFileSync(
    "uv",
    ["run", "--directory", "../backend", "python", "-m", "app.cli", "create-admin", "--email", email, "--name", "Root"],
    { input: `${PASSWORD}\n`, stdio: ["pipe", "ignore", "inherit"] },
  );
}

export async function mainCash(request: APIRequestContext) {
  const accounts = await api<{ id: number; code: string | null }[]>(request, "GET", "/settlements/accounts");
  return accounts.find((a) => a.code === "main_cash")!.id;
}

/** A delivery settlement (yesterday) of $`expected`, counted `counted` in $10.000 bills into the main cash box; any
 * difference (`assigned`) goes to the driver's internal account. */
export async function settlement(request: APIRequestContext, driverId: number, expected: number, counted: number) {
  const id = await openSettlement(request, driverId, String(expected));
  const box = await mainCash(request);
  await api(request, "PUT", `/settlements/deliveries/${id}/count`, {
    lines: [{ denomination_code: "ARS_10000", quantity: counted / 10000, destination_account_id: box }],
  });
  const preview = await api<{ preview_token: string }>(request, "GET", `/settlements/deliveries/${id}/closing-preview`);
  const difference = Math.abs(expected - counted);
  await api(request, "POST", `/settlements/deliveries/${id}/close`, {
    version: 1,
    preview_token: preview.preview_token,
    allocations: difference ? [{ target: "EMPLOYEE", employee_id: driverId, amount: String(difference) }] : [],
    confirm: true,
  });
  return id;
}

export async function setUp(page: Page, tag: string) {
  const suffix = `${Date.now()}${Math.floor(Math.random() * 1000)}`;
  const email = `e2e-r8-${tag}-${suffix}@districom.local`;
  const name = `Empleado R8 ${tag} ${suffix}`;
  createPlatformAdmin(email);
  await login(page, "/rendiciones", email, PASSWORD);
  await api(page.request, "PUT", "/auth/access-grants", { email, key: AREA, role: "admin" });
  return { email, name, employeeId: await driver(page.request, name) };
}

export async function noSideScroll(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
}
