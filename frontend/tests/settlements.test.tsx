import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/layout/app-shell";
import { ModuleSelector } from "@/components/layout/module-selector";
import { MODULES } from "@/modules/registry";

import { jsonResponse, renderWithQuery } from "./render";

const navigation = vi.hoisted(() => ({ pathname: "/rendiciones", replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => navigation.pathname,
  useRouter: () => ({ replace: navigation.replace, push: vi.fn() }),
}));

const commercial = { key: "commercial", name: "Inteligencia Comercial", role: "viewer" };
const settlements = { key: "settlements", name: "Rendiciones", role: "operator" };
const user = (modules: unknown[], role = "viewer") => ({ id: 2, email: "o@b.c", full_name: "Olga", role, modules });

function renderShell(modules: unknown[], role = "viewer") {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, user(modules, role)));
  return renderWithQuery(
    <AppShell modules={MODULES}>
      <p>contenido</p>
    </AppShell>,
  );
}

describe("AppShell with Rendiciones", () => {
  it("shows the Rendiciones navigation with R phase badges", async () => {
    navigation.pathname = "/rendiciones";
    renderShell([commercial, settlements]);
    expect(await screen.findByRole("link", { name: "Inicio" })).toHaveAttribute("href", "/rendiciones");
    expect(screen.getByText("Tesorería")).toBeInTheDocument();
    expect(screen.getByTitle("Disponible en la fase R1")).toHaveTextContent("R1");
    expect(screen.getByText("contenido")).toBeInTheDocument();
  });

  it("switches between modules from the menu under the wordmark", async () => {
    navigation.pathname = "/rendiciones";
    renderShell([commercial, settlements]);
    await userEvent.click(await screen.findByRole("button", { name: /Cambiar de módulo/ }));
    const menu = screen.getByRole("navigation", { name: "Módulos" });
    expect(menu).toHaveTextContent("Inteligencia Comercial");
    expect(screen.getByRole("link", { name: "Inteligencia Comercial" })).toHaveAttribute("href", "/comercial");
    expect(screen.getByRole("link", { name: "Todos los módulos" })).toHaveAttribute("href", "/");
  });

  it("has no switcher with a single module", async () => {
    navigation.pathname = "/comercial";
    renderShell([commercial]);
    expect(await screen.findByText("Inteligencia Comercial")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Cambiar de módulo/ })).not.toBeInTheDocument();
  });

  it("blocks the screens of a module the user has no role in", async () => {
    navigation.pathname = "/rendiciones/cajas";
    renderShell([commercial]);
    expect(await screen.findByText("No tenés acceso a Rendiciones")).toBeInTheDocument();
    expect(screen.queryByText("contenido")).not.toBeInTheDocument();
  });

  it("shows Empleados only to platform admins", async () => {
    navigation.pathname = "/comercial";
    renderShell([commercial], "admin");
    expect(await screen.findByRole("link", { name: "Empleados" })).toHaveAttribute("href", "/empleados");
  });
});

describe("ModuleSelector", () => {
  it("shows one card per accessible module, with the official Rendiciones logo", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, user([commercial, settlements])));
    renderWithQuery(<ModuleSelector modules={MODULES} />);
    expect(await screen.findByRole("link", { name: "Entrar a Rendiciones" })).toHaveAttribute("href", "/rendiciones");
    expect(screen.getByRole("link", { name: "Entrar a Inteligencia Comercial" })).toHaveAttribute("href", "/comercial");
    expect(screen.getByRole("img", { name: "DistriCom Rendiciones" })).toHaveAttribute(
      "src",
      "/brand/districom-rendiciones.png",
    );
    expect(screen.getByText("Operador")).toBeInTheDocument();
  });

  it("goes straight to the only module", async () => {
    navigation.replace.mockClear();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, user([settlements])));
    renderWithQuery(<ModuleSelector modules={MODULES} />);
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("/rendiciones"));
  });
});
