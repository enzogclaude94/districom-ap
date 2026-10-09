import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { VehicleList } from "@/features/vehicles/vehicle-list";
import type { Vehicle } from "@/lib/api/vehicles";

import { jsonResponse, renderWithQuery } from "./render";

type Handler = (init: RequestInit | undefined) => Response;

const me = (role: "admin" | "viewer", settlements: "operator" | "admin" = "operator") => ({
  id: 9,
  email: "u@d.c",
  full_name: "Usuario",
  role,
  modules: [{ key: "settlements", name: "Rendiciones", role: settlements }],
});

const vehicle = (overrides: Partial<Vehicle>): Vehicle => ({
  id: 1,
  plate: "AB123CD",
  plate_display: "AB 123 CD",
  description: "Camión reparto norte",
  active: true,
  created_at: "2026-10-09T10:00:00Z",
  created_by_name: "Admin",
  updated_at: "2026-10-09T10:00:00Z",
  updated_by_name: "Admin",
  ...overrides,
});

const ACTIVE = [vehicle({})];
const ALL = [
  ...ACTIVE,
  vehicle({ id: 2, plate: "ABC123", plate_display: "ABC 123", description: "Utilitario", active: false }),
];

function mockApi(user: ReturnType<typeof me>, routes: Record<string, Handler> = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const key = `${init?.method ?? "GET"} ${url.pathname.replace("/api/v1", "")}`;
    if (key === "GET /auth/me") return jsonResponse(200, user);
    if (key === "GET /vehicles") return jsonResponse(200, url.searchParams.get("include_inactive") ? ALL : ACTIVE);
    const handler = routes[key];
    return handler
      ? handler(init)
      : jsonResponse(404, { code: "not_found", message: key, details: null, request_id: null });
  });
}

const sent = (spy: ReturnType<typeof mockApi>, method: string, path: string) =>
  spy.mock.calls
    .filter(([input, init]) => String(input) === `/api/v1${path}` && init?.method === method)
    .map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>);

describe("vehicles of the platform", () => {
  it("lets a Rendiciones admin consult but not administer them", async () => {
    mockApi(me("viewer", "admin"));
    renderWithQuery(<VehicleList />);
    const row = (await screen.findByText("AB 123 CD")).closest("tr")!;
    expect(row).toHaveTextContent("Camión reparto norte");
    expect(within(row).getByText("Activo")).toBeInTheDocument();
    expect(await screen.findByText(/Solo un administrador de DistriCom los modifica/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Nuevo vehículo" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Corregir|Desactivar|Activar/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Historia de AB 123 CD" })).toBeInTheDocument();
  });

  it("shows deactivated vehicles under «Todos»", async () => {
    mockApi(me("viewer"));
    renderWithQuery(<VehicleList />);
    await screen.findByText("AB 123 CD");
    expect(screen.queryByText("ABC 123")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Todos" }));
    const row = (await screen.findByText("ABC 123")).closest("tr")!;
    expect(within(row).getByText("Desactivado")).toBeInTheDocument();
  });

  it("creates a vehicle with a normalized plate and explains a duplicate", async () => {
    const spy = mockApi(me("admin"), {
      "POST /vehicles": (init) => {
        const body = JSON.parse(String(init?.body)) as { plate: string };
        return body.plate === "AB123CD"
          ? jsonResponse(409, {
              code: "vehicle_plate_taken",
              message: "La patente AB 123 CD ya es de otro vehículo (Camión reparto norte).",
              details: { vehicle_id: 1, active: true },
              request_id: null,
            })
          : jsonResponse(
              201,
              vehicle({ id: 3, plate: body.plate, plate_display: "AC 456 DE", description: "Camioneta" }),
            );
      },
    });
    renderWithQuery(<VehicleList />);
    await userEvent.click(await screen.findByRole("button", { name: "Nuevo vehículo" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Patente"), "ab-123-cd");
    await userEvent.type(within(dialog).getByLabelText("Descripción"), "  Otro   camión ");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear vehículo" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("ya es de otro vehículo");

    await userEvent.clear(within(dialog).getByLabelText("Patente"));
    await userEvent.type(within(dialog).getByLabelText("Patente"), "ac 456 de");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear vehículo" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Vehículo AC 456 DE registrado.");
    expect(sent(spy, "POST", "/vehicles")).toEqual([
      { plate: "AB123CD", description: "Otro camión" },
      { plate: "AC456DE", description: "Otro camión" },
    ]);
  });

  it("requires a description and a valid plate before sending", async () => {
    const spy = mockApi(me("admin"));
    renderWithQuery(<VehicleList />);
    await userEvent.click(await screen.findByRole("button", { name: "Nuevo vehículo" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Patente"), "AB/1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear vehículo" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("entre 5 y 10 letras o números");
    await userEvent.clear(within(dialog).getByLabelText("Patente"));
    await userEvent.type(within(dialog).getByLabelText("Patente"), "AB123CD");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear vehículo" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Indicá una descripción");
    expect(sent(spy, "POST", "/vehicles")).toEqual([]);
  });

  it("corrects only what changed and deactivates", async () => {
    const spy = mockApi(me("admin"), {
      "PATCH /vehicles/1": (init) => {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        return jsonResponse(200, vehicle({ ...body, active: body.active === undefined ? true : Boolean(body.active) }));
      },
    });
    renderWithQuery(<VehicleList />);
    await userEvent.click(await screen.findByRole("button", { name: "Corregir AB 123 CD" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.clear(within(dialog).getByLabelText("Descripción"));
    await userEvent.type(within(dialog).getByLabelText("Descripción"), "Camión Iveco");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: "Desactivar AB 123 CD" }));
    expect(await screen.findByRole("status")).toHaveTextContent("quedó desactivado");
    expect(sent(spy, "PATCH", "/vehicles/1")).toEqual([{ description: "Camión Iveco" }, { active: false }]);
  });

  it("shows the history of changes with who and when", async () => {
    mockApi(me("viewer"), {
      "GET /vehicles/1": () =>
        jsonResponse(200, {
          ...vehicle({ plate: "AB124CD", plate_display: "AB 124 CD" }),
          history: [
            {
              id: 1,
              event_type: "created",
              occurred_at: "2026-10-09T10:00:00Z",
              user_name: "Admin",
              before: null,
              after: { plate: "AB123CD", description: "Camión reparto norte", active: true },
            },
            {
              id: 2,
              event_type: "plate_changed",
              occurred_at: "2026-10-09T11:00:00Z",
              user_name: "Enzo",
              before: { plate: "AB123CD" },
              after: { plate: "AB124CD" },
            },
          ],
        }),
    });
    renderWithQuery(<VehicleList />);
    await userEvent.click(await screen.findByRole("button", { name: "Historia de AB 123 CD" }));
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByText("Patente corregida")).toBeInTheDocument();
    expect(within(dialog).getByText("AB123CD → AB124CD")).toBeInTheDocument();
    expect(within(dialog).getByText(/Enzo/)).toBeInTheDocument();
  });
});
