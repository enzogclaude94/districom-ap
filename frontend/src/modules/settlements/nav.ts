import { ClipboardList, Fuel, Home, Landmark, Users } from "lucide-react";

import type { NavSection } from "@/lib/modules";

/** Rendiciones navigation. Items stay disabled until their roadmap phase (R1–R8) ships. */
export const SETTLEMENTS_NAV: NavSection[] = [
  { label: "Inicio", icon: Home, href: "/rendiciones", exact: true },
  {
    label: "Operación diaria",
    icon: ClipboardList,
    items: [
      { label: "Rendiciones de reparto", href: "/rendiciones/repartos" },
      { label: "Boca de expendio", href: "/rendiciones/boca", phase: 7 },
      { label: "Pendientes", href: "/rendiciones/pendientes", phase: 3 },
    ],
  },
  {
    label: "Tesorería",
    icon: Landmark,
    items: [
      { label: "Cajas", href: "/rendiciones/cajas" },
      { label: "Cartera de cheques", href: "/rendiciones/cheques", phase: 4 },
      { label: "Pagos", href: "/rendiciones/pagos", phase: 6 },
    ],
  },
  { label: "Combustible", icon: Fuel, href: "/rendiciones/combustible", phase: 5 },
  { label: "Liquidación semanal", icon: Users, href: "/rendiciones/liquidaciones", phase: 8 },
];
