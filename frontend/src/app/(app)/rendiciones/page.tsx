import { CalendarClock } from "lucide-react";
import type { Metadata } from "next";

import { EmptyState } from "@/components/common/empty-state";
import { SettlementsLogo } from "@/modules/settlements/logo";

export const metadata: Metadata = { title: "Rendiciones" };

export default function SettlementsHomePage() {
  return (
    <>
      <div className="space-y-3">
        <SettlementsLogo height={64} priority />
        <h1 className="sr-only">Rendiciones</h1>
        <p className="max-w-2xl text-sm text-muted-foreground">
          Qué dinero debía ingresar, qué ingresó y por qué medio, qué quedó pendiente, qué gastos lo justificaron y
          dónde terminó cada valor.
        </p>
      </div>
      <EmptyState
        icon={CalendarClock}
        title="El módulo está habilitado"
        description="Ya podés usar Rendiciones de reparto, en Operación diaria, y Cajas, en Tesorería. Las próximas funciones llegan por etapas: pendientes (R3), cartera de cheques (R4) y combustible (R5)."
      />
    </>
  );
}
