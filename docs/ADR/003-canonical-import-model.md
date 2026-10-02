# ADR 003 — Modelo canónico de importación con adaptadores

- Estado: Aceptado · 2026-10-02

## Contexto
Hoy los datos llegan por Excel/CSV de AXUM y STORE SYS; mañana pueden llegar por API. Los nombres de columnas varían entre exportaciones.

## Decisión
`TabularReader (formato) → SourceAdapter (sistema) → Normalización → Resolución de entidades → Modelo canónico → Analítica`. El lector de formato y el adaptador de sistema son capas distintas: el `SourceAdapter` recibe filas crudas (`RawRecord`) y no conoce XLSX, CSV ni XLS. La analítica solo lee el modelo canónico. Mapping de columnas configurable y guardado como plantilla. Sin librería de dataframes en V1 (`openpyxl` read-only + `csv`), validación por fila con Pydantic.

## Consecuencias
- Reemplazar Excel por API = un cliente de API que emite `RawRecord` (o un adaptador nuevo).
- Soportar `.xls` (probable en STORE SYS) = registrar un `TabularReader` nuevo; el parser se elige con los exports reales.
- Cada fila importada es trazable a su batch.
- Se reevalúa Polars si un import supera ~1 M de filas.
