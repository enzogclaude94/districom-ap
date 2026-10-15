"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { useId, useState, type FormEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api/client";
import { formatDate, formatMoney, todayIso } from "@/lib/format";
import { textareaClass } from "@/modules/settlements/delivery/form";

import { employeeFinanceApi, employeeFinanceQueryKey, type CompensationTerm } from "./api";
import { SALARY_NOTICE, parseSalary } from "./labels";

function message(error: unknown) {
  return error instanceof ApiError ? error.message : "No se pudo guardar. Volvé a intentar.";
}

function Problem({ children }: { children: ReactNode }) {
  return (
    <div
      role="alert"
      className="flex gap-2 rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger"
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
      <p>{children}</p>
    </div>
  );
}

/** A new salary term: open from its start; the one in effect must be ended first (no overlap). */
export function NewTermDialog({ employeeId, onClose }: { employeeId: number; onClose: () => void }) {
  const ids = { from: useId(), salary: useId(), notes: useId() };
  const queryClient = useQueryClient();
  const [validFrom, setValidFrom] = useState(todayIso());
  const [salary, setSalary] = useState("");
  const [notes, setNotes] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: (baseSalary: string) =>
      employeeFinanceApi.createTerm(employeeId, {
        valid_from: validFrom,
        base_salary: baseSalary,
        notes: notes.trim() || null,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: employeeFinanceQueryKey });
      onClose();
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const parsed = parseSalary(salary);
    const local = !validFrom ? "Indicá desde cuándo rige." : parsed === null ? "Ingresá un sueldo válido." : null;
    setProblem(local);
    if (!local && parsed !== null && !create.isPending) create.mutate(parsed);
  };
  return (
    <Dialog title="Cargar condición salarial" description={SALARY_NOTICE} onClose={onClose}>
      <form className="space-y-4" onSubmit={submit} noValidate>
        <div className="space-y-1.5">
          <Label htmlFor={ids.from}>Vigente desde</Label>
          <Input
            id={ids.from}
            type="date"
            value={validFrom}
            disabled={create.isPending}
            onChange={(event) => setValidFrom(event.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={ids.salary}>Sueldo base interno mensual</Label>
          <Input
            id={ids.salary}
            inputMode="decimal"
            autoComplete="off"
            placeholder="$ 0,00"
            value={salary}
            disabled={create.isPending}
            onChange={(event) => setSalary(event.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={ids.notes}>Nota (opcional)</Label>
          <textarea
            id={ids.notes}
            className={textareaClass}
            maxLength={1000}
            value={notes}
            disabled={create.isPending}
            onChange={(event) => setNotes(event.target.value)}
          />
        </div>
        {(problem || create.error) && <Problem>{problem ?? message(create.error)}</Problem>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? "Guardando…" : "Guardar condición"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/** Ends the term on a day (inclusive), once, with a reason; the history is never rewritten. */
export function EndTermDialog({ term, onClose }: { term: CompensationTerm; onClose: () => void }) {
  const ids = { to: useId(), reason: useId() };
  const queryClient = useQueryClient();
  const [validTo, setValidTo] = useState(todayIso() < term.valid_from ? term.valid_from : todayIso());
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const end = useMutation({
    mutationFn: () => employeeFinanceApi.endTerm(term.id, { valid_to: validTo, reason: reason.trim() }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: employeeFinanceQueryKey });
      onClose();
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const local = !validTo ? "Indicá hasta cuándo rige." : !reason.trim() ? "Indicá el motivo." : null;
    setProblem(local);
    if (!local && !end.isPending) end.mutate();
  };
  return (
    <Dialog
      title="Terminar condición salarial"
      description={`${formatMoney(term.base_salary)} desde el ${formatDate(term.valid_from)}. Para un cambio de sueldo, terminala y cargá la nueva desde el día siguiente.`}
      onClose={onClose}
    >
      <form className="space-y-4" onSubmit={submit} noValidate>
        <div className="space-y-1.5">
          <Label htmlFor={ids.to}>Último día de vigencia</Label>
          <Input
            id={ids.to}
            type="date"
            min={term.valid_from}
            value={validTo}
            disabled={end.isPending}
            onChange={(event) => setValidTo(event.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={ids.reason}>Motivo</Label>
          <Input
            id={ids.reason}
            maxLength={500}
            autoComplete="off"
            value={reason}
            disabled={end.isPending}
            onChange={(event) => setReason(event.target.value)}
          />
        </div>
        {(problem || end.error) && <Problem>{problem ?? message(end.error)}</Problem>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit" disabled={end.isPending}>
            {end.isPending ? "Guardando…" : "Terminar condición"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
