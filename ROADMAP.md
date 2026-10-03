# ROADMAP — DistriCom

> v0.3 · 2026-10-03. Estados: `PLANNED`, `PREPARATION` (análisis previo, sin implementación), `IN_PROGRESS`, `BLOCKED`, `DONE`.
> Una fase es `DONE` solo si cumple la Definition of Done de fase (`CLAUDE.md §7`) y todos sus criterios críticos están en PASS.

DistriCom es una plataforma modular (ADR 006). Este roadmap tiene dos carriles: **Plataforma** (núcleo compartido) e **Inteligencia Comercial**, el único módulo en desarrollo. Otros módulos no tienen fases hasta que se aprueben.

### Plataforma

| Ítem | Nombre | Estado | Depende de |
|---|---|---|---|
| PL‑1 | Preparación modular (P0‑1 … P0‑7 de la revisión del 2026-10-03) | **IN_PROGRESS** | Fase 0 |
| PL‑2 | Identidades compartidas (`employees`, `employee_functions`, `customers`, `localities`) y framework de importación | PLANNED (se construye dentro de la Fase 1) | PL‑1 |
| PL‑3 | Roles por módulo (`user_module_roles`) y gestión de usuarios | PLANNED (solo cuando exista un segundo módulo) | PL‑1 |

### Inteligencia Comercial

| Fase | Nombre | Estado | Depende de |
|---|---|---|---|
| 0 | Fundación y arquitectura | **DONE** (aprobada y mergeada el 2026-10-02, PR #1) | — |
| 1 | Importación y maestros | **PREPARATION** (análisis de exportaciones reales, `docs/DATA_INTAKE.md`) | 0 · PL‑1 · muestras reales y aprobación de Enzo para implementar |
| 2 | Dashboard general | PLANNED | 1 |
| 3 | Rutas y preventistas | PLANNED | 2 |
| 4 | Cliente 360° y estados | PLANNED | 1 (puede ir en paralelo con 3) |
| 5 | Portfolio y penetración | PLANNED | 2, 4 |
| 6 | Motor de oportunidades y cross‑selling | PLANNED | 4, 5 |
| 7 | Riesgo, dormidos y recuperación | PLANNED | 4, 6 |
| 8 | Objetivos e incentivos | PLANNED | 3, 6, 7 |
| 9 | Reuniones, reportes y mensajes | PLANNED | 3–8 |
| 10 | Boca de expendio | PLANNED | 2 (independiente del resto) |
| 11 | Hardening y futuro | PLANNED | todas |

---

## Fase 0 — Fundación y arquitectura
**Alcance:** monorepo, Git, documentación, Docker Compose + PostgreSQL, FastAPI con health/errores/logging, Alembic, auth base (sesiones + roles), Next.js con sistema de diseño inicial y layout, tests base, CI, `.env.example`, ADRs, estrategia de demo data.
**Criterios:** F0‑1 … F0‑16 en `PLAN.md §7`. **Resultado:** todos en PASS (`PLAN.md §10`).

## Fase 1 — Importación y maestros
**Preparación (actual):** recibir y perfilar exportaciones reales de AXUM y STORE SYS, responder D3–D6 con datos y proponer mappings para aprobación (`PLAN.md §11`). La implementación empieza solo con autorización explícita de Enzo.
**Alcance:** identidades de plataforma (`employees` + función `commercial.salesperson` en `employee_functions`, `customers`, `localities`), maestros comerciales, asignaciones con historia, ventas/ítems, visitas, programación, pedidos, canales, `import_*`, `external_entity_mappings`, pipeline completo, pantalla Importaciones (subir, mapping, preview, errores, confirmación, historial), revisión de mappings pendientes, generador de demo data por pipeline, página inicial de Calidad de datos.
**Criterios:**
- XLSX y CSV aceptados; archivo inválido (extensión, tamaño, estructura) rechazado con mensaje útil.
- Columnas mapeables, plantilla guardable y auto‑detectada.
- **Importar dos veces el mismo archivo no cambia el conteo de ventas ni de ítems** (test obligatorio).
- Reimport con cambios actualiza y lo registra.
- Entidades desconocidas/ambiguas quedan en revisión, nunca fusionadas.
- Cada fila importada rastreable a su batch; batch con auditoría completa.
- Error crítico → rollback total y batch `FAILED` con evidencia.
- Demo data carga por pipeline con seed fijo.
- Ningún error silencioso (todo rechazo aparece en `import_errors`).
- Semántica de NC/devoluciones confirmada con datos reales (D4).

## Fase 2 — Dashboard general
**Alcance:** filtros globales en URL, períodos y comparación, KPIs jerarquizados (ventas, preventa/boca, compradores, activos, sin compra, nuevos, cobertura, cumplimiento de visitas, conversión, ticket, SKU, categorías), variaciones, tendencias/sparklines, drill‑down básico.
**Criterios:** cada KPI en `METRICS.md`; el período cambia todos los KPIs; comparaciones equivalentes; drill‑down funciona; carga con demo; tests de métricas pasan; ninguna métrica contradictoria entre vistas (test que compara el mismo KPI vía dos endpoints).

## Fase 3 — Rutas y preventistas
**Alcance:** control de ruta (cumplimiento de visitas programadas y cobertura de cartera como métricas separadas, no visitados, compradores, sin compra, conversión, venta, mix), comparación de rutas por dimensión, ficha 360° de preventista, análisis multidimensional (perfiles A–E del prompt).
**Criterios:** 50/40/31 → 80 % cobertura y 77,5 % conversión; el fixture de `route_execution` da 66,7 % de cumplimiento y 75 % de cobertura; el drill‑down de cobertura lista clientes y el de cumplimiento lista visitas cliente‑día; listas de componentes navegables; comparación de rutas sin ranking compuesto arbitrario; filtros; tests.

## Fase 4 — Cliente 360° y estados
**Alcance:** ficha de cliente, frecuencia esperada, overdue ratio, motor de estados con fallback, historia SCD‑2, backfill desde `history_start_date`, eventos de recuperación, productos habituales/ocasionales/abandonados/nuevos.
**Criterios:** clasificación no depende de días fijos; fixtures de distintos ritmos (5 vs 30 días); fallback registrado; estado explicable; recuperaciones registradas; 360° < 2 s.

## Fase 5 — Portfolio y penetración
**Alcance:** categorías, marcas, productos, penetración con universos auditables, breakdowns (ruta, preventista, tipo, localidad), matriz cliente × categoría paginada, cliente × producto filtrada, peers y benchmark (mediana, percentiles, tamaño de grupo).
**Criterios:** denominador auditable; segmentos chicos marcados; drill‑down compradores/no compradores; matrices con paginación server‑side; tests.

## Fase 6 — Motor de oportunidades y cross‑selling
**Alcance:** contrato de regla (id, versión, inputs, condiciones, score, motivos, exclusiones, vigencia), primeras reglas (`CATEGORY_CROSS_SELL`, `PRODUCT_CROSS_SELL`, `DORMANT_RECOVERY`, `AT_RISK_RETENTION`, `PRODUCT_REACTIVATION`, `VISITED_NO_PURCHASE`), `cross_sell_rules`, scoring 0–100 documentado en `docs/OPPORTUNITY_ENGINE.md` **solo con señales que los datos permiten**, prioridades, dedup, ciclo de vida, atribución de conversión.
**Criterios:** motivos visibles; sin duplicados diarios; scoring determinístico; reglas versionadas; cambios de estado; conversión detectada y medible; tests exhaustivos por regla.

## Fase 7 — Riesgo, dormidos y recuperación
**Alcance:** vistas de riesgo (señal y valor separados), dormidos, recuperados, campañas/listas simples, performance de recuperación por preventista, export CSV/XLSX y copia.
**Criterios:** riesgo usa frecuencia histórica; estados auditables; historia preservada; ventas por recuperación medibles; listas filtrables y exportables.

## Fase 8 — Objetivos e incentivos
**Alcance:** tipos de objetivo, progreso, faltante, tendencia, proyección lineal, motor de sugerencias de incentivos con baseline, candidatos y normalización por cartera; aprobación humana.
**Criterios:** múltiples tipos; baseline visible; progreso automático; cada sugerencia explica por qué; target considera cartera y candidatos reales; ningún incentivo se activa sin aprobación.

## Fase 9 — Reuniones, reportes y mensajes
**Alcance:** informe semanal de 15 secciones, lista de acciones con drill‑down, layout de impresión/PDF (CSS print), mensaje por preventista para copiar a WhatsApp.
**Criterios:** generado desde datos; cambios vs semana anterior correctos; oportunidades prioritarias incluidas; acciones abren clientes; PDF sin cortes absurdos; botón copiar funciona.

## Fase 10 — Boca de expendio
**Alcance:** ventas diarias, día de semana, hora (si hay timestamps), tickets, ticket promedio, top productos/categorías/marcas, co‑ocurrencia básica, tendencias.
**Criterios:** boca no contamina preventa; filtros correctos; heatmap solo si hay hora (si no, mensaje explícito); top productos y co‑ocurrencia verificables con fixtures.

## Fase 11 — Hardening y futuro
**Alcance:** performance medida, observabilidad, pulido UX, backup/restore probado, documentación final, readiness de APIs, puntos de extensión ML/IA (sin ML productivo).
**Criterios:** Definition of Done de V1 (`CLAUDE.md §7.3`).
