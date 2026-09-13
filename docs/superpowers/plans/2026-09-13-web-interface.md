# Web Interface Implementation Plan

**Goal:** UI funcional del MVP y configuración local de Google compartida con Hermes.
**Architecture:** React/TypeScript separado, bundle estático servido por Django,
sesiones y CSRF del backend. Contrato actual /api/actions sin duplicar autorización.

- [x] Preparar bootstrap público y ruta /app/; probar CSRF, configuración y ausencia de secretos.
- [x] Crear shell React, cliente API tipado y navegación por bases/objetos.
- [x] Implementar explorador con paginación, esquema y consultas SQL.
- [x] Implementar roles/invitados, estado vectorial y conexión del asistente.
- [x] Integrar build en Compose y comprobar login/redirección con Google.
- [x] Probar flujos de UI (incluyendo invitado y errores) y verificar visualmente con navegador.
- [x] Ejecutar build, tests relevantes y controles; documentar callbacks pendientes si Google aún los rechaza.

Propiedad de archivos durante desarrollo paralelo: backend gateway y tests nuevos
por worker backend; pantallas administrativas TSX por worker UI; shell/explorador,
cliente, estilos, configuración, integración y pruebas de navegador por coordinador.

Estado: implementación local verificada. El consentimiento Google real sigue
pendiente de autorizar las URI en el cliente OAuth compartido con Hermes.
