import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/layout/app-shell";
import { SettingsTable } from "@/features/settings/settings-table";
import { isInModule } from "@/lib/modules";
import { MODULES } from "@/modules/registry";

import { jsonResponse, renderWithQuery } from "./render";

const navigation = vi.hoisted(() => ({ pathname: "/comercial" }));
vi.mock("next/navigation", () => ({
  usePathname: () => navigation.pathname,
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

const admin = (modules: unknown[]) => ({ id: 1, email: "a@b.c", full_name: "Ana", role: "admin", modules });
const commercialAccess = { key: "commercial", name: "Inteligencia Comercial", role: "admin" };

describe("module registry", () => {
  it("matches a module by its base path only", () => {
    const [commercial] = MODULES;
    expect(isInModule("/comercial", commercial)).toBe(true);
    expect(isInModule("/comercial/rutas", commercial)).toBe(true);
    expect(isInModule("/comercialx", commercial)).toBe(false);
    expect(isInModule("/configuracion", commercial)).toBe(false);
  });
});

describe("AppShell", () => {
  it("shows the active module navigation plus platform pages", async () => {
    navigation.pathname = "/comercial";
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, admin([commercialAccess])));
    renderWithQuery(
      <AppShell modules={MODULES}>
        <p>contenido</p>
      </AppShell>,
    );
    expect(await screen.findByText("Inteligencia Comercial")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Inicio" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Configuración" })).toHaveAttribute("href", "/configuracion");
    expect(screen.getByRole("link", { name: "Vehículos" })).toHaveAttribute("href", "/vehiculos");
    expect(screen.getByRole("link", { name: "Terceros" })).toHaveAttribute("href", "/terceros");
    expect(screen.getByText("Equipo y clientes")).toBeInTheDocument();
  });

  it("keeps the module navigation on platform pages", async () => {
    navigation.pathname = "/configuracion";
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, admin([commercialAccess])));
    renderWithQuery(
      <AppShell modules={MODULES}>
        <p>contenido</p>
      </AppShell>,
    );
    expect(await screen.findByRole("link", { name: "Configuración" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Inicio" })).not.toHaveAttribute("aria-current");
  });

  it("hides modules the user cannot access", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, admin([])));
    renderWithQuery(
      <AppShell modules={MODULES}>
        <p>contenido</p>
      </AppShell>,
    );
    expect(await screen.findByText("Sin módulos habilitados")).toBeInTheDocument();
    expect(screen.queryByText("contenido")).not.toBeInTheDocument();
  });
});

describe("SettingsTable", () => {
  it("renders one section per scope with labels from the API", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(200, [
        {
          scope: "platform",
          label: "General",
          settings: [{ key: "platform.currency", label: "Moneda", description: "", value: "ARS", is_default: true }],
        },
        {
          scope: "commercial",
          label: "Inteligencia Comercial",
          settings: [
            {
              key: "commercial.active_window_days",
              label: "Ventana de cliente activo (días)",
              description: "",
              value: 120,
              is_default: false,
            },
          ],
        },
      ]),
    );
    renderWithQuery(<SettingsTable />);
    expect(await screen.findByText("General")).toBeInTheDocument();
    expect(screen.getByText("Inteligencia Comercial")).toBeInTheDocument();
    expect(screen.getByText("Moneda")).toBeInTheDocument();
    expect(screen.getByText("commercial.active_window_days")).toBeInTheDocument();
    expect(screen.getByText("Personalizado")).toBeInTheDocument();
  });

  it("shows a list of objects (cash denominations) item by item", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(200, [
        {
          scope: "settlements",
          label: "Rendiciones",
          settings: [
            {
              key: "settlements.cash_denominations",
              label: "Denominaciones de efectivo",
              description: "",
              value: [
                { code: "ARS_20000", value: "20000.00", active: true, suggested_destination: "main_cash" },
                { code: "ARS_100", value: "100.00", active: true, suggested_destination: "change_cash" },
              ],
              is_default: true,
            },
          ],
        },
      ]),
    );
    renderWithQuery(<SettingsTable />);
    expect(
      await screen.findByText(
        "code: ARS_20000 · value: 20000.00 · active: true · suggested_destination: main_cash | " +
          "code: ARS_100 · value: 100.00 · active: true · suggested_destination: change_cash",
      ),
    ).toBeInTheDocument();
  });
});
