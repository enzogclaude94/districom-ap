# ADR 010 — Finanzas de empleados y liquidación interna

- Estado: Aceptado · 2026-10-15 (arquitectura de R8 aprobada por Enzo con precisiones: decisiones D1–D14 y precisiones A–D)
- Amplía: ADR 006 (plataforma modular), ADR 007 (módulo Rendiciones) y ADR 008 (ledger). No los reemplaza.
- **Supersede parcialmente ADR 007** en una sola mención: donde ADR 007 dice «liquidación semanal de empleados», vale el modelo **por período** de este ADR (§4). ADR 007 no se edita.
- Propuesta de origen: `PROPUESTA_R8_CUENTA_INTERNA_Y_LIQUIDACION_DE_EMPLEADOS.md` (2026-10-14).

Este ADR separa lo **DECIDIDO** (vale para todo R8) de lo **IMPLEMENTADO** (lo que ya existe en el código). Al cierre de R8‑1 está implementado: el área protegida con permiso explícito, la privacidad de Cajas, la condición salarial (`0025`) y la cuenta interna en solo lectura. Nada de R8‑2 a R8‑5 existe todavía.

## Contexto
R8 cubre la **relación económica interna** entre DistriCom y cada empleado: sueldo, bonos, incentivos, faltantes y sobrantes ya asignados, consumos, adelantos, préstamos y cuotas, ajustes y el pago de la liquidación. **No es RRHH:** quedan afuera asistencia, vacaciones, licencias, ART, cargas sociales, recibos legales, AFIP/ARCA, convenio y contabilidad de nómina.

Antes de R8 la cuenta interna `EMPLOYEE` (una por empleado, R2) ya existía y la movían los cierres de rendición (`SETTLEMENT_CLOSE`) y los incobrables asignados (`PENDING_WRITE_OFF`), con sus reversos. Cajas la mostraba a cualquier `settlements.viewer`: con sueldos y préstamos adentro eso sería una fuga, así que la privacidad va **antes** del primer sueldo.

## Decisión

### 1. Área protegida dentro de Rendiciones — DECIDIDO · IMPLEMENTADO (R8‑1, D1, D12)
- R8 vive en el módulo Rendiciones (mismo ledger, misma única puerta de ADR 007/008) como el **área protegida** `settlements.employee_finance` («Empleados»). No es un módulo aparte.
- **La plataforma modela las áreas, no las endpoints.** `ModuleSpec.protected_areas` declara `ProtectedAreaSpec(key, name, explicit_grant_required)`; la clave usa el prefijo del módulo (`<módulo>.<área>`, validado al instalar). La capa central de autorización (`app/platform/auth/access.py`) resuelve `area_role`, `has_area_access` y `require_area_access`: ninguna endpoint mira strings.
- **Permiso explícito, también para el admin de plataforma.** Con `explicit_grant_required = True` (el caso de Empleados) el admin de plataforma **no hereda** el área: necesita su propio permiso. Un área con `False` heredaría el admin, como un módulo; el flag es por área.
- **El área exige además un rol en su módulo.** Un permiso del área sin rol en Rendiciones no llega a nada, y un rol de Rendiciones (incluso admin) no da el área.
- **Sin el área: 404 y nada que descubrir.** Toda endpoint del área responde `404 not_found` «No encontrado.» sin detalles, también las de escritura y aunque el recurso no exista. Con el área pero por debajo del rol requerido: `403 forbidden`.
- **Roles del área:** `viewer` (lee), `operator` (lee; en R8‑2 registra consumos), `admin` (condición salarial y, en fases siguientes, lo que decide sobre dinero; tabla completa en §6 de la propuesta, con D9).
- **Almacenamiento:** el permiso es una fila de `user_module_roles` con `module_key = 'settlements.employee_finance'` (texto de 40 caracteres, sin migración de plataforma). Alta y baja auditadas como `platform.module_role.{granted, changed, revoked}`.
- **Bootstrap documentado.** El admin de plataforma administra permisos sin poder leer el área antes de tenerla:
  - CLI: `grant-module-role --module settlements.employee_finance --role admin` / `revoke-module-role`;
  - API (solo admin de plataforma): `PUT /api/v1/auth/access-grants` `{email, key, role}` y `POST /api/v1/auth/access-grants/revoke` `{email, key}`. Responden quién tiene qué rol; nunca datos del área.
  - Un admin de plataforma puede otorgarse el área a sí mismo; queda auditado con su usuario.
- **Frontend:** `/auth/me` devuelve `areas` (solo permisos explícitos). `areaRole()` no tiene atajo de admin de plataforma. El menú «Empleados» (`NavItem.area`) y las pantallas solo existen con el área; sin ella la pantalla se lee como «Página no encontrada».

### 2. Privacidad — DECIDIDO · IMPLEMENTADO (R8‑1, D2)
- **Cajas, sin el área:**
  - `GET /settlements/accounts` no lista cuentas `EMPLOYEE` ni (desde su fase) las cuentas técnicas de R8;
  - `GET /settlements/accounts/{id}` y sus movimientos responden `404 account_not_found`, igual que una cuenta inexistente.
- **Operaciones de R8 vistas desde una caja o el Banco** (adelanto, préstamo, pago, y los demás tipos R8 y sus reversos): se ven fecha, importe, la caja y el sentido, para conciliar. Se ocultan el empleado, el tipo exacto, la descripción, el documento de origen y toda pata fuera de Caja/Banco. Tipo `EMPLOYEE_RESERVED`, texto «Movimiento de empleado (reservado)», sin acción de reverso. La regla está en `employee_finance/privacy.py` y ya cubre todos los tipos R8 decididos aunque todavía no existan.
- **Rendiciones (R2/R7) no cambia:** la asignación de un faltante o sobrante sigue visible donde se decide (la rendición y su cierre en Cajas). Eso no habilita el saldo `EMPLOYEE`, sus otros movimientos, sueldos ni préstamos.
- **Auditoría:** los eventos `settlements.employee_finance.*` se leen solo con el área (no hay visor de auditoría general).
- **Logs:** nunca la identidad de un empleado junto a un importe sensible. Los errores 403/404 no llevan datos sensibles.

### 3. Cuenta `EMPLOYEE` — DECIDIDO · IMPLEMENTADO en solo lectura (R8‑1, D3, precisión D)
- **Una cuenta por empleado**, la existente; se sigue creando la primera vez que algo la mueve. Sin cuentas paralelas ni subcuenta de préstamos.
- **Signo:** positivo = el empleado le debe a la empresa («Debe a la empresa»); negativo = la empresa le debe («La empresa le debe»); cero = «Sin saldo».
- **La cuenta es neta.** Cualquier desglose (qué es préstamo, qué está a pagar) sale de **documentos + aplicaciones**, nunca de descomponer el saldo con aritmética.
- **R8‑1:** `GET /settlements/employee-finance/employees` (saldo, lectura, condición vigente y próxima), `GET /employees/{id}` y `GET /employees/{id}/movements` («Movimientos de la cuenta interna»): cada pata `EMPLOYEE` con fecha, importe con signo, su origen (faltante, sobrante, corrección de rendición, incobrable asignado y su reverso) y su documento (rendición de reparto o de boca, pendiente de cobro). Un reverso apunta al documento de la operación que revierte. Se lee del ledger y de los documentos; no se copia nada. Todavía no hay aplicaciones: la pantalla no habla de «aplicado/pendiente».

### 4. Modelo por período — DECIDIDO (R8‑4)
- Cada liquidación tiene `period_start`/`period_end` (inclusivos), `payment_date` informativa y `kind`: `REGULAR` (no se superpone con otra `REGULAR` del mismo empleado, exclusión GiST) o `EXTRAORDINARY`.
- **Sin columna de frecuencia.** El sueldo se liquida mensual; un pago semanal de dinero es un **adelanto**, no un sueldo dividido. Esto reemplaza la mención «liquidación semanal» de ADR 007.

### 5. Condición salarial — DECIDIDO · IMPLEMENTADO (R8‑1, `0025`, D5)
- `settlements.employee_compensation_terms`: `employee_id`, `valid_from`, `valid_to` (inclusivos), `base_salary numeric(14,2) ≥ 0`, `notes`, `created_by`, `created_at`, `ended_by`, `ended_at`, `end_reason`.
- **Definición:** `base_salary` es el **sueldo base interno mensual**, el importe nominal que DistriCom usa para su liquidación interna. No es bruto ni neto legal, aportes, convenio ni recibo oficial. La pantalla dice «Sueldo base interno (mensual). No es el recibo de sueldo legal.»
- Nace abierta, para un empleado **activo** (como toda asignación nueva); puede empezar en el futuro si no se superpone. Sin superposición por empleado (exclusión GiST con rango `[]`). Se termina **una sola vez** con día, quién, cuándo y motivo; empleado, inicio, sueldo, nota y autor son inmutables; sin `DELETE`. Un cambio de sueldo = terminar la vigente y crear la nueva. Un empleado inactivo conserva su historia y su condición abierta se puede terminar.
- La base repite cada regla (trigger `trg_employee_compensation_terms_guard`, `forbid_delete`); bajar de `0025` con condiciones cargadas se rechaza.
- Lectura: viewer/operator/admin del área. Alta y fin: admin del área. Auditoría `settlements.employee_finance.compensation_term.{created, ended}`.
- **Uso en la liquidación (R8‑4, D5):** sin prorrateo automático. Mes calendario completo con una sola condición vigente → la línea Sueldo se propone; período parcial, cambio de sueldo o varias condiciones → aviso y la línea SALARY se carga a mano, con nota.

### 6. Cuenta, liquidación y pago son tres dominios — DECIDIDO (R8‑2 a R8‑5, D4, D10)
- **Cuenta interna:** saldo continuo; cada hecho es una pata `EMPLOYEE` con su documento.
- **Liquidación interna** (`employee_payrolls`, en pantalla «Liquidación interna», nunca «recibo legal», D13): agrega haberes (SALARY, BONUS, INCENTIVE manual, OTHER_CREDIT) y **aplica** movimientos de la cuenta. Devengado al **cerrar** con `PAYROLL_CLOSE` («Sueldos y bonos internos» +H · `EMPLOYEE` −H).
- **Pago:** documento propio `EMPLOYEE_PAYOUT` (`employee_payouts` + ítems), **no** `PAYMENT` de R6 ni un tercero por empleado. Efectivo + transferencia, sin cheque, sin interés, varios pagos por liquidación; Σ pagos vigentes ≤ neto del cierre vigente.
- Consumos (solo importe, D11), adelantos (admin del área + operator de Rendiciones si mueve Caja/Banco; consumos, operator del área, D9), ajustes, préstamos y cuotas son documentos propios; todo dinero pasa por `ledger/service.py`; sin `DELETE`.

### 7. Aplicaciones — DECIDIDO (R8‑4, precisiones A y B)
- La unidad de aplicación es la **pata `EMPLOYEE`** del ledger; la pata original nunca cambia. Aplicación parcial permitida para faltantes, sobrantes, consumos, adelantos y ajustes; Σ |aplicaciones vigentes| ≤ |pata|.
- **Reversos de rendición (precisión A):** las patas de reverso son independientes e inmutables. El motor las neutraliza aplicando **ambas**: +30.000 y −30.000 se muestran agrupadas como «Corrección de rendición · efecto neto $0», las dos aplicadas por completo. Si el original estaba aplicado 10.000 y llega el reverso −30.000, la próxima liquidación aplica +20.000 del original y −30.000 del reverso (crédito neto 10.000). Un reverso nuevo después de la vista previa vence el token.
- **Cuotas (precisión B):** estados estructurales `SCHEDULED` / `DEFERRED` / `CANCELLED`; «Pendiente de aplicar» y «Aplicada» son derivados. Una cuota se aplica **completa** (sin parciales en R8). El invariante del cronograma se define sobre las `SCHEDULED` y las cadenas de reemplazo (`deferred_from_id`).

### 8. Cierre, reapertura y correcciones — DECIDIDO (R8‑4, D6, D7, D8, precisión C)
- Vista previa con token; cierre versionado con snapshot; reabrir deja el cierre anterior vigente hasta el nuevo (como R2/R7).
- **Neto ≥ 0** obligatorio; sin tope automático de descuento: la vista previa muestra haberes, deducciones, % y neto (D6).
- **Precisión C:** el cierre sin haberes a devengar no genera `PAYROLL_CLOSE` de $0 (el neto puede no ser 0: puede compensar saldos). El nombre `ZERO` resulta engañoso; se propone `NO_ACCRUAL` («sin devengado») y se confirma antes de migrar en R8‑4.
- **D7:** una liquidación con pagos vigentes **no se reabre**: se revierten los pagos o se corrige después con un ajuste.
- **D8:** un documento R8 aplicado en un cierre vigente no se revierte directo. Si la liquidación no está pagada, un admin la reabre y corrige; si ya está pagada, ajuste compensatorio en un período posterior, con motivo, referencia al documento original y auditoría.

### 9. Fases — DECIDIDO
| Fase | Contenido | Migración | Estado |
|---|---|---|---|
| R8‑1 | Área protegida con permiso explícito, privacidad de Cajas, condición salarial, cuenta interna en solo lectura | `0025` | IMPLEMENTADO (aprobado por Enzo el 2026-10-16; `0025` congelada) |
| R8‑2 | Consumos, adelantos y ajustes | `0026` | DECIDIDO |
| R8‑3 | Préstamos, cuotas y postergación | `0027` | DECIDIDO |
| R8‑4 | Liquidación: períodos, líneas, aplicaciones, vista previa, cierre, snapshot, reapertura | `0028` | DECIDIDO |
| R8‑5 | Pago `EMPLOYEE_PAYOUT`, consolidación y E2E de punta a punta | `0029` | DECIDIDO |

Cada fase frena con su informe y la aprobación de Enzo.

## Consecuencias
- Un usuario puede administrar Rendiciones sin ver nada de sueldos, y un admin de plataforma no ve sueldos por serlo.
- Los tests y el E2E que leen saldos `EMPLOYEE` en Cajas lo hacen con un usuario que tiene el área (en CI, el admin E2E recibe el permiso por CLI).
- Otras áreas futuras usan el mismo mecanismo (`ProtectedAreaSpec`), sin código especial en endpoints.
