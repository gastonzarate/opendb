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

## Bloqueo de Dokploy encontrado antes del despliegue

- Host `macaco`, EC2 Graviton ARM64, IP pública `3.149.225.221`.
- `https://dockploy.macaco.ai` devuelve 502; puerto 3000 rechaza conexiones.
- Partición raíz 145 GiB útiles, 100% ocupada, 0 disponibles.
- El manager de Swarm falla con `WAL error cannot be repaired: unexpected EOF`
  y `max entry size limit exceeded`. No están corriendo los servicios del panel.
- Siguen corriendo contenedores de Macaco y Traefik. El API existente responde 401.
- Hay aproximadamente 97 GB de imágenes reportadas como recuperables y permanece
  el volumen PostgreSQL 16 de Dokploy. No se eliminaron imágenes ni volúmenes y no
  se reinició Docker/Swarm.

## Intervención propuesta, pendiente de autorización

1. Respaldar el disco y los datos/configuración de Dokploy. Inventariar servicios,
   redes y volúmenes antes de tocar el estado de Swarm. Una copia en caliente no
   equivale a un backup consistente de Raft.
2. Recuperar espacio primero con cachés/imágenes revisadas, preservando los
   contenedores activos, volúmenes de datos y las imágenes necesarias para rollback.
3. Evaluar backups de Swarm; restaurar o reconstruir el plano de control únicamente
   después de definir la ventana de mantenimiento. Puede afectar las apps existentes.
   `--force-new-cluster` no garantiza reparar un WAL corrupto.
4. Validar Macaco y Dokploy antes de crear el proyecto/Compose separado de OpenDB.

Referencia de recuperación:
[Docker Swarm administration](https://docs.docker.com/engine/swarm/admin_guide/).

## Dominio y OAuth pendientes

`opendb.macaco.ai` es una propuesta, aún no confirmada. No tiene registro A y el DNS
se administra en Spaceship, fuera de Route 53 en esta cuenta. Para ese dominio:

- DNS: A `opendb` → `3.149.225.221`.
- Dokploy: dominio HTTPS hacia servicio `gateway`, puerto 8080.
- Google OAuth: añadir las URLs exactas
  `https://opendb.macaco.ai/accounts/google/login/callback/` y
  `https://opendb.macaco.ai/auth/callback` al cliente configurado.
- MCP público: `https://opendb.macaco.ai/mcp`.

La infraestructura AWS creada genera cargos aunque el despliegue de aplicación
esté pendiente. No hay todavía una instancia web OpenDB publicada en Dokploy.

## Validación de RDS completada

Con conexión TLS verify-full por túnel SSH se aplicaron las migraciones de control
(45 registradas). Se probó un usuario/base sintéticos: aprovisionamiento, escritura,
registro e indexación con embedding determinista de prueba, búsqueda híbrida en
modo léxico y borrado físico. El usuario/base sintéticos fueron eliminados.

RDS no tiene el bypass de un superusuario PostgreSQL real. El aprovisionador ahora
recibe explícitamente INHERIT/SET sobre el rol propietario recién validado/creado;
la relación inversa no se concede. La prueba local confirma que el propietario
no puede asumir el rol administrativo. pgvector disponible: 0.8.2.

Validación final del código: 424 pruebas backend pasaron, 1 omitida; imagen
x86_64 de producción compilada con el ajuste RDS; smoke ASGI/HTML/JS, permisos
no-root, aislamiento de Compose, checksum/redacción del modelo y pre-commit pasaron.
La ejecución nativa ARM64 y el login público siguen pendientes del despliegue real.
