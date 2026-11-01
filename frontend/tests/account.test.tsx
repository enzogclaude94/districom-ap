import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/layout/app-shell";
import { deviceLabel, OwnSessions } from "@/features/auth/own-sessions";
import { PasswordForm } from "@/features/auth/password-form";
import { MODULES } from "@/modules/registry";

import { jsonResponse, renderWithQuery } from "./render";

const router = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/comercial",
  useRouter: () => router,
}));

const user = { id: 1, email: "ana@districom.test", full_name: "Ana", role: "admin", modules: [], areas: [] };

async function fillPassword(current: string, next: string, confirm = next) {
  await userEvent.type(screen.getByLabelText(/Contraseña (actual|temporal)/), current);
  await userEvent.type(screen.getByLabelText("Contraseña nueva"), next);
  await userEvent.type(screen.getByLabelText("Repetí la contraseña nueva"), confirm);
  await userEvent.click(screen.getByRole("button", { name: "Cambiar contraseña" }));
}

describe("PasswordForm", () => {
  it("checks length and confirmation before calling the API", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    renderWithQuery(<PasswordForm />);
    await fillPassword("actual", "corta");
    expect(await screen.findByText("Usá al menos 12 caracteres.")).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("Contraseña nueva"));
    await userEvent.type(screen.getByLabelText("Contraseña nueva"), "una-frase-bien-larga");
    await userEvent.click(screen.getByRole("button", { name: "Cambiar contraseña" }));
    expect(await screen.findByText("Las contraseñas no coinciden.")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends the change and confirms that the other sessions were closed", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, user));
    renderWithQuery(<PasswordForm />);
    await fillPassword("la-contraseña-actual", "una-frase-bien-larga");
    expect(await screen.findByRole("status")).toHaveTextContent("Tus otras sesiones se cerraron.");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/auth/password");
    expect(JSON.parse(String(init?.body))).toEqual({
      current_password: "la-contraseña-actual",
      new_password: "una-frase-bien-larga",
    });
  });

  it("shows the backend's reason", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(422, {
        code: "invalid_current_password",
        message: "La contraseña actual no es correcta.",
        details: null,
        request_id: "r1",
      }),
    );
    renderWithQuery(<PasswordForm />);
    await fillPassword("mal", "una-frase-bien-larga");
    expect(await screen.findByRole("alert")).toHaveTextContent("La contraseña actual no es correcta.");
  });
});

describe("AppShell with a temporary password", () => {
  it("shows only the password change, then the app", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse(200, { ...user, must_change_password: true }))
      .mockResolvedValueOnce(
        jsonResponse(200, {
          ...user,
          must_change_password: false,
          modules: [{ key: "commercial", name: "Inteligencia Comercial", role: "admin" }],
        }),
      );
    renderWithQuery(
      <AppShell modules={MODULES}>
        <p>contenido</p>
      </AppShell>,
    );
    expect(await screen.findByRole("heading", { name: "Elegí tu contraseña" })).toBeInTheDocument();
    expect(screen.queryByText("contenido")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Contraseña temporal")).toBeInTheDocument();
    await fillPassword("abcd-efgh-jkmn-pqrs", "una-frase-bien-larga");
    expect(await screen.findByText("contenido")).toBeInTheDocument();
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/auth/password");
  });
});

const sessions = [
  {
    id: 7,
    created_at: "2026-10-30T12:00:00Z",
    last_seen_at: "2026-10-30T13:00:00Z",
    expires_at: "2026-11-06T12:00:00Z",
    ip: "192.168.1.25",
    access_origin: "lan",
    user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
    current: true,
  },
  {
    id: 9,
    created_at: "2026-10-29T12:00:00Z",
    last_seen_at: "2026-10-30T11:00:00Z",
    expires_at: "2026-11-05T12:00:00Z",
    ip: "100.101.102.103",
    access_origin: "tailscale",
    user_agent: "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36",
    current: false,
  },
];

describe("OwnSessions", () => {
  it("lists the sessions with network and IP, and closes another one", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse(200, sessions))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(jsonResponse(200, sessions.slice(0, 1)));
    renderWithQuery(<OwnSessions />);
    const table = await screen.findByRole("table", { name: "Sesiones abiertas" });
    expect(within(table).getByText("Red del local")).toBeInTheDocument();
    expect(within(table).getByText("Acceso remoto (Tailscale)")).toBeInTheDocument();
    expect(within(table).getByText("100.101.102.103")).toBeInTheDocument();
    expect(within(table).getByText("Esta sesión")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Cerrar la sesión de Chrome en Android/ }));
    await waitFor(() => expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/auth/sessions/9/revoke"));
    await waitFor(() => expect(screen.queryByText("Acceso remoto (Tailscale)")).not.toBeInTheDocument());
    expect(router.replace).not.toHaveBeenCalled();
  });

  it("closing this session goes to the login", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse(200, sessions))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    renderWithQuery(<OwnSessions />);
    await userEvent.click(await screen.findByRole("button", { name: "Cerrar esta sesión" }));
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/login"));
  });

  it("closes all the other sessions", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse(200, sessions))
      .mockResolvedValueOnce(jsonResponse(200, { revoked: 1 }))
      .mockResolvedValueOnce(jsonResponse(200, sessions.slice(0, 1)));
    renderWithQuery(<OwnSessions />);
    await userEvent.click(await screen.findByRole("button", { name: "Cerrar la otra sesión" }));
    await waitFor(() => expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/auth/sessions/revoke-others"));
  });
});

describe("deviceLabel", () => {
  it.each([
    [sessions[0].user_agent, "Chrome en Windows"],
    [sessions[1].user_agent, "Chrome en Android"],
    [
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 Version/17.0 Safari/605.1.15",
      "Safari en macOS",
    ],
    [null, "Dispositivo desconocido"],
  ])("%s → %s", (agent, label) => {
    expect(deviceLabel(agent)).toBe(label);
  });
});
