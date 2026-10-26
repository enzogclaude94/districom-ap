import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EmployeeForm } from "@/features/employees/employee-form";
import { EmployeeFunctions } from "@/features/employees/employee-functions";
import { EmployeeList } from "@/features/employees/employee-list";
import type { Employee } from "@/lib/api/employees";
import { formatDate } from "@/lib/format";

import { jsonResponse, renderWithQuery } from "./render";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

const pedro: Employee = {
  id: 7,
  full_name: "Pedro Gómez",
  active: true,
  hire_date: "2025-03-01",
  phone: null,
  external_axum_id: null,
  functions: [
    {
      id: 3,
      function_code: "commercial.salesperson",
      label: "Preventista",
      valid_from: "2026-01-01",
      valid_to: null,
      source: "MANUAL",
      current: true,
    },
  ],
};

describe("EmployeeForm", () => {
  it("requires a name and sends empty optional fields as null", async () => {
    const onSubmit = vi.fn();
    renderWithQuery(<EmployeeForm submitLabel="Crear" pending={false} error={null} onSubmit={onSubmit} />);
    await userEvent.click(screen.getByRole("button", { name: "Crear" }));
    expect(await screen.findByText("Ingresá el nombre.")).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();

    await userEvent.type(screen.getByLabelText("Nombre y apellido"), "  Ana Ruiz ");
    await userEvent.click(screen.getByRole("button", { name: "Crear" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({ full_name: "Ana Ruiz", hire_date: null, phone: null }));
  });
});

describe("EmployeeList", () => {
  it("lists employees with their current functions", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, { items: [pedro], total: 1 }));
    renderWithQuery(<EmployeeList />);
    expect(await screen.findByRole("link", { name: "Pedro Gómez" })).toHaveAttribute("href", "/empleados/7");
    expect(screen.getByText("Preventista")).toBeInTheDocument();
    expect(screen.getByText("01/03/2025")).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/v1/employees?active=true&limit=50&offset=0");
  });

  it("explains the empty state", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, { items: [], total: 0 }));
    renderWithQuery(<EmployeeList />);
    expect(await screen.findByText("Todavía no hay empleados")).toBeInTheDocument();
  });

  it("tells non-admins they cannot manage employees", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(403, { code: "forbidden", message: "No.", details: null, request_id: "r" }),
    );
    renderWithQuery(<EmployeeList />);
    expect(await screen.findByText("Sin permisos")).toBeInTheDocument();
  });
});

describe("EmployeeFunctions", () => {
  it("assigns a registered function from a date", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input).endsWith("/function-codes")) {
        return jsonResponse(200, [{ code: "commercial.salesperson", label: "Preventista" }]);
      }
      return jsonResponse(201, pedro);
    });
    renderWithQuery(<EmployeeFunctions employee={{ ...pedro, functions: [] }} />);
    expect(screen.getByText("Todavía no tiene funciones asignadas.")).toBeInTheDocument();
    await screen.findByRole("option", { name: "Preventista" });
    await userEvent.selectOptions(screen.getByLabelText("Asignar función"), "commercial.salesperson");
    await userEvent.clear(screen.getByLabelText("Desde"));
    await userEvent.type(screen.getByLabelText("Desde"), "2026-01-01");
    await userEvent.click(screen.getByRole("button", { name: "Asignar" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/api/v1/employees/7/functions");
    expect(JSON.parse(String(init?.body))).toEqual({
      function_code: "commercial.salesperson",
      valid_from: "2026-01-01",
    });
  });

  it("shows history and lets an open period be closed", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, []));
    renderWithQuery(<EmployeeFunctions employee={pedro} />);
    expect(screen.getByText("Vigente")).toBeInTheDocument();
    expect(screen.getByText("Sin fin")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cerrar período" }));
    expect(screen.getByLabelText("Fin de Preventista")).toBeInTheDocument();
  });
});

describe("formatDate", () => {
  it("formats ISO business dates as dd/mm/yyyy", () => {
    expect(formatDate("2026-10-04")).toBe("04/10/2026");
    expect(formatDate(null)).toBe("—");
  });
});
