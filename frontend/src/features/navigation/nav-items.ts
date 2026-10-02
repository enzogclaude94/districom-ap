import type { LucideIcon } from "lucide-react";
import { Boxes, Database, Home, Settings, Store, Target, Users } from "lucide-react";

export interface NavItem {
  label: string;
  href: string;
  /** Roadmap phase that delivers the screen; disabled until it ships. */
  phase?: number;
}

export interface NavSection {
  label: string;
  icon: LucideIcon;
  href?: string;
  phase?: number;
  items?: NavItem[];
}

/** Sidebar hierarchy from the master prompt §105. */
export const NAV_SECTIONS: NavSection[] = [
  { label: "Inicio", icon: Home, href: "/" },
  {
    label: "Comercial",
    icon: Users,
    items: [
      { label: "Preventistas", href: "/comercial/preventistas", phase: 3 },
      { label: "Rutas", href: "/comercial/rutas", phase: 3 },
      { label: "Clientes", href: "/comercial/clientes", phase: 4 },
      { label: "Oportunidades", href: "/comercial/oportunidades", phase: 6 },
    ],
  },
  {
    label: "Portfolio",
    icon: Boxes,
    items: [
      { label: "Productos", href: "/portfolio/productos", phase: 5 },
      { label: "Categorías", href: "/portfolio/categorias", phase: 5 },
      { label: "Marcas", href: "/portfolio/marcas", phase: 5 },
      { label: "Penetración", href: "/portfolio/penetracion", phase: 5 },
    ],
  },
  {
    label: "Gestión",
    icon: Target,
    items: [
      { label: "Objetivos", href: "/gestion/objetivos", phase: 8 },
      { label: "Incentivos", href: "/gestion/incentivos", phase: 8 },
      { label: "Reuniones", href: "/gestion/reuniones", phase: 9 },
    ],
  },
  { label: "Boca de expendio", icon: Store, href: "/boca", phase: 10 },
  {
    label: "Datos",
    icon: Database,
    items: [
      { label: "Importaciones", href: "/datos/importaciones", phase: 1 },
      { label: "Calidad", href: "/datos/calidad", phase: 1 },
    ],
  },
  { label: "Configuración", icon: Settings, href: "/configuracion" },
];
