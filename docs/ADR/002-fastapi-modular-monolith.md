# ADR 002 — FastAPI y monolito modular

- Estado: Aceptado · 2026-10-02

## Contexto
3 preventistas, ~600 clientes, pocos usuarios internos. Mucha lógica de datos, reglas y scoring que puede evolucionar hacia estadística/ML.

## Decisión
Backend único en Python + FastAPI, organizado por dominio (`imports/`, `analytics/`, `customer_status/`, `opportunities/`…). Frontend Next.js separado. Un proceso por capa, sin microservicios, colas ni brokers.

## Consecuencias
- Despliegue simple (Docker Compose en un host).
- Estado en memoria (p. ej. rate limit de login) válido mientras haya una sola instancia del backend; si se escala horizontalmente, mover a PostgreSQL.
- Los módulos operacionales se comunican por servicios Python, lo que permite separarlos en el futuro.
- Excepción deliberada: `analytics/` hace consultas de solo lectura que cruzan directamente el modelo canónico (ARCHITECTURE.md §4.1), para no encadenar servicios artificialmente en consultas BI.
