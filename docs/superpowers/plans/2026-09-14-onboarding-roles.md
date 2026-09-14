# Alta automática, onboarding y roles

Objetivo autorizado: una base propia por cuenta, primer ingreso orientado a conectar
el asistente, roles con descripción y una conexión local comprensible.

Diseño: conservar el aislamiento PostgreSQL y OneToOne existente. Provisionar al
resolver la identidad Google fuera de transacciones de login; los fallos dejan la
cuenta utilizable y permiten reintentar. Persistir onboarding en la base personal.
Las descripciones explican intención al asistente; nunca conceden permisos.
Compartidas siguen disponibles aunque cada usuario tenga una base propia.

- [x] Backend: provisión automática, onboarding persistido, descripción/edición de
  roles vía API y MCP; pruebas de idempotencia, fallos y autorización.
- [x] App: backfill automático si falta base propia; conectar como primera pantalla
  hasta completar onboarding; ocultar búsqueda de tablas fuera de Mis datos.
- [x] Accesos: selector por rol, nombre/descripción editables, datos y personas
  dentro del rol seleccionado; pruebas de interacción y permisos.
- [x] Conexión: instrucciones copiables Claude Code primero y Codex, prueba de lectura
  y distinción localhost/servidor remoto; no simular conexión verificada.
- [x] Integración: tests backend/frontend, build, pruebas de navegador y revisión.

No cambiar datos existentes ni permisos, no eliminar exploración SQL. Preservar
cambios anteriores locales HTTPS y ALLOWED_HOSTS. Google OAuth real requiere
interacción del usuario; no introducir bypass de autenticación.

Verificación: 346 pruebas backend pasan (1 omisión por endpoint opcional de modelo
real); 44 frontend pasan en Node24, build y formato correctos. Seis escenarios de
navegador HTTPS pasan; uno se repitió tras ERR_NETWORK_CHANGED de Chromium.
Revisión independiente cerrada: conservación de bases compartidas ante fallo de
provisión y edición de nombre de rol corregidas y cubiertas por regresiones.
Migración 0004 aplicada localmente y MCP reiniciado. OAuth de Claude Code necesita
la interacción del usuario desde su equipo; no se simuló ni se completó ese login.
