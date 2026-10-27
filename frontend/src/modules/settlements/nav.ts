import { ClipboardList, Fuel, Home, Landmark, PlugZap, SlidersHorizontal, Users } from "lucide-react";

import type { NavSection } from "@/lib/modules";
import { PendingAlertBadge } from "@/modules/settlements/pending/alerts";

/** Rendiciones navigation. Items stay disabled until their roadmap phase (R1–R9) ships. */
export const SETTLEMENTS_NAV: NavSection[] = [
  { label: "Inicio", icon: Home, href: "/rendiciones", exact: true },
  {
    label: "Operación diaria",
    icon: ClipboardList,
    items: [
      { label: "Rendiciones de reparto", href: "/rendiciones/repartos" },
      { label: "Bocas", href: "/rendiciones/boca" },
      { label: "Pendientes", href: "/rendiciones/pendientes", indicator: PendingAlertBadge },
    ],
  },
  {
    label: "Tesorería",
    icon: Landmark,
    items: [
      { label: "Cajas", href: "/rendiciones/cajas" },
      { label: "Cierre del día", href: "/rendiciones/cierre-del-dia" },
      { label: "Cartera de cheques", href: "/rendiciones/cheques" },
      { label: "Pagos", href: "/rendiciones/pagos" },
    ],
  },
  { label: "Combustible", icon: Fuel, href: "/rendiciones/combustible" },
  // Protected area (ADR 010): listed only with an explicit grant, not even for the platform admin.
  {
    label: "Empleados",
    icon: Users,
    area: "settlements.employee_finance",
    items: [
      { label: "Cuentas internas", href: "/rendiciones/empleados", exact: true },
      { label: "Liquidaciones internas", href: "/rendiciones/empleados/liquidaciones" },
      { label: "Pagos de liquidaciones", href: "/rendiciones/empleados/pagos" },
    ],
  },
  // Protected area (R9-1): Store Sys codes of the employees, also only with an explicit grant.
  {
    label: "Integración Store Sys",
    icon: PlugZap,
    area: "settlements.store_sys_import",
    href: "/rendiciones/integracion-store-sys",
  },
  { label: "Configuración del módulo", icon: SlidersHorizontal, href: "/rendiciones/configuracion" },
];
