import { Boxes, Database, Home, Store, Target, Users } from "lucide-react";

import type { NavSection } from "@/lib/modules";

/** Inteligencia Comercial navigation (master prompt §105). Platform links are added by the Sidebar. */
export const COMMERCIAL_NAV: NavSection[] = [
  { label: "Inicio", icon: Home, href: "/comercial", exact: true },
  {
    label: "Equipo y clientes",
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
      { label: "Productos", href: "/comercial/portfolio/productos", phase: 5 },
      { label: "Categorías", href: "/comercial/portfolio/categorias", phase: 5 },
      { label: "Marcas", href: "/comercial/portfolio/marcas", phase: 5 },
      { label: "Penetración", href: "/comercial/portfolio/penetracion", phase: 5 },
    ],
  },
  {
    label: "Gestión",
    icon: Target,
    items: [
      { label: "Objetivos", href: "/comercial/gestion/objetivos", phase: 8 },
      { label: "Incentivos", href: "/comercial/gestion/incentivos", phase: 8 },
      { label: "Reuniones", href: "/comercial/gestion/reuniones", phase: 9 },
    ],
  },
  { label: "Boca de expendio", icon: Store, href: "/comercial/boca", phase: 10 },
  {
    label: "Datos",
    icon: Database,
    items: [
      { label: "Importaciones", href: "/comercial/datos/importaciones", phase: 1 },
      { label: "Calidad", href: "/comercial/datos/calidad", phase: 1 },
    ],
  },
];
