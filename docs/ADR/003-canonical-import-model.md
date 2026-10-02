# ADR 003 — Modelo canónico de importación con adaptadores

- Estado: Aceptado · 2026-10-02

## Contexto
Hoy los datos llegan por Excel/CSV de AXUM y STORE SYS; mañana pueden llegar por API. Los nombres de columnas varían entre exportaciones.

## Decisión
`Source Adapter → Normalización → Resolución de entidades → Modelo canónico → Analítica`. La analítica solo lee el modelo canónico. Mapping de columnas configurable y guardado como plantilla. Sin librería de dataframes en V1 (`openpyxl` read-only + `csv`), validación por fila con Pydantic.

## Consecuencias
- Reemplazar Excel por API = un adaptador nuevo.
- Cada fila importada es trazable a su batch.
- Se reevalúa Polars si un import supera ~1 M de filas.
