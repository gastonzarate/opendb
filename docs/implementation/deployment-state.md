# Estado del despliegue OpenDB en Macaco — 2026-09-14

## Recursos creados exclusivamente para OpenDB

- Cuenta AWS: Macaco, perfil local `macacoai`, región `us-east-2`.
- RDS: `opendb-prod`, PostgreSQL 17.11, `db.t4g.micro`, 20 GiB gp3,
  almacenamiento cifrado, crecimiento máximo 100 GiB, backups 7 días,
  protección contra borrado, sin dirección pública.
- Red: VPC existente de Macaco. Grupo `opendb-prod-db` permite TCP 5432
  únicamente desde el grupo del servidor Dokploy. No se modificó `macacodb`.
- Base de control: `opendb_control`; usuario de aplicación del mismo nombre,
  sin CREATEDB/CREATEROLE/SUPERUSER. Administrador separado `opendb_admin`,
  con contraseña maestra administrada por RDS/Secrets Manager.
- Bucket privado: `opendb-artifacts-872154182820-us-east-2`, versionado y cifrado,
  acceso público bloqueado y TLS obligatorio. Contiene el GGUF con checksum
  verificado. La URL firmada inicial dura siete días; regenerarla si el primer
  arranque ocurre después. El volumen persistente evita descargas posteriores.
- Entorno local privado: `.envs/.production/dokploy.env` y
  `.envs/.production/.aws-bootstrap.json`, permisos 0600 y excluidos de Git.
  No copiar sus valores a documentación, logs o argumentos visibles.

Las credenciales Google se reutilizaron de la configuración local autorizada.
Las claves de sesión, derivación y MCP de producción son nuevas y deben preservarse.
No se trasladaron transcripciones ni bases personales locales al ambiente nuevo.

## Infraestructura compartida: Dokploy recuperado con autorización

- Host `macaco`, EC2 Graviton ARM64 `i-0b792bc3e0030bccb`, IP pública
  `3.149.225.221`, privada `172.31.41.158`, región `us-east-2`.
- Causa observada: raíz llena y registro final incompleto del WAL de Swarm;
  la reparación automática no pudo completar su copia `.broken` sin espacio.
  Traefik respondía 502 porque no estaba disponible el servicio del panel.
- Antes de modificar el host se inició el snapshot EBS
  `snap-0c36fbdd29ff45f72` del volumen `vol-0c93ce4d1810f4812` (150 GiB).
  AWS confirmó el snapshot `completed` al 100% al terminar el despliegue.
- Se verificó una copia local privada de `/etc/dokploy`, el estado completo de
  Swarm, el volumen PostgreSQL 16 de Dokploy y su configuración Docker, junto
  con inventario de contenedores, imágenes, redes y volúmenes. Después de recuperar
  PostgreSQL se obtuvo además un `pg_dump` lógico de Dokploy. Los archivos están
  fuera del repositorio, con permisos 0600, en
  `/home/gastonzarate/.local/state/macaco-dokploy-recovery-20260914/`.
  Las copias físicas de Swarm y el snapshot se tomaron en caliente.
- Se eliminaron 89 imágenes antiguas sin etiqueta, revisadas por servicio Compose,
  conservando todas las etiquetadas, todas las referenciadas por contenedores y
  hasta dos versiones sin etiqueta recientes por servicio. Se limpió únicamente
  caché de compilación sin uso de más de siete días. No se podaron volúmenes,
  redes ni contenedores. La raíz pasó de 0 disponibles a unos 59 GiB libres (60% usada).
- Al liberar espacio, Docker reparó el WAL y recuperó los servicios originales.
  Luego se ejecutó `docker swarm init --force-new-cluster --advertise-addr 172.31.41.158`
  sobre ese estado recuperado para quitar el estado local residual de error.
  No se reinició el daemon Docker ni se reconstruyó la base de Dokploy.
- Dokploy se fijó a la imagen ARM64 verificada v0.25.11:
  `dokploy/dokploy@sha256:abfff2a8d5cea84de1a6ba00c3e3837fb4590512eb8a1cf3c2d70b192aa8ecd1`.
  Se preservaron las definiciones originales, credenciales, configuraciones y
  el volumen `dokploy-postgres-database` (PostgreSQL 16).
- Verificación: Swarm `active`, nodo `Ready/Leader`, servicios `dokploy`,
  `dokploy-postgres` y `dokploy-redis` a 1/1. Panel y API autenticada
  `project.all`: HTTP 200; proyectos originales presentes. `app.macaco.ai` y
  `langfuse.macaco.ai`: HTTP 200; `api.macaco.ai`: HTTP 401 esperado sin autenticación.
  Los 11 contenedores que ya corrían conservaron sus IDs y fechas de arranque.

Referencia de recuperación:
[Docker Swarm administration](https://docs.docker.com/engine/swarm/admin_guide/).

## OpenDB desplegado en Dokploy

- Código de aplicación: `772d1d5` en `main`; fuente Git HTTPS pública.
- Proyecto `OpenDB`: `vIVhfHSzjFWu7LlXlSkqR`.
- Environment: `87sUKnStaVAwc5jI4TIVu`.
- Compose: `GHSjp-IiqzhjHVo6WC9Ll`, nombre `opendb-dahhij`.
- Deployment `DDHBloOyRwNKubGI59OKy`: `done`, terminado a las 03:50 UTC.
- Imagen construida y ejecutada nativamente en ARM64:
  `sha256:6d8d70b30dc152bbac843eea0a6e84428ad0fdd8e0f4ba616f287aa2345d56e8`.
- Web, MCP, indexer, embeddings y Redis corriendo. Únicamente `gateway` está
  conectado a `dokploy-network`; los demás comparten la red propia de OpenDB.
- Web, Redis y embeddings saludables. Bootstrap y `/app/` devolvieron HTTP 200
  usando resolución explícita del dominio a la IP del host. MCP POST sin token
  devolvió 401. Estas comprobaciones omitieron validación del certificado porque
  DNS/TLS público todavía no están configurados.
- Prueba real en AWS: usuario/base sintéticos, ingesta con metadata descriptiva,
  descubrimiento e indexación automática con el GGUF real, búsqueda híbrida con
  pesos 0/50/100 y borrado físico. Usuario y base de prueba eliminados.
- No se migraron datos de la base personal local. Sus siete tablas de dominio sí
  recibieron etiquetas, descripciones y resúmenes locales; el fingerprint SQL
  permaneció igual. El backup previo de metadata queda privado fuera de Git.

## Dominio, certificado y OAuth pendientes

`opendb.macaco.ai` quedó como dominio provisional, aún sin confirmación del usuario.
No tiene registro A y el DNS se administra en Spaceship, fuera de Route 53 en esta
cuenta. Para habilitar acceso público normal:

- DNS: A `opendb` → `3.149.225.221`.
- Dokploy: ya está creado el dominio HTTPS hacia `gateway`, puerto 8080,
  ID `H5DSETGhhMS6LpvBo0W8l`. Su certificado está en `none` para no solicitar
  certificados contra un DNS inexistente. Después de propagar el A, seleccionar
  Let's Encrypt y redesplegar OpenDB; verificar HTTPS sin omitir el certificado.
- Google OAuth: añadir las URLs exactas
  `https://opendb.macaco.ai/accounts/google/login/callback/` y
  `https://opendb.macaco.ai/auth/callback` al cliente configurado.
- MCP público: `https://opendb.macaco.ai/mcp`.

La infraestructura AWS creada genera cargos. La aplicación ya corre en Dokploy;
el acceso por DNS público y el login Google de ese dominio siguen pendientes.

## Validación de RDS completada

Con conexión TLS verify-full por túnel SSH se aplicaron las migraciones de control
(45 registradas). Se probó un usuario/base sintéticos: aprovisionamiento, escritura,
registro e indexación con embedding determinista de prueba, búsqueda híbrida en
modo léxico y borrado físico. El usuario/base sintéticos fueron eliminados.

RDS no tiene el bypass de un superusuario PostgreSQL real. El aprovisionador ahora
recibe explícitamente INHERIT/SET sobre el rol propietario recién validado/creado;
la relación inversa no se concede. La prueba local confirma que el propietario
no puede asumir el rol administrativo. pgvector disponible: 0.8.2.

Validación con metadata descriptiva: 444 pruebas backend pasaron, 1 omitida;
54 pruebas frontend y 10 de navegador pasaron sobre los assets actualizados.
Build, formatter, pre-commit, smoke ASGI/HTML/JS, permisos no-root, aislamiento de
Compose y checksum/redacción del modelo pasaron. ARM64 y embeddings reales se
verificaron en producción. El login Google público aún no se verificó.
