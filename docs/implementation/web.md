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
