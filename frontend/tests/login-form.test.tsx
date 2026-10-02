import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LoginForm, safeNextPath } from "@/features/auth/login-form";

import { jsonResponse, renderWithQuery } from "./render";

describe("LoginForm", () => {
  it("validates required fields without calling the API", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    renderWithQuery(<LoginForm onSuccess={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Ingresar" }));
    expect(await screen.findByText("Ingresá tu email.")).toBeInTheDocument();
    expect(screen.getByText("Ingresá tu contraseña.")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends credentials with the CSRF header and calls onSuccess", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, { id: 1, email: "a@b.c", full_name: "Ana", role: "admin" }));
    const onSuccess = vi.fn();
    renderWithQuery(<LoginForm onSuccess={onSuccess} />);
    await userEvent.type(screen.getByLabelText("Email"), "a@b.c");
    await userEvent.type(screen.getByLabelText("Contraseña"), "secreto-largo");
    await userEvent.click(screen.getByRole("button", { name: "Ingresar" }));

    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/auth/login");
    expect(new Headers(init?.headers).get("X-Requested-With")).toBeTruthy();
    expect(JSON.parse(String(init?.body))).toEqual({ email: "a@b.c", password: "secreto-largo" });
  });

  it("shows the backend error message on invalid credentials", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(401, {
        code: "invalid_credentials",
        message: "Email o contraseña incorrectos.",
        details: null,
        request_id: "r1",
      }),
    );
    const onSuccess = vi.fn();
    renderWithQuery(<LoginForm onSuccess={onSuccess} />);
    await userEvent.type(screen.getByLabelText("Email"), "a@b.c");
    await userEvent.type(screen.getByLabelText("Contraseña"), "mala");
    await userEvent.click(screen.getByRole("button", { name: "Ingresar" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Email o contraseña incorrectos.");
    expect(onSuccess).not.toHaveBeenCalled();
  });
});

describe("safeNextPath", () => {
  it.each([
    [null, "/"],
    ["/configuracion", "/configuracion"],
    ["//evil.com", "/"],
    ["/\\evil.com", "/"],
    ["https://evil.com", "/"],
  ])("%s → %s", (input, expected) => {
    expect(safeNextPath(input)).toBe(expected);
  });
});
