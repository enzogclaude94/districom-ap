import { expect, test, type Page } from "@playwright/test";

import { login } from "./helpers";
import { api } from "./settlement-setup";

const operatorEmail = process.env.E2E_OPERATOR_EMAIL ?? "e2e-operator@districom.local";
const operatorPassword = process.env.E2E_OPERATOR_PASSWORD ?? "operator-password-123";

async function noHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

/** Bocas (platform) and their responsables (Rendiciones), R7-1, on desktop and on the phone. */
test("a platform admin registers a boca, names two responsables, ends one and deactivates it; an operator only consults", async ({
  page,
}, testInfo) => {
  const mobile = testInfo.project.name === "mobile";
  const stamp = `${String(Date.now()).slice(-6)}${mobile ? "M" : "D"}`;
  const code = `E2E-${stamp}`;
  const visible = (text: string) => page.getByText(text, { exact: true }).filter({ visible: true });

  await login(page, "/bocas");
  await expect(page.getByRole("heading", { name: "Bocas de expendio" })).toBeVisible();
  await api(page.request, "POST", "/employees", { full_name: `Ana Boca ${stamp}`, hire_date: null, phone: null });
  await api(page.request, "POST", "/employees", { full_name: `Beto Boca ${stamp}`, hire_date: null, phone: null });

  // 1. create, with the code written in lower case: stored in upper case
  await page.getByRole("button", { name: "Nueva boca" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Código").fill(code.toLowerCase());
  await dialog.getByLabel("Nombre").fill(`Boca ${stamp}`);
  await dialog.getByLabel("Descripción (opcional)").fill("Boca de prueba");
  await dialog.getByRole("button", { name: "Crear boca" }).click();
  await expect(page.getByRole("status")).toHaveText(`Boca ${code} registrada.`);
  await expect(visible(code)).toBeVisible();
  await noHorizontalScroll(page);

  // 2. correct the name; the code is not editable
  await page
    .getByRole("button", { name: `Corregir ${code}` })
    .filter({ visible: true })
    .click();
  await expect(dialog.getByLabel("Código")).toBeDisabled();
  await dialog.getByLabel("Nombre").fill(`Boca ${stamp} corregida`);
  await dialog.getByRole("button", { name: "Guardar" }).click();
  await expect(visible(`Boca ${stamp} corregida`)).toBeVisible();

  // 3. its page: two responsables at once
  await page
    .getByRole("link", { name: `Ficha de ${code}` })
    .filter({ visible: true })
    .click();
  await expect(page.getByRole("heading", { name: `${code} · Boca ${stamp} corregida` })).toBeVisible();
  const card = page.getByTestId("outlet-responsibilities");
  for (const name of [`Ana Boca ${stamp}`, `Beto Boca ${stamp}`]) {
    await card.getByRole("button", { name: "Sumar responsable" }).click();
    await dialog.getByLabel("Empleado").selectOption({ label: name });
    await dialog.getByRole("button", { name: "Sumar responsable" }).click();
    await expect(page.getByRole("status")).toHaveText("Responsable sumado.");
  }
  const today = page.getByRole("region", { name: "Responsables del día" });
  await expect(today.getByText(`Ana Boca ${stamp}`).filter({ visible: true })).toBeVisible();
  await expect(today.getByText(`Beto Boca ${stamp}`).filter({ visible: true })).toBeVisible();

  // 4. the same employee twice in the same boca: the backend refuses it
  await card.getByRole("button", { name: "Sumar responsable" }).click();
  await dialog.getByLabel("Empleado").selectOption({ label: `Ana Boca ${stamp}` });
  await dialog.getByRole("button", { name: "Sumar responsable" }).click();
  await expect(dialog.getByRole("alert")).toContainText("ya es responsable de esta boca");
  await dialog.getByRole("button", { name: "Cancelar" }).click();

  // 5. end Ana's, once, with a reason
  await today
    .getByRole("button", { name: `Terminar Ana Boca ${stamp}` })
    .filter({ visible: true })
    .click();
  await dialog.getByLabel("Motivo").fill("Pasó a reparto");
  await dialog.getByRole("button", { name: "Terminar" }).click();
  await expect(page.getByRole("status")).toContainText("quedó terminada");
  const history = page.getByRole("region", { name: "Historia de responsables" });
  await expect(history.getByText(/Terminada: Pasó a reparto/).filter({ visible: true })).toBeVisible();
  await expect(
    history.getByRole("button", { name: `Terminar Ana Boca ${stamp}` }).filter({ visible: true }),
  ).toHaveCount(0);
  await noHorizontalScroll(page);

  // 6. deactivate, with its warning: no new responsable, the open one still ends
  await page.goto("/bocas");
  await page
    .getByRole("button", { name: `Desactivar ${code}` })
    .filter({ visible: true })
    .click();
  await expect(dialog).toContainText("no puede abrir rendiciones nuevas ni sumar responsables");
  await dialog.getByRole("button", { name: "Desactivar" }).click();
  await expect(page.getByRole("status")).toContainText(`${code} quedó desactivada`);
  await expect(visible(code)).toHaveCount(0);
  await page.getByRole("button", { name: "Todas" }).click();
  await page
    .getByRole("link", { name: `Ficha de ${code}` })
    .filter({ visible: true })
    .click();
  await expect(card).toContainText("La boca está desactivada: no suma responsables nuevos");
  await expect(card.getByRole("button", { name: "Sumar responsable" })).toHaveCount(0);
  await expect(
    today.getByRole("button", { name: `Terminar Beto Boca ${stamp}` }).filter({ visible: true }),
  ).toBeVisible();

  // 7. an operator of Rendiciones consults bocas and responsables, administers nothing
  await page.context().clearCookies();
  await login(page, "/bocas", operatorEmail, operatorPassword);
  await page.getByRole("button", { name: "Todas" }).click();
  await expect(visible(code)).toBeVisible();
  await expect(page.getByText("Solo un administrador de DistriCom las modifica.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Nueva boca" })).toHaveCount(0);
  await page
    .getByRole("link", { name: `Ficha de ${code}` })
    .filter({ visible: true })
    .click();
  await expect(today.getByText(`Beto Boca ${stamp}`).filter({ visible: true })).toBeVisible();
  await expect(card.getByRole("button", { name: /Sumar responsable|Terminar/ })).toHaveCount(0);
  await noHorizontalScroll(page);
});
