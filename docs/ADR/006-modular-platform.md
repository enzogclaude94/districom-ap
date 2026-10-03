# ADR 006 — DistriCom como plataforma modular

- Estado: Aceptado · 2026-10-03 (aprobado por Enzo)
- Amplía: ADR 002 (organización de paquetes y alcance de la excepción analítica)

## Contexto
DistriCom nació como plataforma de inteligencia comercial. Enzo decidió que DistriCom sea una **plataforma modular**: un núcleo compartido y módulos de negocio. Inteligencia Comercial pasa a ser el primer módulo, con su alcance, métricas y roadmap sin cambios. Hay al menos un módulo futuro identificado, cuyo desarrollo **no** está aprobado.

La revisión del repositorio (2026-10-03) encontró que el código de Fase 0 es casi todo infraestructura compartida y sin ciclos de dependencias, pero que los nombres, la configuración, los roles, la navegación y el modelo de datos planeado para Fase 1 estaban pensados solo para Inteligencia Comercial.

## Decisión
1. **Una sola aplicación:** un login, una sesión, un backend, un frontend y una base de datos. Sin microservicios, apps separadas, microfrontends, plugins ni bases por módulo.
2. **Tres niveles en el backend:** `core/` + `db/` (técnico), `platform/` (negocio compartido: usuarios, acceso, configuración, auditoría, sistemas fuente, identidades compartidas, framework de importación) y `modules/<módulo>/` (hoy solo `commercial`).
3. **Regla de dependencias verificada en CI:** módulo → plataforma → core/db; la plataforma nunca importa de un módulo; un módulo no importa internals de otro (solo un `public.py` declarado). Backend: import-linter. Frontend: ESLint `no-restricted-imports`.
4. **Una base, un schema por módulo:** plataforma en `public`, Inteligencia Comercial en `commercial`. FKs módulo → plataforma permitidas; plataforma → módulo prohibidas. Una sola cadena Alembic.
5. **Única fuente de verdad para datos compartidos.** `employees` es la identidad transversal y única de cada persona; las funciones (preventista y, en el futuro, otras) se registran en `employee_functions` con historia (empleado, función, desde, hasta), en plataforma. Los datos propios de cada función viven en el módulo dueño. Lo mismo para `customers` (identidad en plataforma, relaciones y derivados comerciales en el módulo).
6. **Rutas de preventa** son entidad del módulo comercial y no representan recorridos operativos de otro tipo.
7. **Comprobantes de STORE SYS** (`sales`, `sale_items`) permanecen en el módulo comercial, tal como estaban planteados.
8. **Configuración por scope:** variables de entorno para infraestructura (incluida `BUSINESS_TIMEZONE`); `app_settings` con claves `platform.*` y `<módulo>.*`, cada scope con sus defaults junto a su dueño.
9. **Acceso por módulo en etapas:** Etapa 0 deriva el rol del módulo del rol de plataforma mediante `require_module_access`; los roles por módulo (tabla `user_module_roles`) se crean solo cuando exista un segundo módulo.
10. **Frontend:** shell de plataforma; cada módulo con prefijo de URL (`/comercial`) y navegación propia; mismo branding y design system.
11. **Sin anticipar módulos futuros:** no se crean tablas, modelos, funciones, endpoints, pantallas ni parámetros de módulos no aprobados. Solo infraestructura genérica que Inteligencia Comercial ya necesita.

## Consecuencias
- Agregar un módulo es una lista de chequeo (ARCHITECTURE.md §4.5), no una migración arquitectónica.
- La reorganización de Fase 0 es mecánica: mueve paquetes y renombra claves de configuración sin cambiar tablas ni el flujo de autenticación.
- Las URLs del módulo comercial cambian a `/comercial/…` antes de que existan pantallas reales.
- Costo aceptado: las tablas de módulo se califican con su schema en SQL y migraciones.
