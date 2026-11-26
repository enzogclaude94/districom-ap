"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, MapPin } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { mastersApi, mastersQueryKey, type ZoneDetail, type ZoneRule } from "@/lib/api/masters";
import { formatDate, todayIso } from "@/lib/format";

import { MASTERS, WEEKDAYS, ZONE_ORIGINS, ZONE_ORIGIN_TONES, ZONE_TYPES, daysLabel } from "./labels";
import { EditDialog, Fact, Field, selectClass, useMastersAccess, yesNo } from "./shared";

const zonesKey = [...mastersQueryKey, "zones"] as const;
const rulesKey = [...mastersQueryKey, "zone-rules"] as const;
/** Types that are never visited (the backend refuses a visit for them). */
const NOT_VISITED = ["OUTLET", "UNCLASSIFIED"];

export const RULES_REMINDER =
  "Una regla nueva o modificada solo afecta zonas que aparezcan después. No cambia automáticamente zonas existentes.";

function DayPicker({
  days,
  onChange,
  disabled,
}: {
  days: number[];
  onChange: (d: number[]) => void;
  disabled: boolean;
}) {
  return (
    <fieldset disabled={disabled} className="space-y-1.5">
      <legend className="text-sm font-medium">Días de visita</legend>
      <div className="flex flex-wrap gap-3">
        {WEEKDAYS.map(([day, , long]) => (
          <label key={day} className="flex items-center gap-1.5 text-sm">
            <input
              type="checkbox"
              checked={days.includes(day)}
              onChange={(e) => onChange(e.target.checked ? [...days, day].sort() : days.filter((d) => d !== day))}
            />
            {long}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function VisitFields({
  type,
  setType,
  visit,
  setVisit,
  days,
  setDays,
  allowUnclassified,
}: {
  type: string;
  setType: (v: string) => void;
  visit: boolean;
  setVisit: (v: boolean) => void;
  days: number[];
  setDays: (v: number[]) => void;
  allowUnclassified: boolean;
}) {
  const noVisit = NOT_VISITED.includes(type);
  return (
    <>
      <Field label="Tipo de zona">
        {(id) => (
          <select
            id={id}
            className={selectClass}
            value={type}
            onChange={(e) => {
              setType(e.target.value);
              if (NOT_VISITED.includes(e.target.value)) {
                setVisit(false);
                setDays([]);
              }
            }}
          >
            {Object.entries(ZONE_TYPES)
              .filter(([value]) => allowUnclassified || value !== "UNCLASSIFIED")
              .map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
          </select>
        )}
      </Field>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={visit}
          disabled={noVisit}
          onChange={(e) => {
            setVisit(e.target.checked);
            if (!e.target.checked) setDays([]);
          }}
        />
        Se visita
        {noVisit && <span className="text-xs text-muted-foreground">(este tipo de zona no se visita)</span>}
      </label>
      <DayPicker days={days} onChange={setDays} disabled={!visit} />
    </>
  );
}

function ConfigureDialog({ zone, onClose }: { zone: ZoneDetail; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(zone.display_name);
  const [type, setType] = useState(zone.zone_type);
  const [visit, setVisit] = useState(zone.requires_visit);
  const [days, setDays] = useState(zone.weekdays);
  const [from, setFrom] = useState(todayIso());
  const [active, setActive] = useState(zone.active);
  const save = useMutation({
    mutationFn: (reason: string) =>
      mastersApi.configureZone(zone.id, {
        zone_type: type,
        requires_visit: visit,
        weekdays: days,
        effective_from: from,
        display_name: name.trim(),
        active,
        reason,
        expected_updated_at: zone.updated_at,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: zonesKey });
      onClose();
    },
  });
  return (
    <EditDialog
      title={`Configurar ${zone.source_name}`}
      description="Desde ahora la zona queda configurada a mano y ninguna regla la vuelve a tocar. Configurar una zona no reasigna clientes."
      onClose={onClose}
      onSave={(reason) => save.mutate(reason)}
      pending={save.isPending}
      error={save.error}
    >
      <Field label="Nombre visible">
        {(id) => <Input id={id} value={name} maxLength={120} required onChange={(e) => setName(e.target.value)} />}
      </Field>
      <VisitFields
        type={type}
        setType={setType}
        visit={visit}
        setVisit={setVisit}
        days={days}
        setDays={setDays}
        allowUnclassified
      />
      <Field label="Vigente desde" hint="Los días se cambian hacia adelante: no antes del último cambio.">
        {(id) => <Input id={id} type="date" value={from} required onChange={(e) => setFrom(e.target.value)} />}
      </Field>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
        Activa
      </label>
    </EditDialog>
  );
}

function RuleDialog({ rule, onClose }: { rule: ZoneRule | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [pattern, setPattern] = useState("");
  const [priority, setPriority] = useState(String(rule?.priority ?? 100));
  const [type, setType] = useState(rule?.zone_type ?? "PRESALE");
  const [visit, setVisit] = useState(rule?.requires_visit ?? true);
  const [days, setDays] = useState(rule?.weekdays ?? []);
  const [description, setDescription] = useState(rule?.description ?? "");
  const [active, setActive] = useState(rule?.active ?? true);
  const save = useMutation({
    mutationFn: (reason: string) => {
      const common = {
        priority: Number(priority) || 0,
        zone_type: type,
        requires_visit: visit,
        weekdays: days,
        description: description.trim() || null,
      };
      return rule === null
        ? mastersApi.createZoneRule({ ...common, pattern: pattern.trim() })
        : mastersApi.updateZoneRule(rule.id, { ...common, active, reason, expected_updated_at: rule.updated_at });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: rulesKey });
      onClose();
    },
  });
  return (
    <EditDialog
      title={rule ? `Modificar la regla «${rule.pattern}»` : "Nueva regla de zonas"}
      description={RULES_REMINDER}
      onClose={onClose}
      onSave={(reason) => save.mutate(reason)}
      pending={save.isPending}
      error={save.error}
      requireReason={rule !== null}
    >
      {rule === null && (
        <Field label="El nombre de la zona contiene" hint="Sin distinguir mayúsculas ni acentos.">
          {(id) => (
            <Input id={id} value={pattern} maxLength={120} required onChange={(e) => setPattern(e.target.value)} />
          )}
        </Field>
      )}
      <Field label="Prioridad" hint="Gana la de número menor cuando varias coinciden.">
        {(id) => <Input id={id} type="number" value={priority} onChange={(e) => setPriority(e.target.value)} />}
      </Field>
      <VisitFields
        type={type}
        setType={setType}
        visit={visit}
        setVisit={setVisit}
        days={days}
        setDays={setDays}
        allowUnclassified={false}
      />
      <Field label="Descripción">
        {(id) => <Input id={id} value={description} maxLength={500} onChange={(e) => setDescription(e.target.value)} />}
      </Field>
      {rule !== null && (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
          Activa
        </label>
      )}
    </EditDialog>
  );
}

function ZoneRules() {
  const { isAdmin } = useMastersAccess();
  const rules = useQuery({ queryKey: rulesKey, queryFn: mastersApi.zoneRules });
  const [editing, setEditing] = useState<ZoneRule | null | undefined>(undefined);
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
        <div className="space-y-1">
          <CardTitle>Reglas de clasificación de zonas</CardTitle>
          <p className="text-sm text-muted-foreground">{RULES_REMINDER}</p>
        </div>
        {isAdmin && (
          <Button size="sm" variant="secondary" onClick={() => setEditing(null)}>
            Nueva regla
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {rules.isPending ? (
          <Skeleton className="h-20" />
        ) : rules.isError ? (
          <ErrorState error={rules.error} onRetry={() => void rules.refetch()} />
        ) : rules.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">No hay reglas: toda zona nueva queda sin configurar.</p>
        ) : (
          <Table stacked>
            <TableHeader>
              <TableRow>
                <TableHead>Prioridad</TableHead>
                <TableHead>Contiene</TableHead>
                <TableHead>Tipo</TableHead>
                <TableHead>Días</TableHead>
                <TableHead>Estado</TableHead>
                {isAdmin && <TableHead className="sr-only">Acciones</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rules.data.map((r) => (
                <TableRow key={r.id}>
                  <TableCell data-label="Prioridad" className="tabular-nums">
                    {r.priority}
                  </TableCell>
                  <TableCell data-label="Contiene">
                    <span className="font-medium">{r.pattern}</span>
                    {r.description && <span className="block text-xs text-muted-foreground">{r.description}</span>}
                  </TableCell>
                  <TableCell data-label="Tipo">{ZONE_TYPES[r.zone_type] ?? r.zone_type}</TableCell>
                  <TableCell data-label="Días">{r.requires_visit ? daysLabel(r.weekdays) : "No se visita"}</TableCell>
                  <TableCell data-label="Estado">
                    {r.active ? <Badge tone="success">Activa</Badge> : <Badge>Inactiva</Badge>}
                  </TableCell>
                  {isAdmin && (
                    <TableCell data-label="Acciones">
                      <Button variant="ghost" size="sm" onClick={() => setEditing(r)}>
                        Modificar
                      </Button>
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
      {editing !== undefined && <RuleDialog rule={editing} onClose={() => setEditing(undefined)} />}
    </Card>
  );
}

/** `/comercial/datos/maestros/zonas`: zones as imports created them, how each is configured, and the rules. */
export function ZoneList() {
  const zones = useQuery({ queryKey: zonesKey, queryFn: mastersApi.zones });
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Zonas</CardTitle>
          <p className="text-sm text-muted-foreground">
            Las zonas llegan con el maestro de clientes. Acá se configura su tipo y sus días de visita; la zona de cada
            cliente la asigna el maestro.
          </p>
        </CardHeader>
        <CardContent>
          {zones.isPending ? (
            <Skeleton className="h-20" />
          ) : zones.isError ? (
            <ErrorState error={zones.error} onRetry={() => void zones.refetch()} />
          ) : zones.data.length === 0 ? (
            <EmptyState
              icon={MapPin}
              title="Todavía no hay zonas"
              description="Aparecen al importar el maestro de clientes de STORE SYS."
            />
          ) : (
            <Table stacked>
              <TableHeader>
                <TableRow>
                  <TableHead>Zona en STORE SYS</TableHead>
                  <TableHead>Nombre visible</TableHead>
                  <TableHead>Tipo</TableHead>
                  <TableHead>Días de visita</TableHead>
                  <TableHead>Configuración</TableHead>
                  <TableHead>Estado</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {zones.data.map((z) => (
                  <TableRow key={z.id}>
                    <TableCell data-label="Zona en STORE SYS">
                      <Link
                        href={`${MASTERS}/zonas/${z.id}`}
                        className="font-medium underline-offset-2 hover:underline"
                      >
                        {z.source_name}
                      </Link>
                    </TableCell>
                    <TableCell data-label="Nombre visible">{z.display_name}</TableCell>
                    <TableCell data-label="Tipo">{ZONE_TYPES[z.zone_type] ?? z.zone_type}</TableCell>
                    <TableCell data-label="Días de visita">
                      {z.requires_visit ? daysLabel(z.weekdays) : "No se visita"}
                    </TableCell>
                    <TableCell data-label="Configuración">
                      <Badge tone={ZONE_ORIGIN_TONES[z.configuration_origin]}>
                        {ZONE_ORIGINS[z.configuration_origin] ?? z.configuration_origin}
                      </Badge>
                    </TableCell>
                    <TableCell data-label="Estado">
                      {z.active ? <Badge tone="success">Activa</Badge> : <Badge>Inactiva</Badge>}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      <ZoneRules />
    </div>
  );
}

/** `/comercial/datos/maestros/zonas/[id]`: one zone, the rule that classified it and its visit-day history. */
export function ZoneDetailView({ zoneId }: { zoneId: number }) {
  const { isAdmin } = useMastersAccess();
  const [editing, setEditing] = useState(false);
  const zone = useQuery({ queryKey: [...zonesKey, "detail", zoneId], queryFn: () => mastersApi.zone(zoneId) });
  const rules = useQuery({ queryKey: rulesKey, queryFn: mastersApi.zoneRules });
  if (zone.isPending) return <Skeleton className="h-40" />;
  if (zone.isError) return <ErrorState error={zone.error} onRetry={() => void zone.refetch()} />;
  const z = zone.data;
  const rule = z.rule_id !== null ? rules.data?.find((r) => r.id === z.rule_id) : undefined;
  return (
    <div className="space-y-4">
      <Link
        href={`${MASTERS}/zonas`}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:underline"
      >
        <ArrowLeft className="size-4" aria-hidden /> Zonas
      </Link>
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <CardTitle>{z.display_name}</CardTitle>
            <Badge tone={ZONE_ORIGIN_TONES[z.configuration_origin]}>
              {ZONE_ORIGINS[z.configuration_origin] ?? z.configuration_origin}
            </Badge>
          </div>
          {isAdmin && (
            <Button size="sm" onClick={() => setEditing(true)}>
              Configurar zona
            </Button>
          )}
        </CardHeader>
        <CardContent>
          <dl className="grid gap-4 sm:grid-cols-3">
            <Fact label="Nombre en STORE SYS">{z.source_name}</Fact>
            <Fact label="Tipo">{ZONE_TYPES[z.zone_type] ?? z.zone_type}</Fact>
            <Fact label="Se visita">{yesNo(z.requires_visit)}</Fact>
            <Fact label="Días vigentes">{z.requires_visit ? daysLabel(z.weekdays) : "No se visita"}</Fact>
            <Fact label="Regla de origen">
              {rule ? `«${rule.pattern}»${rule.description ? ` · ${rule.description}` : ""}` : null}
            </Fact>
            <Fact label="Configurada a mano">{z.configured_at ? formatDate(z.configured_at) : null}</Fact>
            <Fact label="Activa">{yesNo(z.active)}</Fact>
          </dl>
          {z.configuration_origin === "NONE" && (
            <p className="mt-4 text-sm text-warning">
              Ninguna regla clasifica esta zona. Queda sin configurar hasta que un admin la configure.
            </p>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Historia de días de visita</CardTitle>
        </CardHeader>
        <CardContent>
          {z.visit_days.length === 0 ? (
            <p className="text-sm text-muted-foreground">Sin días de visita registrados.</p>
          ) : (
            <Table stacked>
              <TableHeader>
                <TableRow>
                  <TableHead>Día</TableHead>
                  <TableHead>Desde</TableHead>
                  <TableHead>Hasta</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {z.visit_days.map((d) => (
                  <TableRow key={d.id} className={d.voided_at ? "text-muted-foreground line-through" : undefined}>
                    <TableCell data-label="Día">{WEEKDAYS.find(([w]) => w === d.weekday)?.[2]}</TableCell>
                    <TableCell data-label="Desde">{formatDate(d.valid_from)}</TableCell>
                    <TableCell data-label="Hasta">{d.valid_to ? formatDate(d.valid_to) : "Vigente"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      {editing && <ConfigureDialog zone={z} onClose={() => setEditing(false)} />}
    </div>
  );
}
