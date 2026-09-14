# Interfaz web local

React + TypeScript en `frontend/`; Django sirve la aplicación en `/app/` y `/`
redirige allí. Los componentes usan la API existente con cookie de sesión y CSRF.
No se incorpora ningún secreto de Google al bundle. `/api/bootstrap/` es público,
solo devuelve configuración pública y CSRF; los datos requieren login Google.

Incluye inicio de sesión, creación/reintento de base personal, selector de bases
propias/compartidas, tablas y vistas con paginación, esquema y editor SQL. Accesos
administra roles, objetos e invitados por correo; indexación permite registrar
texto, revisar estados y buscar. Conexión muestra la URL MCP y permite copiarla.
Los invitados tienen controles de lectura y el backend sigue autorizando cada acción.

## Arranque

El Compose local + MVP incluye `frontend-build`, un trabajo Node24 que ejecuta
`npm ci` y construye los estáticos antes de iniciar Django. Solo las fuentes y el
lockfile están en Git; los assets generados y node_modules se ignoran.

Para cambios de UI con Node24 instalado en el host:

```sh
cd frontend
npm ci
npm run build
# Desarrollo: reconstruir al guardar, luego refrescar /app/ en el navegador.
npm run dev
# En otra terminal:
npm test
npm run test:browser
```

`test:browser` necesita el stack local en localhost:8000 y Chromium instalado con
`npx playwright install chromium`. `OPENDB_WEB_URL` permite cambiar el origen.
El login inicial y su bootstrap se prueban contra el servidor; los escenarios de
usuario autenticado usan respuestas de API simuladas dentro del navegador. Estas
pruebas no sustituyen la aceptación de Google con una cuenta real.

## Google reutilizado desde Hermes

Las dos credenciales se copiaron por SSH a `.envs/.local/.django`, ignorado por Git
y con permisos0600. No se modificó Hermes ni se mostraron los secretos. MCP está
levantado y sus documentos de descubrimiento OAuth responden200.

Google reconoce el cliente pero devuelve `redirect_uri_mismatch` para OpenDB.
Agregar en el cliente OAuth existente, conservando sus URI de Hermes:

- `http://localhost:8000/accounts/google/login/callback/`
- `http://localhost:8001/auth/callback`

No hay que cambiar el secreto ni el client ID. Cuando se guarden las URI, repetir
el login real desde `/app/`. Si se usa otro origen, las URI deben coincidir exactamente.

## Límites

### Acceso por Tailscale con HTTPS

Para el equipo actual, activar el proxy desde el host:

```sh
sudo tailscale serve --bg --https=443 http://127.0.0.1:8000
```

En `.envs/.local/.django`, configurar `DJANGO_TRUST_HTTPS_PROXY=True` y
recrear el servicio Django para cargar el entorno. Esto reconoce el encabezado
`X-Forwarded-Proto` de un proxy de confianza y genera callbacks HTTPS conservando
el hostname. No habilitar esta opción detrás de proxies que no controlen ese
encabezado. El acceso HTTP por localhost sigue disponible.

Abrir `https://gaston-pc-wsl.tailb9b2ab.ts.net/app/` y agregar en Google Console:

```text
https://gaston-pc-wsl.tailb9b2ab.ts.net/accounts/google/login/callback/
```

Conservar los callbacks existentes. Serve proporciona acceso dentro de la tailnet.
Este comando configura solo la web; MCP conserva su configuración anterior.
La activación requiere sudo en el host y el login real requiere registrar el
callback en Google. No se consideran verificados hasta completar esos pasos.

Las ingestas siguen siendo responsabilidad del asistente conectado; no hay chat
LLM alojado. El editor SQL aplica las reglas del servicio y los permisos del dueño
o invitado. La paginación usa ORDER BY clave primaria cuando existe; las vistas sin
clave y los datos que cambian durante la navegación no ofrecen una foto inmutable.
La pantalla vectorial registra claves primarias simples y texto compatible; los
otros índices UNIQUE admitidos por el backend se pueden registrar mediante MCP.

## Verificación de entrega

- 332 pruebas backend aprobadas; una prueba del modelo real omitida en esta corrida
  porque no se levantó el endpoint adicional de inferencia. El modelo se verificó
  en la entrega anterior y el servicio de embeddings del MVP sigue funcionando.
- 21 pruebas de componentes/API aprobadas, también ejecutadas con Node24 en Docker.
- 4 escenarios Playwright aprobados: login público/CSRF, explorador/SQL/roles,
  invitado en móvil y revocación de acceso. Capturas en frontend/test-results/.
- Build TypeScript/Vite aprobado y ejecutado por Compose. Revisión visual en
  Chromium de login, escritorio y móvil; barra de depuración excluida de la SPA.

## Alta y primer ingreso

Cada cuenta Google obtiene una única base personal automáticamente. Si la preparación
falla, el login sigue funcionando y la interfaz permite reintentar la misma base.
Las cuentas anteriores sin base se completan al abrir la interfaz. Las bases de
otras personas compartidas con el usuario siguen visibles en el selector.

El primer ingreso abre Conectar asistente con una explicación breve. «Ir a mis datos»
guarda que el usuario terminó el onboarding; no afirma que MCP esté conectado. La
exploración queda en Mis datos: el buscador filtra nombres de tablas/vistas, no su
contenido. El asistente es el flujo principal para cargar y consultar información.

Accesos organiza los permisos por rol. Cada rol tiene nombre y descripción disponible
para el asistente vía `list_access`; esta descripción expresa intención, no concede
permisos. Los permisos siguen siendo objetos autorizados explícitamente y las
personas invitadas solo leen. `update_role` permite editar nombre/descripción como
propietario; una descripción admite hasta 2000 caracteres.

## Prueba de desarrollo con Claude Code desde otro equipo

Con la configuración actual MCP escucha únicamente en localhost del servidor. Desde
el equipo donde corre Claude Code, abrir una terminal y mantener este túnel activo
(requiere acceso SSH al servidor con el usuario indicado):

```sh
ssh -N -L 8001:127.0.0.1:8001 gastonzarate@gaston-pc-wsl.tailb9b2ab.ts.net
```

En otra terminal:

```sh
claude mcp add --transport http --scope user opendb http://localhost:8001/mcp
claude
```

Dentro de Claude Code, ejecutar `/mcp`, seleccionar `opendb` y autenticar con Google
usando la misma cuenta que en OpenDB. El callback upstream que debe registrar el
administrador en Google es `http://localhost:8001/auth/callback`. La web HTTPS tiene
su propio callback; configurar uno no configura el otro. Si Claude Code corre en el
mismo servidor, no hace falta túnel.

Prueba sin escritura: «Usá OpenDB: ejecutá list_databases, elegí mi base personal y
consultá catalog. Mostrame las tablas disponibles sin modificar datos ni permisos».
Un catálogo vacío es un resultado válido para una base nueva. La interfaz incluye
comandos copiables y una prueba de carga opcional separada.

El servidor no configura el cliente Claude Code instalado en otro equipo ni puede
completar por el usuario el consentimiento de Google.

### Verificación de estas correcciones

346 pruebas backend aprobadas (1 omisión de modelo real), 44 pruebas frontend en
Node24, build TypeScript/Vite y formato aprobados. Seis escenarios Playwright por
HTTPS: sesión pública, datos/SQL/roles, invitado móvil, revocación, persistencia de
onboarding y onboarding móvil. Un escenario se repitió por ERR_NETWORK_CHANGED.
Los usuarios autenticados de navegador son fixtures; esto no sustituye probar
OAuth real desde Claude Code. Migración 0004 aplicada y MCP reiniciado localmente.

## Conexión del producto publicado en un dominio

El onboarding del producto solo muestra la URL MCP configurada y los pasos de
conexión/autenticación del asistente. SSH, Tailscale y Google Console son tareas de
operación/desarrollo y no aparecen en el flujo de usuario.

Configurar `OPENDB_MCP_BASE_URL=https://mcp.tu-dominio.com` en Django y en el proceso
MCP. El subdominio es un ejemplo, no una dirección aprovisionada. Publicar el servicio
MCP por HTTPS en ese origen, incluyendo `/mcp`, los endpoints de descubrimiento
`/.well-known/*` y los endpoints OAuth del proveedor (`/authorize`, `/token`,
`/register`, `/auth/callback`, etc.). No publicar solo `/mcp`: los clientes necesitan
el descubrimiento y la autenticación en el mismo origen configurado.

Registrar `https://mcp.tu-dominio.com/auth/callback` en el cliente OAuth de Google,
además del callback web `https://app.tu-dominio.com/accounts/google/login/callback/`.
Los dominios definitivos y su proxy/DNS se configuran al desplegar. El usuario final
solo agrega el servidor y autoriza su cuenta; no necesita acceso SSH ni configurar
Google Console. Un asistente de escritorio/CLI sigue ejecutándose en el equipo del
usuario y se conecta al servicio alojado por HTTPS.

La interfaz no deduce la URL MCP desde el dominio web: el servicio puede estar en un
subdominio distinto. Oculta comandos de endpoints loopback cuando la web se abre
desde otro host. Para desarrollo en localhost se conserva el endpoint local; las
instrucciones de túnel solo quedan en esta documentación.

## Entorno actual: web y MCP en el mismo dominio

Desde esta corrección, el entorno Tailscale usa un gateway Nginx compartido:

- Web: `https://gaston-pc-wsl.tailb9b2ab.ts.net/app/`
- MCP: `https://gaston-pc-wsl.tailb9b2ab.ts.net/mcp`
- Callback MCP de Google: `https://gaston-pc-wsl.tailb9b2ab.ts.net/auth/callback`

`OPENDB_MCP_BASE_URL` está configurado con ese origen en el entorno local privado.
El gateway enruta `/mcp`, descubrimiento y OAuth a MCP; el resto va a Django. TLS
sigue en Tailscale Serve, que apunta al puerto 8000 del host. Ese puerto ahora lo
ocupa el gateway y solo se publica en loopback; Django y MCP quedan en la red Docker.
No hace falta túnel para conectar un cliente dentro de la tailnet. El dominio
Tailscale sigue siendo privado; el dominio público futuro requiere su propio ingreso
HTTPS y DNS, con la misma separación de rutas.

Para reiniciar este entorno, incluir siempre el override de dominio:

```sh
docker compose -f docker-compose.local.yml -f docker-compose.mvp.yml \
  -f docker-compose.domain.yml --profile mcp up -d
```

Requiere Compose con soporte `!reset` (se verificó en Compose 5.0.2). No arrancar
solo los dos primeros archivos mientras el gateway esté activo: competirían por
el puerto 8000. La prueba de túnel de la sección anterior aplica únicamente al
modo local sin este override y con `OPENDB_MCP_BASE_URL=http://localhost:8001`.

El cambio de origen requiere volver a autenticar clientes MCP. Registrar el nuevo
callback en Google Console conservando los callbacks web/Hermes; esa configuración
externa y el consentimiento del usuario no los resuelve el proxy.

Configuración basada en [proxy HTTP de Nginx](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)
y [override de Compose](https://docs.docker.com/reference/compose-file/merge/).


## Textos indexados y respuestas simples

El servidor MCP indica al asistente que confirme guardados cotidianos en una o dos
frases. El modelo relacional, SQL, conteos y decisiones de esquema quedan internos,
salvo que el usuario pida detalles. La indexación de texto narrativo forma parte del
flujo; no se ofrece como mejora opcional ni requiere otra confirmación.

Mis datos permite elegir tablas y vistas desde la barra lateral (Navegación en
móvil). No hay un segundo selector encima de los registros. Las tablas vacías
o sin permisos se muestran sin inventar registros.

Tras actualizar las instrucciones del servidor, volver a conectar MCP o abrir una
sesión nueva del asistente para que reciba las instrucciones actuales. OpenDB
proporciona estas instrucciones; la respuesta en lenguaje natural la produce el
cliente asistente externo.

## Borrar la base personal

El dueño puede usar «Borrar base de datos» al pie de la barra lateral. Un diálogo
explica la eliminación irreversible y requiere «Borrar definitivamente»; cancelar
no envía ninguna operación. La acción web `delete_database` exige sesión Google,
CSRF y propiedad de la base, y no se publica como herramienta MCP.

Se elimina la base física y sus accesos, conservando un registro de estado para
que el login no vuelva a crearla. La interfaz descarta los datos del explorador
y ofrece «Crear base vacía» como acción explícita. Los fallos permiten reintentar
el borrado; la cuenta de usuario permanece activa.


## Instrucciones del asistente y búsqueda híbrida

`complete_onboarding` es exclusivamente web, como `delete_database`; no aparece
entre las tools MCP. El servidor usa `gateway/contract.py:INSTRUCTIONS` y las
reglas complementarias del recurso `opendb://guides/ingestion`. Las fuentes y
metadatos son datos no confiables, no instrucciones. El agente usa ingest para
cargas con procedencia, mantiene anotaciones y confirma el resultado brevemente.

`search_vectors` acepta `semantic_weight` (0..100, default 50): el agente elige
el balance semántico/léxico. Consultar `vectors.md` para ranking, permisos y límites.
Después de actualizar el servidor hay que reconectar el cliente MCP para recibir
las instrucciones y descripciones nuevas.
