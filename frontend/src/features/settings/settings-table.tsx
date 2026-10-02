"use client";

import { useQuery } from "@tanstack/react-query";
import { Lock } from "lucide-react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ApiError } from "@/lib/api/client";
import { settingsApi } from "@/lib/api/settings";

/** Human labels for METRICS.md §6 parameter keys. */
const SETTING_LABELS: Record<string, string> = {
  currency: "Moneda",
  business_timezone: "Zona horaria de negocio",
  week_start: "Inicio de semana comercial",
  history_start_date: "Inicio de historia confiable",
  new_customer_warmup_days: "Días de calentamiento para clientes nuevos",
  active_window_days: "Ventana de cliente activo (días)",
  penetration_window_days: "Ventana de penetración (días)",
  min_segment_size: "Tamaño mínimo de segmento",
  conversion_window_days: "Ventana visita → factura (días)",
  frequency_lookback_days: "Historia para frecuencia (días)",
  min_peer_group_size: "Tamaño mínimo de grupo de pares",
  default_expected_interval_days: "Intervalo de compra por defecto (días)",
  status_thresholds: "Umbrales de estado de cliente (overdue ratio)",
  min_days_for_at_risk: "Días mínimos para “en riesgo”",
  opportunity_priority_thresholds: "Umbrales de prioridad de oportunidades",
  opportunity_attribution_window_days: "Ventana de atribución de oportunidades (días)",
  category_level_for_mix: "Nivel de categoría para mix",
};

function formatValue(value: unknown) {
  if (value !== null && typeof value === "object") {
    return Object.entries(value)
      .map(([key, v]) => `${key}: ${String(v)}`)
      .join(" · ");
  }
  return String(value);
}

export function SettingsTable() {
  const query = useQuery({ queryKey: ["settings"], queryFn: settingsApi.list });

  if (query.isPending) {
    return (
      <div className="space-y-2" aria-busy="true">
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-9 w-full" />
        ))}
      </div>
    );
  }
  if (query.error instanceof ApiError && query.error.status === 403) {
    return (
      <EmptyState
        icon={Lock}
        title="Sin permisos"
        description="Solo los administradores pueden ver y modificar la configuración comercial."
      />
    );
  }
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Parámetros comerciales</CardTitle>
        <CardDescription>
          Valores usados por las métricas y reglas (definidos en METRICS.md). La edición llega en una fase posterior.
        </CardDescription>
      </CardHeader>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Parámetro</TableHead>
              <TableHead>Valor</TableHead>
              <TableHead className="text-right">Origen</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {query.data.map((setting) => (
              <TableRow key={setting.key}>
                <TableCell>
                  <p className="font-medium">{SETTING_LABELS[setting.key] ?? setting.key}</p>
                  <p className="font-mono text-xs text-muted-foreground">{setting.key}</p>
                </TableCell>
                <TableCell className="font-mono text-xs">{formatValue(setting.value)}</TableCell>
                <TableCell className="text-right">
                  <Badge tone={setting.is_default ? "neutral" : "info"}>
                    {setting.is_default ? "Por defecto" : "Personalizado"}
                  </Badge>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
