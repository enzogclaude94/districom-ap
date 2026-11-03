# Requisitos del servidor DistriCom

Para elegir el equipo antes del piloto S3 (decisión D5 pendiente: no se compra nada en S1). Considera **Comercial y Rendiciones funcionando a la vez** en la misma instalación (ADR 013).

## Carga esperada

| Factor | Estimado |
|---|---|
| Usuarios simultáneos | 5–15 en la LAN + 1–3 remotos |
| Rendiciones | Realista medido en R9‑4: unas 25.000 operaciones y 84.000 asientos de ledger por año; p95 de pantallas < 250 ms en una máquina de desarrollo |
| Comercial (Fase 1+) | ~600 clientes, 500+ SKU, historia desde 2026: unos cientos de miles de líneas de venta por año, importaciones de archivos de hasta 25 MB y consultas analíticas |
| Base de datos | Pocos GB por año, incluidos los índices. El WAL diario archivado es del mismo orden que lo escrito ese día. |
| Procesos | PostgreSQL, un proceso backend, un proceso Node, Caddy y los timers; picos al importar, al hacer backups y al construir imágenes en una actualización |

## Equipo

| Ítem | Mínimo | Recomendado | Por qué |
|---|---|---|---|
| Tipo | PC de escritorio **dedicada** | Mini‑servidor o torre de servidor de oficina | No compartirlo con un puesto de trabajo: un apagado o un programa del usuario corta a todos |
| CPU | 4 núcleos x86‑64 (2018 o posterior) | 6–8 núcleos | La construcción de imágenes en una actualización y las importaciones de Comercial son los picos |
| RAM | 8 GB | **16 GB** | PostgreSQL usa la RAM libre como caché; con 16 GB entran la base completa y holgura para Comercial |
| Discos del sistema y datos | 2 × SSD 500 GB en **RAID 1** | 2 × SSD NVMe/SATA 1 TB en RAID 1, de línea empresarial o con buen TBW | El espejo evita el corte si muere un disco. **No es un backup.** |
| Disco de backup | 1 × HDD/SSD 1 TB separado | 2 TB, idealmente externo USB 3 o un NAS en la LAN | `repo1` de pgBackRest, archivos y dumps. Separado de los discos de datos. |
| Red | 1 GbE por cable, IP fija o reserva DHCP | Ídem, con un switch decente | Wi‑Fi no |
| UPS | 600 VA para el servidor | **1000–1500 VA** para el servidor **y** el router/ONT, con USB para apagado ordenado (NUT) | El acceso remoto necesita que el router siga encendido. PostgreSQL no pierde datos en un corte, pero el apagado ordenado evita chequeos largos. |
| BIOS | — | «Encender al volver la energía» activado | Vuelve solo después de un corte largo |
| Ubicación | Lugar ventilado | Rack o mueble cerrado con llave | Acceso físico = acceso a los datos |

## Sistema y red

- **Ubuntu Server 24.04 LTS** (soporte hasta 2029), sin entorno gráfico. Software RAID 1 desde el instalador.
- Docker Engine y Compose del repositorio oficial de Docker (`deploy/host/install.sh`).
- Hora sincronizada por NTP (chrony). La zona de negocio se configura en la app (`BUSINESS_TIMEZONE`).
- Router:
  - **ningún port‑forward** hacia el servidor y UPnP desactivado;
  - ideal: que permita un **registro DNS local**. Si no, se usa el DNS opcional del servidor o el archivo `hosts` de cada PC.
- Internet: cualquier enlace estable. El WAL se envía fuera del local de forma continua y son pocos MB por hora en operación normal. Opcional: router con respaldo 4G para mantener el acceso remoto y el envío de backups durante cortes.

## Servicios externos (se contratan antes de S3, no en S1)

| Servicio | Para qué | Costo estimado (informe S0 v2 §7) |
|---|---|---|
| Dominio propio con DNS en Cloudflare | Nombre y certificado HTTPS público | Dominio: anual bajo; DNS: sin costo |
| Tailscale Standard | Acceso remoto | US$ 8 por usuario remoto por mes |
| Bucket S3 compatible (Cloudflare R2, Backblaze B2 o AWS S3) | `repo2` y archivos fuera del local | < US$ 5 por mes |
| Proveedor de identidad con MFA (Google Workspace o Microsoft 365) | Login de Tailscale | Sin costo si la empresa ya lo tiene |

## Personas

Un **responsable** y un **suplente** del servidor, definidos antes de S3. Los dos tienen que poder seguir `docs/OPERATIONS.md`, acceder al resguardo de credenciales y estar en `group:districom-it` de Tailscale.

## Servidor Windows 11 Pro (ADR 016, propuesto)

Decisión de Enzo del 2026-11-03: DistriCom nativo en Windows 11 Pro, en el mismo equipo que Store Sys. Mientras el ADR 016 esté propuesto, lo anterior de este documento (Linux) sigue vigente. Plan completo en `docs/WINDOWS_DEPLOYMENT.md`.

| Ítem | Equipo disponible | Falta o a verificar |
|---|---|---|
| Placa y CPU | ASUS PRIME Z390‑A, Intel Core i3‑9100 (4 núcleos) | Requisitos de Store Sys, para confirmar que alcanza para los dos sistemas |
| RAM | 8 GB | Ampliación con 16 GB prevista, pendiente de verificación. Mínimo 16 GB; recomendado 24–32 GB |
| ECC | No soportada por la placa | Riesgo aceptado en el ADR 016 |
| Discos de datos | RAID 1 de Intel RST, 250 GB | Que sean dos discos reales en espejo, estado SMART, monitoreo de Intel RST; espacio que ocupe Store Sys |
| Disco de backup | WD Purple 1 TB | Estado SMART y horas de uso |
| Sistema | Windows 10 Pro 22H2 (sin soporte desde el 14/10/2025) | **Windows 11 Pro**: TPM 2.0 (Intel PTT), Secure Boot, disco GPT/UEFI; BitLocker |
| UPS | No informada | Para el servidor y el router/ONT |
| Licencias | — | Límite de 20 dispositivos de Windows cliente frente a los usuarios de DistriCom y Store Sys |

El equipo no se modifica hasta la autorización de Enzo.
