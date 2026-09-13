# OpenDB MVP: orden de implementación

**Spec:** ../specs/2026-09-13-mvp-design.md
**Estado:** planificación; no hay funcionalidad del MVP implementada todavía.

Este roadmap separa el MVP en incrementos verificables. No reemplaza los planes
detallados de cada incremento. pgvector, embeddings y compartir están incluidos
en el MVP completo; el orden no significa posponerlos a otra versión del producto.

| Etapa | Resultado | Condición de aceptación |
|---|---|---|
| 1. Bases privadas | Aprovisionar y conectar cada base con credenciales propias | Dos identidades PostgreSQL no acceden a la base de la otra ni a la de control |
| 2. Identidad y MCP | Login Google, autorización MCP y acceso a la base propia | Autenticación completa desde cliente MCP; identidad ajena rechazada |
| 3. Esquema y catálogo | Descubrir y modificar tablas/vistas reales con descripciones | Cambios visibles en catálogo; esquema interno protegido |
| 4. Ingesta | Operaciones estructuradas, fuentes, relaciones e idempotencia | Reunión descompuesta; reintento sin duplicados; rollback ante fallos |
| 5. Vectores | Embeddings locales, fragmentación, búsqueda y sincronización | Actualización concurrente no publica vector obsoleto; fallo del modelo recuperable |
| 6. Compartir | Roles acumulables y permisos de lectura a tablas/vistas | Unión y revocación correctas; una vista no abre sus tablas fuente |
| 7. Integración | Flujos MCP completos y API reutilizable para futura UI | Reuniones y gastos desde dos dueños y un invitado con restricciones |

## Alcance del primer plan

El plan `2026-09-13-private-databases.md` cubre infraestructura y aprovisionamiento.
No expone todavía SQL a un cliente MCP. El executor SQL requiere sus propias
validaciones antes de recibir sentencias del agente.

## Decisiones técnicas que deben cerrarse en sus incrementos

- Etapa 2: flujo OAuth MCP verificado con SDK elegido; Google identifica usuarios,
  pero su token no debe aceptarse automáticamente como token de recurso de OpenDB.
- Etapa 3: validación de sentencias, funciones y privilegios; límites de recursos.
- Etapa 4: contrato versionado de ingesta con referencias y errores estructurados.
- Etapa 5: procedencia/checksum/licencia del GGUF; tokenizer y captura de cambios,
  incluyendo SQL directo y eliminación de estructuras vectorizadas.
- Etapa 6: mecanismo de vistas y revocación en conexiones activas; descubrimiento
  y búsqueda limitados al contenido autorizado.

No crear el frontend ni facturación durante estos incrementos.
