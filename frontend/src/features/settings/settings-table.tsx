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

function formatValue(value: unknown): string {
  // Lists of objects (e.g. settlements.cash_denominations): one item per group.
  if (Array.isArray(value)) return value.map(formatValue).join(" | ");
  if (value !== null && typeof value === "object") {
    return Object.entries(value)
      .map(([key, v]) => `${key}: ${formatValue(v)}`)
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
        description="Solo los administradores pueden ver la configuración."
      />
    );
  }
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;

  return (
    <div className="space-y-6">
      {query.data.map((scope) => (
        <Card key={scope.scope}>
          <CardHeader>
            <CardTitle>{scope.label}</CardTitle>
            <CardDescription>
              {scope.scope === "platform"
                ? "Parámetros generales de DistriCom."
                : `Parámetros del módulo ${scope.label}. Los que se pueden cambiar se editan desde la configuración del módulo.`}
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
                {scope.settings.map((setting) => (
                  <TableRow key={setting.key}>
                    <TableCell>
                      <p className="font-medium">{setting.label}</p>
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
      ))}
    </div>
  );
}
