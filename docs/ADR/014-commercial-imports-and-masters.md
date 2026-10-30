# ADR 014 — Importación y maestros de Inteligencia Comercial (Fase 1)

- **Estado:** Aceptado · 2026-10-30. Diseño F1.0 aprobado por Enzo con las decisiones D‑A…D‑K y los ajustes 1–3. La implementación empieza en F1.1b.
- **Amplía:** ADR 003 (modelo canónico de importación) y ADR 006 (plataforma modular).
- **Reemplaza en parte:**
  - De ADR 003: las plantillas de columnas editables. También la lista de formatos (ahora incluye XLS BIFF).
  - De ADR 006: la definición de `routes` como rutas de preventa de AXUM.
  - El resto de ADR 003 y ADR 006 sigue vigente.
- **Numeración:** el PR #54 (Servidor) ocupa la ADR 013. Si al mergear el número siguiente libre cambia, este ADR se renumera en el mismo PR.
- **Detalle operativo:** `docs/IMPORTS.md`. **Tablas:** `DATA_MODEL.md` §0, §4–§7 y §10.

Igual que ADR 011, este documento separa tres cosas:
- lo **verificado** en el repositorio y en las muestras reales;
- las **decisiones**;
- los **supuestos pendientes de verificar**.

Un supuesto nunca se presenta como hecho.

## 1. VERIFICADO

### 1.1 Repositorio (`main` en `66c1b3e`, migraciones `0001`–`0031`)
- **No existe infraestructura de importación:**
  - ni lectores de archivos, uploads, jobs, staging, mappings, `customers` ni schema `commercial`;
  - `upload_max_size_mb` está definido y no se usa;
  - `python-multipart` es solo dependencia de desarrollo.
- **Plataforma reutilizable:**
  - `employees` + `employee_functions` (función `commercial.salesperson` registrada) y `user_module_roles` (`viewer < operator < admin`);
  - `audit_events` (`record_change` con before/after) y `app_settings`;
  - `source_systems` (sembrada con AXUM, STORE_SYS, MANUAL y DEMO, sin FKs);
  - `outlets` y la validación de CUIT.
- **`employees.external_store_id` lo administra Rendiciones** (área `settlements.store_sys_import`, con motivo y auditoría); ADR 011 deja abierto si Comercial lo usa. `external_axum_id` no tiene escritor.
- **La única entrada STORE SYS existente es la de Rendiciones** (ADR 011): un contrato JSON de importe esperado. No lee archivos y no se toca.
- **Reglas del hilo Servidor (S0 v2 §10.2):**
  - archivos solo bajo `UPLOAD_DIR` (nombre UUID, volumen con backup);
  - `UPLOAD_MAX_SIZE_MB` y progreso de subida;
  - una transacción por lote y lock consultivo por tipo de import;
  - procesos como CLI idempotente; sin Celery ni Redis; sin estado en memoria;
  - Comercial no toca despliegue, CI ni administración de usuarios.

### 1.2 Muestras reales recibidas el 2026-10-30
Las muestras son de **formato**, no historia productiva, y no se commitean. El perfil completo está en `docs/IMPORTS.md §3`.

| Archivo | Formato real | Hallazgo que condiciona el diseño |
|---|---|---|
| `clientes.xls` | OLE2 **BIFF7** (Excel 5/95), codepage 1252 | Maestro real de clientes STORE SYS: `codigo` único; `zona` y `vend` son **texto**, no códigos; 10 nombres repetidos; CUIT vacío en 739/820 |
| `ventas.xls` | BIFF7 | Una fila por artículo vendido. **Sin número de comprobante, sin importes y sin código de cliente:** el cliente viene como nombre de fantasía. `vendedor` y `zona` coinciden 236/236 con la asignación actual del maestro. Filas idénticas repetidas legítimas (9). Sin filtros visibles en el archivo |
| `comprobantes emitidos.xls` | BIFF7 | Una fila por comprobante, sin ítems. `nommov` = tipo + letra + PPPP‑NNNNNNNN en un solo texto. `codcli` presente. `netosa` sin signo y `total` con signo. Fecha y **hora**. Tipos: FACTURA A/B, COMP. INTERNO X, RECIBOS X, DEVOLUCION X, NOTA DE CREDITO A, RETIRO PR., VALE PERSONAL V y «FACTURA» sin letra con neto 0 |
| `clientes_por_zona.xlsx`, `clientes_por_vendedor.xlsx` | XLSX generados con openpyxl (no por STORE SYS) | Informes derivados de `clientes.xls`, con resúmenes y columnas calculadas. **No son fuente de importación** |

**No llegaron** el maestro de artículos ni el CSV de AXUM No Compra. Sus adaptadores quedan sin cerrar (§3).

## 2. DECISIONES

### 2.1 Propiedad (D‑A)
- **Plataforma (`public`)** tiene el framework genérico y la identidad de clientes:
  - `import_jobs`, `import_files` e `import_rows` (staging);
  - `external_entity_mappings` y `data_quality_issues`;
  - los lectores CSV/XLSX/XLS y el registro de tipos de import;
  - `customers`.
- **Comercial (`commercial`)** tiene los adaptadores por reporte y todos los maestros y hechos comerciales.
- Cada módulo registra sus tipos de import y sus códigos de calidad en `ModuleSpec`, como ya hace con funciones de empleado y settings. La plataforma nunca importa un módulo.

### 2.2 Pipeline en dos pasos (D‑F)
`subir → detectar → analizar (staging + preview, sin escritura canónica) → confirmar (operator) → escribir en una transacción → auditar`.
- Analizar **nunca** escribe datos canónicos.
- Los estados de `import_jobs` son `pending`, `processing`, `completed`, `completed_with_warnings`, `failed`, `duplicate` y `cancelled` (D‑E). Todo intento queda registrado.

### 2.3 Formatos
- **Detección por firma, nunca por extensión:**
  - OLE2 → lector XLS (`xlrd` 2.x, BIFF2–8; las muestras son BIFF7);
  - ZIP con `[Content_Types].xml` → XLSX (`openpyxl` read‑only, `data_only`; `.xlsm` rechazado; `defusedxml`);
  - texto → CSV (encoding UTF‑8 con o sin BOM o CP1252; separador `;`, `,` o tab).
- Un `.xls` que sea HTML o SpreadsheetML se rechaza con un mensaje claro hasta que exista una muestra que justifique su lector.
- El usuario nunca convierte archivos y la app nunca escribe sobre el original.

### 2.4 Adaptadores fijos, sin plantillas de columnas
Los reportes son exports fijos.
- Cada adaptador declara sus columnas requeridas y opcionales con alias, más su `version` (que se guarda en el job).
- Si STORE SYS cambia un encabezado, se agrega un alias y sube la versión.
- `import_column_mappings` (ADR 003) **no se implementa**.

### 2.5 Staging
- **Una tabla genérica `import_rows`** con `raw` jsonb, `normalized` jsonb, `status`, `messages`, `business_key` y `row_hash`.
- Se guarda la fila de cada archivo, incluidas las de título, vacías o subtotales (`skipped`).
- **Sin purga por defecto**: permite reprocesar (§2.11). La retención es configurable.

### 2.6 Resolución de entidades (D‑B, D‑C)
- **Orden:**
  1. mapping existente (`confirmed`/`excluded`);
  2. ID externo exacto;
  3. regla segura explícita y configurable;
  4. `pending`;
  5. decisión manual.
- Nunca hay fuzzy matching ni fusión silenciosa por nombre.
- **El vínculo entidad ↔ sistema vive solo en `external_entity_mappings`:** `(entity_type, source_system, namespace, external_id)` único, con `status` `pending`, `confirmed`, `ambiguous`, `rejected` o `excluded`. Por eso `customers` no tiene columnas `external_*`.
- **AXUM y STORE SYS son espacios de IDs independientes (D‑B):**
  - `commercial.axum_customer_code_matches_store_sys = false`;
  - una regla de equivalencia solo se habilita después de comprobarla con muestras reales y con aprobación de Enzo.
- **Vendedor de STORE SYS (D‑C):**
  - Mapping `employee / STORE_SYS / seller`. Como el export trae nombre, `external_id` es la **etiqueta normalizada** y se confirma a mano.
  - Comercial **lee** `employees.external_store_id` y **nunca lo escribe**.
  - Los imports **nunca crean empleados**.
  - «BOCA DE EXPENDIO» y otros pseudo‑vendedores se marcan `excluded`.
- **Cliente:**
  - Lo crea solo el maestro de STORE SYS (`created_from_master`) y los comprobantes lo resuelven por `codcli` exacto.
  - Las ventas (sin código) se resuelven por un mapping `customer / STORE_SYS / customer_name`, que exige confirmación manual.
  - El sistema puede **proponer** como candidato al único cliente del maestro con ese nombre exacto normalizado, pero no lo confirma solo.
  - Nombre repetido → `ambiguous`.
  - Los hechos sin resolver se guardan igual, con `customer_id NULL` y el valor fuente, y se completan al resolver el mapping, sin reimportar.

### 2.7 Idempotencia
1. **Archivo idéntico:** `sha256` + `import_type` contra un job `completed`/`completed_with_warnings` → job `duplicate`, sin escritura. El nombre del archivo no participa.
2. **Clave natural** por entidad (`docs/IMPORTS.md §5`):
   - cliente por código STORE SYS;
   - producto por código;
   - comprobante por `nommov` normalizado (tipo + letra + punto de venta + número);
   - asignaciones y precios por vigencia.
   - Si un comprobante existente llega con otro contenido, **no se pisa**: la fila se rechaza y se abre un issue.
3. **Ventas artículos/clientes, reemplazo por período y scope (D‑G corregido):**
   - El archivo no trae filtros, así que el scope **no se puede deducir del archivo**. Lo declara el operador al analizar: «export completo, sin filtros» o «filtrado por…».
   - Se guarda en `import_jobs.scope` junto con las dimensiones observadas (vendedores, zonas, fechas).
   - **El reemplazo automático** solo ocurre si el job nuevo y el anterior declaran scope completo y el nuevo cubre los mismos días. Las líneas anteriores de esos días quedan `superseded`, nunca borradas.
   - **En cualquier otro caso** (scopes distintos o no comparables, solapamiento parcial ambiguo) la confirmación **se detiene**. Hace falta una decisión explícita de un admin, auditada.
   - Nada se reemplaza por suposición.
   - Las filas idénticas dentro de un archivo se conservan con su `occurrence`.
4. **AXUM No Compra (ajuste 1):**
   - **No hay restricción UNIQUE** sobre `fecha + cliente + preventista + motivo`.
   - Cada fila conserva su ocurrencia (`import_job_id` + `occurrence`).
   - La estrategia contra el reimport del mismo período se define con el CSV real y se aprueba antes de F1.4.
5. **Concurrencia:** lock consultivo de PostgreSQL por `import_type` y una transacción por job.

### 2.8 Zonas y visitas (ajuste 2, D‑H, D‑J)
- **Modelo:**
  - `commercial.zones` (nombre fuente, tipo, `requires_visit`) y `zone_visit_days` con vigencia reemplazan a `routes` y `route_schedules`.
  - `zone_classification_rules` (patrón → tipo, visita y días) **solo propone** la configuración de una zona **la primera vez que aparece**. Después manda la configuración explícita de la zona y una regla nunca la pisa.
- **Reglas de negocio de Enzo** (Lumivi L/M/V, Majusa M/J/V, Bandera J, Boca de Expendio sin visita). Los alias reales observados en STORE SYS son:
  - `RUTA 01/02 - LUVI`;
  - `RUTA 04/06 - MAJU`;
  - `RUTA 07 - TACAÑITAS/BANDERA`;
  - `RUTA 08 - BOCA DE EXPENDIO`;
  - además, `RUTA 03/05 - MISA`, **sin regla definida**: esas zonas entran `UNCLASSIFIED` con un issue hasta que Enzo defina sus días.
  - Que `LUVI` = Lumivi y `MAJU` = Majusa es una inferencia por el nombre: se confirma antes de sembrar las reglas en F1.2.
- La zona «Boca de Expendio» **no se vincula** con `public.outlets` en Fase 1 (D‑J).
- **No se crean** `visits`, `scheduled_visits`, `orders` ni `order_items` (D‑H). Quedan documentadas como futuras. No se infieren visitas ni cobertura.

### 2.9 Comprobantes, ventas y universo comercial
- Comprobantes (`documents`) y líneas de venta (`sale_lines`) son **dos hechos separados y sin enlace**: las fuentes no comparten clave.
- `document_types` es configurable, con `document_class`, `sign` y `commercial_universe`:
  - `RETIRO PR.` es `INTERNAL_MOVEMENT` y queda fuera del universo comercial (regla de Enzo);
  - un tipo nuevo entra `UNCLASSIFIED` y **fuera** del universo hasta que un admin lo clasifica;
  - la clasificación propuesta para los demás tipos observados es interpretación contable y **requiere confirmación de Enzo** (`CLAUDE.md §12`) antes de sembrarla.
- El neto canónico con signo es `netosa × sign` del tipo. Si el signo de `total` lo contradice, queda un warning.

### 2.10 Productos, listas de precio y familias (D‑D, D‑K)
- **Campos fuente y canónicos separados en `products`:**
  - fuente: `source_*`;
  - canónicos: `canonical_category_id`, `product_kind`, `analytics_eligible`, `family_id`.
- Un reimport solo actualiza los `source_*`. Lo manual nunca se pisa.
- El rubro fuente se traduce a la categoría canónica con `source_category_mappings`, que es explícito.
- **Producto sin categoría y sin clasificar:** `analytics_eligible = false` hasta clasificarlo.
- **Listas de precio:** `commercial.price_lists` guarda número, nombre, semántica y tipo, sembrada con las listas 1–5 que confirmó Enzo. `product_prices` guarda la historia por vigencia.
- **Familias:** `product_families` tiene tabla, modelo, API y asignación explícita. La pantalla llega con la fase de portfolio. Nunca se infiere una familia por texto.

### 2.11 Calidad, auditoría y reproceso
- **`data_quality_issues` persistente:**
  - códigos registrados por módulo;
  - severidad y estado `open`, `resolved` o `ignored`;
  - dedupe por `(code, dedupe_key)` mientras está abierto;
  - vínculo al job, la fila y la entidad.
- Ninguna fila problemática se descarta.
- **Cambios manuales auditados** en `audit_events` (before, after y motivo en el payload): mappings, configuración, categorías, `analytics_eligible`, familias, asignaciones, reprocesos y resolución de issues.
- **Reproceso:**
  - re‑resolver mappings completa los hechos pendientes;
  - reprocesar un job crea otro job (`reprocess_of_job_id`) desde el staging o el archivo, con las mismas claves naturales y solo con rol admin;
  - un job caído en `processing` pasa a `failed` (CLI `imports recover`).

### 2.12 Permisos (D‑I)
| Rol | Puede |
|---|---|
| `viewer` | Leer |
| `operator` | Subir, analizar, confirmar imports y resolver mappings operativos |
| `admin` | Configuración (zonas y días, listas, categorías, tipos de comprobante, reglas), reprocesos, reemplazos de período que exigen decisión y cambios manuales sensibles |

Todo bajo `require_module_access("commercial", …)`.

### 2.13 Inicio productivo (ajuste 3)
- Las muestras son de formato. DistriCom empieza su historia el día que entra en funcionamiento.
- La carga histórica es una decisión separada.
- Los fixtures del repo son **sintéticos** con la misma estructura, nunca datos reales.

## 3. SUPUESTOS SIN VERIFICAR (se cierran antes de la subfase indicada)

| # | Supuesto | Se verifica con | Antes de |
|---|---|---|---|
| S1 | Días de visita de las zonas MISA (`RUTA 03` y `RUTA 05`) | Enzo | F1.2 (siembra de reglas) |
| S2 | `LUVI` = Lumivi y `MAJU` = Majusa | Enzo | F1.2 |
| S3 | Clasificación contable de COMP. INTERNO X, RECIBOS X, DEVOLUCION X, VALE PERSONAL V y «FACTURA» sin letra con neto 0 | Enzo | F1.2 (siembra de `document_types`) |
| S4 | Si STORE SYS puede exportar *Ventas artículos/clientes* con código de cliente (eliminaría el mapping por nombre) | Enzo / STORE SYS | F1.3 |
| S5 | Formato, encoding, columnas y listas 1–7 del maestro de artículos | Muestra real | F1.3 (adaptador de artículos) |
| S6 | Estructura del CSV AXUM No Compra y estrategia de ocurrencias | Muestra real | F1.4 |
| S7 | Que `ventas.xls` siempre traiga una fecha por fila (en la muestra hay un solo día) | Muestra de varios días | F1.3 |
| S8 | Que las propiedades OLE de los `.xls` (último guardado por un usuario de Windows) no indiquen que pasaron por Excel antes de subirse | Enzo | F1.3 |

## 4. CONSECUENCIAS

- **Docs:** `DATA_MODEL.md` cambia las tablas de Fase 1 (§0, §4–§7, §10–§12). `METRICS.md` **no** cambia en Fase 1, pero queda una deuda declarada: `visit.conversion_order` y el denominador de `visit.conversion_invoiced` suponen visitas que hoy no existen y se redefinen antes de la fase de métricas.
- **Dependencias nuevas** (F1.1b): `openpyxl`, `xlrd`, `defusedxml` y `python-multipart` (runtime); `xlwt` solo en desarrollo, para generar fixtures `.xls`.
- **Dependencias de otros hilos:**
  - `UPLOAD_DIR` como volumen persistente con backup lo provee el hilo Servidor (PR #54). Comercial solo agrega la variable.
  - Las migraciones se numeran desde el `main` vigente al abrir cada PR.
- **Lo que no se hace en Fase 1:**
  - métricas, dashboards, scoring, IA ni recomendaciones;
  - visitas, pedidos ni programación;
  - plantillas de columnas;
  - vínculo de zonas con bocas;
  - pantalla de familias;
  - sugerencias por similitud de texto.
