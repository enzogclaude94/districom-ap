import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ExpenseCategories } from "@/modules/settlements/config/expense-categories";
import type { ExpenseCategory } from "@/modules/settlements/delivery/api";

import { jsonResponse, renderWithQuery } from "./render";

type Role = "viewer" | "operator" | "admin";
type Handler = (init: RequestInit | undefined) => Response;

const me = (role: Role) => ({
  id: 9,
  email: "u@d.c",
  full_name: "Usuario",
  role: "viewer",
  modules: [{ key: "settlements", name: "Rendiciones", role }],
});

const CATEGORIES: ExpenseCategory[] = [
  { id: 1, name: "Gomería", active: false },
  { id: 2, name: "Peajes", active: true },
];

function mockApi(role: Role, routes: Record<string, Handler> = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const key = `${init?.method ?? "GET"} ${url.pathname.replace("/api/v1", "")}`;
    if (key === "GET /auth/me") return jsonResponse(200, me(role));
    if (key === "GET /settlements/expense-categories") {
      expect(url.searchParams.get("include_inactive")).toBe("true");
      return jsonResponse(200, CATEGORIES);
    }
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

describe("expense categories configuration", () => {
  it("lists active and deactivated categories; only an admin can change them", async () => {
    mockApi("operator");
    renderWithQuery(<ExpenseCategories />);
    const row = (await screen.findByText("Gomería")).closest("tr")!;
    expect(within(row).getByText("Desactivada")).toBeInTheDocument();
    expect(screen.getByText("Peajes").closest("tr")).toHaveTextContent("Activa");
    expect(screen.getByText(/Solo un administrador las modifica/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Crear categoría" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Renombrar" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Desactivar|Activar/ })).not.toBeInTheDocument();
  });

  it("creates a category with a normalized name and explains a duplicate", async () => {
    const spy = mockApi("admin", {
      "POST /settlements/expense-categories": (init) => {
        const body = JSON.parse(String(init?.body)) as { name: string };
        return body.name === "Peajes"
          ? jsonResponse(409, {
              code: "expense_category_name_taken",
              message: "Ya existe la categoría «Peajes».",
              details: { category_id: 2, active: true },
              request_id: null,
            })
          : jsonResponse(201, { id: 3, name: body.name, active: true });
      },
    });
    renderWithQuery(<ExpenseCategories />);
    const input = await screen.findByLabelText("Nueva categoría");
    await userEvent.click(screen.getByRole("button", { name: "Crear categoría" }));
    expect(screen.getByText("Ingresá el nombre de la categoría.")).toBeInTheDocument();

    await userEvent.type(input, "  Peajes ");
    await userEvent.click(screen.getByRole("button", { name: "Crear categoría" }));
    expect(await screen.findByText("Ya existe la categoría «Peajes».")).toBeInTheDocument();

    await userEvent.clear(input);
    await userEvent.type(input, "  Lavado   de  camión ");
    await userEvent.click(screen.getByRole("button", { name: "Crear categoría" }));
    expect(await screen.findByText("Categoría «Lavado de camión» creada.")).toBeInTheDocument();
    expect(sent(spy, "POST", "/settlements/expense-categories")).toEqual([
      { name: "Peajes" },
      { name: "Lavado de camión" },
    ]);
  });

  it("points to the list when the duplicate is a deactivated category", async () => {
    mockApi("admin", {
      "POST /settlements/expense-categories": () =>
        jsonResponse(409, {
          code: "expense_category_name_taken",
          message: "Ya existe la categoría «gomeria».",
          details: { category_id: 1, active: false },
          request_id: null,
        }),
    });
    renderWithQuery(<ExpenseCategories />);
    await userEvent.type(await screen.findByLabelText("Nueva categoría"), "gomeria");
    await userEvent.click(screen.getByRole("button", { name: "Crear categoría" }));
    expect(
      await screen.findByText("Ya existe la categoría «gomeria». Está desactivada: activala desde la lista."),
    ).toBeInTheDocument();
  });

  it("deactivates, activates and renames without deleting anything", async () => {
    const spy = mockApi("admin", {
      "PATCH /settlements/expense-categories/2": (init) =>
        jsonResponse(200, { id: 2, name: "Peajes", active: true, ...JSON.parse(String(init?.body)) }),
      "PATCH /settlements/expense-categories/1": (init) =>
        jsonResponse(200, { id: 1, name: "Gomería", active: false, ...JSON.parse(String(init?.body)) }),
    });
    renderWithQuery(<ExpenseCategories />);
    await userEvent.click(await screen.findByRole("button", { name: "Desactivar Peajes" }));
    expect(await screen.findByText("«Peajes» quedó desactivada.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Activar Gomería" }));
    expect(await screen.findByText("«Gomería» está activa otra vez.")).toBeInTheDocument();

    const peajes = screen.getByText("Peajes").closest("tr")!;
    await userEvent.click(within(peajes).getByRole("button", { name: "Renombrar" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.clear(within(dialog).getByLabelText("Nombre"));
    await userEvent.type(within(dialog).getByLabelText("Nombre"), " Peajes y   autopistas ");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar" }));
    expect(await screen.findByText("Categoría renombrada a «Peajes y autopistas».")).toBeInTheDocument();

    await waitFor(() =>
      expect(sent(spy, "PATCH", "/settlements/expense-categories/2")).toEqual([
        { active: false },
        { name: "Peajes y autopistas" },
      ]),
    );
    expect(sent(spy, "PATCH", "/settlements/expense-categories/1")).toEqual([{ active: true }]);
    expect(spy.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
  });
});
