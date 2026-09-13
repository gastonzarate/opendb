# Entrega local del MVP — 2026-09-13

Código en `feat/mvp`, dentro de `opendb/`. El diseño y las decisiones aprobadas se
implementaron en servicios compartidos por MCP y la API de Django.

## Componentes

| Componente | Implementación y comprobación |
| --- | --- |
| Bases privadas | Base y rol PostgreSQL por dueño; clúster de datos separado del de control, provisión serializada y reintentable. Credenciales reales de un dueño rechazadas en otras bases. |
| Identidad | Google `sub` verificado mediante allauth/OAuth MCP; sesiones solo Google en API, CSRF y rechazo de identidad aportada en el payload. Consentimiento real pendiente de credenciales. |
| SQL y catálogo | Tablas/vistas reales; política AST de sentencias, relaciones, tipos y funciones. Catálogo descubre esquema y relaciones autorizadas. Historial de fuentes solo para el dueño. |
| Ingesta | Contrato JSON versionado, DDL + registros relacionados + anotaciones en una transacción, fingerprint y replay idempotente. Reuniones normalizadas y gastos con decimales exactos. |
| Embeddings | GGUF exacto de la Pi, 384 dimensiones y tokenizer real; pgvector, cola protegida, worker con leases/versiones y reintentos. SQL directo captura cambios de texto. |
| Compartir | Roles acumulables por email verificado, tablas/vistas explícitas y SELECT real en PostgreSQL. Revocación coordinada con sincronización y DDL; OIDs conservan identidad tras renombrar. |
| Integración | MCP SDK → dispatcher → PostgreSQL con dos dueños, reunión, gastos, invitado, vista SQL y búsqueda vectorial sobre una vista compartida. API usa el mismo dispatcher. |

## Resultado verificado

- **303 tests pasaron** en 29,80 segundos, incluyendo el modelo real.
- Todos los hooks de pre-commit pasaron: formato, Ruff, JSON/YAML/TOML,
  detección de claves privadas, django-upgrade y plantillas.
- `manage.py check`: sin incidencias; `makemigrations --check`: sin cambios.
- Imagen Docker construida y stack local saludable; migraciones aplicadas.
- Quedan avisos de dependencias: deprecación de Authlib/httpx y ausencia del
  directorio collectstatic en tests. No se ocultaron para producir una suite verde.

## Ejecución y comprobaciones

Instrucciones reproducibles en [README](../../README.md). El stack local incluye
Django, PostgreSQL de control, PostgreSQL personal con pgvector, llama-server,
indexer y correo de desarrollo. MCP se activa mediante el perfil `mcp` después de
configurar las credenciales de Google y sus callbacks.

```bash
scripts/test
uv run ruff check .
uv run pre-commit run --all-files
uv run python manage.py check --settings=config.settings.integration
uv run python manage.py makemigrations --check --dry-run --settings=config.settings.integration
```

El clúster de tests es desechable, está en localhost:55439 y se usa exclusivamente
para fixtures que crean y eliminan bases y roles propios. El test del modelo real
requiere `OPENDB_REAL_EMBEDDINGS_URL` y se omite cuando no se configura. Las otras
pruebas vectoriales usan un modelo determinista para probar versiones y permisos,
sin presentarlo como evaluación semántica.

## Decisiones y límites de esta entrega

- El asistente externo interpreta y normaliza; OpenDB valida y persiste. Las
  restricciones SQL no prueban que una interpretación del contenido sea correcta.
- SQL se limita a 500 filas y 4 MiB por respuesta, con timeout de 5 segundos. SELECT
  usa cursor del servidor; DML RETURNING se consume sin acumular todas sus filas.
  La serialización ocurre antes del commit. `bytea` usa `{type:"bytea",base64:...}`.
  RETURNING sin filas informa `columns:[]`; SELECT vacío conserva sus columnas.
- Los secretos, el GGUF y las bases locales no están en Git. El modelo copiado es
  el artefacto de embeddings, no información personal de la Raspberry.
  [Proveniencia, licencia y checksum](vectors.md#model-artifact-and-reproducible-deployment).
- El aprovisionador comprueba marcadores de propiedad de roles y bases. Un proceso
  terminado entre CREATE DATABASE y su COMMENT deja una base cerrada sin marcador;
  ese caso excepcional requiere inspección administrativa antes de recuperarla.
  No adopta automáticamente una base sin marcador, aunque pertenezca al administrador.
- Mantener estable `OPENDB_DB_CREDENTIAL_KEY`. Rotarla requiere actualizar los roles
  y conexiones de forma coordinada. No cambiarla como parte de un reinicio normal.
- No hay transacción distribuida entre control y datos. Las operaciones de compartir
  exigen autocommit en control y un lock de datos compartido con DDL/sincronización.
  Las vistas HTTP que ejecutan servicios no usan `ATOMIC_REQUESTS`.
- MCP OAuth usa estado cifrado persistente y una sola réplica. Hay concurrencia
  limitada para que una búsqueda lenta no bloquee todas las operaciones.
- No se probaron Google/browser ni un asistente remoto con una cuenta real. Es la
  comprobación externa pendiente, con los callbacks y secretos descritos en la guía.
- Este despliegue es local. Frontend de exploración, facturación, operación cloud,
  aislamiento de recursos físicos y cifrado E2E no forman parte de esta entrega.
  El backend ve el contenido para procesarlo. La licencia de OpenDB sigue pendiente.
