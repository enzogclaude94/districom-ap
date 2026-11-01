"use client";

import { Check, Copy, KeyRound } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import type { TemporaryPassword } from "@/lib/api/users";
import { formatDateTime } from "@/lib/format";

/**
 * A temporary password, shown once: the server keeps only its hash. Whoever reads it hands it over in person or
 * through a channel other than email; the user must replace it on their first login.
 */
export function TemporaryPasswordNotice({ result }: { result: TemporaryPassword }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(result.temporary_password);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div role="status" className="space-y-3 rounded-md border border-warning/40 bg-warning-soft p-4 text-sm">
      <p className="flex items-center gap-2 font-medium">
        <KeyRound className="size-4" aria-hidden />
        Contraseña temporal de {result.user.full_name}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <code
          aria-label="Contraseña temporal"
          className="rounded bg-surface px-3 py-1.5 font-mono text-base tracking-wider select-all"
        >
          {result.temporary_password}
        </code>
        <Button size="sm" variant="secondary" onClick={() => void copy()}>
          {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
          {copied ? "Copiada" : "Copiar"}
        </Button>
      </div>
      <p className="text-muted-foreground">
        Se muestra <strong>solo esta vez</strong>. Vence el {formatDateTime(result.expires_at)}; al ingresar con ella,
        DistriCom le pide elegir una propia. Entregala en persona o por un canal distinto del email.
      </p>
    </div>
  );
}
