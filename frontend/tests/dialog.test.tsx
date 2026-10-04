import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { Dialog } from "@/components/ui/dialog";

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button">Fondo antes</button>
      <button type="button" onClick={() => setOpen(true)}>
        Abrir
      </button>
      {open && (
        <Dialog title="Mover entre cajas" onClose={() => setOpen(false)}>
          <input aria-label="Importe" />
          <button type="button" disabled>
            Deshabilitado
          </button>
          <button type="button">Registrar</button>
        </Dialog>
      )}
      <button type="button">Fondo después</button>
    </>
  );
}

describe("Dialog", () => {
  it("has an accessible title and starts on its first field", async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "Abrir" }));
    expect(screen.getByRole("dialog", { name: "Mover entre cajas" })).toHaveAttribute("aria-modal", "true");
    expect(screen.getByLabelText("Importe")).toHaveFocus();
  });

  it("keeps Tab and Shift+Tab inside the dialog", async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "Abrir" }));
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Registrar" })).toHaveFocus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Cerrar" })).toHaveFocus();
    await userEvent.tab();
    expect(screen.getByLabelText("Importe")).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(screen.getByRole("button", { name: "Cerrar" })).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(screen.getByRole("button", { name: "Registrar" })).toHaveFocus();
    for (let i = 0; i < 6; i += 1) {
      await userEvent.tab();
      expect(screen.getByRole("dialog")).toContainElement(document.activeElement as HTMLElement);
    }
  });

  it("brings focus back if something outside takes it", async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "Abrir" }));
    screen.getByRole("button", { name: "Fondo después" }).focus();
    expect(screen.getByRole("dialog")).toContainElement(document.activeElement as HTMLElement);
  });

  it("closes with Escape or the close button and returns focus to the opener", async () => {
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Abrir" });
    await userEvent.click(opener);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();

    await userEvent.click(opener);
    await userEvent.click(screen.getByRole("button", { name: "Cerrar" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });
});
