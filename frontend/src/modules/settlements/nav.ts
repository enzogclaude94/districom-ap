import { ClipboardList, Fuel, Home, Landmark, SlidersHorizontal, Users } from "lucide-react";

import type { NavSection } from "@/lib/modules";
import { PendingAlertBadge } from "@/modules/settlements/pending/alerts";

/** Rendiciones navigation. Items stay disabled until their roadmap phase (R1–R8) ships. */
export const SETTLEMENTS_NAV: NavSection[] = [
  { label: "Inicio", icon: Home, href: "/rendiciones", exact: true },
  {
    label: "Operación diaria",
    icon: ClipboardList,
    items: [
      { label: "Rendiciones de reparto", href: "/rendiciones/repartos" },
      { label: "Rendiciones de boca", href: "/rendiciones/boca" },
      { label: "Pendientes", href: "/rendiciones/pendientes", indicator: PendingAlertBadge },
    ],
  },
  {
    label: "Tesorería",
    icon: Landmark,
    items: [
      { label: "Cajas", href: "/rendiciones/cajas" },
      { label: "Cartera de cheques", href: "/rendiciones/cheques" },
      { label: "Pagos", href: "/rendiciones/pagos" },
    ],
  },
  { label: "Combustible", icon: Fuel, href: "/rendiciones/combustible" },
  { label: "Liquidación semanal", icon: Users, href: "/rendiciones/liquidaciones", phase: 8 },
  { label: "Configuración del módulo", icon: SlidersHorizontal, href: "/rendiciones/configuracion" },
];
