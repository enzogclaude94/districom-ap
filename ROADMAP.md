# ROADMAP — DistriCom

> v0.4 · 2026-10-04. v0.4: carril **Rendiciones** (ADR 007); PL‑1 en DONE; empleados y roles por módulo adelantados a R0.
> Estados: `PLANNED`, `PREPARATION` (análisis previo, sin implementación), `IN_PROGRESS`, `BLOCKED`, `DONE`.
> Una fase es `DONE` solo si cumple la Definition of Done de fase (`CLAUDE.md §7`) y todos sus criterios críticos están en PASS.

DistriCom es una plataforma modular (ADR 006). Este roadmap tiene tres carriles: **Plataforma** (núcleo compartido), **Inteligencia Comercial** y **Rendiciones** (ADR 007). Otros módulos no tienen fases hasta que se aprueben.

### Plataforma

| Ítem | Nombre | Estado | Depende de |
|---|---|---|---|
| PL‑1 | Preparación modular (P0‑1 … P0‑7 de la revisión del 2026-10-03) | **DONE** (PR #3, mergeado el 2026-10-04) | Fase 0 |
| PL‑2a | Empleados: `employees` + `employee_functions` con alta manual y registro de funciones por módulo | **DONE** (R0, ADR 007) | PL‑1 |
| PL‑2b | Identidades `customers`, `localities`, vinculación por imports y framework de importación | PLANNED (se construye dentro de la Fase 1) | PL‑2a |
| PL‑3 | Roles por módulo (`user_module_roles`, `viewer < operator < admin`) y selector de módulos | **DONE** (R0) | PL‑1 |
| PL‑4 | Pantalla de gestión de usuarios | PLANNED (mientras tanto, CLI) | PL‑3 |

### Inteligencia Comercial

| Fase | Nombre | Estado | Depende de |
|---|---|---|---|
| 0 | Fundación y arquitectura | **DONE** (aprobada y mergeada el 2026-10-02, PR #1) | — |
| 1 | Importación y maestros | **PREPARATION** (análisis de exportaciones reales, `docs/DATA_INTAKE.md`) | 0 · PL‑1 · PL‑2a · muestras reales y aprobación de Enzo para implementar |
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

### Rendiciones

| Fase | Nombre | Estado | Depende de |
|---|---|---|---|
| R0 | Habilitación del módulo | **DONE** (aprobada por Enzo el 2026-10-04) | PL‑1 |
| R1 | Ledger y cajas | **IN_PROGRESS** (aprobada el 2026-10-04; R1‑1 aprobado, R1‑2 en revisión) | R0 |
| R2 | Rendición de reparto básica | PLANNED | R1 |
| R3 | Pendientes de cobro | PLANNED | R2 |
| R4 | Cartera de cheques | PLANNED | R2 |
| R5 | Combustible y vehículos | PLANNED | R2 |
| R6 | Pagos a proveedores y terceros | PLANNED | R1 · R4 |
| R7 | Boca de expendio | PLANNED | R2–R5 |
| R8 | Cuenta interna de empleados y liquidación semanal | PLANNED | R1 · R2 |
| R9 | Endurecimiento | PLANNED | todas |

Cada fase empieza solo con aprobación explícita de Enzo y termina con un informe y una pausa para revisión.

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

---

## Rendiciones — detalle por fase

Modelo conceptual en `DATA_MODEL.md §13`; decisiones en ADR 007. Cada fase crea solo sus tablas, con migración `settlements: …`, tests desde el inicio y docs actualizadas.

### R0 — Habilitación del módulo
**Alcance:** ADR 007 y documentación; `employees` + `employee_functions` en plataforma (servicio, API, alta/edición/baja lógica, registro de funciones por módulo, pantalla de administración); `user_module_roles` (`viewer < operator < admin`) con CLI de asignación y `require_module_access` adaptado; módulo `settlements` (schema vacío, router, parámetros, función `settlements.delivery_driver`); `/rendiciones` con el logo oficial; home de plataforma con selector de módulos; bloqueo de rutas de módulos sin acceso.
**Fuera de alcance:** ledger, cajas, rendiciones, pendientes, cheques, gastos, combustible, pagos, liquidaciones (ninguna tabla de negocio de Rendiciones).
**Criterios:** suite anterior en verde; un usuario sin rol en Rendiciones recibe 403 en `/api/v1/settlements/*` y ve "sin acceso" en `/rendiciones`; un usuario con dos módulos cambia entre ellos; los contratos de dependencias fallan si un módulo importa otro; funciones de empleado sin solapamientos (verificado en la base) y sin UPDATE in place; migraciones up/down/up.

### R1 — Ledger y cajas
**Alcance:** `accounts`, `financial_operations`, `ledger_entries`; servicio de operaciones (balance, idempotencia, reversos); Caja principal, Caja de cambio y Banco; aportes y devoluciones de socios, depósitos, movimientos entre cajas.
**Resultado visible:** pantalla Cajas con saldos y movimientos con su origen.
**Criterios:** suma cero garantizada por la base; doble envío no duplica; mismo id con otro contenido es conflicto; reverso deja saldo y rastro; saldos = suma de entradas; cajas y Banco nunca negativos, ni hoy ni históricamente, también con operaciones simultáneas; ajustes solo admin con motivo; auditoría.
**Decisiones de Enzo (2026-10-04):** cajas solo por importe, sin denominaciones (P2); cuentas de aportes por socio creadas por un admin, sin cantidad ni nombres fijos (P3); cuentas técnicas Saldo inicial y Ajustes (D4); sin negativo en cajas, Banco arranca sin negativo (D5); idempotencia con huella y única puerta de escritura (ADR 008). En R2 el efectivo de una rendición podrá repartirse entre Caja principal y Caja de cambio (P1): ningún código asume una caja destino fija.
**PRs:** R1‑1 núcleo del ledger (migración `0005`, servicio, invariantes, tests) · R1‑2 API de cajas · R1‑3 pantalla Cajas. Cada uno espera la aprobación de Enzo antes del siguiente.

### R2 — Rendición de reparto básica
**Alcance:** rendición (fecha, repartidor, importe esperado con origen `MANUAL`), conteo de efectivo por denominación con agregado, transferencias verificadas, gastos con categorías editables, diferencia y su asignación a uno o varios empleados, cierre atómico hacia el ledger, reapertura y corrección por admin con reverso.
**Resultado visible:** pantalla de rendición con tablero fijo (esperado, efectivo, transferencias, cheques, gastos, pendientes, justificado, diferencia).

### R3 — Pendientes de cobro
**Alcance:** pendiente desde la rendición, pantalla global, alertas internas calculadas al leer (`settlements.pending_alert_hours`), acreditación por cualquier medio con su operación.

### R4 — Cartera de cheques
**Alcance:** cheque único (físico / ECHEQ) con titular y entregador, historial de estados (en cartera, depositado, rechazado, entregado con destinatario), alta desde rendición, pendiente u operación extraordinaria.

### R5 — Combustible y vehículos
**Alcance:** vehículos; carga de combustible como detalle de un gasto (camión, empleado, litros, precio por litro), desde rendición o desde caja; historial de combustible.

### R6 — Pagos a proveedores y terceros
**Alcance:** terceros; pagos realizados con uno o varios medios (efectivo de una caja, transferencia, cheques de cartera), cuya suma es el total.

### R7 — Boca de expendio
**Alcance:** boca como punto que rinde (`outlets`), una rendición diaria con entregas parciales, misma lógica de rendición.

### R8 — Empleados: cuenta interna y liquidación semanal
**Alcance:** condiciones de pago, consumos, adelantos, préstamos y cuotas, liquidación semanal (sueldo, bonos, incentivos, faltantes, sobrantes) y su pago.

### R9 — Endurecimiento
**Alcance:** reportes de cierre diario, contrato para recibir el importe esperado desde Store Sys (sin implementar la integración), performance, E2E del flujo de punta a punta.
