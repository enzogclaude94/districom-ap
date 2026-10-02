# ADR 005 — Registro centralizado de métricas en el backend

- Estado: Aceptado · 2026-10-02

## Contexto
El prompt maestro exige que cada KPI tenga una única definición y que no se recalcule distinto en cada pantalla.

## Decisión
`METRICS.md` es la fuente de verdad. Cada métrica tiene un `id` estable implementado una sola vez en `backend/app/analytics/metrics/`. La API devuelve valor, numerador, denominador y referencia a la definición; el frontend solo formatea.

## Consecuencias
- Un KPI nuevo exige: definición en `METRICS.md` → test con fixture verificable a mano → implementación.
- El futuro asistente IA podrá consumir el mismo registro.
