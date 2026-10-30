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
| R1 | Ledger y cajas | **DONE** (aprobada por Enzo el 2026-10-04; PRs #7–#9 mergeados) | R0 |
| R2 | Rendición de reparto básica | **DONE** (aprobada por Enzo el 2026-10-05; PRs #10–#12 mergeados) | R1 |
| R3 | Pendientes de cobro | **DONE** (2026-10-07; R3‑1, R3‑2 y R3‑3 aprobados y mergeados, PRs #13, #14 y #16) | R2 |
| R4 | Cartera de cheques | **DONE** (aprobada por Enzo el 2026-10-09; PRs #17, #18 y #19) | R2 · R3 |
| R5 | Combustible y vehículos | **DONE** (R5‑1 a R5‑4; `0014`–`0017` congeladas) | R2 |
| R6 | Pagos a proveedores y terceros | **DONE** (2026-10-11) | R1 · R4 |
| R7 | Boca de expendio | **DONE** (2026-10-14; R7‑1 a R7‑4, ADR 009) | R2–R5 |
| R8 | Cuenta interna y liquidación de empleados | DONE (2026-10-26; arquitectura aprobada 2026-10-15, ADR 010; R8‑1 a R8‑5 DONE; `0025`–`0029` congeladas) | R1 · R2 |
| R9 | Endurecimiento | IN_PROGRESS (aprobada como etapa 2026-10-26; R9‑1 a R9‑4 DONE, R9‑5 en curso) | todas |

Cada fase empieza solo con aprobación explícita de Enzo y termina con un informe y una pausa para revisión.

### Servidor e infraestructura (ADR 013)

| Etapa | Nombre | Estado | Depende de |
|---|---|---|---|
| S0 | Arquitectura del servidor central, multiusuario y con acceso remoto | **DONE** (informe S0 v2 aprobado por Enzo el 2026-10-29 con las decisiones D1–D9) | — |
| S1 | Infraestructura de despliegue (Caddy, Tailscale, pgBackRest, scripts, runbook), sin despliegue productivo | IN_PROGRESS (autorizada por Enzo el 2026-10-29; PR #54 en revisión final, sin merge) | S0 |
| S2 | Plataforma: administración de usuarios, contraseñas y sesiones; IP real y modo de acceso; verificación de `Origin` | PLANNED | S1 |
| S3 | Piloto en el equipo real: dominio, Tailscale, bucket externo, pruebas LAN y remotas, simulacro de pérdida total | PLANNED (requiere hardware, dominio y contrataciones aprobadas) | S1 · S2 |
| S4 | Producción de Rendiciones en paralelo con el método actual | PLANNED | S3 |
| S5 | Endurecimiento y crecimiento (Comercial en el mismo servidor) | PLANNED | S4 |

Cada etapa termina con un informe y la aprobación de Enzo. Despliegue, red, backups, acceso remoto, operación y coordinación del CI son del hilo Servidor (D7, D9).

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
**PRs:** R1‑1 núcleo del ledger (migración `0005`, servicio, invariantes, tests) · R1‑2 API de cajas (migración `0006`, endpoints por rol) · R1‑3 pantalla Cajas. Cada uno espera la aprobación de Enzo antes del siguiente.

### R2 — Rendición de reparto básica
**Alcance:** rendición (fecha, repartidor, importe esperado con origen `MANUAL`), conteo de efectivo por denominación con agregado, transferencias verificadas, gastos con categorías editables, diferencia y su asignación a uno o varios empleados, cierre atómico hacia el ledger, reapertura y corrección por admin con reverso.
**Resultado visible:** pantalla de rendición con tablero fijo (esperado, efectivo, transferencias, cheques, gastos, pendientes, justificado, diferencia).
**Decisiones de Enzo (2026-10-04):**
- Un repartidor puede tener más de una rendición por día: `sequence_number` generado por el sistema, único por tipo, repartidor y día (R2‑D7).
- El efectivo se cuenta por denominación con un destino por línea (Caja principal o Caja de cambio). Cada denominación sugiere un destino configurable; el operador lo cambia (R2‑D3).
- Las denominaciones son el parámetro `settlements.cash_denominations`, con código estable. Arranca con billetes de $20.000, $10.000, $2.000, $1.000, $500, $200 y $100: la operación no usa monedas ni billetes de menos de $100 (R2‑D4, R2‑P4).
- Un agregado es efectivo que aparece después del conteo, o que se suma sin rehacerlo. Nunca modifica el conteo, no se edita ni se borra, y se anula con motivo (R2‑D5).
- Solo una transferencia `VERIFIED` justifica dinero. `DECLARED` y `REJECTED` no cuentan, y una rendición con transferencias `DECLARED` no se cierra. «El cliente transfiere mañana» es un pendiente (R3). El operator verifica o rechaza; un admin corrige una decisión con motivo, sin borrar la anterior (R2‑P3, R2‑P5).
- Faltante y sobrante se asignan a uno o varios empleados, a la empresa o a ambos, con suma exacta. La empresa solo con admin y motivo; un sobrante nunca es ingreso automático (R2‑D1, R2‑D2, R2‑P2).
- Cuentas `EMPLOYEE` solo para diferencias; sueldos, préstamos y liquidación siguen en R8 (R2‑D6).
- Reabrir no toca el ledger: el cierre anterior sigue vigente hasta el nuevo cierre, que publica atómicamente su reverso y el cierre nuevo en la fecha de la rendición, validando el efecto neto (R2‑P1).

**PRs:** R2‑1 rendición y carga, sin ledger (migración `0007`) · R2‑2 cierre y reapertura hacia el ledger · R2‑3 pantallas y E2E. Cada uno espera la aprobación de Enzo antes del siguiente.

### R3 — Pendientes de cobro
**Alcance:** pendiente desde la rendición, pantalla global, alertas internas calculadas al leer (`settlements.pending_alert_hours`), acreditación por cualquier medio con su operación.

**Decisiones (Enzo, 2026-10-05):** R3‑D1 a R3‑D7 y las ocho decisiones de la propuesta `PROPUESTA_R3_PENDIENTES_DE_COBRO.md`:
- el pendiente tiene identidad estable entre versiones de la rendición; cada cierre congela el importe que publicó (`settlement_closing_pendings`), que es la autoridad del importe financiero vigente;
- durante una corrección la carga es solo la próxima versión propuesta: el pendiente sigue vigente con el importe del cierre anterior hasta el nuevo cierre;
- cliente en texto (`customer_name`) hasta que exista `public.customers`; sin `customer_id` suelto;
- estados derivados `DRAFT`, `VOIDED`, `OPEN`, `PARTIAL`, `COLLECTED`, `RESOLVED`, sin columna de estado;
- `pending_collection_events` append-only como historia de dominio, independiente de `audit_events`;
- cuenta técnica «Pendientes de cobro» sin negativo; «Incobrables» separada de «Diferencias de rendición»;
- acreditaciones y resoluciones son operaciones propias, de documento: Cajas no las revierte; reverso solo admin con motivo;
- reabrir nunca se bloquea: se rechaza la corrección que deje un pendiente por debajo de lo aplicado (`pending_below_applied`), lo anule o le cambie el cliente con actividad.

**Decisiones de R3‑2 (Enzo, 2026-10-06):**
- D1: la fecha de un cobro o una resolución no es anterior a la rendición ni futura;
- D2: la lista global muestra por defecto `OPEN` y `PARTIAL`; los demás estados, por filtro;
- D3: revertir una resolución también revierte la parte de cada empleado, aunque su cuenta quede negativa (la empresa le debe); no se bloquea por movimientos posteriores;
- D4: un pendiente anulado no se reactiva;
- un reverso dice «la operación estaba mal», nunca es un hecho económico nuevo: lleva la fecha de negocio de la operación original y `created_at` registra cuándo se corrigió; los controles históricos de Caja, Banco y Pendientes se validan en esa fecha (puede dar `insufficient_funds`). Las devoluciones a clientes no se modelan por ahora;
- un admin puede cambiar el responsable de un pendiente después del cierre, sin ledger ni reapertura; es lo único de la carga que cambia con la rendición cerrada.

**Decisiones de R3‑3 (Enzo, 2026-10-07):**
- D1: el contador de pendientes en alerta va en el Inicio de Rendiciones (tarjeta con cantidad, saldo y enlace a la lista filtrada) y como insignia discreta en el menú; nunca como notificación invasiva;
- D2: a un operator se le ocultan las acciones que no puede usar (resolver, revertir, cambiar responsable); la API sigue siendo la autoridad;
- D3: un admin edita `settlements.pending_alert_hours` desde la configuración del módulo, guardado en `app_settings` (de 1 a 720 horas); sin scheduler, email, WhatsApp ni push;
- D4: un cobro se registra desde el detalle o como acción rápida de la lista, siempre releyendo el pendiente y con un paso de confirmación explícito.

**PRs:** R3‑1 pendientes en la rendición y en el cierre (migración `0009`) · R3‑2 acreditaciones, resoluciones y alertas (`0010`) · R3‑3 pantallas y E2E (sin migración). Cada uno espera la aprobación de Enzo antes del siguiente.

### R4 — Cartera de cheques
**Alcance:** cheque único (físico / ECHEQ) con titular y quien lo entregó separados, historial append-only, alta desde rendición, desde el cobro de un pendiente u operación extraordinaria, depósito, acreditación y rechazo.

**Decisiones (Enzo, 2026-10-07), antes de autorizar R4‑1:**
- R4‑D1: depositar no es dinero en Banco. Dos pasos: Cartera → Cheques depositados (en tránsito) al depositar; en tránsito → Banco al acreditar. Estados al menos en cartera, depositado, acreditado y rechazado, derivados de eventos.
- R4‑D2: un rechazo crea un **nuevo** pendiente de R3 a nombre de quien entregó el cheque, vinculado al cheque; el pendiente original no se reabre. Patas exactas según dónde estaba el cheque (en tránsito o ya acreditado).
- R4‑D3: un cheque físico recibido justifica la rendición (con quién lo recibió y cuándo); un ECHEQ necesita verificación, como una transferencia.
- R4‑D4: la entrega a terceros es de R6 (Pagos); R4 deja el cheque en cartera listo para usarse como medio de pago, sin operación de entrega genérica.
- Identidad robusta por tipo (físico vs. ECHEQ), importe e identidad inmutables una vez publicado, fechas de negocio separadas, cartera y en tránsito como «Valores», separados del efectivo.

**Arquitectura aprobada con ajustes (Enzo, 2026-10-07, decisiones D‑A…D‑K):** rechazo después de acreditar Banco −X / Pendientes +X aunque Banco quede en descubierto, como excepción estricta de `CHEQUE_REJECTION` y su reverso (R4‑2) · altas extraordinarias solo saldo inicial y aporte de socio, admin · `CHEQUE_RETURN` en R4‑2 · re-presentación y comisión fuera de R4 · un cheque mayor que el saldo del pendiente se rechaza · rechazo: operator o admin; revertirlo: admin con motivo · identidad de ECHEQ en dos niveles · catálogo de bancos por FK · responsable del pendiente de un rechazo: el repartidor por defecto · un ECHEQ declarado bloquea el cierre. Más: `payment_type` COMMON/DEFERRED aparte de `kind`, `VERIFICATION_REJECTED` como estado propio, `drawer_*` («Titular / librador») separado de quien lo entregó, snapshot `settlement_closing_cheques`, cuentas «Cartera de cheques» y «Cheques depositados».

| PR | Contenido | Estado |
|---|---|---|
| R4‑1 | Catálogo de bancos (`0011`), cheques físicos y ECHEQ, identidad y duplicados, fechas por tipo, eventos, estados derivados, verificación, Cartera y Cheques depositados, `ledger_entries.cheque_id`, cheques en la rendición y en su cierre (`0012`), snapshot y token, saldo inicial y aporte de socio, API y tests | **DONE** (aprobada por Enzo el 2026-10-07 con catálogo BCRA oficial y plazos como reglas del instrumento; PR #17 mergeado; `0011`/`0012` congeladas) |
| R4‑2 | Depósito, acreditación, rechazo (con la excepción de Banco en descubierto), devolución, nuevo pendiente por rechazo o devolución, cobrar un pendiente con cheque (`0013`) | **DONE** (aprobada por Enzo el 2026-10-08 tras verificar la regla de descubierto en la base; PR #18 mergeado; `0013` congelada) |
| R4‑3 | Pantallas: cartera global con filtros, Valores en Cajas, detalle e historia, cheques en la rendición, depósito, acreditación, rechazo, Banco en descubierto, devolución, cobro con cheque, reversos, vencimientos y E2E | **DONE** (PR #19, aprobada por Enzo el 2026-10-09; sin migraciones) |

### R5 — Combustible y vehículos
**Alcance:** vehículos; carga de combustible como detalle de un gasto (camión, empleado, litros, precio por litro), desde rendición o desde caja; historial de combustible.

**Decisiones iniciales (Enzo, 2026-10-09), antes de la propuesta:**
- R5‑D1: gasto desde Caja/Banco **general** (Caja o Banco −X, Gastos +X), no solo combustible. Combustible es una categoría con detalle especializado. Un solo concepto de gasto, originado en una rendición o directo desde Caja/Banco; sin segunda tabla de categorías ni cuenta paralela.
- R5‑D2: odómetro opcional, ≥ 0, conservado; sin bloquear lecturas menores a una anterior (más adelante, alertas).
- R5‑D3: se cargan litros y precio por litro; el backend calcula `importe = litros × precio`, redondeado a centavos. No hay total manual.
- R5‑D4: vehículos como maestro de plataforma (`public.vehicles`), patente normalizada y única; inactivo no se elige para cargas nuevas pero su historia sigue visible; nunca se borran.
- Propuesta `PROPUESTA_R5_COMBUSTIBLE_VEHICULOS_Y_GASTOS_DIRECTOS.md` **aprobada** por Enzo el 2026-10-09 con sus decisiones 1–4: gasto generalizado con origen rendición o directo; categoría `FUEL` con detalle `fuel_loads` 1:1; importe = `round(litros × precio, 2, HALF_UP)` guardado solo en `expenses.amount` (litros `numeric(10,3)`, precio `numeric(14,4)`); odómetro opcional; empleado obligatorio en combustible (en rendición, por defecto el repartidor; en gasto directo, elección explícita de un empleado activo; distinto de `created_by`); `DIRECT_EXPENSE` con fecha pasada o de hoy, nunca futura, fondos validados desde `business_date` y **sin** la excepción de `CHEQUE_REJECTION`; corrección por reverso + gasto nuevo; sin DELETE; historia derivada. `expense_categories.code` nullable, único e inmutable; `FUEL` puede desactivarse. Gastos `FUEL` históricos sin detalle solo con la marca explícita `legacy_fuel_without_detail` (nunca una exención por fechas).
- Vehículos (decisión 2): maestro de plataforma administrado por el **admin de plataforma**, modelo mínimo (sin marca, modelo ni año), patente normalizada y única por `plate_key`, formato visual calculado, identidad = id, nunca se borran.

| PR | Contenido | Estado |
|---|---|---|
| R5‑1 | Vehículos de plataforma (`0014`, plataforma): API, pantalla, auditoría | **DONE** (PR #21, aprobada por Enzo el 2026-10-09; `0014` congelada; pantalla `/vehiculos` como entrada propia del bloque de plataforma) |
| R5‑2 | `expense_categories.code`, `FUEL`, `legacy_fuel_without_detail` y `fuel_loads` en la rendición (`0015`) | **DONE** (PR #23, aprobada por Enzo el 2026-10-09 con sus desvíos documentados; `0015` congelada) |
| R5‑3 | Gasto directo desde Caja/Banco (`DIRECT_EXPENSE`, `0016`) | **DONE** (PR #25, aprobada por Enzo el 2026-10-09 con sus desvíos documentados; `0016` congelada) |
| R5‑4 | Historial consolidado de combustible (`/rendiciones/combustible`, ficha del vehículo) y E2E | **DONE** (PR #27, aprobada por Enzo el 2026-10-09 con la barrera fail safe de `0017`; `0017` congelada; sin km/l) |

R5 **DONE** el 2026-10-09: PR #27 mergeado (`50b404c`) con CI de main en verde.

### R6 — Pagos a proveedores y terceros
**Alcance:** terceros; pagos realizados con uno o varios medios (efectivo de una caja, transferencia, cheques de cartera), cuya suma es el total.

Propuesta `PROPUESTA_R6_PAGOS_Y_TERCEROS.md` **aprobada** por Enzo el 2026-10-09 con precisiones:
- Registra pagos realizados, no cuentas a pagar: sin deuda ni saldos inventados.
- Terceros como maestro de plataforma `public.third_parties` (UI «Terceros»), CUIT opcional validado y único; el operator puede darlos de alta rápida durante un pago (homónimo: aviso y confirmación explícita; CUIT duplicado: bloqueo); corregir, desactivar y reactivar es del admin de plataforma.
- `settlements.payments` + `payment_items` multi-medio desde el inicio; importe = suma exacta de los ítems; a lo sumo un ítem por cuenta en efectivo/transferencia.
- Conceptos fijos `MERCHANDISE`, `EXPENSE` (categoría activa, nunca `FUEL`), `REFUND`, `OTHER` (solo admin, descripción obligatoria). Contrapartidas técnicas `MERCHANDISE_PAYMENTS`, `EXPENSE`, `THIRD_PARTY_REFUNDS`, `OTHER_PAYMENTS`: clasifican adónde fue el dinero; no son cuentas a pagar, deuda, inventario valorizado ni compras devengadas.
- Fuera de R6: adjuntos; cheque entregado que vuelve rechazado (deuda conocida).
- R6‑2 (autorizada el 2026-10-10): revertir un pago con cheques exige confirmar, cheque por cheque, que cada uno se recuperó físicamente del tercero; sin eso no hay reverso. Un cheque vencido para presentar a la fecha del pago no se entrega (ajuste de Enzo).
- R6‑3 (autorizada el 2026-10-10): consolidación sin ampliar el dominio financiero; nada de cuentas por pagar, facturas, saldos ni vencimientos de deuda.

| PR | Contenido | Estado |
|---|---|---|
| R6‑1 | Terceros + pagos con efectivo y transferencia (cheque rechazado con `payment_method_not_enabled`); `0018` y `0019` | **DONE** (PR #29, aprobada por Enzo el 2026-10-10 con sus desvíos documentados; `0018` y `0019` congeladas) |
| R6‑2 | Entrega de cheques de cartera como medio de pago (`DELIVERED`, «Entregar», reverso con recuperación física por cheque, cheque vencido no se entrega); `0020` | **DONE** (PR #31, aprobada por Enzo el 2026-10-10 con dos ajustes —cheque vencido bloqueado y `CREDITED` confirmado—; mergeado `f9847c8`; `0020` congelada) |
| R6‑3 | Consolidación y cierre: ficha del tercero (totales vigentes, por concepto y por medio, cheques entregados), filtros completos de Pagos en la base, fecha de negocio en tests resuelta al usarla, E2E final de R6; sin migración | **DONE** (PR #32, aprobada por Enzo el 2026-10-11; mergeado `17c02f5`; sin migración) |

R6 **DONE** el 2026-10-11: aprobada por Enzo (R6‑1, R6‑2 y R6‑3) con las deudas conocidas documentadas (cheque entregado que vuelve rechazado, adjuntos); PR #32 mergeado (`17c02f5`); `0018`, `0019` y `0020` congeladas. R7: Enzo pidió primero la propuesta completa (boca como maestro de plataforma, responsables con vigencia, diferencias con la misma semántica que reparto, más de una rendición por día, entregas parciales); sin código hasta su aprobación.

### R7 — Boca de expendio
**Alcance:** boca como punto que rinde (`outlets`), una rendición diaria con entregas parciales, misma lógica de rendición.

Arquitectura aprobada por Enzo el 2026-10-11 (alternativa B, D1–D9, ajustes 1–5), fijada en ADR 009.

| PR | Contenido | Estado |
|---|---|---|
| R7‑1 | Maestro de bocas en plataforma (`0021`) y responsables por vigencia en Rendiciones (`0022`), pantalla `/bocas` | **DONE** (PR #34, aprobada por Enzo el 2026-10-11 con sus desvíos documentados; mergeado `2ce4ebd`, CI de main 4/4; `0021` y `0022` congeladas) |
| R7‑2 | Rendición `OUTLET` sin entregas parciales (esperado nulo/≥ 0, sin pendientes ni combustible, «Cobranzas de boca», cierre, cierre administrativo cero); `0023` | **DONE** (PR #35, aprobada por Enzo el 2026-10-13 con sus desvíos; mergeado `352a512`; `0023` congelada) |
| R7‑3 | Entregas parciales `OUTLET_REMITTANCE` a una caja, «Entregas a cuenta — {Boca}»; `0024` | **DONE** (PR #36, aprobada por Enzo el 2026-10-14 con sus seis desvíos; mergeado `e8b83d4`, CI de main 4/4; `0024` congelada) |
| R7‑4 | Consolidación, experiencia operativa y cierre de boca: tablero Rendiciones › Bocas, ficha, detalle, ayuda del conteo final, móvil y E2E de punta a punta; sin migración | **DONE** (PR #37, aprobada por Enzo el 2026-10-14; mergeado `cab4909`; sin migración) |

R7 **DONE** el 2026-10-14: aprobada por Enzo (R7‑1 a R7‑4: maestro de bocas, responsables, rendición `OUTLET`, cierre administrativo cero, «Cobranzas de boca», entregas parciales, «Entregas a cuenta — {Boca}», múltiples rendiciones, tablero, ficha, detalle, móvil, historia, snapshots y E2E); PR #37 mergeado (`cab4909`); `0021`–`0024` congeladas. Deudas conocidas fuera de R7: fondo fijo, caja propia de boca, remesas de cheques, pendientes/fiado en boca, exportaciones.

### R8 — Empleados: cuenta interna y liquidación
**Alcance:** cuenta interna y liquidación económica del empleado (no RRHH completo): sueldo, bonos, incentivos, faltantes y sobrantes ya asignados, consumos, adelantos, préstamos y cuotas (con postergación), otros ajustes y el pago de la liquidación. Arquitectura aprobada por Enzo el 2026-10-15 con precisiones (D1–D14, A–D; ADR 010): área protegida `settlements.employee_finance` con permiso explícito que el admin de plataforma no hereda; una cuenta `EMPLOYEE` neta por empleado; modelo por período con sueldo base interno mensual (sin liquidación semanal); cuenta, liquidación y pago separados; pago propio `EMPLOYEE_PAYOUT` (no `PAYMENT`).

| Fase | Contenido | Estado |
|---|---|---|
| R8‑1 | Área protegida, privacidad de Cajas, condición salarial (`0025`), cuenta interna en solo lectura | DONE (aprobada por Enzo el 2026-10-16 con sus desvíos; PR #39 mergeado `c88377e`; `0025` congelada) |
| R8‑2 | Consumos, adelantos y ajustes (`0026`) | DONE (aprobada por Enzo el 2026-10-17 con sus desvíos; PR #40 mergeado `a6be05c`; `0026` congelada) |
| R8‑3 | Préstamos, cuotas y postergación (`0027`) | DONE (aprobada por Enzo el 2026-10-18 con un ajuste previo al merge; PR #41 mergeado `1fffb7b`; `0027` congelada) |
| R8‑4 | Liquidación interna: períodos, líneas, aplicaciones, vista previa, cierre, snapshot, reapertura (`0028`) | DONE (aprobada por Enzo el 2026-10-19 con sus 10 desvíos; PR #42 mergeado `adf8a4e`; `0028` congelada) |
| R8‑5 | Pago `EMPLOYEE_PAYOUT`, consolidación y E2E de punta a punta (`0029`) | DONE (aprobada por Enzo el 2026-10-19 con sus 11 desvíos; PR #43 mergeado `144b2bb` el 2026-10-26 con CI 4/4 y la suite completa en verde; `0029` congelada) |

Cada fase frena con su informe y la aprobación de Enzo.

### R9 — Endurecimiento
**Alcance:** contrato para recibir el importe esperado desde Store Sys (sin implementar la integración), reporte diario de cierre, E2E del flujo de punta a punta, performance y preparación de producción. Aprobada como etapa por Enzo el 2026-10-26, con este orden:

| Fase | Contenido | Estado |
|---|---|---|
| R9‑1 | Contrato de ingreso Store Sys → DistriCom: esperado de una rendición por contrato canónico, eventos del esperado, autoridad con confirmación, reparto abierto sin esperado (`0030`; ADR 011) | DONE (aprobada por Enzo el 2026-10-27 con dos ajustes previos al merge; PR #45 mergeado `61b9f77` con CI 4/4 y la suite completa en verde; `0030` congelada) |
| R9‑2 | Reporte diario de cierre por caja y banco: vista calculada por `business_date`, sin arqueo ni conciliación, preferentemente sin migración | DONE (aprobada por Enzo el 2026-10-28 con un ajuste previo al merge: una cuenta creada después del día no aparece; PR #47 mergeado `a8c79b9`, sin migración, ADR 012 aceptado) |
| R9‑3 | E2E completo de Rendiciones de punta a punta | DONE (aprobada por Enzo el 2026-10-28 con sus 8 desvíos; PR #49 mergeado `e680ff7`, sin código productivo ni migración) |
| R9‑4 | Performance | **DONE** (aprobada por Enzo el 2026-10-29): parte sin schema PR #51 (`efc5d06`); `0031` PR #52 (`68297c7`), congelada; mejoras de CI (CI‑1/2/3) PR #53 (`66c1b3e`), con `main` 4/4; sin saldos materializados |
| R9‑5 | Hardening operativo y preparación de producción | IN_PROGRESS (propuesta v2 aprobada por Enzo el 2026-10-29 con precisiones: S1 = infraestructura, R9‑5 = aplicación; PRs R9‑5‑1 a R9‑5‑6, uno por vez). R9‑5‑1 (sesiones y limiter) en revisión |

Cada fase frena con su informe y la aprobación de Enzo. Sin parser de archivos, carpetas, conexión, cron, RPA ni API keys en R9‑1.
