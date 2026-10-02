import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { ApiError } from "@/lib/api/client";

describe("EmptyState", () => {
  it("explains why there is no content", () => {
    render(<EmptyState title="Sin datos" description="Importá ventas para empezar." />);
    expect(screen.getByText("Sin datos")).toBeInTheDocument();
    expect(screen.getByText("Importá ventas para empezar.")).toBeInTheDocument();
  });
});

describe("ErrorState", () => {
  it("shows the API message, the request id and retries", async () => {
    const onRetry = vi.fn();
    const error = new ApiError(500, { code: "internal_error", message: "Falló.", details: null, request_id: "abc123" });
    render(<ErrorState error={error} onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Falló.");
    expect(screen.getByText(/abc123/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("never shows raw messages from unknown errors", () => {
    render(<ErrorState error={new Error("TypeError: x is undefined")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Ocurrió un error inesperado.");
  });
});
