# ADR 001 — PostgreSQL como única base de datos

- Estado: Aceptado · 2026-10-02

## Contexto
Datos relacionales (clientes, rutas, ventas, visitas) con historia, analítica por período y necesidad de integridad fuerte (deduplicación de imports, vigencias sin solapamiento).

## Decisión
PostgreSQL (17 en Docker/CI) como único almacenamiento. Sin MongoDB, Redis ni data warehouse separado en V1. Extensiones: `citext` (emails), `btree_gist` (exclusion constraints para vigencias).

## Consecuencias
- SQL analítico, índices parciales, exclusion constraints y vistas materializadas disponibles si hacen falta.
- Los tests de integración usan PostgreSQL real; SQLite no está soportado.
