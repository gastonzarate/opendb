# OpenDB: diseño del MVP

Estado: decisiones funcionales consolidadas; diseño técnico para revisión. Los acuerdos confirmados están en
[el registro de decisiones](2026-09-13-mvp-decisions.md). Las propuestas adicionales
se identifican expresamente. Este documento no afirma que exista implementación.

## Objetivo

Un usuario conecta su asistente a una base PostgreSQL privada mediante MCP.
El asistente administra tablas reales, relaciones, vistas e información vectorial,
reutilizando lo existente y adaptando el esquema. El dueño comparte tablas o vistas
con invitados de solo lectura mediante roles acumulables.

## Arquitectura acordada

- Django: gestión de cuentas, bases, invitaciones y roles en una base de control.
- PostgreSQL con pgvector: una base separada por dueño. El usuario de aplicación
  que consulta una base personal no tiene acceso a las otras bases.
- Capa SQL: estructuras personales dinámicas, independientes del ORM Django.
- MCP: interfaz del asistente, sobre servicios de aplicación reutilizables por API.
- API web: mismos permisos y lógica; frontend independiente en una etapa posterior.
- Embeddings: llama-server local con el modelo de la Raspberry.

Las cuentas se autentican con Google. Los invitados mantienen identidad propia,
pueden pertenecer a varios roles dentro de una base y reciben la unión de permisos.
El dueño administra su esquema sin confirmaciones adicionales, incluidos borrados.
El acceso de dueño a su base no equivale a privilegios de superusuario del clúster.

## Herramientas MCP propuestas

Nombres conceptuales para concretar en el plan de implementación:

- Descubrir: bases accesibles, catálogo, descripción de tablas y relaciones.
- Consultar: SQL autorizado con parámetros y resultados limitados/paginados.
- Administrar estructura: crear y modificar tablas, columnas, restricciones y vistas.
- Ingerir: operación estructurada con fuente, esquema esperado y registros relacionados.
- Vectorizar y buscar: registrar contenido vectorizable, indexar y buscar con filtros.
- Compartir: crear roles, conceder lectura a tablas/vistas, invitar, asignar y revocar.
- Observar: estado de ingestas, errores de indexación e historial de operaciones.

La disponibilidad de una herramienta en el cliente no constituye autorización.
El backend y PostgreSQL deben hacer cumplir el permiso de cada operación.

## Ingesta estructurada: camino principal aceptado

1. El asistente consulta el catálogo y el esquema real antes de diseñar la ingesta.
2. Identifica entidades, convenciones y registros existentes; evita crear estructuras
   equivalentes con nombres distintos. La similitud de nombres no basta para fusionar
   identidades automáticamente.
3. Prepara una operación con identificador idempotente, versión de esquema esperada,
   fuente, cambios necesarios, registros y referencias entre ellos.
4. OpenDB valida identidad, permisos, tipos, relaciones y operaciones admitidas.
5. Ejecuta DDL transaccional admitido, inserciones y anotaciones del catálogo dentro
   de una transacción. Cambios no transaccionales quedan fuera de esta herramienta.
6. Devuelve IDs, cambios realizados y estado de indexación, o un error accionable.

Repetir el mismo ID con el mismo contenido devuelve el resultado registrado;
reutilizarlo con contenido distinto produce conflicto. Esto no sustituye restricciones
UNIQUE ni la política de duplicados semánticos que debe definir el agente.

La normalización semántica la realiza el asistente externo. OpenDB aplica validaciones
deterministas, pero no puede demostrar que interpretó correctamente toda transcripción.
Las instrucciones MCP deben explicar convenciones de modelado, tratamiento de datos
faltantes, consultas previas y uso de la herramienta de ingesta.

### Caso de aceptación: reunión

El agente identifica o crea reuniones, personas, participantes e intervenciones.
Cada intervención conserva reunión, hablante, orden, texto y tiempos cuando la fuente
los contiene. Fechas o identidades desconocidas se mantienen como tales. Se preserva
la fuente original y el vínculo con los registros derivados.

El esquema de reuniones es un caso de prueba, no una estructura obligatoria para
bases de gastos, alquileres u otros usos.

## Catálogo interno

Propuesta: esquema reservado `opendb_catalog`. Estructura técnica obtenida del catálogo
de PostgreSQL y anotaciones persistidas para propósito, unidades, convenciones,
procedencia y configuración vectorial. Evitar duplicar manualmente el esquema real.

El catálogo MCP solo describe objetos autorizados para quien consulta. No exponer
las definiciones privadas de tablas fuente mediante la descripción de una vista.
Las anotaciones se gestionan con herramientas dedicadas; el esquema interno está
protegido de cambios SQL directos del rol personal.

## Compartir tablas y vistas

Roles de lectura definidos por base. Compartir una vista permite consultar su resultado
sin conceder SELECT sobre sus tablas fuente. Retirar un rol solo elimina los permisos
que no se mantengan por otro rol asignado.

Propuesta: invitados usan consultas SELECT sobre objetos autorizados, incluyendo joins,
con identidades PostgreSQL restringidas. Evitar privilegios heredados de PUBLIC,
escritura mediante funciones y reutilización de conexiones con contexto de otro usuario.
La implementación deberá verificar permisos de vistas, funciones y metadatos con tests
adversariales. No basta con comprobar que una cadena SQL comienza con SELECT.

## Vectores e indexación

Modelo observado: `granite-97m-r2-q8_0.gguf`, llama-server con pooling mean y contexto
512; Hermes valida 384 dimensiones. Indexación y búsqueda usan el mismo modelo,
configuración y artefacto identificado por checksum.

El agente selecciona las estructuras y columnas a vectorizar. OpenDB calcula los
vectores; los fragmentos conservan vínculo con registros fuente. Fragmentar según
el presupuesto real de tokens, sin asumir que una intervención equivale a un vector.

### Actualización automática y recuperación: comportamiento aceptado

- Registrar cambios de contenido e intención de indexar en una cola transaccional
  dentro de la base personal, protegida junto al catálogo.
- Un worker obtiene el contenido, calcula fuera de la transacción de ingesta y escribe
  únicamente si la versión fuente sigue vigente.
- Indexación con estados pendiente, lista y fallida; reintentos idempotentes.
- Si cambia un campo registrado como vectorizable, invalidar y reindexar su contenido.
- No devolver embeddings antiguos como si representaran la versión actual.
- Una caída del servicio de embeddings no pierde la ingesta relacional; informar
  claramente que la búsqueda semántica de esa entrada aún no está disponible.

El mecanismo específico para detectar cambios por SQL directo se define antes de
implementar esta capacidad; debe cubrir INSERT, UPDATE y DELETE en tablas registradas.

## Entregas propuestas

1. Identidad Google, acceso MCP, aprovisionamiento de base privada y SQL aislado.
2. Catálogo, cambios de esquema e ingesta estructurada con transacciones e idempotencia.
3. Embeddings locales, pgvector, indexación y recuperación de errores.
4. Roles múltiples, invitaciones y acceso de lectura a tablas/vistas.
5. Prueba integral con reunión y un segundo caso cuantitativo, documentación de conexión.

Las etapas son incrementos de desarrollo; pgvector y permisos forman parte del MVP
completo. La UI de exploración y el cobro cloud no forman parte de esta primera entrega.

## Validación del MVP

- Dos dueños no pueden consultar ni modificar las bases del otro.
- Un invitado con roles A y B ve su unión; revocar A conserva únicamente lo permitido por B.
- Una vista compartida no permite leer las tablas fuente ni columnas no expuestas.
- Una ingesta fallida revierte esquema/registros/anotaciones incluidos en la transacción.
- Reintentar una ingesta completada no duplica sus registros.
- Cambios concurrentes de esquema producen conflicto explícito, no escritura silenciosa.
- Reunión normalizada permite consultas por participante, orden y tiempo conocido.
- Consultas SQL y vectoriales aplican los mismos permisos.
- Fallos y cambios durante la indexación no publican vectores desactualizados.
- Catálogo accesible por MCP no revela objetos privados al invitado.

## Comprobaciones técnicas previas a cada implementación

Compatibilidad del flujo OAuth de MCP con Google como proveedor de identidad;
provisión de roles y conexiones PostgreSQL; procedencia y licencia del modelo;
fragmentación con su tokenizer; mecanismos de revocación y captura de cambios.
Resolver estas comprobaciones en el diseño técnico del incremento correspondiente,
sin presentar el scaffold actual como si ya ofreciera estas garantías.
