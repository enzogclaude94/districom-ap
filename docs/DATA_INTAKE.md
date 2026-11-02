# DATA_INTAKE — Exportaciones reales para la Fase 1

> v0.2 · 2026-10-30 · Estado: **muestras STORE SYS recibidas; faltan maestro de artículos y AXUM No Compra** (§7).
> Este documento fue el pedido inicial de exportaciones (2026-10-02). La lista de reportes cambió con el brief de Fase 1 de Enzo (2026-10-29): las fuentes vigentes, su formato real y sus reglas están en `docs/IMPORTS.md` y ADR 014. §1–§6 se conservan como historia del pedido; donde contradigan a `docs/IMPORTS.md`, manda `docs/IMPORTS.md`.

## 1. Cómo exportar y enviar

- **Exportá tal como sale del sistema** (`.xlsx`, `.xls`, `.csv` o `.txt`). No abras y vuelvas a guardar en Excel: cambia fechas, borra ceros a la izquierda de los códigos y redondea importes.
- Anotá **de qué menú/reporte sale cada archivo** y qué filtros u opciones usaste (una captura de la pantalla de exportación sirve). Lo necesitamos para que el proceso sea repetible cada semana.
- Si un reporte tiene opción "con detalle" / "por ítem", elegila.
- Subí los archivos a este hilo. Se guardan en los archivos del proyecto (`districom/samples/`), **nunca en el repositorio** de GitHub.
- **Datos personales:** podés mandarlos reales (el proyecto es privado y no se copian a logs ni al repo). Si preferís anonimizar, reemplazá nombres, teléfonos y direcciones, pero **conservá códigos de cliente, códigos de producto y CUIT**, o reemplazalos de forma **consistente en ambos sistemas** (el mismo cliente con el mismo reemplazo en AXUM y en STORE SYS). Sin eso no se puede validar el cruce de clientes entre sistemas.

## 2. Período recomendado

| Qué | Período | Por qué |
|---|---|---|
| Programación, visitas y pedidos (AXUM) | **4 semanas completas de lunes a domingo: 2026‑08‑31 → 2026‑09‑27** (mínimo aceptable: 2 semanas, 2026‑09‑14 → 2026‑09‑27) | Cubre al menos dos ciclos de las rutas semanales y quincenales, incluye un cambio de mes y es chico para revisarlo a mano. |
| Comprobantes (STORE SYS) | **2026‑08‑31 → 2026‑09‑30** (3 días más que AXUM) | Los pedidos del último día se facturan D+1/D+2; sin esos días la conversión facturada sale artificialmente baja y no se puede medir el desfase pedido → factura (D5). |
| Maestros (clientes, productos, rutas, vendedores) | **Foto al día de la exportación** | Son el estado actual; la historia de asignaciones se reconstruye desde los movimientos. |

Conviene que el período incluya, si existen: al menos **una nota de crédito**, **un comprobante anulado**, **ventas de mostrador / boca de expendio**, **un cliente que cambió de ruta o preventista** y **una visita sin compra con motivo**. Si no aparecen en esas fechas, alcanza con una muestra puntual de cada caso.

La historia completa (2026‑01‑01 → hoy) **no hace falta ahora**: se pide recién cuando el importador esté validado, para la carga inicial.

## 3. Archivos que necesito

Prioridad: **P1** imprescindible para diseñar; **P2** muy útil; **P3** solo si existe.

### AXUM (preventa)

| ID | Archivo | Prioridad | Columnas que espero encontrar (si existen) |
|---|---|---|---|
| A1 | **Clientes** (maestro) | P1 | código, razón social, nombre de fantasía, CUIT/DNI, tipo/canal de cliente, localidad/zona, **ruta**, **preventista**, días de visita/frecuencia, activo/inactivo, fecha de alta, (teléfono, dirección) |
| A2 | **Rutas y preventistas** | P1 (si no vienen dentro de A1) | código y nombre de ruta, días de la semana, frecuencia (semanal/quincenal), preventista asignado; listado de preventistas con código |
| A3 | **Programación / hoja de ruta** | P1 | fecha, cliente, ruta, preventista: qué clientes había que visitar cada día |
| A4 | **Visitas / resultado de visita** | P1 | fecha, **hora** (si la tiene), cliente, preventista, ruta, visitado sí/no, resultado (compró / no compró / cerrado / no visitado), **motivo de no compra**, ¿hubo pedido? |
| A5 | **Pedidos con detalle** | P1 | número de pedido, fecha, cliente, preventista, ruta, código de producto, cantidad, precio, estado (tomado/anulado), total |
| A6 | Productos (si AXUM tiene catálogo propio) | P2 | código de producto en AXUM, descripción, código de barras |

### STORE SYS (facturación)

| ID | Archivo | Prioridad | Columnas que espero encontrar (si existen) |
|---|---|---|---|
| S1 | **Comprobantes con detalle por ítem** | P1 | tipo de comprobante (FA/FB/NC/ND/otros), letra, **punto de venta**, número, fecha, **hora**, código de cliente, nombre, CUIT, **vendedor**, caja/depósito/sucursal, código de producto, descripción, cantidad, precio unitario, descuento, **neto, IVA, total**, **estado (anulado)**, comprobante asociado (para NC) |
| S2 | **Clientes** (maestro) | P1 | código, razón social, fantasía, CUIT, condición de IVA, tipo de cliente, localidad, vendedor, activo, fecha de alta |
| S3 | **Productos** (maestro) | P1 | código, código de barras, descripción, **rubro/categoría**, **marca**, proveedor, presentación (ml, envase), unidades por bulto, retornable, activo |
| S4 | Vendedores | P2 | código, nombre, activo |
| S5 | Encabezados de comprobante sin detalle | P3 | solo si S1 no trae totales por comprobante |

No necesito por ahora: listas de precios, costos, stock ni cuenta corriente.

## 4. Qué voy a validar en cada archivo

| Archivo | Validación | Decisión que destraba |
|---|---|---|
| Todos | Formato real (xlsx/xls/csv, separador, codificación), fila de encabezados, formato de fechas, separador decimal, filas de totales o subtotales intercaladas, cantidad de filas | Lector por formato (`TabularReader`) y plantilla de mapping |
| Todos | Unicidad de las claves (código de cliente, de producto, número de comprobante, de pedido) | Business keys e idempotencia del import |
| A1 + S2 | ¿El código de cliente es el mismo en ambos sistemas? ¿Coincide el CUIT? % de clientes que cruzan por cada vía; duplicados y "Consumidor Final" | **D3**, matching de identidad (R2), clientes genéricos (R9) |
| A5/A6 + S3 | ¿El código de producto es el mismo? | **D3** para productos |
| S1 | Tipos de comprobante presentes; si la NC viene con signo negativo o positivo; si los importes son con o sin IVA; cómo figura un anulado; si la NC referencia a la factura | **D4** y definición final de `net_amount` |
| S1 | Cómo se distingue la venta de mostrador (punto de venta, caja, vendedor, cliente genérico, serie) | **D6** / canal (R8) |
| S1 | Si trae hora | Heatmap de boca (R12) |
| S1 vs A1 | Vendedor del comprobante vs. preventista asignado al cliente | Atribución de ventas (R10) |
| A5 vs S1 | Días entre pedido y factura del mismo cliente | **D5**, ventana de conversión facturada (R1) |
| A3 | Si la programación existe como archivo o hay que derivarla de días de visita + asignaciones | Origen de `scheduled_visits` (`AXUM` vs. `DERIVED`) |
| A4 | Valores reales de resultado y motivo; si las no‑visitas aparecen o solo las visitas hechas | Cumplimiento y cobertura (`METRICS.md`) |
| S3 | Calidad de rubro/marca/presentación (vacíos, inconsistencias) | Categorías para penetración y cross‑selling |
| A1 | Cómo cambia la ruta/preventista de un cliente (¿hay fecha?) | Historia de asignaciones |

## 5. Preguntas para responder junto con los archivos

Respuestas cortas alcanzan; si no sabés alguna, la deduzco de los datos y te la confirmo.

1. ¿AXUM y STORE SYS usan el mismo código de cliente? ¿Y de producto? (D3)
2. En STORE SYS, ¿las NC/devoluciones son un comprobante aparte? ¿Los importes del reporte incluyen IVA? (D4)
3. ¿Cuántos días pasan normalmente entre el pedido y la factura? (D5)
4. ¿Cómo reconocés una venta de mostrador en STORE SYS? (D6)
5. ¿Hay rutas quincenales o con frecuencia distinta a semanal?
6. ¿Cada cuánto vas a poder exportar (diario, semanal) y quién lo va a hacer?

## 6. Qué hago cuando lleguen

1. Perfil de cada archivo: columnas, tipos, nulos, claves, volúmenes y ejemplos (sin copiar datos personales a documentos del repo).
2. Informe de hallazgos con respuesta propuesta a D3–D6 y a los riesgos R1, R2, R8–R12 de `PLAN.md`.
3. Propuesta de mapping por archivo y ajustes al modelo (`DATA_MODEL.md`), para tu aprobación.
4. Recién con tu aprobación se implementan los importadores (Fase 1).

## 7. Estado al 2026-10-30

Las muestras son **de formato**: sirven para verificar firma, encoding, estructura, columnas, claves y granularidad, y para construir fixtures sintéticos. No son la historia productiva inicial y **no se commitean** (se guardan en los archivos del proyecto, fuera del repositorio).

| Fuente vigente (brief de Fase 1) | Muestra | Estado | Perfil |
|---|---|---|---|
| STORE SYS · Clientes (con zona y vendedor) | `clientes.xls` | Recibida | `docs/IMPORTS.md §3.2` |
| STORE SYS · Ventas artículos/clientes | `ventas.xls` (1 día) | Recibida; falta una de varios días (ADR 014 S7) | `docs/IMPORTS.md §3.3` |
| STORE SYS · Comprobantes emitidos | `comprobantes emitidos.xls` (1 día) | Recibida | `docs/IMPORTS.md §3.4` |
| STORE SYS · Maestro de artículos | — | **Pendiente** | `docs/IMPORTS.md §3.5` |
| AXUM · No Compra (CSV) | — | **Pendiente** | `docs/IMPORTS.md §3.6` |
| (derivados) Clientes por zona / por vendedor | `.xlsx` generados con openpyxl | No son fuente | `docs/IMPORTS.md §3.1` |

Lo que ya no se pide en Fase 1 (D‑H): programación, visitas y pedidos de AXUM (A3–A5 de §3). Se vuelven a pedir cuando exista una fuente confiable.
