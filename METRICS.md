# METRICS — Fuente de verdad de métricas comerciales

> v0.1 · 2026-10-02
> **Regla:** ningún KPI aparece en la interfaz si no está definido aquí. Cada métrica tiene un `id` estable que coincide con el registro en `backend/app/analytics/metrics/`. El frontend nunca calcula métricas: muestra el valor, el numerador, el denominador y la definición que devuelve la API.

## 1. Conceptos base

### 1.1 Transacción válida
Comprobante en `sales` con `status = 'VALID'`. Los anulados (`CANCELLED`) se excluyen de **todas** las métricas.

### 1.2 Venta neta
`net_amount` de cada comprobante válido, **sin impuestos**, después de descuentos, con signo:
- `INVOICE`, `DEBIT_NOTE` → positivo.
- `CREDIT_NOTE` (incluye devoluciones) → negativo.

`venta neta = Σ facturas − Σ notas de crédito/devoluciones` en el período, según `business_date` del propio comprobante (una NC de junio sobre una factura de mayo resta en junio).
⚠️ Supuesto provisional (PLAN D4): validar contra semántica real de STORE SYS antes de cerrar Fase 1.

### 1.3 Compra válida (para contar compradores)
Un cliente **compró** en un período si tiene al menos un comprobante `INVOICE` válido con `net_amount > 0` en ese período. Las NC no generan compra. Un cliente con factura y NC que la anula totalmente **sí** cuenta como comprador en el período de la factura (decisión conservadora y simple; revisable).

### 1.4 Cliente genérico
`customers.is_generic = true` (consumidor final / mostrador). Sus ventas cuentan en ventas totales y en Boca; se excluye de todo conteo de clientes, universos, estados y oportunidades.

### 1.5 Atribución
Las métricas por preventista/ruta usan `sales.salesperson_id` / `sales.route_id` (atribución resuelta, ver DATA_MODEL §6). Visitas y programación usan sus propias columnas. Los universos de clientes por preventista/ruta usan la **asignación vigente en la fecha de fin del período**.

## 2. Períodos y comparación

| Preset | Rango | Período de comparación |
|---|---|---|
| Hoy / Ayer | 1 día | día anterior |
| Semana actual | lunes → hoy | mismo tramo de la semana anterior |
| Semana anterior | lunes → domingo | semana previa completa |
| Mes actual | día 1 → hoy | mismo tramo del mes anterior (si el mes anterior es más corto, se recorta a su último día) |
| Mes anterior | mes completo | mes previo completo |
| Últimos 30 días / rango personalizado de N días | N días | N días inmediatamente anteriores |

- Inicio de semana configurable (`week_start`, default lunes).
- Variación: `variation_pct = (current − previous) / previous`.
  - `previous = 0` y `current = 0` → `0`.
  - `previous = 0` y `current ≠ 0` → `null`, con etiqueta "sin base de comparación". Nunca infinito.
  - Métricas que ya son porcentajes muestran además variación en **puntos porcentuales** (`current − previous`).
- Si el período de comparación cae antes de `history_start_date`, la variación es `null` con etiqueta "sin historia".

## 3. Universos de clientes

| id | Definición | Uso |
|---|---|---|
| `U_REGISTERED` | Clientes no genéricos existentes al fin del período | conteos de cartera |
| `U_ACTIVE` | Registrados con ≥1 compra válida en los `active_window_days` (default **90**) previos al fin del período | denominador de penetración por defecto |
| `U_ASSIGNED` | Registrados con asignación de ruta vigente | cartera de ruta/preventista |
| `U_RECENT_BUYERS` | Compradores en el período | promedios por cliente |
| `U_ELIGIBLE_CATEGORY(c)` | `U_ACTIVE` menos tipos de cliente excluidos para la categoría `c` (`category_exclusions` configurable; vacío por defecto) | penetración de categoría |
| `U_ELIGIBLE_PRODUCT(p)` | `U_ELIGIBLE_CATEGORY(categoría de p)` | penetración de producto |

Toda métrica que use un universo devuelve el **tamaño del denominador** y permite listar sus miembros (auditable).

## 4. Catálogo de métricas

Plantilla: **Definición · Fórmula · Numerador / Denominador · Filtros · Exclusiones · Devoluciones · Nulos · Dimensiones.**
Filtros globales aplicables salvo indicación: período, preventista, ruta, localidad, tipo de cliente, categoría, marca, producto, canal.
"Dimensiones" = cortes permitidos en tablas/breakdowns.

### Ventas

#### `sales.net_total` — Ventas totales
- **Definición:** venta neta del período.
- **Fórmula:** `Σ net_amount` de comprobantes válidos. Con filtro de categoría/marca/producto: `Σ sale_items.line_net_amount` de las líneas que cumplen.
- **Exclusiones:** anulados. **Devoluciones:** restan (§1.2). **Nulos:** comprobantes sin canal → canal `OTRO`; sin cliente resuelto → cuentan en total, no en cortes por cliente.
- **Dimensiones:** día/semana/mes, canal, preventista, ruta, localidad, tipo de cliente, categoría, marca, producto.

#### `sales.net_presale` — Ventas preventa
`sales.net_total` con canal = `PREVENTA`.

#### `sales.net_counter` — Ventas boca de expendio
`sales.net_total` con canal = `BOCA_EXPENDIO`.

#### `sales.invoice_count` — Tickets válidos
- `COUNT(*)` de comprobantes `INVOICE` válidos con `net_amount > 0`. NC no restan tickets.

#### `sales.avg_ticket` — Ticket promedio
- `sales.net_total / sales.invoice_count`. Denominador 0 → `null`.
- Nota: el numerador incluye NC (venta neta real); el denominador solo facturas.

#### `sales.avg_per_buyer` — Venta promedio por cliente comprador
- `sales.net_total (excluye genéricos) / customers.buyers`. Denominador 0 → `null`.

### Clientes

#### `customers.buyers` — Clientes compradores
- `COUNT(DISTINCT customer_id)` con compra válida (§1.3) en el período. Excluye genéricos.
- **Dimensiones:** preventista, ruta, localidad, tipo, canal, categoría, marca, producto (comprador **de** esa categoría/marca/producto).

#### `customers.active` — Clientes activos
- `|U_ACTIVE|` al fin del período.

#### `customers.active_without_purchase` — Clientes sin compra
- `U_ACTIVE` (calculado al **inicio** del período) que no compraron en el período. Drill‑down: lista.

#### `customers.new_buyers` — Clientes nuevos (compradores)
- Clientes cuya **primera compra válida histórica** cae dentro del período **y** es posterior a `history_start_date + new_customer_warmup_days` (default 2026‑01‑01 + 60). Antes de esa fecha la métrica devuelve `null` con etiqueta "historia insuficiente".

#### `customers.new_registered` — Clientes nuevos (registrados)
- Clientes con `registered_at` en el período. Métrica secundaria; los indicadores comerciales priorizan `new_buyers`.

#### `customers.recovered` — Clientes recuperados
- `COUNT(DISTINCT customer_id)` en `customer_recovery_events` con `recovered_on` en el período. Ver §5.4.
- Métrica asociada `customers.recovered_revenue`: `Σ amount` de esos eventos + ventas posteriores del cliente en el período.

#### `customers.at_risk` — Clientes en riesgo
- Clientes con `health_status ∈ {AT_RISK}` al fin del período. Variantes: `customers.attention`, `customers.dormant`, `customers.lost`.

### Mix

#### `mix.avg_sku_per_buyer` — SKU distintos promedio por cliente
- `Σ_c |SKU distintos comprados por c en el período| / customers.buyers`.
- **No** es `SKU distintos globales / compradores`.
- SKU con cantidad neta ≤ 0 para el cliente en el período (devuelto por completo) no cuenta.

#### `mix.avg_categories_per_buyer` — Categorías promedio por cliente
- Igual que el anterior con categorías de **nivel 1** (configurable `category_level_for_mix`).

#### `mix.avg_brands_per_buyer` — Marcas promedio por cliente
- Ídem con marcas.

### Ejecución de rutas

Dos preguntas distintas, dos métricas que conviven y **no se mezclan**:

| Pregunta | Métrica | Unidad | Drill‑down |
|---|---|---|---|
| ¿El preventista cumplió el plan de visitas? | `route.schedule_compliance` | visitas **cliente × día** | lista de visitas cliente‑día (cumplidas / incumplidas) |
| ¿Qué porcentaje de los clientes previstos fue alcanzado? | `route.coverage` | **clientes únicos** | lista de clientes (visitados / no visitados) |

Base común: `scheduled_visits` del período (filtrable por ruta, preventista, localidad, tipo de cliente). Ambas métricas exponen `schedule_source` (`AXUM` / `DERIVED`, ver DATA_MODEL §6). Varias visitas del mismo cliente el mismo día cuentan una vez.

#### `route.scheduled_visits` — Visitas programadas
- Pares distintos `(customer_id, business_date)` en `scheduled_visits` del período.
- Un cliente programado lunes y jueves aporta **2**.

#### `route.completed_visits` — Visitas programadas cumplidas
- Pares programados `(customer_id, business_date)` con al menos una visita `visited = true` **ese mismo día**.
- Una visita en otro día no cumple el par programado (se reporta en `route.unscheduled_visits`).

#### `route.schedule_compliance` — Cumplimiento de visitas programadas
- **Fórmula:** `route.completed_visits / route.scheduled_visits`.
- **Responde:** ¿se cumplió el plan de visitas?
- **Nulos:** sin programación en el período → `null` con aviso "sin programación".
- **Drill‑down:** visitas cliente‑día cumplidas e incumplidas (`route.missed_visits = scheduled_visits − completed_visits`).
- **Dimensiones:** ruta, preventista, día de semana, localidad, tipo de cliente.

#### `route.scheduled_customers` — Clientes programados
- Clientes distintos con al menos una visita programada en el período.

#### `route.visited_customers` — Clientes visitados
- Clientes de `route.scheduled_customers` con al menos una visita `visited = true` en el período, **en cualquier día** (el cliente fue alcanzado aunque no haya sido el día planificado).

#### `route.coverage` — Cobertura de cartera
- **Fórmula:** `route.visited_customers / route.scheduled_customers`.
- **Responde:** ¿qué porcentaje de los clientes previstos fue alcanzado?
- **Exclusiones:** clientes visitados que no estaban programados no suman (van a `route.unscheduled_visits`).
- **Nulos:** sin programación → `null` con aviso.
- **Drill‑down:** clientes visitados / no visitados (`route.not_visited_customers = scheduled_customers − visited_customers`).
- **Ejemplo verificable:** 5 clientes programados, 4 visitados → **80 %**. 50 / 40 → **80 %**.

#### `route.unscheduled_visits` — Visitas fuera de programa
- Pares `(customer_id, business_date)` con visita `visited = true` sin programación ese día. Informativa; no entra en ninguna de las dos métricas anteriores.

#### Ejemplo que distingue ambas métricas
Semana con programación lunes (L) y jueves (J):

| Cliente | Programado | Visitado | Pares programados | Pares cumplidos |
|---|---|---|---|---|
| A | L, J | L | 2 | 1 |
| B | L | L | 1 | 1 |
| C | J | — | 1 | 0 |
| D | L, J | L, J | 2 | 2 |
| **Total** | | | **6** | **4** |

- `route.schedule_compliance` = 4 / 6 = **66,7 %** (se incumplieron A‑jueves y C‑jueves).
- `route.coverage` = 3 / 4 = **75 %** (A, B y D alcanzados; C no).
- Variante: si C se visita el **viernes** (sin programación), cobertura = 4 / 4 = **100 %**, cumplimiento sigue en **66,7 %** y `route.unscheduled_visits` = 1.

Fixture: `backend/tests/fixtures/route_execution.py`.

### Conversión de visitas

Se mide sobre **clientes únicos visitados** (la misma unidad que cobertura), como en el ejemplo del prompt: 40 visitados, 31 compradores → 77,5 %.

#### `visit.conversion_order` — Conversión visita → pedido (principal)
- **Fórmula:** clientes visitados con al menos una visita con pedido (`had_order = true`, `outcome = PURCHASE` o fila en `orders` ese día) / `route.visited_customers`.
- **Ejemplo verificable:** 4 visitados, 3 con compra → **75 %**. 40 / 31 → **77,5 %**.

#### `visit.conversion_invoiced` — Conversión visita → factura
- Clientes visitados con compra válida (§1.3) en `[visit_date, visit_date + conversion_window_days]` (default **2**, PLAN R1) para alguna de sus visitas / `route.visited_customers`.
- Una factura se asocia a la visita más reciente previa del cliente (no se cuenta dos veces).

#### `route.visited_no_purchase` — Visitados sin compra
- `route.visited_customers − clientes visitados que compraron` (según la conversión principal). Drill‑down: clientes.

### Penetración

#### `penetration.category(c)` — Penetración de categoría
- **Fórmula:** `|clientes de U_ELIGIBLE_CATEGORY(c) que compraron c en la ventana| / |U_ELIGIBLE_CATEGORY(c)|`.
- **Ventana:** `penetration_window_days` (default **90**) hasta el fin del período (configurable), no el período del filtro (que suele ser demasiado corto).
- **Segmentos chicos:** si el denominador < `min_segment_size` (default **10**) se muestra el valor con badge "muestra chica" y no participa de rankings.
- **Dimensiones:** ruta, preventista, tipo de cliente, localidad.
- **Drill‑down:** compradores / no compradores.

#### `penetration.product(p)`, `penetration.brand(b)`
- Ídem con producto (universo `U_ELIGIBLE_PRODUCT(p)`) o marca.

### Oportunidades

| id | Definición |
|---|---|
| `opp.detected` | Oportunidades con `detected_at` en el período |
| `opp.open` | Oportunidades en `PENDING`/`COMMUNICATED`/`WORKED` al fin del período |
| `opp.worked` | Transiciones a `WORKED` en el período |
| `opp.converted` | Transiciones a `CONVERTED` en el período |
| `opp.conversion_rate` | `converted / (converted + expired + rejected + open vencidas)` de oportunidades cuya ventana de atribución terminó en el período (cohorte cerrada, evita sesgo de las recién creadas) |
| `opp.converted_revenue` | `Σ converted_amount` en el período |

### Objetivos

#### `goal.progress`
- `actual` = la métrica referida por `metric_id` sobre `[period_start, min(hoy, period_end)]` con los filtros del objetivo.
- `pct = actual / target`; `remaining = max(target − actual, 0)`; `elapsed_pct = días transcurridos / días totales`.
- `projection = actual / días_transcurridos × días_totales` (**extrapolación lineal del ritmo actual, no predicción**). Solo para métricas acumulativas (ventas, conteos). Para ratios (cobertura, conversión, promedios) la proyección es `null` y se muestra el valor actual.

## 5. Estado del cliente (`customer_status_v1`)

### 5.1 Frecuencia esperada
1. Días distintos con compra válida (no facturas), desde `history_start_date`, dentro de `frequency_lookback_days` (default **180**).
2. Intervalos entre días consecutivos.
3. Se descartan intervalos fuera de `[Q1 − 1,5·IQR, Q3 + 1,5·IQR]` cuando hay ≥ 6 intervalos.
4. `expected_interval = mediana(intervalos)`.
5. Observaciones = cantidad de intervalos usados.
- Ejemplo: compras 2, 7, 12 y 17 de junio → intervalos 5,5,5 → **5 días**.

### 5.2 Confianza y fallback
| Observaciones | Confianza | Método |
|---|---|---|
| ≥ 6 | HIGH | INDIVIDUAL |
| 3–5 | MEDIUM | INDIVIDUAL |
| < 3 | LOW | fallback: mediana de `expected_interval` (con confianza ≥ MEDIUM) del grupo, en orden: tipo + ruta → tipo → ruta → cartera; un grupo se usa si tiene ≥ `min_peer_group_size` (default **8**) clientes; si ninguno, `default_expected_interval_days` (default **14**) |

Se registra `interval_method`. Con confianza LOW la UI muestra el estado con indicador "estimación débil" y sin decimales falsos.

### 5.3 Overdue ratio y estados
- `days_since_last_purchase = reference_date − last_purchase_date`.
- `overdue_ratio = days_since_last_purchase / expected_interval`.

| health_status | Condición (defaults configurables) |
|---|---|
| NORMAL | ratio < 1,25 |
| ATTENTION | 1,25 ≤ ratio < 1,75 |
| AT_RISK | 1,75 ≤ ratio < 2,50 |
| DORMANT | 2,50 ≤ ratio < 4,00 |
| LOST | ratio ≥ 4,00 |
| NO_PURCHASES | nunca compró |

- Verificación: intervalo 5, 15 días sin compra → ratio 3,0 → **DORMANT**. Intervalo 30, 15 días → ratio 0,5 → **NORMAL**.
- Además, piso absoluto `min_days_for_at_risk` (default **7**): un cliente con intervalo de 2 días no pasa a AT_RISK con 4 días sin comprar si eso cae en un fin de semana largo. Revisable con datos reales.

### 5.4 Segmento de frecuencia (independiente del estado)
| Segmento | expected_interval |
|---|---|
| FREQUENT | ≤ 7 días |
| REGULAR | 8–21 días |
| OCCASIONAL | > 21 días |
| INSUFFICIENT_DATA | < 3 observaciones y sin fallback de grupo |

### 5.5 Recuperación
Evento cuando un cliente cuyo estado vigente era `DORMANT` o `LOST` registra una compra válida no anulada. Se guarda fecha, estado previo, venta, monto y preventista atribuido. El historial de estados no se borra al volver a NORMAL.

## 6. Parámetros (defaults)

| key | default |
|---|---|
| `currency` | `ARS` |
| `business_timezone` | `America/Argentina/Buenos_Aires` |
| `week_start` | `MONDAY` |
| `history_start_date` | `2026-01-01` |
| `new_customer_warmup_days` | 60 |
| `active_window_days` | 90 |
| `penetration_window_days` | 90 |
| `min_segment_size` | 10 |
| `conversion_window_days` | 2 |
| `frequency_lookback_days` | 180 |
| `min_peer_group_size` | 8 |
| `default_expected_interval_days` | 14 |
| `status_thresholds` | `{attention:1.25, at_risk:1.75, dormant:2.5, lost:4.0}` |
| `min_days_for_at_risk` | 7 |
| `opportunity_priority_thresholds` | `{high:70, medium:45}` |
| `opportunity_attribution_window_days` | 30 |
| `category_level_for_mix` | 1 |

## 7. Tests obligatorios de métricas

Cada métrica tiene al menos un fixture verificable a mano en `backend/tests/unit/metrics/`. Mínimos ya fijados: cobertura 80 % / conversión 75 %; cobertura 80 % / conversión 77,5 %; cumplimiento 66,7 % vs. cobertura 75 % con el mismo plan (y 100 % de cobertura con visita fuera de programa); ratio 3 vs 0,5; frecuencia 5 días; `previous = 0`; SKU promedio por cliente ≠ SKU globales / compradores.
