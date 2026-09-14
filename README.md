# OpenDB

MVP local de base de datos personal: Django administra identidades y acceso;
PostgreSQL guarda una base separada por dueño. Un asistente externo usa MCP para
consultar, modelar e ingerir información. OpenDB no aloja un LLM conversacional ni
extrae entidades por su cuenta; calcula embeddings con un modelo local.

Incluye tablas y vistas SQL reales, catálogo filtrado por permisos, ingesta
transaccional con referencias, fuente original e idempotencia, y ejemplos de
reuniones y gastos. Los invitados tienen roles de lectura acumulables; compartir
una vista no concede lectura de sus tablas fuente. pgvector permite búsqueda
semántica con indexación asíncrona y reintentos cuando cambia el texto.

## Arranque local

El entorno actual por dominio usa también `docker-compose.domain.yml`. Para
reiniciarlo, seguí [la configuración del gateway](docs/implementation/web.md#entorno-actual-web-y-mcp-en-el-mismo-dominio).
Los comandos de abajo corresponden al modo local sin gateway.

Requisitos: Docker Engine, Docker Compose v2 y Python 3 para los scripts.
Desde este directorio, prepara la configuración sin sobrescribir claves existentes:

```bash
scripts/setup-local
```

Necesitas el GGUF verificado `granite-97m-r2-q8_0.gguf`. Si ya está en `.models/`,
comprueba su hash; si tienes la copia en otra ruta, pásala al mismo script:

```bash
scripts/prepare-model
# Alternativa para importar una copia existente:
scripts/prepare-model /ruta/granite-97m-r2-q8_0.gguf
```

El script exige los bytes fijados en [model.sha256](docs/implementation/model.sha256).
No descarga otro modelo ni acepta un archivo distinto por tener el mismo nombre.
Consulta la [procedencia y configuración](docs/implementation/vectors.md#model-artifact-and-reproducible-deployment):
384 dimensiones, pooling mean y contexto de 512 tokens. No se ha identificado una
descarga pública del GGUF con ese mismo hash; la Raspberry no es una dependencia
del servicio en ejecución.

```bash
docker compose -f docker-compose.local.yml -f docker-compose.mvp.yml up --build -d
```

Web: [localhost:8000](http://localhost:8000). Correo de desarrollo:
[localhost:8025](http://localhost:8025). El arranque aplica las migraciones y levanta
PostgreSQL personal, embeddings y el worker. El servicio MCP usa un perfil separado
y requiere configurar OAuth antes de activarlo; el login web también usa Google.

## Interfaz gráfica

Abrí [localhost:8000/app/](http://localhost:8000/app/). Incluye explorador de tablas y
vistas, esquema, consultas SQL, roles/invitados y estado vectorial. El build de React
se genera automáticamente al levantar Compose. Detalles y pruebas en
[la guía de interfaz](docs/implementation/web.md).

## Google y MCP

Configura un cliente OAuth de Google de tipo **Aplicación web** y su pantalla de
consentimiento. Registra exactamente estas dos URL de retorno:

- `http://localhost:8000/accounts/google/login/callback/`
- `http://localhost:8001/auth/callback`

En `.envs/.local/.django`, completa `OPENDB_GOOGLE_CLIENT_ID` y
`OPENDB_GOOGLE_CLIENT_SECRET`. Mantén `OPENDB_MCP_BASE_URL=http://localhost:8001`
para uso local. Añade los usuarios de prueba en Google si corresponde. Los callbacks
del cliente asistente se autorizan por separado en
`OPENDB_MCP_ALLOWED_CLIENT_REDIRECT_URIS`; detalles en la [guía del gateway](docs/implementation/gateway.md).

Con las credenciales configuradas, recrea los servicios y activa MCP:

```bash
docker compose -f docker-compose.local.yml -f docker-compose.mvp.yml --profile mcp up --build -d
```

Conecta el asistente mediante Streamable HTTP a `http://localhost:8001/mcp`, con
OAuth. Lee el recurso `opendb://guides/ingestion` para el contrato y ejemplos.
El asistente debe consultar el catálogo, reutilizar estructuras e identidades
verificadas y conservar los datos desconocidos como NULL, sin inventarlos.

## Claves y persistencia

`scripts/setup-local` crea los archivos locales ausentes y genera una sola vez
`OPENDB_DB_CREDENTIAL_KEY`, `OPENDB_MCP_JWT_SIGNING_KEY` y
`OPENDB_MCP_STORAGE_ENCRYPTION_KEY`. Conserva estas claves fuera de Git junto con
los volúmenes de datos y estado OAuth; no las regeneres en cada arranque. Cambiarlas
puede invalidar credenciales, tokens o la lectura del estado OAuth existente.
Para una configuración propia, establece también `DJANGO_SECRET_KEY`; los valores
y contraseñas de Compose son de desarrollo. MCP se mantiene en una sola réplica.

```bash
docker compose -f docker-compose.local.yml -f docker-compose.mvp.yml --profile mcp down
```

`down` conserva los volúmenes; `down -v` elimina los datos persistentes.

## Pruebas

Instala `uv`; las pruebas usan Python 3.14 y un PostgreSQL desechable en el puerto
local 55439, separado de los datos del MVP:

```bash
scripts/test
# Opcional: endpoint accesible desde el host con el modelo fijado:
OPENDB_REAL_EMBEDDINGS_URL=http://127.0.0.1:18080 scripts/test tests/integration/test_vectors.py
```

Sin esa variable se omite la prueba del modelo real. El endpoint interno de Compose
no publica automáticamente ese puerto en el host. Las pruebas de OAuth usan datos
simulados: el consentimiento real de Google y la conexión de un asistente real aún
requieren validación con credenciales propias. Este MVP local no está listo para producción.

No se implementó cifrado de extremo a extremo: el backend procesa los datos.
La licencia del código de OpenDB está por definir; la del modelo se documenta por separado.

Documentación: [contratos](docs/implementation/contracts.md),
[ingesta y catálogo](docs/implementation/ingestion.md), [vectores](docs/implementation/vectors.md),
[API y MCP](docs/implementation/gateway.md), [diseño del MVP](docs/superpowers/specs/2026-09-13-mvp-design.md)
y [decisiones](docs/superpowers/specs/2026-09-13-mvp-decisions.md).

## Deployment on Dokploy and AWS

See [the production deployment guide](docs/implementation/deployment.md) for the
ARM64 Compose stack, private RDS connection, model initialization and OAuth setup.
The [deployment state](docs/implementation/deployment-state.md) records resources
prepared in Macaco's AWS account and the infrastructure prerequisites still pending.
