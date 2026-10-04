"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { useId, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cashApi, cashQueryKey, type Account } from "@/modules/settlements/cash/api";

import { cashErrorView } from "./errors";

interface Props {
  accounts: Account[];
  onClose: () => void;
  onDone: (account: Account) => void;
}

/** Creates a partner contribution account. A name equivalent to an existing one is refused, never varied. */
export function OwnerAccountDialog({ accounts, onClose, onDone }: Props) {
  const nameId = useId();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => cashApi.createOwnerAccount(name.trim()),
    onSuccess: (account) => {
      void queryClient.invalidateQueries({ queryKey: cashQueryKey });
      onDone(account);
      onClose();
    },
  });
  const names = (id: number) => accounts.find((account) => account.id === id)?.name;
  const error = create.error ? cashErrorView(create.error, names) : null;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const local = name.trim() ? null : "Ingresá el nombre del socio.";
    setProblem(local);
    if (!local && !create.isPending) create.mutate();
  };

  return (
    <Dialog
      title="Crear cuenta de aporte"
      description="Una cuenta por socio. Acumula lo que aporta y lo que se le devuelve."
      onClose={onClose}
    >
      <form className="space-y-4" onSubmit={submit} noValidate>
        <div className="space-y-1.5">
          <Label htmlFor={nameId}>Nombre del socio</Label>
          <Input
            id={nameId}
            maxLength={120}
            autoComplete="off"
            value={name}
            disabled={create.isPending}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        {(problem || error) && (
          <div
            role="alert"
            className="flex gap-2 rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger"
          >
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
            <p>{problem ?? error?.message}</p>
          </div>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? "Creando…" : "Crear cuenta"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
