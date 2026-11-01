import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { UserDetail } from "@/features/users/user-detail";
import { NewUserButton, UserList } from "@/features/users/user-list";

import { jsonResponse, renderWithQuery } from "./render";

vi.mock("next/navigation", () => ({
  usePathname: () => "/usuarios",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

const admin = { id: 1, email: "enzo@districom.test", full_name: "Enzo", role: "admin", modules: [], areas: [] };
const catalog = {
  modules: [
    { key: "commercial", name: "Inteligencia Comercial" },
    { key: "settlements", name: "Rendiciones" },
  ],
  areas: [{ key: "settlements.employee_finance", name: "Empleados", module_key: "settlements" }],
};
const ana = {
  id: 7,
  email: "ana@districom.test",
  full_name: "Ana Pérez",
  role: "viewer",
  active: true,
  must_change_password: false,
  last_login_at: "2026-10-30T12:00:00Z",
  password_changed_at: null,
  created_at: "2026-10-29T12:00:00Z",
  modules: [],
  areas: [],
  open_sessions: 1,
  sessions: [
    {
      id: 31,
      created_at: "2026-10-30T12:00:00Z",
      last_seen_at: "2026-10-30T12:30:00Z",
      expires_at: "2026-11-06T12:00:00Z",
      ip: "100.101.102.103",
      access_origin: "tailscale",
      user_agent: "Mozilla/5.0 (Windows NT 10.0) Chrome/130.0 Safari/537.36",
    },
  ],
  activity: [
    {
      occurred_at: "2026-10-30T12:00:00Z",
      event_type: "LOGIN",
      actor_id: 7,
      actor_name: "Ana Pérez",
      payload: { ip: "100.101.102.103", access_origin: "tailscale" },
    },
  ],
};
const temporary = {
  user: { ...ana, must_change_password: true },
  temporary_password: "abcd-efgh-jkmn-pqrs",
  expires_at: "2026-11-02T12:00:00Z",
};

/** Answers by URL and method, so the order of the component's requests does not matter. */
function api(routes: Record<string, unknown>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const key = `${init?.method ?? "GET"} ${String(input)}`;
    const match = Object.keys(routes).find((route) => key.startsWith(route));
    if (!match) throw new Error(`unexpected ${key}`);
    return jsonResponse(200, routes[match]);
  });
}

describe("UserList", () => {
  it("lists users with their role, access and state", async () => {
    api({
      "GET /api/v1/users?": {
        items: [
          { ...ana, modules: [{ key: "settlements", name: "Rendiciones", role: "operator" }] },
          { ...ana, id: 1, full_name: "Enzo", role: "admin", must_change_password: true },
        ],
        total: 2,
      },
    });
    renderWithQuery(<UserList />);
    const table = await screen.findByRole("table", { name: "Usuarios" });
    expect(within(table).getByRole("link", { name: "Ana Pérez" })).toHaveAttribute("href", "/usuarios/7");
    expect(within(table).getByText("Rendiciones")).toBeInTheDocument();
    expect(within(table).getByText("Todos los módulos")).toBeInTheDocument();
    expect(within(table).getByText("Contraseña temporal")).toBeInTheDocument();
  });

  it("creates a user and shows the temporary password once", async () => {
    const fetchMock = api({ "POST /api/v1/users": temporary, "GET /api/v1/users": { items: [], total: 0 } });
    renderWithQuery(<NewUserButton />);
    await userEvent.click(screen.getByRole("button", { name: "Nuevo usuario" }));
    await userEvent.type(screen.getByLabelText("Nombre y apellido"), "Ana Pérez");
    await userEvent.type(screen.getByLabelText("Email"), "ana@districom.test");
    await userEvent.click(screen.getByRole("button", { name: "Crear usuario" }));
    expect(await screen.findByLabelText("Contraseña temporal")).toHaveTextContent("abcd-efgh-jkmn-pqrs");
    expect(screen.getByText(/solo esta vez/)).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      email: "ana@districom.test",
      full_name: "Ana Pérez",
      role: "viewer",
    });
  });
});

describe("UserDetail", () => {
  it("shows access, sessions with their network, and activity", async () => {
    api({ "GET /api/v1/auth/me": admin, "GET /api/v1/users/access-catalog": catalog, "GET /api/v1/users/7": ana });
    renderWithQuery(<UserDetail userId={7} />);
    expect(await screen.findByRole("heading", { name: "Ana Pérez" })).toBeInTheDocument();
    const sessions = screen.getByRole("table", { name: "Sesiones del usuario" });
    expect(within(sessions).getByText("Acceso remoto (Tailscale)")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Actividad de la cuenta" })).toHaveTextContent("Ingresó");
    // An area needs its module: disabled until the user has a role in Rendiciones.
    expect(await screen.findByLabelText("Empleados")).toBeDisabled();
    expect(screen.getByLabelText("Rendiciones")).toBeEnabled();
  });

  it("gives a module role", async () => {
    const fetchMock = api({
      "GET /api/v1/auth/me": admin,
      "GET /api/v1/users/access-catalog": catalog,
      "GET /api/v1/users/7": ana,
      "PUT /api/v1/users/7/access": {
        ...ana,
        modules: [{ key: "settlements", name: "Rendiciones", role: "operator" }],
      },
    });
    renderWithQuery(<UserDetail userId={7} />);
    await userEvent.selectOptions(await screen.findByLabelText("Rendiciones"), "operator");
    await waitFor(() => expect(screen.getByLabelText("Empleados")).toBeEnabled());
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(JSON.parse(String(put?.[1]?.body))).toEqual({ key: "settlements", role: "operator" });
  });

  it("resets the password after a confirmation and shows the temporary one", async () => {
    api({
      "GET /api/v1/auth/me": admin,
      "GET /api/v1/users/access-catalog": catalog,
      "GET /api/v1/users": ana,
      "POST /api/v1/users/7/reset-password": temporary,
    });
    renderWithQuery(<UserDetail userId={7} />);
    await userEvent.click(await screen.findByRole("button", { name: "Resetear contraseña" }));
    expect(screen.getByText(/se cierran todas sus sesiones/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Generar contraseña temporal" }));
    expect(await screen.findByLabelText("Contraseña temporal")).toHaveTextContent("abcd-efgh-jkmn-pqrs");
  });

  it("shows the backend's refusal", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const key = `${init?.method ?? "GET"} ${String(input)}`;
      if (key.startsWith("POST")) {
        return jsonResponse(409, {
          code: "last_admin",
          message: "Es el último administrador activo.",
          details: null,
          request_id: "r",
        });
      }
      if (key.includes("access-catalog")) return jsonResponse(200, catalog);
      if (key.includes("auth/me")) return jsonResponse(200, admin);
      return jsonResponse(200, ana);
    });
    renderWithQuery(<UserDetail userId={7} />);
    await userEvent.click(await screen.findByRole("button", { name: "Desactivar" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Desactivar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Es el último administrador activo.");
  });

  it("on one's own account there is no reset nor deactivation", async () => {
    api({
      "GET /api/v1/auth/me": { ...admin, id: 7 },
      "GET /api/v1/users/access-catalog": catalog,
      "GET /api/v1/users/7": ana,
    });
    renderWithQuery(<UserDetail userId={7} />);
    expect(await screen.findByText(/Es tu cuenta/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resetear contraseña" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Desactivar" })).not.toBeInTheDocument();
  });
});
