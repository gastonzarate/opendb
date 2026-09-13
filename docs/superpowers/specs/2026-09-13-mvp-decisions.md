# OpenDB MVP: decisiones del brainstorming

## Decisiones confirmadas por el usuario

- Backend Django sobre la base generada con Cookiecutter; frontend independiente después.
- PostgreSQL: una base personal separada por dueño, con tablas SQL reales y vistas.
- Claude/Codex usa MCP para decidir estructuras, cargar y consultar datos.
- El dueño opera sin confirmaciones adicionales, incluidos cambios destructivos.
- Invitados solo lectura: cada uno puede tener varios roles por base; sus permisos se suman.
- Cada rol agrupa tablas y vistas compartidas. Una vista no implica acceso a sus tablas fuente.
- Identidad mediante Google; autorización mediante OpenDB y permisos de PostgreSQL.
- Gestión por MCP y API para futura web de tablas, vistas y accesos.
- pgvector desde el comienzo. El asistente decide qué estructuras vectorizar.
- Catálogo interno por base para facilitar el descubrimiento por el agente.
- Embeddings locales con el mismo modelo que usa la Raspberry.

## Modelo verificado en la Raspberry

Inspección de proceso activo el 2026-09-13, mediante Tailscale SSH, sin cambios:

```text
llama-server -m /home/gaston/granite-97m-r2-q8_0.gguf
  --embeddings --pooling mean -c 512 -t 4
  --host 127.0.0.1 --port 8080
```

Servicio: llama-embed.service. El código de Hermes valida vectores de 384 dimensiones
y consulta /v1/embeddings. Nombre del archivo observado: granite-97m-r2-q8_0.gguf.
El modelo, su configuración y su checksum deben acompañar la implementación para
que la indexación y las consultas utilicen el mismo espacio vectorial.
La Pi es referencia; no se decidió usarla como dependencia del servicio cloud.

## Propuesta técnica para concretar en el diseño

- Base de gestión Django separada de las bases personales; modelos Django para
  identidades, bases e invitaciones. Las tablas personales no son modelos Django.
- Servicio local de embeddings compartido por el backend, accesible únicamente
  por la red interna del despliegue. No requiere API externa de embeddings.
- Catálogo con estructura leída de PostgreSQL y anotaciones semánticas persistidas;
  descubrimiento filtrado por permisos, también para columnas vectoriales.
- Indexación y consulta semántica con el mismo modelo y configuración; sin aceptar
  dimensiones o vectores inventados por el asistente.

## Aspectos que debe resolver el diseño completo

Autenticación MCP compatible con clientes y Google, aprovisionamiento y revocación
de roles PostgreSQL, ejecución SQL aislada, semántica de vistas, mantenimiento del
catálogo, fragmentación de textos largos y sincronización de embeddings ante cambios.
Este archivo registra acuerdos y propuestas; no afirma que el MVP esté implementado.

## Administración semántica de la base: requisito confirmado

El asistente actúa como administrador personal de la base, no solo como cliente
SQL. Cada ingesta debe inspeccionar el catálogo, reutilizar estructuras existentes,
descomponer el contenido en entidades y relaciones apropiadas y adaptar el esquema
cuando sea necesario para mantener consistencia. Esto aplica tanto a información
relacional como a contenido vectorial.

Ejemplo confirmado por el usuario: una transcripción de reunión debe representarse
con participantes, intervenciones, horas/tiempos y orden, además del contenido.
No implica imponer un esquema de reuniones a todas las bases personales.

### Flujo de ingesta aceptado como camino principal de escritura

1. Consultar catálogo y estructura real, detectar entidades y convenciones existentes.
2. Diseñar el mapeo y los cambios mínimos de esquema; reutilizar tablas y relaciones.
3. Validar tipos, claves, restricciones y procedencia; preservar el documento original.
4. Aplicar los cambios y registros relacionados en una transacción cuando corresponda,
   con idempotencia por operación y controles de concurrencia para cambios de esquema.
5. Registrar anotaciones del catálogo junto a los cambios estructurales.
6. Vectorizar contenido apropiado con referencias a sus registros fuente; registrar
   su estado para distinguir datos persistidos de índices pendientes o fallidos.

La interpretación y normalización semántica pertenece al asistente externo; OpenDB
provee contexto, herramientas y validaciones deterministas. No puede garantizar
interpretación correcta de cualquier documento mediante permisos SQL solamente.

### Ejemplo de modelado, no esquema universal

- reuniones: identificador, título, fecha y zona horaria cuando estén disponibles.
- personas y reunión_participantes: identidad y relación con la reunión.
- intervenciones: reunión, participante/hablante, orden, texto y tiempos disponibles.
- fuentes: contenido original y procedencia para trazabilidad y reingesta.
- fragmentos vectoriales: contenido, vínculo a intervenciones fuente, modelo y estado.

No inventar fechas, horas ni identidades ausentes. Mantener hablantes no identificados
y valores desconocidos explícitos. Un fragmento semántico puede agrupar varias
intervenciones; no asumir que una intervención equivale a un embedding.

Confirmado: ingesta estructurada como camino principal; SQL sigue disponible.
Confirmado: actualización automática de embeddings ante cambios de texto, con
indexación recuperable y conservación de la ingesta relacional si el modelo falla.
