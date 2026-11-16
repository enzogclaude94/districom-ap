# IMPORTS — Importación de AXUM y STORE SYS

> v0.1 · 2026-10-30 · Fase 1 de Inteligencia Comercial. Decisiones: ADR 014 (cliente de ventas: ADR 017; AXUM Compró / No compró: ADR 018). Tablas: `DATA_MODEL.md` §4–§7.
> Estado: **diseño aprobado (F1.0); infraestructura de plataforma implementada en F1.1b** (lectores, pipeline, staging, lock, issues base, API y CLI; `app/platform/imports/`, `app/platform/quality/`). Los adaptadores de STORE SYS y AXUM llegan desde F1.2. Lo marcado *pendiente de muestra* se cierra con el archivo real antes de escribir su adaptador.

Este documento dice qué fuente alimenta qué entidad, cómo se lee cada archivo, qué se excluye, cómo se evita duplicar y qué pasa con cada error. Si una regla de importación cambia, se actualiza acá en el mismo commit (`CLAUDE.md §9`).

## 1. Principios

1. **Nunca se conecta lógica comercial a columnas de archivo.** El flujo es `archivo → lector (formato) → adaptador (reporte) → normalización → validación → resolución de entidades → modelo canónico`. La analítica futura lee solo el modelo canónico.
2. **No se modifica el archivo original.** Se guarda tal como se subió, con nombre UUID bajo `UPLOAD_DIR`, y se lee en modo solo lectura.
3. **No se descarta nada en silencio.** Cada fila del archivo queda en el staging con su estado y su motivo, y cada problema genera un issue de calidad investigable.
4. **No hay identidades por nombre.** Un nombre puede *proponer* un candidato, pero nunca confirma una identidad.
5. **Historia desde la puesta en marcha.** Las muestras de desarrollo son de formato. La carga histórica es una decisión aparte.

## 2. Pipeline y estados

```
SUBIR → DETECTAR → ANALIZAR ─────────────► PREVIEW ── confirmar (operator) ──► ESCRIBIR ──► AUDITAR
          │           │ import_rows (raw,        │                          una transacción,
          │           │ normalized, mensajes)    │                          lock por tipo
          │           └ nada canónico            └ cancelar → cancelled
          └ mismo sha256 + tipo ya importado → duplicate (antes de analizar, y otra vez bajo el lock al confirmar)
```

**Pasos:**

1. **Subir.**
   - Se valida el tamaño (`UPLOAD_MAX_SIZE_MB`) y la extensión permitida (`.xls`, `.xlsx`, `.csv`).
   - Se verifica la **firma** del contenido (§4).
   - Se calcula el SHA‑256 y se guarda el original. Se crean `import_files` e `import_jobs`.
2. **Detectar.**
   - El lector arma las hojas y filas crudas.
   - Cada adaptador registrado puntúa su firma de encabezados: si uno solo supera el umbral, ese es el tipo; si no, elige el usuario.
   - Con el tipo conocido se verifica el archivo idéntico (§5.1): si ya estaba importado, el job queda `duplicate` y no se analiza.
   - Si el usuario elige un tipo cuya firma no coincide, el job pasa a `failed` con `failure_reason = header_mismatch` (no es un código de issue).
3. **Analizar.**
   - El adaptador normaliza y valida cada fila.
   - Se resuelven entidades **sin escribir** nada canónico: mappings, zonas nuevas e issues solo se **proponen** en `import_rows.normalized` y se crean al confirmar.
   - Se guardan `import_rows` y se calcula el resumen: filas detectadas, válidas, con warning, rechazadas, duplicadas, pendientes de resolución, nuevas, actualizadas y sin cambio.
4. **Preview.**
   - Muestra el resumen, las filas rechazadas, los warnings y lo que la confirmación reemplazaría (§5.3).
5. **Confirmar.**
   - Lo hace un `operator`: el job pasa a `processing`, toma el lock consultivo del tipo y abre **una sola transacción**.
   - Termina en `completed` o `completed_with_warnings`.
   - Un error estructural o inesperado deshace todo y deja el job en `failed` con `failure_reason`.
6. **Auditar.**
   - Se registran `platform.import.*` en `audit_events` (sin PII) y se abren o cierran issues de calidad.

**Estados de `import_jobs`:**

| Estado | Significado |
|---|---|
| `pending` | Analizado, espera confirmación |
| `processing` | Detectando y analizando (recién subido) o escribiendo (confirmado) |
| `completed` | Escrito, sin warnings |
| `completed_with_warnings` | Escrito, con filas rechazadas, pendientes o warnings |
| `failed` | No escribió nada |
| `duplicate` | El mismo archivo ya estaba importado; no escribió nada |
| `cancelled` | El usuario descartó el análisis |

**Formas de ejecución:**
- **Síncrona:** es la forma por defecto en V1.
- **Tarea en el mismo proceso:** se usa si un análisis supera ~20 s medidos. El estado vive en la base, sin Celery ni Redis.
- **CLI:** `python -m app.cli imports analyze|confirm|reprocess|recover`, para operación y recuperación:
  - `imports analyze --file <ruta> --email <operator> [--type <tipo>]`: sube y analiza como ese usuario;
  - `imports confirm --job <id> --email <operator>` / `imports reprocess --job <id> --email <admin>`;
  - `imports recover [--older-than-minutes 30]`.
  
  Imprime un resumen JSON con el estado y los contadores, nunca valores de filas. Sale con 0 si terminó, 2 si el job quedó `failed` y 1 si no se pudo ni intentar (usuario, permisos, extensión, tamaño).

**Registro de tipos (F1.1b).** Cada módulo declara sus tipos en `ModuleSpec.import_types` (`ImportTypeSpec`: clave con el prefijo del módulo, nombre, sistema fuente y adaptador) y sus códigos de issue en `ModuleSpec.quality_codes`. El adaptador implementa `score(workbook)` (0–1, umbral 0,8), `analyze(db, workbook, ctx)` y `write(db, ctx, filas)`; la plataforma hace todo lo demás. `write` recibe todas las filas escribibles y devuelve **exactamente un** resultado por fila (`inserted`, `updated`, `unchanged` o `superseded`; una fila sin cambios es `unchanged`, nunca se omite): si no, la confirmación falla entera. El análisis corre en una transacción `READ ONLY`: un adaptador que intenta escribir falla con `unexpected_error`.

## 3. Fuentes → entidades

### 3.1 Resumen

| Tipo de import | Archivo STORE SYS / AXUM | Formato real | Granularidad | Alimenta | Estado |
|---|---|---|---|---|---|
| `commercial.store_sys.customers` | Clientes (`clientes.xls`) | XLS BIFF7, cp1252 | 1 fila = 1 cliente (foto actual) | `public.customers` + mapping, `commercial.zones`, `customer_zone_assignments`, `customer_salesperson_assignments` | Formato verificado |
| `commercial.store_sys.sales_lines` | Ventas artículos/clientes (`ventas.xls`) | XLS BIFF7, cp1252 | 1 fila = 1 artículo vendido a 1 cliente en 1 día (sin comprobante) | `commercial.sale_lines` | Cerrado con export real de varios días |
| `commercial.store_sys.documents` | Comprobantes emitidos (`comprobantes emitidos.xls`) | XLS BIFF7, cp1252 | 1 fila = 1 comprobante (sin ítems) | `commercial.documents` | Formato verificado |
| `commercial.store_sys.products` | Maestro de artículos (`lista de articulos.xls`) | XLS BIFF7, cp1252 | 1 fila = 1 artículo (foto actual) | `commercial.products`, `product_prices` | Formato verificado |
| `commercial.axum.no_purchase` | AXUM Compró / No compró (`Compro no compro - <mes>.xlsx`) | XLSX (export .NET, todo texto), filas de grupo por preventista | 1 fila = 1 visita con su resultado; solo las de motivo generan evento | `commercial.no_purchase_events` | Cerrado con export real (ADR 018) |

**No son fuente:**
- `clientes_por_zona.xlsx` y `clientes_por_vendedor.xlsx`: son informes derivados de `clientes.xls`, generados con openpyxl, con resúmenes, una hoja por zona o vendedor y columnas calculadas.
- La detección los rechaza por firma de encabezados (`failure_reason = header_mismatch`).

### 3.2 STORE SYS · Clientes (`commercial.store_sys.customers`)

**Estructura:**
- Una hoja (`clientes`), encabezado en la fila 1 con los nombres de campo crudos y datos desde la fila 2. Sin títulos, subtotales ni agrupaciones.

| Columna fuente | Tipo leído | Normalización | Destino |
|---|---|---|---|
| `codigo` | número (float) | entero → texto (`1.0` → `"1"`) | mapping `customer/STORE_SYS/customer` (`external_id`) |
| `fantasia` | texto | recorte de espacios | `customers.display_name` |
| `razons` | texto | recorte; vacío → `NULL` | `customers.legal_name` |
| `direccion` | texto | recorte | `customers.address` (PII) |
| `telefono` | texto | solo dígitos; vacío → `NULL` | `customers.phone` (PII) |
| `cuit` | texto | dígitos; placeholder `"  -        -"` → `NULL`; 11 dígitos con dígito verificador válido → `tax_id`; inválido → `NULL` + warning | `customers.tax_id` (no único) |
| `zona` | texto | `name_key` (mayúsculas, sin tildes, espacios colapsados); vacío → sin zona | `commercial.zones` + `customer_zone_assignments` |
| `vend` | texto (nombre, no código) | etiqueta normalizada | mapping `employee/STORE_SYS/seller` + `customer_salesperson_assignments` |

**Reglas:**

- **Altas:**
  - Un código nuevo **crea** el cliente (`created_from_master`). Es la única fuente que crea clientes.
  - Un código conocido actualiza sus atributos. Si `row_hash` no cambió, la fila queda `unchanged`.
- **Asignaciones de zona y vendedor:** son una **foto**, con la fecha que el operador declara al analizar (por defecto, la fecha de negocio del día).
  - Si la asignación cambió, se cierra la vigente el día anterior a la foto y se abre otra.
  - Si no cambió, no se hace nada.
  - Si la zona o el vendedor vienen vacíos, se cierra la vigente y se abre el issue `commercial.customer.without_zone` / `without_salesperson`.
  - Nunca hay `UPDATE` en el lugar.
- **Cliente ausente:** un cliente conocido que no aparece en la foto no se desactiva. Se abre `commercial.customer.missing_from_master`.
- **Pseudo‑vendedor** («BOCA DE EXPENDIO»): su mapping se marca `excluded`. La asignación guarda la etiqueta y `employee_id NULL`.
- **Clientes genéricos** (Consumidor Final, Cliente Mayorista): `is_generic` lo marca un admin o una regla configurable por código, nunca por nombre.

**Observado en la muestra:**
- 820 clientes con códigos únicos; zona vacía en 263 y vendedor vacío en 216.
- CUIT válido en 80; vacío o placeholder en 739; uno de 10 dígitos.
- 10 nombres de fantasía repetidos entre clientes distintos.

**Implementado en F1.3a** (contrato: el export original `clientes`, BIFF7; los `.xlsx` derivados no son contrato):
- La fecha de la foto se declara al subir (`snapshot_date` del formulario o `--snapshot-date` en la CLI); sin ella, la fecha de negocio de hoy. Una foto anterior a la última aplicada falla con `snapshot_out_of_order` (en el análisis y otra vez bajo el lock); dos fotos del mismo día anulan (`voided_by_job_id`) la asignación que abrió la primera.
- Un código repetido en el archivo rechaza todas sus filas (`duplicate_code`). Un código con mapping no confirmado (alguien lo dejó pendiente) nunca crea un segundo cliente: fila `unchanged` + issue `commercial.customer.unresolved_code`.
- Vendedor: `observe` crea el mapping `employee/STORE_SYS/seller` en `pending` (nunca un empleado, nunca `employees.external_store_id`) con el issue `commercial.seller.unmapped`; la fila queda `pending_resolution`. Un admin lo vincula o lo excluye (BOCA DE EXPENDIO): excluido, la asignación guarda la etiqueta con `salesperson_id NULL` y sin issue. El cambio de vendedor se decide por la **etiqueta** normalizada, no por el empleado.
- La zona nueva se crea con su regla una sola vez (MISA queda `UNCLASSIFIED` con issue; Boca de Expendio sin días de visita).
- Los issues `without_zone`, `without_salesperson` y `missing_from_master` se cierran solos cuando el cliente vuelve con zona, vendedor o en la foto.
- El mismo archivo (mismo `sha256`) subido otro día es `duplicate` (§5.1) y no se aplica: para registrar una foto nueva tiene que ser otro export.
- Validado con la muestra real (820 clientes): 820 altas, 557 asignaciones de zona, 604 de vendedor, 8 zonas (2 MISA sin configurar), 6 vendedores pendientes; el reproceso queda 820 `unchanged`.

### 3.3 STORE SYS · Ventas artículos/clientes (`commercial.store_sys.sales_lines`)

**Estructura:**
- Hoja `ventas`, con encabezado en la fila 1 y sin filtros, títulos ni subtotales visibles.

| Columna fuente | Tipo leído | Normalización | Destino |
|---|---|---|---|
| `codigo` | número | entero → texto | `source_product_code` → `product_id` por código |
| `descripcion` | texto | tal cual | `source_product_description` |
| `cantidad` | número | `numeric(12,3)`; puede ser **negativa** (devolución) o fraccionaria (`0.5`) | `quantity` |
| `rubro` | texto | tal cual; vacío permitido | `source_category` |
| `cod_cliente` | número | entero → texto | `source_customer_code` → mapping `customer/STORE_SYS/customer` (**exacto**) → `customer_id` |
| `cliente` | texto (nombre) | tal cual; opcional | `source_customer_name` (solo trazabilidad, nunca resuelve) |
| `vendedor` | texto | etiqueta normalizada | `source_seller` → mapping `seller` → `employee_id` |
| `zona` | texto | `name_key`; vacío permitido | `source_zone` → `zone_id` |
| `fecha` | fecha Excel | `date` (datemode del libro) | `business_date` |
| `lista_de_precios` | texto (`LISTA n`) | número de lista; vacío → `NULL` | `price_list_id` |

**Reglas:**

- **Sin importes ni comprobante.** Este hecho mide **unidades** por cliente, producto y día. Los importes están en Comprobantes y **no se pueden enlazar** con las líneas.
- **Atribución.** `vendedor` y `zona` coinciden con la asignación del maestro del cliente (236/236 en la muestra). Se guardan **como fuente**, no como atribución del documento. La atribución histórica se resuelve con las tablas de asignación (`DATA_MODEL §5`).
- **Cliente por código exacto** (ADR 017, reemplaza en esto a ADR 014 §2.6). `cod_cliente` se resuelve con el mapping `customer/STORE_SYS/customer` que crea el maestro de clientes. El nombre **no** resuelve: se guarda como fuente. Un código sin mapping deja la línea con `customer_id NULL`, la fila `pending_resolution` y el issue `commercial.customer.unresolved_code`. Las ventas no crean clientes ni mappings.
- **Producto por código exacto.** `codigo` contra `products.source_code`; nunca por descripción.
- **Producto desconocido.** Si no existe en el maestro, la línea se guarda con `product_id NULL` y se abre el issue `commercial.product.unknown`. Las ventas no crean productos.
- **Filas idénticas.** Son legítimas (9 en la muestra) y **no se deduplican**: cada una conserva su `occurrence`.
- **Idempotencia por período y scope.** Ver §5.3.

**Implementado y cerrado en F1.3a** (contrato: export `ventas` con `codigo` y `cod_cliente`; ADR 014 S4/S7 cerrados, ADR 017). El formato de un día sin `cod_cliente` ya no se acepta (`missing_columns`).
- El scope se declara al subir (`scope` = `FULL` o `FILTERED`, `scope_note` con los filtros; `--scope` / `--scope-note` en la CLI). Sin declarar, cuenta como no `FULL`.
- `import_jobs.scope` guarda `declared`, `note`, `days`, `sellers`, `zones`, `price_lists` y `filters_detected` (vacío: el export no muestra filtros).
- Producto desconocido: línea con `product_id NULL`, issue `commercial.product.unknown` por código y `commercial.sale_line.product_pending` por job. Vendedor sin vincular: `commercial.seller.unmapped`.
- Zona: se resuelve por `name_key`; una zona que no existe queda `zone_id NULL` con warning (las ventas no crean zonas). Lista: `LISTA n` → `price_lists.list_number`; una lista no configurada queda `price_list_id NULL` con warning (nunca se inventa).
- Cantidades negativas y fraccionarias (hasta 3 decimales) se guardan tal cual; las filas idénticas se numeran con `occurrence`.
- `rubro` se guarda como fuente y nunca asigna categoría canónica.
- **Validado con el export real de octubre 2026** (7414 líneas, 27 días del 01 al 31; los únicos días sin filas son los 4 domingos): 408/408 códigos de cliente resueltos por código, 0 inconsistencias código↔nombre (3 nombres con dos códigos, que el código separa); 194 de 214 códigos de artículo en el maestro (20 desconocidos, 257 líneas pendientes); 179 cantidades negativas y 69 fraccionarias guardadas tal cual; 387 filas idénticas conservadas. Reimport idéntico → `duplicate`; reproceso `FULL` → reemplaza las 7414; ver §5.3 para reemplazos parciales.

### 3.4 STORE SYS · Comprobantes emitidos (`commercial.store_sys.documents`)

**Estructura:**
- Hoja `comprobantes emitidos`, con encabezado en la fila 1.

| Columna fuente | Tipo leído | Normalización | Destino |
|---|---|---|---|
| `fechor` | fecha+hora Excel | `timestamptz` en `BUSINESS_TIMEZONE` | `issued_at` (habilita análisis por hora) |
| `nommov` | texto `"<TIPO> <LETRA> PPPP-NNNNNNNN"` | se separan tipo, letra, punto de venta y número; espacios colapsados | `source_type_name`, `letter`, `point_of_sale`, `number`, `document_key` |
| `nombre` | texto | tal cual | `source_customer_name` |
| `codcli` | número | entero → texto; `0` → sin cliente | mapping `customer/STORE_SYS/customer` (exacto) → `customer_id` |
| `netosa` | número | `numeric(14,2)`, **sin signo** en la fuente | `net_amount` (el neto con signo se calcula al leer: `net_amount × document_types.sign`) |
| `total` | número | `numeric(14,2)`, **con signo** en la fuente | `total_amount` |
| `descue` | número | `numeric(14,2)` | `discount_amount` |
| `fechas` | fecha Excel | `date` | `business_date` |

**Tipos observados** (clasificación en `commercial.document_types`, editable por admin):

| `source_type_name` | En la muestra | Clase propuesta | Universo comercial | Estado |
|---|---|---|---|---|
| `RETIRO PR.` | 4 (codcli 0, neto 0) | `INTERNAL_MOVEMENT` | **No** | **Confirmado por Enzo** |
| `FACTURA A`, `FACTURA B` | 11 | `INVOICE` | Sí | A confirmar |
| `NOTA DE CREDITO A` | 1 (total negativo) | `CREDIT_NOTE` (signo −1) | Sí | A confirmar |
| `DEVOLUCION X` | 11 (total negativo) | `RETURN` (signo −1) | Sí | A confirmar |
| `COMP. INTERNO X` | 78 (incluye Consumidor Final) | `INVOICE` (comprobante interno) | Sí | A confirmar |
| `RECIBOS X` | 72 (neto 0) | `RECEIPT` (cobranza) | No | A confirmar |
| `VALE PERSONAL V` | 3 (neto 0; `codcli` que no coincide con el maestro, a confirmar en S3) | `EMPLOYEE_VOUCHER` | No | A confirmar |
| `FACTURA` sin letra, punto de venta `0000` | 2 (neto 0, total > 0) | `UNCLASSIFIED` | No | A confirmar |

**Reglas:**
- Hasta que Enzo confirme la clasificación (interpretación contable, `CLAUDE.md §12`), solo se siembra `RETIRO PR.`. El resto entra `UNCLASSIFIED`, **fuera del universo**, con el issue `commercial.document.unclassified_type`.
- **Un tipo nuevo** que no está en la tabla se agrega como `UNCLASSIFIED` y queda fuera del universo hasta que un admin lo clasifique.
- **Universo comercial:** en la analítica futura es `document_types.commercial_universe = true`. Ningún KPI filtra tipos por nombre.
- **Signo:**
  - El neto canónico lleva el signo de la clase.
  - Si `total` contradice ese signo, se guarda un warning.
  - Si `netosa` es 0 en un tipo del universo, se guarda un warning.
- **Clave natural:** `document_key = tipo normalizado + letra + punto de venta + número`. Fue única en la muestra.
  - Un reimport con el mismo `row_hash` → `duplicate`.
  - Un reimport con contenido distinto → fila `rejected` + issue `commercial.document.changed`. **Nunca se pisa.**
- **`codcli = 0`:** sin cliente, válido para tipos fuera del universo. En un tipo del universo es un warning.

**Implementado en F1.3a** (contrato: el export original `comprobantes emitidos`):
- La clasificación de §3.4 ya está confirmada por Enzo y sembrada (F1.2): solo `COMP. INTERNO X` y `FACTURA` sin letra quedan `UNCLASSIFIED`. La tabla de arriba conserva la propuesta original.
- `nommov` se lee como `<tipo> PPPP-NNNNNNNN`; el tipo incluye su letra tal como STORE SYS lo nombra (`FACTURA A`, `RECIBOS X`, `VALE PERSONAL V`, `RETIRO PR.` y `FACTURA` sin letra). `document_key = name_key(tipo) | PPPP | NNNNNNNN`.
- `codcli` se resuelve por mapping exacto `customer/STORE_SYS/customer`, **salvo** en las clases `EMPLOYEE_VOUCHER` e `INTERNAL_MOVEMENT`, cuyo código no es de cliente (vale de personal, retiro): se guarda en `source_customer_code` y nunca se vincula. Un código sin mapping deja la fila `pending_resolution` y el issue `commercial.customer.unresolved_code`; los comprobantes no crean clientes ni mappings de clientes (solo el maestro).
- Una fila con contenido distinto para una clave ya importada se rechaza y abre `commercial.document.changed` (uno por comprobante); la misma clave dos veces en el archivo: igual → la segunda `duplicate`; distinta → ambas rechazadas.
- Un tipo `UNCLASSIFIED` (nuevo o sembrado) suma ocurrencias al issue `commercial.document.unclassified_type` de su tipo.
- `fechor` se guarda como `timestamptz` en `BUSINESS_TIMEZONE`.
- Validado con la muestra real (182 comprobantes): 182 altas, 175 vinculados a un cliente, 80 de tipos sin clasificar (78 `COMP. INTERNO X`, 2 `FACTURA`); los vales y retiros sin cliente; el reproceso queda 182 `duplicate`.

### 3.5 STORE SYS · Maestro de artículos (`commercial.store_sys.products`)

**Estructura del export real** (verificado 2026-11-05 con el maestro de Enzo):
- XLS BIFF7 (`D0 CF 11 E0`, `biff_version 70`), cp1252, una hoja `lista de articulos`, encabezado en la fila 1, sin títulos, subtotales ni celdas combinadas; ordenado por descripción.
- **Solo tres columnas.** No vienen rubro, línea, `lista1`…`lista7`, costo, IVA, impuesto interno, tope de descuento, código de barras, capacidad, pack ni peso: lo que se esperaba (§3.5 anterior) no está en este export.

| Columna fuente | Tipo leído | Normalización | Destino |
|---|---|---|---|
| `codart` | número entero (2 a 7 dígitos) | entero → texto | `products.source_code` (identidad) |
| `desart` | texto | espacios colapsados | `products.source_description` |
| `precio` | número, hasta 2 decimales | `numeric(14,2)` | `product_prices` de la lista configurada (ver abajo) |

**Reglas:**
- **Identidad:** `codart`. Este es el único import que crea productos. Un código repetido en el archivo rechaza todas sus filas y abre `commercial.product.duplicate_code`.
- **Reimport:** solo actualiza `source_*` (`unchanged` si el contenido es igual). Nunca pisa categoría, tipo, elegibilidad ni familia.
- **Rubro:** el export no lo trae. Todo producto nuevo queda sin categoría, `analytics_eligible = false` (D‑D) y con `commercial.product.without_category`. No se crean mappings de rubro y no se infiere nada de la descripción (ni familia, ni capacidad, ni pack). `commercial.product.unmapped_category` queda para un export que traiga rubro.
- **Columnas fuera del contrato:** si un export trae otras columnas, se guardan textuales en `source_extra` sin interpretarlas, y el preview las lista (`source_columns_without_contract`).
- **Precio:** el export no dice a qué lista corresponde `precio`. La lista la fija el parámetro confirmado `commercial.store_sys_articles_price_list` (número de lista existente en `price_lists`). **Sin valor (default), los precios no se cargan:** quedan en el staging del job, el preview dice `prices.loaded = false` y se abre `commercial.product.prices_not_loaded`. Una lista que no existe tampoco carga (nunca se crea una lista desde un export). La confirmación usa la lista que anunció el preview. La decide un admin del módulo, con motivo y auditada (`commercial.config.articles_price_list_set`): `GET`/`PUT /api/v1/commercial/price-lists/articles-master` `{"list_number": n | null, "reason": "…"}`. Rige desde el próximo import o reproceso del maestro; no toca precios ya cargados.
- **`precio` = Lista 1** (Enzo, 2026-11-05). STORE SYS no lo declara: se confirmó con el export real de ventas de octubre con importes (`codigo, descripcion, cantidad, final, rubro, vendedor, zona, fecha, lista_de_precios`). Comparando `final ÷ cantidad` con el `precio` del maestro, la mediana de la relación es 1,000 en Lista 1 (1,017 en Lista 2 y 0,971 en Lista 3), y 135 de 155 últimos precios cobrados con Lista 1 coinciden dentro de ±$0,05. Las diferencias son bonificaciones (líneas en $0), descuentos y redondeos. `commercial seed-config` lo deja como parámetro auditado (`app_settings` con el motivo, evento `commercial.config.articles_price_list_set`) **solo si nadie lo fijó**: una decisión posterior de un admin (otra lista o ninguna) no se deshace.
- **Historia de precios:** precio igual → nada; distinto → cierre el día anterior a la foto + alta; dos fotos del mismo día → la primera se anula. Precio 0 → se guarda (es lo que dice STORE SYS) con warning `zero_price`; vacío → sin precio (`without_price`); negativo, texto o más de dos decimales → no se carga (`invalid_price`). El producto se crea igual en esos casos.
- **Foto:** como Clientes, el archivo es una foto con fecha (declarada; si no, el día hábil de la carga). Una foto anterior a la última aplicada se rechaza (`snapshot_out_of_order`). Un producto ausente de la foto nunca se desactiva: queda con `commercial.product.missing_from_master` (info), que se resuelve si vuelve a aparecer.

**Validado con el maestro real (2026-11-05, no commiteado):** 281 artículos, 0 códigos repetidos, 0 descripciones repetidas, ninguna celda vacía, 2 precios en 0 (artículos de regalo), 47 precios con centavos, máximo 3.436.400 (`SERVICIO LOGISTICO`). Primer import (antes de confirmar la lista): 281 productos creados, 281 sin categoría, 0 precios cargados y 1 issue `prices_not_loaded`. El mismo archivo con la misma fecha → `duplicate`; el reproceso → 281 `unchanged`; el mismo archivo con fecha nueva → 281 `unchanged`. Después de `seed-config` (Lista 1 confirmada), el reproceso del maestro del 05/11 carga 281 precios en Lista 1 (2 en $0) y resuelve `prices_not_loaded`; el reproceso siguiente → 281 `unchanged`, sin precios nuevos. Con el maestro cargado, 61 de los 63 códigos de la muestra de ventas de un día se resuelven.

### 3.6 AXUM · Compró / No compró (`commercial.axum.no_purchase`)

**Implementado en F1.4** con el export real de octubre 2026 (ADR 018, que reemplaza en esto a ADR 014 §1.2, §2.6 D‑B y §2.7.4). Adaptador: `app/modules/commercial/imports/no_purchase.py`.

**Formato real:**
- XLSX generado por AXUM (`System.IO.Packaging`), una hoja `Data`, todas las celdas como texto.
- Fila 1: encabezado `'' | Cliente Id | Nombre | Direccion | Compro? | Fecha y Hora Subida | Hora Ini. | Hora Fin. | Minutos` (la columna A no tiene título).
- Cada preventista empieza con una fila de grupo `Vendedor: NN` en la columna A; entre grupos hay una fila en blanco. En las filas de datos, la columna A está vacía.
- `Compro?` dice `Si` o el motivo de no compra. `Fecha y Hora Subida` es `dd/mm/aaaa hh:mm:ss`; `Hora Ini.` y `Hora Fin.` son `hh:mm:ss`; `Minutos` es `m:ss` sin ceros a la izquierda.
- El reporte no trae el período pedido, los filtros ni la fecha de la visita.

**Muestra real (octubre 2026):** 2767 filas = 1 encabezado + 3 grupos (`01`, `02`, `40`) + 2 en blanco + 2761 de datos (1490 `Si`, 1271 con motivo), del 02/10 al 31/10 en 26 días.

**Filas:**
- Encabezado, grupos y blancos → `skipped`.
- `Si` → `skipped`, con el mensaje informativo `purchase_reported` «Compró: la fila queda trazable pero no genera un evento de no compra.». Conserva su `normalized` y sus avisos de horario en el staging. **No es una visita modelada** y no se usa para inferir visitas, cobertura, cumplimiento ni conversión.
- Con motivo → un evento en `commercial.no_purchase_events`.
- **Rechazos** (`error`): fila antes de cualquier grupo (`missing_salesperson`), `Cliente Id` vacío o no numérico (`missing_customer_code` / `invalid_customer_code`), `Compro?` vacío (`missing_result`), `Fecha y Hora Subida` vacía o inválida (`missing_uploaded_at` / `invalid_uploaded_at`).
- **Avisos** (`warning`, la fila se carga): hora de inicio o fin inválida o vacía, `Minutos` inválido, fin antes de inicio (`end_before_start`), `Minutos` distinto de fin − inicio (`minutes_mismatch`), inicio posterior a la subida (`start_after_upload`), nombre vacío.

**Fecha de negocio:** el día de `Fecha y Hora Subida`. Se guardan `uploaded_at_source`, `start_time_source`, `end_time_source`, `duration_source` (texto tal cual) y `duration_seconds`, sin combinar horas. Una fila con inicio posterior a la subida conserva el día de la subida, con aviso (en la muestra son 3, todas `Si`).

**Cliente (`Cliente Id`), en este orden:**
1. Mapping `customer/AXUM/customer` exacto: si existe, manda (confirmado → `customer_id`; pendiente → `NULL`).
2. Si no existe y `commercial.axum_customer_code_matches_store_sys` es `true` (sembrado así por la decisión de Enzo, ADR 018): el mapping `customer/STORE_SYS/customer` del mismo código, y el nombre normalizado debe coincidir exactamente con el nombre de fantasía o la razón social del cliente.
   - Coinciden → al confirmar se crea `customer/AXUM/customer` `confirmed`, `method = rule`, mismo cliente, y un evento de auditoría `commercial.axum.customers_linked_by_rule` (solo ids de mappings).
   - El nombre no coincide → mapping pendiente con ese cliente como candidato, evento con `customer_id NULL`, aviso `customer_name_mismatch` e issue `commercial.axum_customer.name_mismatch`.
   - El código no existe en STORE SYS → pendiente, aviso `customer_unknown_code` e issue `commercial.axum_customer.unresolved`.
3. Con la regla en `false`: pendiente (`customer_unresolved`) e issue `commercial.axum_customer.unresolved`.

Nunca se crean clientes, nunca se resuelve solo por nombre y no hay fuzzy matching. La regla la cambia solo un admin de Comercial, con motivo y auditoría: `GET/PUT /api/v1/commercial/axum-customer-code-rule`. Un cambio aplica al próximo import; los mappings ya creados no cambian.

**Preventista:** el código de la fila de grupo. Cada código del reporte (también uno con solo `Si`, como `40` en la muestra) se observa como `employee/AXUM/salesperson` con etiqueta «Vendedor NN»; si no está confirmado, el evento queda con `salesperson_id NULL` y se abre `commercial.axum_salesperson.unmapped`. Ningún import confirma preventistas, crea empleados ni usa `employee/STORE_SYS/seller` (F1.5).

**Motivo:** `reason_source` guarda el texto tal cual. Su categoría sale de `no_purchase_reason_mappings` (AXUM). Los 6 de la muestra estaban sembrados: TIENE STOCK → `STOCK`; CERRADO, NO ESTA EL ENCARGADO y VACACIONES → `NO_DISPONIBLE`; COMPRA POR BEES → `OTRO_CANAL` (otro canal, no demanda perdida); SIN DINERO → `RESTRICCION_FINANCIERA`. Un motivo nuevo se crea sin categoría, con `reason_category_id NULL` y el issue `commercial.no_purchase.reason_unmapped`.

**Ocurrencias:** sin UNIQUE sobre fecha + cliente + preventista + motivo. Filas idénticas en todo su contenido se conservan como eventos separados con `occurrence` 1, 2, …, y la segunda lleva el aviso informativo `identical_row`.

**Estado de la fila:** `pending_resolution` si el cliente, el preventista o el motivo quedan sin resolver; si no, `valid` o `warning`. Todas se cargan.

**Preview** (`metadata.preview`, solo conteos): `report` (filas de datos, grupos, `Si` salteadas, eventos, rechazadas, días), `customers` (regla, vinculados, por regla, pendientes, código inexistente, nombre distinto, eventos sin cliente), `salespeople` (vistos, pendientes, eventos), `reasons` (por categoría, sin categoría), `date_warnings` (todas las filas) y `date_warnings_events` (entre los eventos), y `replacement` (§5.4).

## 4. Formatos y lectores

| Formato | Firma | Lector | Notas |
|---|---|---|---|
| XLS (BIFF2–8) | `D0 CF 11 E0 A1 B1 1A E1` (OLE2) | `xlrd` 2.x | Las muestras de STORE SYS son **BIFF7** (Excel 5/95), que `openpyxl` no lee. Fechas con el `datemode` del libro; codepage del libro (cp1252: `Ñ` correcta). Códigos numéricos llegan como float y se convierten a entero‑texto. Los ceros a la izquierda no existen en la fuente |
| XLSX | ZIP `PK\x03\x04` con `[Content_Types].xml` | `openpyxl` `read_only=True, data_only=True` | Sin evaluar fórmulas ni ejecutar macros; `.xlsm` rechazado; `defusedxml`; límite de descompresión |
| CSV | texto | `csv` stdlib | Encoding UTF‑8 (con o sin BOM) → CP1252, o rechazo (`unknown_encoding`). Separador detectado entre `;`, `,` y tab. Todas las celdas llegan como texto: el adaptador interpreta los decimales según la columna (`1.234,56` o `1234.56`) |
| HTML / SpreadsheetML con extensión `.xls` | `<html`, `<?xml` | — | Rechazo con `failure_reason = unsupported_format` hasta que una muestra real lo justifique |

El lector entrega `RawSheet` (filas de celdas `str | Decimal | date | datetime | None` con número de fila real). El adaptador no importa `xlrd`, `openpyxl` ni `csv`, y un contrato de import‑linter lo verifica.

**Detalle de F1.1b:**
- La firma decide el formato; la extensión solo filtra lo que se puede subir. Si no coinciden (un CSV llamado `.xls`), se lee por la firma y se registra `extension_mismatch` en el job.
- Un ZIP sin `[Content_Types].xml` y `xl/workbook.xml` no es XLSX (`unsupported_format`); un archivo con bytes NUL que no es OLE2 ni ZIP tampoco.
- Números: un valor entero llega como `Decimal` entero (`1001`, nunca `1001.0`); el adaptador lo pasa a texto si es un código. Una fecha‑hora a medianoche llega como `date`.
- Topes: 200.000 filas por hoja (`too_many_rows`) y 500 columnas (`too_many_columns`); XLSX con más de 300 MB descomprimidos o una parte de más de 10 MB con razón de compresión > 200 (`file_too_complex`).
- Las filas en blanco se conservan (con `skipped`) para que los números de fila sean los del archivo. En CSV, una celda entre comillas con saltos de línea conserva el número de la línea donde empieza el registro.

## 5. Idempotencia

### 5.1 Identidad del import (archivo idéntico)
Un upload repite un import cuando coinciden `sha256` + `import_type` + **las declaraciones que el tipo hace parte de su identidad**, contra un job `completed` o `completed_with_warnings`.
- Cada tipo lo declara en su `ImportTypeSpec.identity` (la plataforma no conoce tipos): `snapshot_date`, `scope` o ninguna.

| Tipo | Identidad | Por qué |
|---|---|---|
| `commercial.store_sys.customers` | `sha256` + tipo + `snapshot_date` | Es una foto: el mismo archivo como foto de otro día confirma que los clientes seguían ahí (`last_seen_on`) |
| `commercial.store_sys.products` | `sha256` + tipo + `snapshot_date` | Es una foto; los precios toman vigencia desde esa fecha |
| `commercial.store_sys.sales_lines` | `sha256` + tipo + `scope` | El scope declarado decide qué se reemplaza (§5.3); una declaración equivocada se corrige subiendo el mismo archivo con el scope correcto |
| `commercial.store_sys.documents` | `sha256` + tipo | Sin declaraciones: un comprobante se identifica por su clave (§5.2) |

- **Fecha de la foto sin declarar:** es el día hábil de la carga. Se escribe en `metadata.declared` (con `metadata.defaulted = ["snapshot_date"]`), así el análisis, la confirmación, el reproceso y la identidad usan el mismo día. La de un job completado es su `import_jobs.snapshot_date`.
- **Scope:** se compara el declarado (`FULL`, `FILTERED` o sin declarar; sin declarar solo coincide con sin declarar). La nota del scope no participa: describe los filtros, no cambia la estrategia.
- Una declaración que el tipo no usa no cambia nada: el mismo archivo de comprobantes con otro scope sigue siendo `duplicate`.
- La identidad calculada queda en `metadata.identity`.
- Si coincide, el job nuevo queda `duplicate` con `duplicate_of_job_id`. Se registra un issue informativo (`platform.import.duplicate_file`, uno por identidad) y no se escribe nada.
- **El nombre del archivo nunca participa.**
- Un import nuevo por identidad (otra fecha, otro scope) pasa por todas las reglas de su tipo: filas `unchanged`, foto fuera de orden rechazada, reemplazo de ventas que se bloquea y espera la decisión de un admin.
- Un reproceso deliberado es otra operación (admin, §7): no pasa por el control de duplicado y hereda las declaraciones del job original.

### 5.2 Clave natural (mismo contenido, otro archivo)

| Entidad | Clave | Igual | Distinto |
|---|---|---|---|
| Cliente | mapping `customer/STORE_SYS/customer` + código | `unchanged` | `updated` (el valor anterior queda en el staging del job previo) |
| Asignación zona / vendedor | asignación vigente del cliente | nada | cierre (día anterior a la foto) + alta |
| Zona | `name_key` | nada | — (las zonas no se renombran solas: un nombre nuevo es otra zona) |
| Producto | `source_code` | `unchanged` | se actualizan solo `source_*` |
| Precio | `(producto, lista)` vigente | nada | cierre + alta |
| Comprobante | `document_key` | `duplicate` | `rejected` + issue `document.changed` (nunca se pisa) |
| Línea de venta | — (no hay clave segura) | §5.3 | §5.3 |
| No‑compra | — (sin UNIQUE; ajuste 1) | §5.4 | §5.4 |

### 5.3 Ventas artículos/clientes: reemplazo por período **y scope** (D‑G)

El archivo no trae número de comprobante, y dos líneas idénticas pueden ser ventas distintas. Deduplicar por contenido perdería ventas. El reemplazo funciona así:

1. **Período.** Al analizar se registran `period_from`/`period_to` y el **conjunto de días** presentes en las filas.
2. **Scope.** El archivo no muestra filtros, así que el operador **declara** el scope del export:
   - `FULL`: sin filtros, todos los vendedores y zonas;
   - `FILTERED`: con la descripción de los filtros usados.
   
   Se guarda en `import_jobs.scope` junto con las dimensiones observadas: vendedores, zonas, listas y días presentes.
3. **Reemplazo automático.** Ocurre solo si:
   - el job nuevo **y** todos los jobs anteriores con líneas vigentes en esos días son `FULL`;
   - el nuevo cubre esos días completos.
   
   Entonces las líneas anteriores de esos días pasan a `superseded`, nunca se borran, y se cargan las nuevas en la misma transacción. El preview lo anuncia: «reemplaza N líneas de los días X–Y (job #k)».
4. **En cualquier otro caso** la confirmación **se detiene**:
   - algún scope `FILTERED`;
   - días sin las mismas condiciones de cobertura;
   - superposición parcial que no se puede delimitar por día.
   
   Un admin decide explícitamente: reemplazar los días indicados, cargar como adicional (solo si el operador declara que el contenido no se superpone) o cancelar. La decisión queda auditada con motivo.
5. **Archivo idéntico:** se resuelve por §5.1 antes de llegar acá.

La estrategia definitiva se valida con una muestra de varios días antes de F1.3 (ADR 014 S7).

**Implementado en F1.3a** (`app/modules/commercial/sales/service.py`):
- El preview anuncia `replacement`: `action` (`insert`, `replace` o `blocked`), los días superpuestos, los jobs y líneas que reemplazaría, los días sin filas en el archivo nuevo y los motivos (`new_scope_not_full`, `previous_scope_not_full`, `days_without_rows`).
- Un período que se superpone en parte se reemplaza **por día**: solo los días compartidos, si las condiciones se cumplen.
- `blocked` sin decisión → la confirmación responde 409 `import_decision_required` y el job sigue `pending` sin escribir nada.
- La decisión es de un **admin del módulo**, con motivo: `POST /api/v1/imports/{id}/confirm` con `{"decision": "replace" | "additional", "reason": "…"}` (CLI: `--decision`, `--reason`). `replace` reemplaza todos los días superpuestos del período; `additional` carga sin reemplazar. Una decisión cuando no hace falta → 409 `import_decision_not_needed`.
- Se auditan `platform.import.decision_applied` (acción y motivo) y `commercial.sales.lines_superseded` (líneas, días, jobs reemplazados, si fue automático).
- **Validado con el export real de octubre 2026** (ADR 014 S7 cerrado; la estrategia no cambió):
  - `FULL` de 27 días sobre base vacía → `insert` 7414; el mismo archivo `FULL` otra vez → `duplicate`; reproceso → `replace` de los 27 días, 7414 `SUPERSEDED` y 7414 vigentes;
  - un `FULL` de los días 20–31 → reemplaza solo esos 11 días (3132 líneas); los días 01–19 quedan del job anterior;
  - un `FULL` de un solo día (el 23, que contiene la muestra de un día anterior) → reemplaza solo ese día;
  - los domingos no traen filas en ningún export y no bloquean (no son días del período con filas);
  - un `FILTERED` sobre días con líneas `FULL` → `blocked` (`new_scope_not_full`), 409 y el job queda `pending` sin escribir; el mismo archivo `FULL` → `replace`.
  - Vigentes al final: 4282 + 2870 + 262 = 7414, igual que el export completo.

### 5.4 AXUM Compró / No compró: toda superposición la decide un admin (ADR 018)

El reporte no trae el período pedido ni los filtros, y una visita sincronizada tarde puede aparecer recién en un export posterior. Nada prueba qué reemplaza un archivo, así que no se copia el reemplazo automático de ventas.

- **Mismo archivo** (`sha256` + tipo) → `duplicate`, sin escritura.
- **Sin no compras activas en el período** del archivo (primer y último día con datos, incluidas las filas `Si`) → `insert`.
- **Cualquier no compra activa en ese período** → `blocked`, motivo `no_purchase_overlap`. Al confirmar sin decisión: 409 `import_decision_required`, con el plan en `details` (`overlap_days`, `lines` = no compras activas de los preventistas del archivo, `kept_other_salespeople`, `replaces` con el job anterior). También si el archivo no trae ninguna no compra.
- La decide un **admin** de Comercial, con motivo obligatorio (`platform.import.decision_applied`):
  - `replace`: las no compras activas del período **de los preventistas que trae el archivo** pasan a `SUPERSEDED` con `superseded_by_job_id`, nunca se borran (`commercial.no_purchase.events_superseded`, con conteo, período, preventistas y jobs reemplazados; `metadata.superseded_events`). Las de otros preventistas no se tocan.
  - `additional`: se cargan y se conservan todas.
- Un operador que manda una decisión recibe 403; una decisión sin bloqueo, 409 `import_decision_not_needed`.

### 5.5 Concurrencia
- Lock consultivo de PostgreSQL por `import_type` al confirmar (`pg_advisory_xact_lock`, se libera con el commit o el rollback).
- Una transacción por job.
- Dos confirmaciones del mismo tipo se serializan; las de tipos distintos no se esperan.
- La espera del lock tiene su propio tope de 60 s (por encima del `lock_timeout` de 10 s del backend). Si vence, la confirmación responde 409 `import_in_progress` y el job vuelve a `pending` sin haber escrito nada: se puede confirmar de nuevo.
- Bajo el lock se vuelve a buscar un archivo idéntico ya completado: si dos jobs del mismo archivo esperaban confirmación, el segundo queda `duplicate`.

## 6. Resolución de entidades y mappings

`public.external_entity_mappings`: `(entity_type, source_system, namespace, external_id)` es único.

| entity_type / source / namespace | `external_id` | Cómo se crea | Auto‑confirmación |
|---|---|---|---|
| `customer / STORE_SYS / customer` | código | el maestro de clientes (comprobantes y ventas solo lo leen) | Sí (`created_from_master`, mismo sistema y código) |
| `customer / AXUM / customer` | `Cliente Id` | Compró / No compró | Sí, con la regla de ADR 018 (`rule`: mismo código y nombre en STORE SYS); si no, `pending` |
| `employee / STORE_SYS / seller` | etiqueta normalizada | maestro de clientes, ventas | **No**; los pseudo‑vendedores se marcan `excluded` |
| `employee / AXUM / salesperson` | código de la fila `Vendedor: NN` | Compró / No compró | **No** (F1.5) |

Zonas y productos **no usan mappings**: se identifican por `commercial.zones.name_key` y `commercial.products.source_code` (DATA_MODEL §1, excepción 2). Una zona nueva se crea `UNCLASSIFIED` al confirmar y recibe la regla que corresponda.

**Orden de resolución:**
1. mapping `confirmed` o `excluded`;
2. ID externo exacto (solo donde la tabla lo dice);
3. regla segura explícita y configurable;
4. `pending`, o `ambiguous` con `candidates`;
5. decisión manual con motivo.

**Estados:** `pending`, `confirmed`, `ambiguous`, `rejected` y `excluded`.

**Reglas:**
- Cada decisión se audita (`platform.mapping.*`, before/after y motivo).
- Reasignar un mapping confirmado exige motivo.
- **Un import nunca cambia un mapping existente.** Si encuentra un conflicto, abre un issue.
- Confirmar un mapping completa los hechos guardados con `*_id NULL` que lo esperaban y cierra sus issues, en una transacción auditada.
- **No hay sugerencias por similitud de texto en Fase 1.**

**Implementado en F1.2** (`app/platform/mappings/`, migración `0034`):
- `resolve` / `resolve_many` solo leen (los usa el análisis, en su transacción `READ ONLY`); `observe` escribe al confirmar el import y, si el mapping existe, solo refresca `external_label` y `last_seen_job_id`.
- Un import crea `confirmed` solo con método automático (`created_from_master`, `exact_external_id`, `rule`) y su entidad. Si no, `pending` (con a lo sumo un candidato **exacto**, p. ej. `customers.exact_name_candidates`, que es una propuesta y nunca se confirma sola) o `ambiguous` (dos o más).
- Decisiones: un `operator` del módulo confirma, rechaza o excluye un mapping abierto; un `admin` vincula un código excluido, excluye uno confirmado o lo reasigna. Excluir, reasignar y vincular un excluido exigen motivo. La entidad debe existir y estar activa (`invalid_mapping_entity`). La auditoría guarda estado, entidad, método, tipo, namespace y motivo, **nunca** el código ni la etiqueta externa.
- No hay endpoint que cree un cliente ni un mapping: solo los imports (F1.3). API en `/api/v1/commercial/customers` y `/mappings`.

## 7. Errores, calidad de datos y reproceso

**Tres niveles:**

| Nivel | Ejemplo | Resultado |
|---|---|---|
| Estructural | columnas requeridas faltantes, archivo ilegible, firma no soportada | job `failed`, nada escrito, `failure_reason` con detalle |
| Fila | fecha inválida, código vacío, cantidad no numérica | fila `rejected` en `import_rows` + issue `platform.import.invalid_row`; el job sigue (`completed_with_warnings`) |
| Entidad | cliente sin zona, producto sin categoría, mapping pendiente | hecho guardado + issue persistente |

**`public.data_quality_issues`:**
- Los códigos los registra cada módulo, con severidad y regla de dedupe.
- Un issue abierto con el mismo `(code, dedupe_key)` suma ocurrencias en lugar de duplicarse.
- Estados: `open`, `resolved` (automático cuando la condición desaparece, o manual) e `ignored` (manual, con nota).

**Códigos de Fase 1** (registrados en el `ModuleSpec`; F1.3a implementa los que tienen fuente hoy):
- `platform.import.duplicate_file`, `invalid_row`, `missing_columns`;
- `commercial.customer.without_zone`, `without_salesperson`, `missing_from_master`, `unresolved_code` (código STORE SYS sin mapping, desde comprobantes y ventas);
- `commercial.seller.unmapped` (vendedor STORE SYS sin vincular);
- `commercial.product.unknown` (código de ventas que no está en el maestro), `without_category`, `unmapped_category` (rubro sin mapping; el export actual no trae rubro), `duplicate_code` (código repetido en el maestro), `missing_from_master` (info), `prices_not_loaded` (precio sin lista confirmada);
- `commercial.sale_line.product_pending` (por job);
- `commercial.zone.unconfigured`;
- `commercial.document.unclassified_type`, `changed`;
- pendientes de su muestra: `commercial.no_purchase.*` (AXUM, F1.4).

**Reproceso:**
- **Re‑resolver:** al confirmar mappings, los hechos pendientes se completan solos.
- **Reprocesar un job** (solo admin):
  - crea un job nuevo con `reprocess_of_job_id`;
  - lee otra vez el archivo guardado con el lector y el adaptador actuales (ADR 014 §2.11 admite staging o archivo; F1.1b usa siempre el archivo, que nunca cambia, así un arreglo del lector también se aplica);
  - aplica las mismas claves naturales (y §5.3 para ventas);
  - el job original queda intacto.
  - no aplica el chequeo de archivo idéntico (es la forma explícita de volver a aplicarlo).
- **Recuperar:** `imports recover` pasa a `failed` (`interrupted`) un job que lleva más de 30 minutos en `processing` y cuyo tipo no tiene el lock tomado; uno que sigue confirmándose nunca se toca. Como era una transacción, no deja datos a medias.

**`failure_reason`** (código estable; el texto para mostrar va en `failure_detail`):

| Código | Cuándo |
|---|---|
| `empty_file`, `unsupported_format`, `unreadable_file`, `unknown_encoding` | El contenido no se puede leer (§4) |
| `too_many_rows`, `too_many_columns`, `file_too_complex` | Supera los topes del lector (§4) |
| `type_not_detected`, `ambiguous_type` | Ningún tipo o más de uno supera el umbral: hay que elegir el tipo |
| `header_mismatch` | El tipo elegido no coincide con los encabezados |
| `missing_columns` | El adaptador no encuentra columnas requeridas (+ issue `platform.import.missing_columns`) |
| `interrupted` | Lo marcó `imports recover` |
| `adapter_contract_violation` | El adaptador no devolvió exactamente un resultado por cada fila escribible (`valid`, `warning`, `pending_resolution`): faltó alguna, sobró o se repitió. Rollback total; el log lleva solo tipo y contadores |
| `unexpected_error` | Cualquier otra excepción; se loguea con `job_id`, nunca con valores |
| otros del adaptador | Un error estructural propio del reporte (`StructuralError`), con su código |

**Retención:**
- El staging no se purga por defecto (`platform.imports.row_retention_days` = sin límite).
- Los archivos originales no se borran desde la app.

## 8. Permisos (D‑I)

| Acción | Rol en `commercial` |
|---|---|
| Ver imports, filas, maestros, mappings e issues | `viewer` |
| Subir, analizar, confirmar, cancelar, resolver mappings operativos, confirmar candidatos en lote | `operator` |
| Configuración (zonas, días, reglas, listas, categorías, tipos de comprobante, motivos), reprocesar, decidir reemplazos de ventas bloqueados, reasignar un mapping confirmado, `analytics_eligible`, familias | `admin` |

El admin de plataforma es admin del módulo (ARCHITECTURE §8). Cada tipo toma los permisos del módulo dueño: quien no tiene acceso al módulo no ve sus tipos ni sus jobs (404). Un job sin tipo detectado lo ven quien lo subió y el admin de plataforma.

**API genérica (F1.1b)**, bajo `/api/v1/imports`:

| Método y ruta | Rol | Qué hace |
|---|---|---|
| `GET /types` | `viewer` | Tipos visibles, con `can_import` |
| `POST /` (multipart `file`, `import_type`, `snapshot_date`, `scope`, `scope_note` opcionales) | `operator` | Sube, detecta y analiza; 201 con el job (`pending`, `failed` o `duplicate`). Lo declarado queda en `metadata.declared` y lo hereda un reproceso |
| `GET /` (`status`, `import_type`, `module`, `limit`, `offset`) | `viewer` | Jobs visibles; `module` deja solo los tipos de ese módulo y los archivos sin tipo reconocido (F1.3b) |
| `GET /{id}`, `GET /{id}/rows` (`status`, `code`), `GET /{id}/issues` | `viewer` | Detalle con preview y contadores, filas del staging (`code`: solo las que tienen ese mensaje), issues ; en las filas, un `viewer` recibe solo número, estado, resultado, clave, entidad y mensajes: `raw` y `normalized` (celdas del archivo, que pueden traer nombres, CUIT, teléfonos y direcciones) solo para `operator` o `admin`, con otro schema (`ImportRowDetailOut`), no con nulos |
| `GET /{id}/messages` | `viewer` | Mensajes del análisis agrupados por código, severidad y columna, con la cantidad de filas (F1.3b). Solo textos del adaptador, nunca valores de las filas |
| `POST /{id}/confirm`, `POST /{id}/cancel` | `operator` | Confirma o descarta un job `pending` (409 `import_job_not_pending` si no lo está). 409 `import_decision_required` si el adaptador no puede probar qué reemplaza |
| `POST /{id}/confirm` con `{decision, reason}` | `admin` | Responde una confirmación bloqueada (§5.3); `can_decide` en el detalle |
| `POST /{id}/reprocess` | `admin` | Job nuevo sobre el mismo archivo |

Errores: 413 `request_too_large` (supera `UPLOAD_MAX_SIZE_MB`), 422 `invalid_extension`, `invalid_scope`, `snapshot_in_future`, `decision_reason_required`, 409 `import_in_progress`, `import_decision_required`, `import_decision_not_needed`.

**Pantalla Importaciones (F1.3b)**, `/comercial/datos/importaciones`:
- Subir: archivo, tipo (o detección automática), día de la foto (maestros) y alcance (ventas: completo o filtrado con la descripción de los filtros). Un alcance sin declarar se trata como filtrado: nunca reemplaza solo.
- Revisar (`/comercial/datos/importaciones/{id}`): qué encontró DistriCom en palabras de negocio (período, días, alcance, filas por estado, resumen del adaptador), qué requiere atención (`/messages`) con sus filas (número, estado, código y observaciones; nunca las celdas del archivo) y qué pasa si se confirma (para ventas, el reemplazo previsto).
- Confirmar o descartar (`operator`); una venta bloqueada la decide un `admin` con `replace` o `additional` y motivo obligatorio. La confirmación es un pedido síncrono: mientras corre, el botón queda deshabilitado y la pantalla muestra el avance; si no llega respuesta (sin conexión, 502, 503 o 504) avisa que puede haberse cargado igual y vuelve a leer el estado. El backend rechaza una segunda confirmación (409 `import_job_not_pending`).
- Historial: fecha, archivo, reporte, usuario, estado, filas y resultado, con acceso al detalle.

**Rendimiento medido con el export real de ventas de octubre (7414 líneas, local, a través del proxy de Next.js):** subir y analizar 2,9 s; confirmar 4,7 s. Antes de F1.3b la confirmación tardaba 206 s porque la plataforma escribía el resultado de cada fila con su propio `UPDATE` ORM, cuadrático en las filas; ahora es un `UPDATE` masivo por clave primaria. Un test de 7500 líneas por la API cuida que no vuelva.

## 9. Seguridad y privacidad

- **Subida:**
  - Límite `UPLOAD_MAX_SIZE_MB`; el límite del reverse proxy lo define Caddy (`CADDY_MAX_BODY`, 30 MB por defecto, ADR 013) y debe ser mayor o igual.
  - Lista blanca de extensión **y** firma.
  - Nombre original sanitizado, solo para mostrar.
  - En disco va con UUID bajo `UPLOAD_DIR`, nunca dentro del contenedor ni del webroot.
- XLSX sin fórmulas evaluadas ni macros; protección contra XML malicioso y contra descompresión excesiva.
- **PII** (nombre, dirección, teléfono, CUIT): nunca en logs ni en `audit_events`. Los logs del import llevan `job_id`, contadores y códigos de error.
- Los archivos reales nunca se commitean. Los fixtures de tests son **sintéticos** con la misma estructura: encabezados, tipos, BIFF7, cp1252, placeholders de CUIT, filas repetidas, tipos de comprobante.

## 10. Lo que Fase 1 no hace

- Métricas, dashboards, scoring, IA ni recomendaciones.
- Visitas, programación ni pedidos.
- Plantillas de columnas editables.
- Fuzzy matching.
- Vínculo de zonas con bocas.
- Pantalla de familias.
- Escribir en STORE SYS, AXUM ni `employees.external_store_id`.
