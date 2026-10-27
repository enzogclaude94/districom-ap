import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DailyClose, DayAccount } from "@/modules/settlements/daily-close/api";
import {
  DailyCloseReport,
  DailyCloseView,
  INVARIANT_ERROR,
  PHYSICAL_COUNT_NOTE,
} from "@/modules/settlements/daily-close/daily-close";
import { SETTLEMENTS_NAV } from "@/modules/settlements/nav";

import { jsonResponse, renderWithQuery } from "./render";

const search = new URLSearchParams();
vi.mock("next/navigation", () => ({ useSearchParams: () => search }));

/** R9-2: «Cierre del día» shows what the backend calculated over the ledger, per cash box and bank, and never
 * presents itself as a physical count, a signed close or a bank reconciliation. */

function account(changes: Partial<DayAccount> = {}): DayAccount {
  return {
    account_id: 1,
    name: "Caja principal",
    kind: "CASH_BOX",
    active: true,
    opening_balance: "50000.00",
    inflows: "108000.00",
    outflows: "48000.00",
    net: "60000.00",
    closing_balance: "110000.00",
    reconciled: true,
    inflow_categories: [
      {
        key: "SETTLEMENT_CLOSE_DELIVERY",
        label: "Cierres de rendición de reparto",
        reverses: null,
        operations: 1,
        amount: "100000.00",
      },
      { key: "ADJUSTMENT", label: "Ajustes", reverses: null, operations: 1, amount: "8000.00" },
    ],
    outflow_categories: [
      {
        key: "REVERSAL:ADJUSTMENT",
        label: "Reverso de ajustes",
        reverses: "ADJUSTMENT",
        operations: 1,
        amount: "8000.00",
      },
      {
        key: "EMPLOYEE_RESERVED",
        label: "Movimiento de empleado (reservado)",
        reverses: null,
        operations: 2,
        amount: "40000.00",
      },
    ],
    ...changes,
  };
}

function view(changes: Partial<DailyClose> = {}): DailyClose {
  return {
    business_date: "2026-10-26",
    calculated_at: "2026-10-27T13:05:00Z",
    accounts: [
      account(),
      account({
        account_id: 3,
        name: "Banco",
        kind: "BANK",
        active: false,
        opening_balance: "0.00",
        inflows: "0.00",
        outflows: "0.00",
        net: "0.00",
        closing_balance: "0.00",
        inflow_categories: [],
        outflow_categories: [],
      }),
    ],
    totals: [
      {
        kind: "CASH_BOX",
        opening_balance: "50000.00",
        inflows: "108000.00",
        outflows: "48000.00",
        net: "60000.00",
        closing_balance: "110000.00",
      },
      {
        kind: "BANK",
        opening_balance: "0.00",
        inflows: "0.00",
        outflows: "0.00",
        net: "0.00",
        closing_balance: "0.00",
      },
    ],
    reconciled: true,
    employee_movements_reserved: true,
    settlements: {
      total: 3,
      open: 1,
      closed: 1,
      being_corrected: 1,
      without_expected: 1,
      store_sys_pending: 1,
      items: [
        {
          id: 10,
          type: "DELIVERY",
          name: "Juan Pérez",
          sequence_number: 1,
          financial_state: "BEING_CORRECTED",
          being_corrected: true,
          expected_missing: false,
          expected_update_pending: false,
          close_kind: "FINANCIAL",
          expected_amount: "100000.00",
          expected_origin: { source: "STORE_SYS", legacy: false, external_reference: "LIQ-77", revision: 2 },
          justified: "100000.00",
          difference: "0.00",
        },
        {
          id: 11,
          type: "OUTLET",
          name: "Boca CENTRO",
          sequence_number: 1,
          financial_state: "CLOSED",
          being_corrected: false,
          expected_missing: false,
          expected_update_pending: true,
          close_kind: "FINANCIAL",
          expected_amount: "80000.00",
          expected_origin: { source: "MANUAL", legacy: false, external_reference: null, revision: null },
          justified: "70000.00",
          difference: "-10000.00",
        },
        {
          id: 12,
          type: "DELIVERY",
          name: "Ana Ruiz",
          sequence_number: 2,
          financial_state: "NOT_CLOSED",
          being_corrected: false,
          expected_missing: true,
          expected_update_pending: false,
          close_kind: null,
          expected_amount: null,
          expected_origin: null,
          justified: null,
          difference: null,
        },
      ],
    },
    cheques: {
      portfolio_count: 1,
      portfolio_amount: "30000.00",
      in_transit_count: 0,
      in_transit_amount: "0.00",
      deposited_count: 0,
      deposited_amount: "0.00",
      credited_count: 1,
      credited_amount: "100000.00",
      rejected_count: 1,
      rejected_amount: "50000.00",
      returned_count: 0,
      returned_amount: "0.00",
    },
    ...changes,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  search.delete("fecha");
});

describe("Cierre del día (R9-2)", () => {
  it("is in the Tesorería menu", () => {
    const treasury = SETTLEMENTS_NAV.find((s) => s.label === "Tesorería");
    expect(treasury?.items?.map((i) => i.href)).toContain("/rendiciones/cierre-del-dia");
  });

  it("shows each account's opening, inflows, outflows, net and closing", () => {
    renderWithQuery(<DailyCloseReport data={view()} />);
    const box = screen.getByTestId("account-1");
    expect(box).toHaveTextContent("Saldo inicial contable$ 50.000,00");
    expect(box).toHaveTextContent("Ingresos del día$ 108.000,00");
    expect(box).toHaveTextContent("Egresos del día$ 48.000,00");
    expect(box).toHaveTextContent("Movimiento neto$ 60.000,00");
    expect(box).toHaveTextContent("Saldo final contable$ 110.000,00");
    expect(screen.getByTestId("totals-CASH_BOX")).toHaveTextContent("Total cajas");
    // An account inactive today still shows the day, with its state as a secondary mark.
    expect(screen.getByTestId("account-3")).toHaveTextContent("Hoy inactiva");
  });

  it("shows a reversal as its own line and the reserved employee movements without detail", () => {
    renderWithQuery(<DailyCloseReport data={view()} />);
    const reversal = screen.getByTestId("category-REVERSAL:ADJUSTMENT");
    expect(reversal).toHaveTextContent("Reverso de ajustes");
    expect(within(reversal).getByText("Reverso")).toBeInTheDocument();
    expect(screen.getByTestId("category-ADJUSTMENT")).toHaveTextContent("$ 8.000,00");
    expect(screen.getByTestId("category-EMPLOYEE_RESERVED")).toHaveTextContent(
      "Movimiento de empleado (reservado) (2 operaciones)$ 40.000,00",
    );
    expect(screen.getByTestId("limitation")).toHaveTextContent("suman en los totales");
  });

  it("says there is no physical count and never shows an arqueo difference", () => {
    renderWithQuery(<DailyCloseReport data={view()} />);
    expect(screen.getByTestId("limitation")).toHaveTextContent(PHYSICAL_COUNT_NOTE);
    expect(screen.queryByText(/diferencia contra arqueo/i)).not.toBeInTheDocument();
    expect(screen.queryByTestId("invariant-error")).not.toBeInTheDocument();
  });

  it("flags a reconciliation failure as an invariant error", () => {
    const broken = account({ reconciled: false });
    renderWithQuery(<DailyCloseReport data={view({ accounts: [broken], reconciled: false })} />);
    expect(screen.getByRole("alert")).toHaveTextContent(`${INVARIANT_ERROR} Cuentas: Caja principal.`);
    expect(screen.getByTestId("account-1")).toHaveTextContent("Error de invariante");
  });

  it("shows the settlements of the day with the close in effect and the Store Sys marks", () => {
    renderWithQuery(<DailyCloseReport data={view()} />);
    const counts = screen.getByTestId("settlement-counts");
    expect(counts).toHaveTextContent("En corrección1");
    expect(counts).toHaveTextContent("Sin esperado1");
    expect(counts).toHaveTextContent("Store Sys pendiente1");
    const corrected = screen.getByTestId("settlement-10");
    expect(corrected).toHaveTextContent("En corrección");
    expect(corrected).toHaveTextContent("Store Sys · Ref. LIQ-77 rev. 2");
    expect(corrected).toHaveTextContent("$ 100.000,00");
    expect(screen.getByTestId("settlement-11")).toHaveTextContent("-$ 10.000,00");
    expect(screen.getByTestId("settlement-11")).toHaveTextContent("Store Sys pendiente");
    const open = screen.getByTestId("settlement-12");
    expect(open).toHaveTextContent("Sin esperado");
    expect(open).toHaveTextContent("Sin cerrar");
    expect(screen.getByText(/nunca su borrador/)).toBeInTheDocument();
  });

  it("shows the cheques at the end of the day", () => {
    renderWithQuery(<DailyCloseReport data={view()} />);
    const cheques = screen.getByTestId("cheques-day");
    expect(cheques).toHaveTextContent("En cartera (1)$ 30.000,00");
    expect(cheques).toHaveTextContent("Acreditados en el día (1)$ 100.000,00");
    expect(cheques).toHaveTextContent("Rechazados en el día (1)$ 50.000,00");
  });

  it("loads the date of the address, downloads its CSV and prints it", async () => {
    search.set("fecha", "2026-10-26");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => jsonResponse(200, view()));
    const print = vi.spyOn(window, "print").mockImplementation(() => undefined);
    renderWithQuery(<DailyCloseView />);
    await screen.findByTestId("daily-close");
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/api/v1/settlements/daily-close?on=2026-10-26");
    expect(screen.getByRole("link", { name: "Descargar CSV" })).toHaveAttribute(
      "href",
      "/api/v1/settlements/daily-close.csv?on=2026-10-26",
    );
    await userEvent.click(screen.getByRole("button", { name: "Imprimir" }));
    expect(print).toHaveBeenCalled();
  });

  it("asks the backend again for another date", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => jsonResponse(200, view()));
    renderWithQuery(<DailyCloseView />);
    await screen.findByTestId("daily-close");
    const input = screen.getByLabelText("Fecha de negocio");
    await userEvent.clear(input);
    await userEvent.type(input, "2026-10-20");
    await waitFor(() =>
      expect(fetchMock.mock.calls.map((c) => String(c[0]))).toContain("/api/v1/settlements/daily-close?on=2026-10-20"),
    );
  });

  it("shows the error of the backend", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      jsonResponse(403, {
        code: "forbidden",
        message: "No tenés acceso a Rendiciones.",
        details: null,
        request_id: "r",
      }),
    );
    renderWithQuery(<DailyCloseView />);
    expect(await screen.findByText("No tenés acceso a Rendiciones.")).toBeInTheDocument();
  });
});
