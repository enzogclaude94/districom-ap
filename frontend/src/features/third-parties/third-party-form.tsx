"use client";

import { useMutation } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { useId, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api/client";
import {
  duplicateCandidates,
  isValidTaxId,
  normalizeTaxId,
  type DuplicateCandidate,
  type DuplicateConfirmation,
  type ThirdParty,
  type ThirdPartyInput,
} from "@/lib/api/third-parties";

const clean = (text: string) => text.split(/\s+/).filter(Boolean).join(" ");

const textareaClass =
  "flex min-h-16 w-full rounded-md border border-border bg-surface px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none";

export function thirdPartyError(error: unknown): string {
  if (!(error instanceof ApiError)) return "No se pudo guardar. Revisá la conexión y volvé a intentar.";
  if (error.code === "third_party_tax_id_taken") {
    const inactive =
      typeof error.details === "object" && error.details !== null && "active" in error.details
        ? (error.details as { active: unknown }).active === false
        : false;
    return inactive
      ? `${error.message} Está desactivado: un administrador de DistriCom puede activarlo desde Terceros.`
      : error.message;
  }
  if (error.status === 403) return "Solo un administrador de DistriCom modifica los terceros.";
  if (error.status === 0 || error.status >= 500) return "No se pudo guardar. Volvé a intentar.";
  return error.message || "No se pudo guardar.";
}

function Candidates({
  candidates,
  onUse,
}: {
  candidates: DuplicateCandidate[];
  onUse?: (candidate: DuplicateCandidate) => void;
}) {
  return (
    <ul className="space-y-1.5" aria-label="Terceros con el mismo nombre">
      {candidates.map((candidate) => (
        <li
          key={candidate.third_party_id}
          className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border px-3 py-2"
        >
          <span>
            <span className="font-medium">{candidate.name}</span>
            <span className="block text-xs text-muted-foreground tabular-nums">
              {candidate.tax_id_display ? `CUIT ${candidate.tax_id_display}` : "Sin CUIT"}
              {!candidate.active && " · desactivado"}
            </span>
          </span>
          {onUse && candidate.active && (
            <Button size="sm" variant="secondary" onClick={() => onUse(candidate)}>
              Usar este
            </Button>
          )}
        </li>
      ))}
    </ul>
  );
}

/**
 * The fields of a third party (name, optional CUIT, notes) and the «posible duplicado» step: when the
 * backend finds a homonym and one of the two has no CUIT, nothing is saved until the user either uses the
 * existing one or confirms, with a reason, that it is another third party. Nothing is ever merged. A
 * repeated CUIT is a hard stop. Used by Terceros (platform admin) and by a payment's quick create.
 */
export function ThirdPartyForm({
  thirdParty = null,
  save,
  onSaved,
  onCancel,
  onUseExisting,
  submitLabel,
}: {
  thirdParty?: ThirdParty | null;
  save: (body: ThirdPartyInput) => Promise<ThirdParty>;
  onSaved: (thirdParty: ThirdParty) => void;
  onCancel: () => void;
  /** Offered next to each active homonym (quick create): pick it instead of registering another one. */
  onUseExisting?: (candidate: DuplicateCandidate) => void;
  submitLabel: string;
}) {
  const ids = { name: useId(), taxId: useId(), notes: useId(), reason: useId() };
  const [name, setName] = useState(thirdParty?.name ?? "");
  const [taxId, setTaxId] = useState(thirdParty?.tax_id_display ?? "");
  const [notes, setNotes] = useState(thirdParty?.notes ?? "");
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const mutation = useMutation({ mutationFn: save });
  const candidates = mutation.error ? duplicateCandidates(mutation.error) : null;

  const value = (): ThirdPartyInput | string => {
    const body = { name: clean(name), tax_id: normalizeTaxId(taxId) || null, notes: notes.trim() || null };
    if (!body.name) return "Indicá el nombre del tercero.";
    if (body.tax_id && !/^\d{11}$/.test(body.tax_id)) return "El CUIT tiene 11 dígitos.";
    if (body.tax_id && !isValidTaxId(body.tax_id)) return "El CUIT no es válido: revisá el dígito verificador.";
    return body;
  };

  const send = (confirmation: DuplicateConfirmation = {}) => {
    const body = value();
    if (typeof body === "string") return setProblem(body);
    setProblem(null);
    mutation.mutate({ ...body, ...confirmation }, { onSuccess: onSaved });
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    send();
  };

  const confirmOther = () => {
    if (!reason.trim()) return setProblem("Indicá por qué es otro tercero.");
    send({ confirm_not_duplicate: true, duplicate_reason: reason.trim() });
  };

  if (candidates) {
    return (
      <div className="space-y-4 text-sm">
        <div
          role="alert"
          className="flex gap-2 rounded-md border border-warning/40 bg-warning-soft px-3 py-2 text-foreground"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
          <p>
            Ya hay un tercero con el nombre «{clean(name)}». Como al menos uno no tiene CUIT, no se puede saber si es el
            mismo. Revisalo antes de seguir: los terceros nunca se fusionan.
          </p>
        </div>
        <Candidates candidates={candidates} onUse={onUseExisting} />
        <div className="space-y-1.5">
          <Label htmlFor={ids.reason}>Si es otro tercero, indicá por qué</Label>
          <textarea
            id={ids.reason}
            className={textareaClass}
            maxLength={500}
            placeholder="Por ejemplo: otra sucursal, otro dueño, otra localidad"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </div>
        {problem && (
          <p role="alert" className="text-danger">
            {problem}
          </p>
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={() => mutation.reset()}>
            Volver a editar
          </Button>
          <Button variant="secondary" disabled={mutation.isPending} onClick={confirmOther}>
            {mutation.isPending ? "Guardando…" : "Es otro tercero: registrarlo"}
          </Button>
        </div>
      </div>
    );
  }

  const error = problem ?? (mutation.error ? thirdPartyError(mutation.error) : null);
  return (
    <form className="space-y-4" onSubmit={submit} noValidate>
      <div className="space-y-1.5">
        <Label htmlFor={ids.name}>Nombre o razón social</Label>
        <Input id={ids.name} maxLength={200} value={name} onChange={(event) => setName(event.target.value)} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={ids.taxId}>CUIT (opcional)</Label>
        <Input
          id={ids.taxId}
          inputMode="numeric"
          autoComplete="off"
          maxLength={20}
          placeholder="30-71234567-1"
          className="tabular-nums"
          value={taxId}
          onChange={(event) => setTaxId(event.target.value)}
        />
        <p className="text-xs text-muted-foreground">
          {thirdParty ? "Dejalo vacío para quitarlo." : "Con o sin guiones. Se valida el dígito verificador."}
        </p>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={ids.notes}>Notas (opcional)</Label>
        <textarea
          id={ids.notes}
          className={textareaClass}
          maxLength={1000}
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
        />
      </div>
      {error && (
        <div role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
          {error}
        </div>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="ghost" onClick={onCancel}>
          Cancelar
        </Button>
        <Button type="submit" disabled={mutation.isPending}>
          {mutation.isPending ? "Guardando…" : submitLabel}
        </Button>
      </div>
    </form>
  );
}
