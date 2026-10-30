# IMPORTS — Importación de AXUM y STORE SYS

> v0.1 · 2026-10-30 · Fase 1 de Inteligencia Comercial. Decisiones: ADR 014. Tablas: `DATA_MODEL.md` §4–§7.
> Estado: **diseño aprobado (F1.0); implementación desde F1.1b.** Lo marcado *pendiente de muestra* se cierra con el archivo real antes de escribir su adaptador.

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
          └ mismo sha256 + tipo ya importado → duplicate (antes de analizar)
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
| `processing` | Escribiendo |
| `completed` | Escrito, sin warnings |
| `completed_with_warnings` | Escrito, con filas rechazadas, pendientes o warnings |
| `failed` | No escribió nada |
| `duplicate` | El mismo archivo ya estaba importado; no escribió nada |
| `cancelled` | El usuario descartó el análisis |

**Formas de ejecución:**
- **Síncrona:** es la forma por defecto en V1.
- **Tarea en el mismo proceso:** se usa si un análisis supera ~20 s medidos. El estado vive en la base, sin Celery ni Redis.
- **CLI:** `python -m app.cli imports analyze|confirm|reprocess|recover`, para operación y recuperación.

## 3. Fuentes → entidades

### 3.1 Resumen

| Tipo de import | Archivo STORE SYS / AXUM | Formato real | Granularidad | Alimenta | Estado |
|---|---|---|---|---|---|
| `commercial.store_sys.customers` | Clientes (`clientes.xls`) | XLS BIFF7, cp1252 | 1 fila = 1 cliente (foto actual) | `public.customers` + mapping, `commercial.zones`, `customer_zone_assignments`, `customer_salesperson_assignments` | Formato verificado |
| `commercial.store_sys.sales_lines` | Ventas artículos/clientes (`ventas.xls`) | XLS BIFF7, cp1252 | 1 fila = 1 artículo vendido a 1 cliente en 1 día (sin comprobante) | `commercial.sale_lines` | Formato verificado |
| `commercial.store_sys.documents` | Comprobantes emitidos (`comprobantes emitidos.xls`) | XLS BIFF7, cp1252 | 1 fila = 1 comprobante (sin ítems) | `commercial.documents` | Formato verificado |
| `commercial.store_sys.products` | Maestro de artículos | *pendiente de muestra* | 1 fila = 1 SKU | `commercial.products`, `product_prices` | Pendiente |
| `commercial.axum.no_purchase` | AXUM No Compra (CSV) | *pendiente de muestra* | 1 fila = 1 no‑compra (cliente, fecha, preventista, motivo) | `commercial.no_purchase_events` | Pendiente |

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

### 3.3 STORE SYS · Ventas artículos/clientes (`commercial.store_sys.sales_lines`)

**Estructura:**
- Hoja `ventas`, con encabezado en la fila 1 y sin filtros, títulos ni subtotales visibles.

| Columna fuente | Tipo leído | Normalización | Destino |
|---|---|---|---|
| `codigo` | número | entero → texto | `source_product_code` → `product_id` por código |
| `descripcion` | texto | tal cual | `source_product_description` |
| `cantidad` | número | `numeric(12,3)`; puede ser **negativa** (devolución) o fraccionaria (`0.5`) | `quantity` |
| `rubro` | texto | tal cual; vacío permitido | `source_category` |
| `cliente` | texto (**nombre**, no código) | etiqueta normalizada | mapping `customer/STORE_SYS/customer_name` → `customer_id` |
| `vendedor` | texto | etiqueta normalizada | `source_seller` → mapping `seller` → `employee_id` |
| `zona` | texto | `name_key`; vacío permitido | `source_zone` → `zone_id` |
| `fecha` | fecha Excel | `date` (datemode del libro) | `business_date` |
| `lista_de_precios` | texto (`LISTA n`) | número de lista; vacío → `NULL` | `price_list_id` |

**Reglas:**

- **Sin importes ni comprobante.** Este hecho mide **unidades** por cliente, producto y día. Los importes están en Comprobantes y **no se pueden enlazar** con las líneas.
- **Atribución.** `vendedor` y `zona` coinciden con la asignación del maestro del cliente (236/236 en la muestra). Se guardan **como fuente**, no como atribución del documento. La atribución histórica se resuelve con las tablas de asignación (`DATA_MODEL §5`).
- **Cliente por nombre.** Como no viene el código, la resolución es:
  - si existe un mapping confirmado, se usa;
  - si no, el sistema **propone** el único cliente activo del maestro cuyo nombre normalizado coincide exactamente. La propuesta queda `pending` hasta que un operador la confirma (se puede confirmar en lote, con auditoría);
  - si hay dos o más clientes con ese nombre, queda `ambiguous`;
  - si no hay ninguno, queda `pending` sin candidato.
  - La línea se guarda igual, con `customer_id NULL`.
  - Si STORE SYS puede exportar el código de cliente en este reporte (ADR 014 S4), el adaptador pasa a resolución exacta.
- **Producto desconocido.** Si no existe en el maestro, la línea se guarda con `product_id NULL` y se abre el issue `commercial.product.unknown`. Las ventas no crean productos.
- **Filas idénticas.** Son legítimas (9 en la muestra) y **no se deduplican**: cada una conserva su `occurrence`.
- **Idempotencia por período y scope.** Ver §5.3.

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

### 3.5 STORE SYS · Maestro de artículos (`commercial.store_sys.products`) — *pendiente de muestra*

Campos esperados según Enzo:
- código, descripción;
- `lista1` … `lista7`;
- línea, rubro, capacidad, pack;
- impuesto interno, tope de descuento, costo, IVA;
- código de barras, peso;
- otros.

Reglas aprobadas:
- **Columnas fuente:** se guardan en `products.source_*`. Las desconocidas van a `source_extra`.
- **Reimport:** solo actualiza `source_*`. Nunca pisa los campos canónicos manuales (categoría, tipo, elegibilidad, familia).
- **Rubro → categoría canónica:** se traduce por `source_category_mappings`. Un rubro vacío no se infiere: el producto queda sin categoría, con `analytics_eligible = false` (D‑D) y el issue `commercial.product.without_category`.
- **Precios:** cada `listaN` con valor actualiza `product_prices` por vigencia (§5.2). Que una lista vacía signifique «sin precio» o «0» se define con la muestra.
- **Código repetido** en el archivo: todas las filas con ese código se rechazan y se abre `commercial.product.duplicate_code`.

### 3.6 AXUM · No Compra (`commercial.axum.no_purchase`) — *pendiente de muestra*

Campos esperados:
- fecha, preventista;
- cliente, razón social, dirección, código;
- motivo.

Reglas aprobadas:
- **Código AXUM:** se resuelve con el mapping `customer/AXUM/customer`, que es **otro espacio** distinto del de STORE SYS (D‑B). La regla `commercial.axum_customer_code_matches_store_sys` está en `false`.
- **Preventista:** mapping `employee/AXUM/salesperson`.
- **Motivo:** se guarda **tal cual** (`reason_source`) y además su categoría canónica opcional, por `no_purchase_reason_mappings`. Un motivo nuevo queda sin categoría, con un issue.
- **Ocurrencias** (ajuste 1): **sin UNIQUE** sobre `fecha + cliente + preventista + motivo`. Cada fila es un evento con su `occurrence`. La estrategia contra el reimport del mismo período se cierra con el CSV real.
- **Visitas:** no se deducen. «Sin venta + sin no‑compra» **no** significa «no visitado».

## 4. Formatos y lectores

| Formato | Firma | Lector | Notas |
|---|---|---|---|
| XLS (BIFF2–8) | `D0 CF 11 E0 A1 B1 1A E1` (OLE2) | `xlrd` 2.x | Las muestras de STORE SYS son **BIFF7** (Excel 5/95), que `openpyxl` no lee. Fechas con el `datemode` del libro; codepage del libro (cp1252: `Ñ` correcta). Códigos numéricos llegan como float y se convierten a entero‑texto. Los ceros a la izquierda no existen en la fuente |
| XLSX | ZIP `PK\x03\x04` con `[Content_Types].xml` | `openpyxl` `read_only=True, data_only=True` | Sin evaluar fórmulas ni ejecutar macros; `.xlsm` rechazado; `defusedxml`; límite de descompresión |
| CSV | texto | `csv` stdlib | Encoding UTF‑8 (con o sin BOM) → CP1252, o rechazo. Separador detectado entre `;`, `,` y tab. Decimales según la columna (`1.234,56` o `1234.56`) |
| HTML / SpreadsheetML con extensión `.xls` | `<html`, `<?xml` | — | Rechazo con `failure_reason = unsupported_format` hasta que una muestra real lo justifique |

El lector entrega `RawSheet` (filas de celdas `str | Decimal | date | datetime | None` con número de fila real). El adaptador no importa `xlrd`, `openpyxl` ni `csv`, y un contrato de import‑linter lo verifica.

## 5. Idempotencia

### 5.1 Archivo idéntico
`sha256` + `import_type` contra un job `completed` o `completed_with_warnings`.
- El job nuevo queda `duplicate` con `duplicate_of_job_id`. Se registra un issue informativo y no se escribe nada.
- **El nombre del archivo no participa.**
- Un reproceso deliberado es otra operación (admin, §7).

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
| No‑compra | — (sin UNIQUE; ajuste 1) | se define con la muestra | — |

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

### 5.4 Concurrencia
- Lock consultivo de PostgreSQL por `import_type` al confirmar.
- Una transacción por job.
- Dos confirmaciones del mismo tipo se serializan.

## 6. Resolución de entidades y mappings

`public.external_entity_mappings`: `(entity_type, source_system, namespace, external_id)` es único.

| entity_type / source / namespace | `external_id` | Cómo se crea | Auto‑confirmación |
|---|---|---|---|
| `customer / STORE_SYS / customer` | código | el maestro de clientes | Sí (`created_from_master`, mismo sistema y código) |
| `customer / STORE_SYS / customer_name` | nombre normalizado | ventas | **No**: se propone un candidato y se confirma a mano |
| `customer / AXUM / customer` | código AXUM | No Compra | **No** (salvo regla D‑B habilitada) |
| `employee / STORE_SYS / seller` | etiqueta normalizada | maestro de clientes, ventas | **No**; los pseudo‑vendedores se marcan `excluded` |
| `employee / AXUM / salesperson` | lo que traiga AXUM | No Compra | **No** |

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

**Códigos de Fase 1:**
- `platform.import.duplicate_file`, `invalid_row`, `missing_columns`;
- `commercial.customer.without_zone`, `without_salesperson`, `unmapped_store_sys`, `unmapped_axum`, `ambiguous`, `missing_from_master`;
- `commercial.product.unknown`, `without_category`, `duplicate_code`;
- `commercial.sale_line.invalid_customer`, `invalid_product`;
- `commercial.no_purchase.unidentified_customer`, `unmapped_reason`;
- `commercial.zone.unconfigured`;
- `commercial.seller.unknown`;
- `commercial.document.unclassified_type`, `changed`.

**Reproceso:**
- **Re‑resolver:** al confirmar mappings, los hechos pendientes se completan solos.
- **Reprocesar un job** (solo admin):
  - crea un job nuevo con `reprocess_of_job_id`;
  - parte de `import_rows.raw`, o del archivo guardado si cambió el lector;
  - aplica las mismas claves naturales (y §5.3 para ventas);
  - el job original queda intacto.
- **Recuperar:** `imports recover` pasa a `failed` un job colgado en `processing`. Como era una transacción, no deja datos a medias.

**Retención:**
- El staging no se purga por defecto (`platform.imports.row_retention_days` = sin límite).
- Los archivos originales no se borran desde la app.

## 8. Permisos (D‑I)

| Acción | Rol en `commercial` |
|---|---|
| Ver imports, filas, maestros, mappings e issues | `viewer` |
| Subir, analizar, confirmar, cancelar, resolver mappings operativos, confirmar candidatos en lote | `operator` |
| Configuración (zonas, días, reglas, listas, categorías, tipos de comprobante, motivos), reprocesar, decidir reemplazos de ventas bloqueados, reasignar un mapping confirmado, `analytics_eligible`, familias | `admin` |

El admin de plataforma es admin del módulo (ARCHITECTURE §8).

## 9. Seguridad y privacidad

- **Subida:**
  - Límite `UPLOAD_MAX_SIZE_MB`; el límite del reverse proxy lo define el hilo Servidor (PR #54) y debe ser mayor o igual.
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
