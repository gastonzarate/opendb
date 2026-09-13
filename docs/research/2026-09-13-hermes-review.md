# Revisión de Hermes en Raspberry Pi

Inspección de solo lectura por Tailscale SSH como gaston, en
`/home/gaston/hermes`. Se revisó código y DDL de `hermes.db` en modo read-only;
no se copiaron documentos, credenciales ni registros personales. No se ejecutaron
pruebas sobre la Pi ni se modificaron servicios.

## Implementación observada

- Python con Peewee, Pydantic, SQLite, sqlite-vec y FastMCP.
- `core/models.py` y DDL real: entities, documents, chunks, action_items,
  agent_runs, tabla vectorial y FTS. Campos metadata almacenados como texto JSON.
- `core/db.py`: deduplicación SHA-256; documento y fragmentos pueden insertarse
  atómicamente. Embeddings validados con 384 dimensiones.
- `core/search.py`: búsqueda textual FTS5 + vectorial fusionadas con RRF.
- `core/embed.py`: llamadas a llama-server en /v1/embeddings.
- `mcp_server/app.py`: herramientas search_knowledge y save_note.
- `mcp_server/auth.py`: Google OAuth, un dueño y lista de invitados por entorno.
- `mcp_server/service.py`: invitados de solo lectura; resultados limitados a
  documentos cuya entidad tiene tipo client. Todos los invitados comparten ese
  filtro; no hay ACL por invitado/cliente/documento en el código inspeccionado.
- El filtrado de invitados ocurre después del ranking con sobrebúsqueda k*3;
  puede devolver menos resultados autorizados de los disponibles.
- `runner/run_agent.py`: automatizaciones mediante claude -p, bloqueo global,
  timeout y registro de ejecución. Es distinto del guardado directo por MCP.

## Implicaciones para el diseño, aún sin aprobar

Django sirve como base de aplicación. La migración conceptual a PostgreSQL puede
conservar entidades/documentos/fragmentos, pero la base personal flexible podría
requerir otro modelo. No se observaron tablas dinámicas de gastos o por nicho en
la base inspeccionada. No inferir que los documentos financieros no existen:
pueden estar en contenido o metadata, que no fueron leídos.

Definir con el usuario: esquema fijo o colecciones tipadas o tablas dinámicas;
ingesta desde asistente externo o desde servidor; unidad de permisos y primer
flujo funcional. Los permisos granulares son una capacidad a construir, no una
migración literal del filtro actual.
