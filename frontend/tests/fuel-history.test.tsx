import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { VehicleDetail } from "@/features/vehicles/vehicle-detail";
import type { FuelLoadPage, FuelLoadRow } from "@/modules/settlements/fuel/api";
import { FuelHistory } from "@/modules/settlements/fuel/fuel-history";
import { correctionNote, liters, origin, pricePerLiter } from "@/modules/settlements/fuel/labels";
import { VehicleFuelLoads } from "@/modules/settlements/fuel/vehicle-fuel-loads";

import { jsonResponse, renderWithQuery } from "./render";

const search = vi.hoisted(() => ({ params: new URLSearchParams() }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => search.params,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

afterEach(() => {
  search.params = new URLSearchParams();
});

const row = (id: number, changes: Partial<FuelLoadRow> = {}): FuelLoadRow => ({
  id,
  expense_id: 100 + id,
  origin: "SETTLEMENT",
  state: "PUBLISHED",
  business_date: "2026-10-08",
  vehicle_id: 21,
  vehicle_plate: "AA 123 BB",
  vehicle_description: "Camión norte",
  vehicle_active: true,
  employee_id: 4,
  employee_name: "Pedro Gómez",
  employee_active: true,
  liters: "10.000",
  price_per_liter: "2000.0000",
  amount: "20000.00",
  odometer_km: 120500,
  settlement_id: 7,
  settlement_sequence: 2,
  settlement_driver_name: "Pedro Gómez",
  paid_from_account_name: null,
  created_by_name: "Oscar Operador",
  created_at: "2026-10-08T15:00:00Z",
  in_effect: null,
  proposed_change: false,
  proposed_void: false,
  ...changes,
});

const DIRECT = row(2, {
  origin: "DIRECT",
  expense_id: 9,
  business_date: "2026-10-09",
  settlement_id: null,
  settlement_sequence: null,
  settlement_driver_name: null,
  paid_from_account_name: "Caja principal",
  employee_name: "Juan Pérez",
  employee_id: 5,
});

const pageOf = (items: FuelLoadRow[], summary: Partial<FuelLoadPage["summary"]> = {}): FuelLoadPage => ({
  items,
  total: items.length,
  limit: 25,
  offset: 0,
  summary: { liters: "1250.500", amount: "1875000.00", weighted_price_per_liter: "1499.40", ...summary },
});

const VEHICLES = [
  { id: 21, plate: "AA123BB", plate_display: "AA 123 BB", description: "Camión norte", active: true },
  { id: 22, plate: "AC456DE", plate_display: "AC 456 DE", description: "Utilitario viejo", active: false },
].map((v) => ({
  ...v,
  created_at: "2026-10-01T10:00:00Z",
  created_by_name: "Admin",
  updated_at: "2026-10-01T10:00:00Z",
  updated_by_name: "Admin",
}));

const EMPLOYEES = [
  { id: 4, full_name: "Pedro Gómez", active: true },
  { id: 5, full_name: "Juan Pérez", active: false },
];

const me = (modules: unknown[] = [{ key: "settlements", name: "Rendiciones", role: "viewer" }]) => ({
  id: 9,
  email: "u@d.c",
  full_name: "Usuario",
  role: "viewer",
  modules,
});

function mockApi(list: (params: URLSearchParams) => FuelLoadPage, user = me()) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = new URL(String(input), "http://localhost");
    const path = url.pathname.replace("/api/v1", "");
    if (path === "/auth/me") return jsonResponse(200, user);
    if (path === "/vehicles") return jsonResponse(200, VEHICLES);
    if (path === "/vehicles/21")
      return jsonResponse(200, {
        ...VEHICLES[0],
        history: [
          {
            id: 1,
            event_type: "plate_changed",
            occurred_at: "2026-10-02T10:00:00Z",
            user_name: "Admin",
            before: { plate: "AA123BC" },
            after: { plate: "AA123BB" },
          },
        ],
      });
    if (path === "/settlements/fuel-loads/employees") return jsonResponse(200, EMPLOYEES);
    if (path === "/settlements/fuel-loads") return jsonResponse(200, list(url.searchParams));
    return jsonResponse(404, { code: "not_found", message: "x", details: null, request_id: null });
  });
}

const listCalls = (spy: ReturnType<typeof mockApi>) =>
  spy.mock.calls
    .map(([input]) => new URL(String(input), "http://l"))
    .filter((url) => url.pathname === "/api/v1/settlements/fuel-loads")
    .map((url) => Object.fromEntries(url.searchParams));

describe("Combustible (R5-4)", () => {
  it("shows the summary of the backend at the top and one table row per load", async () => {
    mockApi(() => pageOf([row(1), DIRECT]));
    renderWithQuery(<FuelHistory />);
    const summary = await screen.findByTestId("fuel-summary");
    expect(summary).toHaveTextContent("1.250,500 l");
    expect(summary).toHaveTextContent("$ 1.875.000,00");
    expect(summary).toHaveTextContent("$ 1.499,40/l");
    const table = screen.getByTestId("fuel-table");
    const first = within(table).getByTestId("fuel-row-1");
    expect(first).toHaveTextContent("08/10/2026");
    expect(first).toHaveTextContent("AA 123 BB");
    expect(first).toHaveTextContent("Camión norte");
    expect(first).toHaveTextContent("10,000 l");
    expect(first).toHaveTextContent("$ 2.000,00/l");
    expect(first).toHaveTextContent("$ 20.000,00");
    expect(first).toHaveTextContent("120.500 km");
    expect(first).toHaveTextContent("Publicado");
    expect(first).toHaveTextContent("Oscar Operador");
  });

  it("links each origin to its own screen, never mixing them", async () => {
    mockApi(() => pageOf([row(1), DIRECT]));
    renderWithQuery(<FuelHistory />);
    const table = await screen.findByTestId("fuel-table");
    expect(within(table).getByRole("link", { name: "Rendición n.º 2 · 08/10/2026" })).toHaveAttribute(
      "href",
      "/rendiciones/repartos/7",
    );
    expect(within(table).getByRole("link", { name: "Gasto directo n.º 9 · Caja principal" })).toHaveAttribute(
      "href",
      "/rendiciones/cajas/gastos/9",
    );
  });

  it("shows a null weighted price as a dash", async () => {
    mockApi(() =>
      pageOf([row(1, { state: "OPEN_SETTLEMENT" })], { liters: "0", amount: "0.00", weighted_price_per_liter: null }),
    );
    renderWithQuery(<FuelHistory />);
    const summary = await screen.findByTestId("fuel-summary");
    expect(within(summary).getByText("Precio promedio").nextSibling).toHaveTextContent(/^—$/);
    expect(summary).toHaveTextContent("0,000 l");
    expect(screen.getByTestId("fuel-table")).toHaveTextContent("En rendición abierta");
  });

  it("labels every state and marks a reversed direct load, kept in the list", async () => {
    mockApi(() =>
      pageOf([
        row(1, { state: "OPEN_SETTLEMENT" }),
        row(2, { state: "IN_CORRECTION" }),
        row(3, { state: "VOIDED" }),
        { ...DIRECT, id: 4, state: "REVERSED" },
        row(5),
      ]),
    );
    renderWithQuery(<FuelHistory />);
    const table = await screen.findByTestId("fuel-table");
    for (const label of ["En rendición abierta", "En corrección", "Anulado", "Revertido", "Publicado"]) {
      expect(within(table).getByText(label)).toBeInTheDocument();
    }
    const reversed = within(table).getByTestId("fuel-row-4");
    expect(within(reversed).getByText("$ 20.000,00")).toHaveClass("line-through");
  });

  it("shows what a correction proposes next to the close in effect", () => {
    const correcting = row(1, {
      state: "IN_CORRECTION",
      liters: "8.000",
      price_per_liter: "2500.0000",
      proposed_change: true,
      in_effect: {
        vehicle_id: 21,
        vehicle_plate: "AA 123 BB",
        employee_id: 4,
        employee_name: "Pedro Gómez",
        liters: "10.000",
        price_per_liter: "2000.0000",
        amount: "20000.00",
      },
    });
    expect(correctionNote(correcting)).toBe(
      "Cierre vigente: 10,000 l · $ 2.000,00/l · AA 123 BB · Pedro Gómez. El cambio aplica al nuevo cierre.",
    );
    expect(correctionNote(row(2, { state: "IN_CORRECTION", proposed_change: true }))).toMatch(/Agregada/);
    expect(correctionNote(row(3, { state: "IN_CORRECTION", proposed_void: true }))).toMatch(/la anula/);
    expect(correctionNote(row(4))).toBeNull();
    expect(liters("1250.5")).toBe("1.250,500 l");
    expect(pricePerLiter("1234.5678")).toBe("$ 1.234,5678/l");
    expect(origin(DIRECT).href).toBe("/rendiciones/cajas/gastos/9");
  });

  it("marks an inactive vehicle and employee, still listed and offered by the filters", async () => {
    mockApi(() =>
      pageOf([row(1, { vehicle_id: 22, vehicle_plate: "AC 456 DE", vehicle_active: false, employee_active: false })]),
    );
    renderWithQuery(<FuelHistory />);
    const table = await screen.findByTestId("fuel-table");
    expect(table).toHaveTextContent("AC 456 DE (desactivado)");
    expect(table).toHaveTextContent("Pedro Gómez (inactivo)");
    await waitFor(() =>
      expect(screen.getByLabelText("Vehículo")).toHaveTextContent("AC 456 DE · Utilitario viejo (desactivado)"),
    );
    await waitFor(() => expect(screen.getByLabelText("Empleado")).toHaveTextContent("Juan Pérez (inactivo)"));
  });

  it("sends every filter to the backend and starts from the first page", async () => {
    const spy = mockApi(() => pageOf([row(1)]));
    renderWithQuery(<FuelHistory />);
    await screen.findByTestId("fuel-table");
    await waitFor(() => expect(screen.getByLabelText("Empleado")).toHaveTextContent("Juan Pérez"));
    await userEvent.selectOptions(screen.getByLabelText("Vehículo"), "22");
    await userEvent.selectOptions(screen.getByLabelText("Empleado"), "5");
    await userEvent.selectOptions(screen.getByLabelText("Origen"), "DIRECT");
    await userEvent.selectOptions(screen.getByLabelText("Estado"), "REVERSED");
    await userEvent.type(screen.getByLabelText("Desde"), "2026-10-01");
    await userEvent.type(screen.getByLabelText("Hasta"), "2026-10-09");
    await waitFor(() =>
      expect(listCalls(spy).at(-1)).toEqual({
        vehicle_id: "22",
        employee_id: "5",
        origin: "DIRECT",
        state: "REVERSED",
        date_from: "2026-10-01",
        date_to: "2026-10-09",
        limit: "25",
        offset: "0",
      }),
    );
  });

  it("starts filtered by the vehicle of the link", async () => {
    search.params = new URLSearchParams("vehiculo=21");
    const spy = mockApi(() => pageOf([row(1)]));
    renderWithQuery(<FuelHistory />);
    await screen.findByTestId("fuel-table");
    expect(listCalls(spy)[0]).toMatchObject({ vehicle_id: "21" });
    expect(screen.getByRole("button", { name: "Limpiar filtros" })).toBeInTheDocument();
  });

  it("pages through the backend", async () => {
    const spy = mockApi((params) => ({ ...pageOf([row(Number(params.get("offset")) + 1)]), total: 30 }));
    renderWithQuery(<FuelHistory />);
    await screen.findByText("1–1 de 30");
    await userEvent.click(screen.getByRole("button", { name: "Siguiente" }));
    await waitFor(() => expect(listCalls(spy).at(-1)).toMatchObject({ offset: "25" }));
  });

  it("tells no loads at all apart from no loads with these filters", async () => {
    mockApi(() => pageOf([], { liters: "0", amount: "0.00", weighted_price_per_liter: null }));
    renderWithQuery(<FuelHistory />);
    expect(await screen.findByText("No existen cargas de combustible")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Origen"), "DIRECT");
    expect(await screen.findByText("No existen cargas con estos filtros")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Limpiar filtros" }));
    expect(await screen.findByText("No existen cargas de combustible")).toBeInTheDocument();
  });

  it("shows cards for mobile with the same data, hidden on desktop", async () => {
    mockApi(() => pageOf([row(1), DIRECT]));
    renderWithQuery(<FuelHistory />);
    const cards = await screen.findByTestId("fuel-cards");
    expect(cards).toHaveClass("md:hidden");
    expect(screen.getByTestId("fuel-table")).toHaveClass("hidden", "md:block");
    const card = within(cards).getByTestId("fuel-card-2");
    expect(card).toHaveTextContent("Juan Pérez");
    expect(card).toHaveTextContent("$ 20.000,00");
    expect(within(card).getByRole("link", { name: "Gasto directo n.º 9 · Caja principal" })).toBeInTheDocument();
  });
});

describe("Vehicle page (R5-4)", () => {
  it("keeps the master's changes apart from the fuel loads, which reuse the history endpoint", async () => {
    const spy = mockApi(() => pageOf([row(1)]));
    renderWithQuery(
      <>
        <VehicleDetail vehicleId={21} />
        <VehicleFuelLoads vehicleId={21} />
      </>,
    );
    expect(await screen.findByText("Cambios del maestro")).toBeInTheDocument();
    expect(await screen.findByText("AA123BC → AA123BB")).toBeInTheDocument();
    expect(await screen.findByText("Cargas de combustible")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ver historial completo de combustible" })).toHaveAttribute(
      "href",
      "/rendiciones/combustible?vehiculo=21",
    );
    await screen.findByTestId("fuel-table");
    expect(listCalls(spy)[0]).toEqual({ vehicle_id: "21", limit: "10", offset: "0" });
  });

  it("shows no fuel to a user without Rendiciones", async () => {
    const spy = mockApi(() => pageOf([row(1)]), me([]));
    renderWithQuery(
      <>
        <VehicleDetail vehicleId={21} />
        <VehicleFuelLoads vehicleId={21} />
      </>,
    );
    expect(await screen.findByText("Cambios del maestro")).toBeInTheDocument();
    expect(screen.queryByText("Cargas de combustible")).not.toBeInTheDocument();
    expect(listCalls(spy)).toEqual([]);
  });
});
