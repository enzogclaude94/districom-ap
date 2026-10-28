import { expect, test, type Page } from "@playwright/test";

import { login } from "./helpers";
import { api, day, driver, openSettlement } from "./settlement-setup";

/**
 * R9-2, «Cierre del día»: a settlement closed by API with business date yesterday shows in yesterday's close of the
 * cash box it entered, with its settlement; the CSV is the same view and the printable page hides the app's frame.
 * Other specs move the same boxes on the same day, so the assertions read only what this test created.
 */

async function closedSettlement(page: Page, name: string) {
  const driverId = await driver(page.request, name);
  const id = await openSettlement(page.request, driverId, "100000");
  const accounts = await api<{ id: number; code: string | null }[]>(page.request, "GET", "/settlements/accounts");
  const box = accounts.find((a) => a.code === "main_cash")!.id;
  await api(page.request, "PUT", `/settlements/deliveries/${id}/count`, {
    lines: [{ denomination_code: "ARS_10000", quantity: 10, destination_account_id: box }],
  });
  const preview = await api<{ preview_token: string }>(
    page.request,
    "GET",
    `/settlements/deliveries/${id}/closing-preview`,
  );
  await api(page.request, "POST", `/settlements/deliveries/${id}/close`, {
    version: 1,
    preview_token: preview.preview_token,
    allocations: [],
    confirm: true,
  });
  return { id, box };
}

test("yesterday's close shows the settlement in its cash box, downloads as CSV and prints", async ({ page }) => {
  await login(page, "/rendiciones");
  const name = `Repartidor R9-2 ${Date.now()}${Math.floor(Math.random() * 1000)}`;
  const { id, box } = await closedSettlement(page, name);

  await page.goto(`/rendiciones/cierre-del-dia?fecha=${day(-1)}`);
  await expect(page.getByRole("heading", { name: "Cierre del día" })).toBeVisible();
  await expect(page.getByLabel("Fecha de negocio")).toHaveValue(day(-1));
  const card = page.getByTestId(`account-${box}`);
  await expect(card).toContainText("Caja principal");
  await expect(card.getByTestId("category-SETTLEMENT_CLOSE_DELIVERY")).toContainText("Cierres de rendición de reparto");
  await expect(page.getByTestId("limitation")).toContainText("Arqueo físico: No registrado.");
  await expect(page.getByTestId("invariant-error")).toHaveCount(0);
  const row = page.getByTestId(`settlement-${id}`);
  await expect(row).toContainText(name);
  await expect(row).toContainText("Cerrada");
  await expect(row).toContainText("$ 100.000,00");

  // No side scroll on a phone.
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);

  // The CSV of the same day, with the session.
  const csv = await page.request.get(`/api/v1/settlements/daily-close.csv?on=${day(-1)}`);
  expect(csv.ok()).toBeTruthy();
  expect(csv.headers()["content-type"]).toContain("text/csv");
  const text = await csv.text();
  expect(text).toContain("Arqueo físico;No registrado");
  expect(text).toContain(`Reparto · ${name} · Rendición 1;Reparto;Cerrada;Esperado;;100000,00`);
  await expect(page.getByRole("link", { name: "Descargar CSV" })).toHaveAttribute(
    "href",
    `/api/v1/settlements/daily-close.csv?on=${day(-1)}`,
  );

  // Printable: the report without the app's frame nor the controls.
  await page.emulateMedia({ media: "print" });
  await expect(page.getByRole("banner")).toBeHidden();
  await expect(page.getByRole("button", { name: "Imprimir" })).toBeHidden();
  await expect(page.getByTestId("daily-close")).toBeVisible();
  await expect(page.getByTestId("daily-close")).toContainText("Cierre del día");
  await page.emulateMedia({ media: "screen" });

  // Another date asks again: two days ago this settlement is not there.
  await page.getByLabel("Fecha de negocio").fill(day(-2));
  await expect(page.getByTestId(`settlement-${id}`)).toHaveCount(0);
});

test("it is in the Tesorería menu", async ({ page, isMobile }) => {
  test.skip(isMobile, "The menu is the same; on a phone it opens behind a button.");
  await login(page, "/rendiciones");
  await page.getByRole("link", { name: "Cierre del día" }).click();
  await expect(page).toHaveURL(/\/rendiciones\/cierre-del-dia$/);
  await expect(page.getByRole("heading", { name: "Cajas y bancos" })).toBeVisible();
});
