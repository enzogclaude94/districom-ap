# ADR 007 — Módulo Rendiciones (`settlements`)

- Estado: Aceptado · 2026-10-04 (aprobado por Enzo)
- Amplía: ADR 006 (no lo reemplaza: la plataforma modular sigue igual)
- Origen: *Informe Inicial — DistriCom Rendiciones* (2026-10-04) y la resolución de Enzo sobre sus contradicciones C1–C6.

## Contexto
ADR 006 dejó a DistriCom como plataforma modular con un solo módulo funcional (Inteligencia Comercial) y prohibió anticipar módulos no aprobados. Enzo aprobó el segundo módulo: **DistriCom Rendiciones**, que reemplaza el proceso administrativo que hoy se lleva en Excel: rendiciones de reparto y de boca de expendio, cajas, transferencias, pendientes de cobro, cartera de cheques, gastos, combustible, pagos a terceros, aportes de socios, y la cuenta interna y liquidación semanal de empleados.

La pregunta que el módulo responde es: *cuánto debía rendir una persona o reparto, cómo se compone, qué ingresó y por qué medio, qué quedó pendiente, qué gastos lo justificaron, qué diferencias hubo y dónde terminó cada valor*.

El relevamiento encontró que la arquitectura ya admite un segundo módulo (ADR 006, `ARCHITECTURE.md §4.5`), pero que las identidades compartidas (`employees`, `employee_functions`, `customers`) solo existían en documentación y estaban atadas a la Fase 1 de Comercial, bloqueada a la espera de exportaciones reales.

## Decisión

1. **Identidad del módulo (C1):** key técnico `settlements` (paquete `app/modules/settlements/`, schema `settlements`, API `/api/v1/settlements`, parámetros `settlements.*`); URL `/rendiciones`; nombre visible **Rendiciones**.
2. **Empleados en plataforma, ya (C2):** `employees` + `employee_functions` se implementan como funcionalidad de plataforma con alta manual, independiente de los imports de Comercial. Cada módulo registra en código los códigos de función que usa (`commercial.salesperson`, `settlements.delivery_driver`). Cuando lleguen los imports, las personas importadas se vinculan por ID externo, nunca por nombre.
3. **Clientes (C3):** mientras no exista el maestro de clientes de plataforma, Rendiciones guarda el nombre del cliente como texto y un `customer_id` opcional que se completa al vincularlo con la identidad central. Rendiciones **no** crea un catálogo de clientes propio ni da de alta clientes de plataforma.
4. **Logo (C4):** el PNG entregado por Enzo es el **logo oficial actual de DistriCom Rendiciones**. Se permiten solo adaptaciones técnicas (recorte, fondo transparente, optimización web; un SVG solo si reproduce exactamente la apariencia). No se adapta al wordmark de la plataforma. La unificación del branding general queda fuera de alcance (`docs/BRAND.md`).
5. **Registro de la decisión (C5):** este ADR; ADR 006 no se modifica.
6. **Alertas (C6):** en V1 las alertas de pendientes son internas y se calculan al leer, contra el parámetro `settlements.pending_alert_hours` (24 h). Sin scheduler, email ni WhatsApp.
7. **Roles por módulo (Etapa 1 de ADR 006):** tabla `user_module_roles` con roles `viewer < operator < admin`. `users.role = admin` identifica al administrador de plataforma, que tiene rol `admin` en todos los módulos; el resto de los usuarios accede solo a los módulos que tenga asignados. Los usuarios existentes conservan su acceso a Comercial.
8. **Ledger propio del módulo:** los movimientos de valores de Rendiciones tienen como fuente de verdad `settlements.accounts` + `financial_operations` + `ledger_entries`:
   - las entradas son **inmutables**; cada operación es **balanceada** (sus entradas suman cero, verificado en la base);
   - cada operación es **idempotente** (`client_request_id` único y unicidad del documento origen);
   - las correcciones son **reversos** (operación opuesta que apunta a la original), nunca ediciones ni borrados;
   - toda entrada es **trazable** a su operación y de ahí al documento origen (rendición, pago, pendiente…);
   - un **único servicio de operaciones** escribe en el ledger, en la misma transacción que el documento;
   - **sin** event sourcing, sin contabilidad formal (plan de cuentas, IVA, balance) y **sin saldos editables**: un saldo es siempre la suma de sus entradas.
9. **Límites:** Rendiciones no importa nada de Comercial ni Comercial de Rendiciones (`ARCHITECTURE.md §4.2`); no hay `public.py` declarado entre ellos. "Boca de expendio" es un **canal de venta** en Comercial y un **punto que rinde** (`settlements.outlets`) en Rendiciones: entidades distintas.
10. **Auditoría con antes/después:** las correcciones de datos de negocio registran en `audit_events.payload` la convención `{action, before, after}` (sin PII). La historia financiera vive en el ledger y en los historiales de estado; la auditoría registra quién hizo qué.
11. **Fuera de V1:** contabilidad formal, cuentas a pagar completas, conciliación con facturas de proveedores o bancaria automática, integración con Store Sys (solo se deja el origen del importe esperado: `MANUAL` hoy, `STORE_SYS` mañana), notificaciones externas.
12. **Avance por fases R0–R9** (`ROADMAP.md`), PRs chicos, tests desde el inicio y aprobación de Enzo al cierre de cada fase.

## Consecuencias
- ADR 006 §11 ("sin anticipar módulos futuros") sigue vigente para cualquier otro módulo; Rendiciones queda aprobado con este alcance.
- Las tablas de negocio de Rendiciones nacen en su fase (R1 en adelante), no antes. R0 solo crea el schema vacío, el registro del módulo, sus parámetros, su función de empleado y su navegación.
- `employees` deja de depender de la Fase 1 de Comercial; la Fase 1 agrega la vinculación por imports y `customers` / `localities`.
- El frontend calcula sumas provisorias mientras el usuario tipea en la pantalla de rendición (aritmética de pantalla); los totales oficiales y el cierre los calcula siempre el backend. No es una excepción a "el frontend no calcula KPIs".
- Convivirán dos lockups visuales (wordmark de plataforma y logo de Rendiciones) con azules levemente distintos, por decisión explícita.
