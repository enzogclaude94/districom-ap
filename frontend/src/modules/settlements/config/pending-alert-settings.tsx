"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2 } from "lucide-react";
import { useId, useState, type FormEvent } from "react";

import { ErrorState } from "@/components/common/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { FormError } from "@/modules/settlements/delivery/form";
import { pendingApi, pendingQueryKey, type AlertSettings } from "@/modules/settlements/pending/api";
import { pendingErrorView } from "@/modules/settlements/pending/errors";

const settingsKey = [...pendingQueryKey, "alert-settings"] as const;

function AlertHoursForm({ settings }: { settings: AlertSettings }) {
  const id = useId();
  const queryClient = useQueryClient();
  const [hours, setHours] = useState(String(settings.alert_hours));
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const save = useMutation({
    mutationFn: pendingApi.updateAlertSettings,
    onSuccess: (updated) => {
      queryClient.setQueryData(settingsKey, updated);
      // Alerts are computed on read: the home card, the menu and the list pick up the new threshold.
      void queryClient.invalidateQueries({ queryKey: pendingQueryKey });
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
      setSaved(true);
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setSaved(false);
    const value = Number(hours);
    if (!/^\d+$/.test(hours.trim()) || value < settings.min_hours || value > settings.max_hours) {
      setProblem(`Ingresá un número entero de ${settings.min_hours} a ${settings.max_hours} horas.`);
      return;
    }
    setProblem(null);
    save.mutate(value);
  };

  return (
    <form className="space-y-3" onSubmit={submit} noValidate>
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1.5">
          <Label htmlFor={id}>Alertar pendientes después de</Label>
          <div className="flex items-center gap-2">
            <Input
              id={id}
              type="number"
              inputMode="numeric"
              min={settings.min_hours}
              max={settings.max_hours}
              step={1}
              className="w-28 tabular-nums"
              value={hours}
              onChange={(event) => {
                setHours(event.target.value);
                setSaved(false);
              }}
            />
            <span className="text-sm">horas</span>
          </div>
        </div>
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? "Guardando…" : "Guardar"}
        </Button>
      </div>
      <FormError message={problem ?? (save.error ? pendingErrorView(save.error).message : null)} />
      {saved && (
        <p role="status" className="flex items-center gap-1 text-sm text-success">
          <CheckCircle2 className="size-4" aria-hidden />
          Guardado. Las alertas se recalculan con el nuevo valor.
        </p>
      )}
    </form>
  );
}

/** `settlements.pending_alert_hours`, stored in `app_settings` like every parameter; an admin edits it here. */
export function PendingAlertSettings() {
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const isAdmin = roleAtLeast(moduleRole(me.data, "settlements"), "admin");
  const query = useQuery({ queryKey: settingsKey, queryFn: pendingApi.alertSettings });

  return (
    <Card data-testid="pending-alert-settings">
      <CardHeader>
        <CardTitle>Alerta de pendientes</CardTitle>
        <CardDescription>
          Un pendiente con saldo aparecerá en alerta cuando supere este tiempo desde su creación.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {query.isPending ? (
          <Skeleton className="h-16 w-full max-w-sm" />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : isAdmin ? (
          <AlertHoursForm settings={query.data} />
        ) : (
          <p className="text-sm">
            Alertar pendientes después de <strong className="tabular-nums">{query.data.alert_hours} horas</strong>.{" "}
            {query.data.is_default && <Badge>Por defecto</Badge>}
            <span className="block text-xs text-muted-foreground">Solo un administrador lo modifica.</span>
          </p>
        )}
      </CardContent>
    </Card>
  );
}
