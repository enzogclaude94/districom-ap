"use client";

import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useId, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/cn";

import { MASTERS, SECTIONS } from "./labels";

export const PAGE_SIZE = 25;
export const selectClass =
  "flex h-9 w-full rounded-md border border-border bg-surface px-3 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none";

/** What the person can do here: a module admin edits; viewer and operator read (F1.7 §15). */
export function useMastersAccess() {
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  return {
    isAdmin: roleAtLeast(moduleRole(me.data, "commercial"), "admin"),
    isPlatformAdmin: me.data?.role === "admin",
  };
}

/** One admin module with a subnav, not eight apps. */
export function MastersTabs() {
  const pathname = usePathname();
  return (
    <nav aria-label="Secciones de Maestros" className="-mx-1 flex gap-1 overflow-x-auto border-b border-border pb-px">
      {SECTIONS.map((s) => {
        const href = `${MASTERS}/${s.key}`;
        const current = pathname === href || pathname.startsWith(`${href}/`);
        return (
          <Link
            key={s.key}
            href={href}
            aria-current={current ? "page" : undefined}
            className={cn(
              "shrink-0 rounded-t-md border-b-2 px-3 py-2 text-sm whitespace-nowrap",
              current
                ? "border-primary font-medium text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {s.label}
          </Link>
        );
      })}
    </nav>
  );
}

export function Pager({ page, total, onPage }: { page: number; total: number; onPage: (page: number) => void }) {
  if (total <= PAGE_SIZE) return null;
  return (
    <div className="mt-3 flex items-center justify-between gap-2 text-sm text-muted-foreground">
      <span className="tabular-nums">
        Página {page + 1} de {Math.ceil(total / PAGE_SIZE)} · {total.toLocaleString("es-AR")} en total
      </span>
      <div className="flex gap-2">
        <Button variant="secondary" size="sm" disabled={page === 0} onClick={() => onPage(page - 1)}>
          Anterior
        </Button>
        <Button
          variant="secondary"
          size="sm"
          disabled={(page + 1) * PAGE_SIZE >= total}
          onClick={() => onPage(page + 1)}
        >
          Siguiente
        </Button>
      </div>
    </div>
  );
}

/** The message a failed save shows: a 409 says someone saved first; a 422 says what is wrong. */
export function saveError(error: unknown) {
  if (!(error instanceof ApiError)) return error ? "No se pudo guardar." : null;
  if (error.code === "changed_meanwhile")
    return "Alguien modificó este dato mientras lo editabas. Cerrá y volvé a abrir para ver la versión actual.";
  return error.message;
}

/**
 * The one shape of an admin edit: the fields, a required reason (audited by the backend) and the save. Errors stay
 * in the dialog; nothing is written from the frontend but through the endpoint. `requireReason` false: a creation,
 * whose endpoint audits it without a reason (no reason field is shown, so none is lost).
 */
export function EditDialog({
  title,
  description,
  onClose,
  onSave,
  pending,
  error,
  children,
  saveLabel = "Guardar",
  requireReason = true,
}: {
  title: string;
  description?: ReactNode;
  onClose: () => void;
  onSave: (reason: string) => void;
  pending: boolean;
  error: unknown;
  children?: ReactNode;
  saveLabel?: string;
  requireReason?: boolean;
}) {
  const reasonId = useId();
  const [reason, setReason] = useState("");
  const message = saveError(error);
  const missing = requireReason && reason.trim() === "";
  return (
    <Dialog title={title} description={description} onClose={onClose} className="max-w-lg">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (!missing) onSave(reason.trim());
        }}
      >
        {children}
        {requireReason && (
          <div className="space-y-1.5">
            <Label htmlFor={reasonId}>Motivo del cambio</Label>
            <textarea
              id={reasonId}
              value={reason}
              maxLength={300}
              rows={2}
              onChange={(e) => setReason(e.target.value)}
              className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            />
            <p className="text-xs text-muted-foreground">Queda registrado en la auditoría junto con quién y cuándo.</p>
          </div>
        )}
        {message && (
          <p role="alert" className="text-sm text-danger">
            {message}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose} disabled={pending}>
            Cancelar
          </Button>
          <Button type="submit" disabled={pending || missing}>
            {pending && <Loader2 className="animate-spin" aria-hidden />}
            {saveLabel}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/** A labeled control inside an edit dialog. */
export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: (id: string) => ReactNode;
  hint?: string;
}) {
  const id = useId();
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children(id)}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

/** Read-only fact of a detail. */
export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm">{children ?? "—"}</dd>
    </div>
  );
}

export const yesNo = (value: boolean) => (value ? "Sí" : "No");
