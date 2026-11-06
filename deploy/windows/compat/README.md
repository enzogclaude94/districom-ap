# W2‑B: compatibilidad de DistriCom en Windows

Comprueba que la aplicación, tal como está, funciona en Windows con PostgreSQL 17 nativo (ADR 016, «Propuesto»). No cambia el código de los módulos: si algo falla por el código de Comercial, Rendiciones o plataforma, se informa y se detiene (decisión de Enzo, 2026-11-06).

## Dónde corre

Corre en tres jobs de CI sobre `windows-latest` (Windows Server), con PostgreSQL 17 de los binarios oficiales. Las bases son descartables: las crea `.github/actions/windows-postgres` en UTF‑8 con ICU `es-AR` y los logs seguros de O7. Los datos son ficticios.

| Job | Criterios |
|---|---|
| Windows compatibility - backend | **B‑1** suite del backend · **B‑2** migraciones · **B‑3** calidad · **B‑4** orden de textos · **B‑5** zona horaria · **B‑6** arranque del backend · **B‑10** componentes reutilizables |
| Windows compatibility - frontend | **B‑7** instalación, lint, formato, tipos, tests y build |
| Windows compatibility - E2E | **B‑9** todos los flujos E2E · **B‑8** frontend de producción (standalone) |

Cada job deja su tabla PASS/FAIL en el log y en el resumen del job.

## Qué hay en esta carpeta

- `checks/`: chequeos de B‑4, B‑5, B‑6 y B‑10. Corren con el entorno del backend:

  ```bash
  cd deploy/windows/compat
  uv run --project ../../../backend pytest
  ```

  Necesitan:
  - `DISTRICOM_COMPAT_DATABASE_URL`: una base descartable, en un clúster ICU `es-AR`;
  - `DISTRICOM_PG_BIN` y `DISTRICOM_PG_LOG`: los binarios y el log de ese clúster;
  - `DISTRICOM_CADDY`: el binario de Caddy.

  Si falta alguna variable, el chequeo falla en vez de saltearse.
- `frontend_standalone_smoke.py`: B‑8.
  - Arma el frontend standalone igual que la imagen Linux.
  - Lo levanta con `node server.js` junto al backend.
  - Revisa las páginas, los headers de seguridad y el rewrite de `/api`.

## PENDIENTE: no se puede dar PASS en CI

- **Windows 11 Pro:** los runners son Windows Server.
- **Servicios de Windows:** PostgreSQL como servicio y backend, frontend y Caddy con WinSW. Pertenece a W2‑C y al laboratorio.
- **Hardware real:** el rendimiento en el i3‑9100 y los discos del servidor.
- **Convivencia con Store Sys.**
