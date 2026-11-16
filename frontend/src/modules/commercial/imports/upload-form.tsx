"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Upload } from "lucide-react";
import { useRouter } from "next/navigation";
import { useId, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api/client";
import { importsApi, importsQueryKey, type ScopeDeclaration } from "@/lib/api/imports";
import { todayIso } from "@/lib/format";

import { IMPORT_TYPES } from "./labels";

const selectClass =
  "flex h-9 w-full rounded-md border border-border bg-surface px-3 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:opacity-50";

export const importHref = (id: number) => `/comercial/datos/importaciones/${id}`;

/**
 * Step 1 of an import: choose the file as STORE SYS exported it, say what DistriCom cannot read from it (the day of
 * a master's snapshot, whether a sales export was filtered) and upload. The server analyses it in the same request,
 * writes nothing and the operator lands on the preview.
 */
export function UploadForm() {
  const ids = { file: useId(), type: useId(), snapshot: useId(), scope: useId(), note: useId() };
  const router = useRouter();
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [importType, setImportType] = useState("");
  const [snapshotDate, setSnapshotDate] = useState("");
  const [scope, setScope] = useState<ScopeDeclaration | "">("");
  const [scopeNote, setScopeNote] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  const chosen = IMPORT_TYPES.find((t) => t.key === importType);
  const asksSnapshot = !chosen || chosen.snapshot;
  const asksScope = !chosen || chosen.scope;

  const upload = useMutation({
    mutationFn: importsApi.upload,
    onSuccess: (job) => {
      void queryClient.invalidateQueries({ queryKey: importsQueryKey });
      router.push(importHref(job.id));
    },
    onError: (error) => setProblem(error instanceof ApiError ? error.message : "No se pudo subir el archivo."),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setProblem(null);
    if (!file) return setProblem("Elegí el archivo exportado de STORE SYS.");
    if (scope === "FILTERED" && !scopeNote.trim()) return setProblem("Contá qué filtros usaste al exportar.");
    upload.mutate({
      file,
      importType: importType || undefined,
      snapshotDate: asksSnapshot ? snapshotDate || undefined : undefined,
      scope: asksScope ? scope || undefined : undefined,
      scopeNote: asksScope && scope === "FILTERED" ? scopeNote : undefined,
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Importar un archivo de STORE SYS</CardTitle>
        <CardDescription>
          Subí el archivo tal como sale del sistema. DistriCom lo revisa y te muestra qué encontró antes de cargar nada.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="grid gap-4 md:grid-cols-2" aria-busy={upload.isPending}>
          <fieldset disabled={upload.isPending} className="contents">
            <div className="space-y-1.5 md:col-span-2">
              <Label htmlFor={ids.file}>Archivo</Label>
              <Input
                id={ids.file}
                type="file"
                accept=".xls,.xlsx,.csv"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
              <p className="text-xs text-muted-foreground">.xls, .xlsx o .csv, sin abrirlo ni modificarlo antes.</p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor={ids.type}>Qué reporte es</Label>
              <select
                id={ids.type}
                className={selectClass}
                value={importType}
                onChange={(e) => setImportType(e.target.value)}
              >
                <option value="">Detectarlo automáticamente</option>
                {IMPORT_TYPES.map((t) => (
                  <option key={t.key} value={t.key}>
                    {t.label}
                  </option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">
                {chosen?.hint ?? "DistriCom lo reconoce por sus columnas. Elegilo solo si no lo reconoce."}
              </p>
            </div>

            {asksSnapshot && (
              <div className="space-y-1.5">
                <Label htmlFor={ids.snapshot}>Día de la foto {chosen ? "" : "(clientes y artículos)"}</Label>
                <Input
                  id={ids.snapshot}
                  type="date"
                  max={todayIso()}
                  value={snapshotDate}
                  onChange={(e) => setSnapshotDate(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  El día en que exportaste el maestro. Si lo dejás vacío, se toma hoy.
                </p>
              </div>
            )}

            {asksScope && (
              <fieldset className="space-y-1.5 md:col-span-2">
                <legend className="text-sm font-medium">Alcance del export {chosen ? "" : "(ventas)"}</legend>
                <div className="flex flex-wrap gap-4 text-sm">
                  <label className="flex items-center gap-2">
                    <input type="radio" name={ids.scope} checked={scope === "FULL"} onChange={() => setScope("FULL")} />
                    Completo, sin filtros
                  </label>
                  <label className="flex items-center gap-2">
                    <input
                      type="radio"
                      name={ids.scope}
                      checked={scope === "FILTERED"}
                      onChange={() => setScope("FILTERED")}
                    />
                    Filtrado (por vendedor, zona…)
                  </label>
                </div>
                <p className="text-xs text-muted-foreground">
                  Solo con un export completo DistriCom reemplaza sola las ventas ya cargadas de esos días. Si no lo
                  indicás, se trata como filtrado.
                </p>
                {scope === "FILTERED" && (
                  <div className="space-y-1.5 pt-1">
                    <Label htmlFor={ids.note}>Qué filtros usaste</Label>
                    <Input
                      id={ids.note}
                      maxLength={300}
                      value={scopeNote}
                      onChange={(e) => setScopeNote(e.target.value)}
                      placeholder="Por ejemplo: solo RUTA 03"
                    />
                  </div>
                )}
              </fieldset>
            )}

            <div className="flex flex-wrap items-center gap-3 md:col-span-2">
              <Button type="submit">
                {upload.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <Upload aria-hidden />}
                {upload.isPending ? "Subiendo y revisando…" : "Subir y revisar"}
              </Button>
              {upload.isPending && (
                <span role="status" className="text-sm text-muted-foreground">
                  Revisando el archivo. Un export de un mes puede tardar unos segundos.
                </span>
              )}
              {problem && (
                <span role="alert" className="text-sm text-danger">
                  {problem}
                </span>
              )}
            </div>
          </fieldset>
        </form>
      </CardContent>
    </Card>
  );
}
