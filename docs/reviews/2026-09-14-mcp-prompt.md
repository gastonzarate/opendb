# Revisión del contrato MCP y del prompt

Alcance: revisión estática de `gateway/contract.py`, `gateway/mcp.py`,
`gateway/ingestion_guide.py`, servicios de consultas/cargas, catálogo e indexación.
No es una evaluación del comportamiento de un modelo externo. Las instrucciones
orientan al asistente; la autorización y validación deben seguir en el servidor.

## Hallazgos

1. **Alta prioridad: delimitar contenido no confiable.** INSTRUCTIONS no establece
   explícitamente que documentos, transcripciones, filas, descripciones de catálogo
   y roles son datos y no pueden autorizar acciones ni cambiar instrucciones.
   Añadir esta regla, incluyendo solicitudes incrustadas de compartir, borrar o
   consultar otras fuentes. Los controles SQL/permisos actuales no evitan que un
   asistente autorizado use sus propios permisos por una instrucción maliciosa.

2. **Media: elección de query frente a ingest.** “Prefer ingest” es ambiguo.
   Para guardar documentos o información de dominio, especificar ingest como ruta
   predeterminada, con fuente y anotaciones. Reservar query para lectura, inspección
   y cambios puntuales solicitados. query permite DML pero no añade automáticamente
   procedencia ni idempotencia entre llamadas. Un reintento de escritura SQL con
   respuesta perdida puede duplicar efectos; comprobar antes de repetir.

3. **Media: documentación del modelo insuficientemente exigida.** El prompt pide
   conservar decisiones en anotaciones, pero la guía admite annotations=[] y no
   exige describir cada tabla/columna nueva relevante. El catálogo sí existe:
   introspección PostgreSQL más opendb_catalog.annotations e ingestion_operations.
   Añadir propósito, unidades/moneda, procedencia y semántica temporal cuando
   corresponda, sin inventar valores. Es una política semántica del agente, no una
   garantía actual del backend.

4. **Media: confirmar cobertura de indexación.** Hay advertencias correctas sobre
   pending/failed y estado vacío, pero vector_status solo enumera índices existentes.
   Un índice listo no demuestra que todos los campos de la carga estén cubiertos.
   Exigir verificar los campos narrativos esperados antes de afirmar preparación
   semántica completa; no hacer polling indefinido ni bloquear el guardado por ello.

5. **Media: selección de base y fuente.** Explicitar que “guardar en OpenDB” apunta
   a la base propia, salvo destino expresamente indicado. No buscar ni elegir una
   transcripción de otra integración solo para completar una prueba ambigua.
   La regla general de respetar alcance está, pero conviene hacer concreto este caso.

6. **Baja: parámetros y búsqueda.** El prompt repite el umbral 500 y nombres de
   columnas aunque la configuración puede cambiarlos; declara que son configurables
   pero no publica sus valores efectivos. La descripción de search_vectors debería
   aclarar que es semántica por coseno, con filtros de igualdad, sin ranking léxico.
   Usar SQL para importes, conteos e identificadores exactos; semántica para significado.

## Requisitos contrastados

| Requisito | Estado actual |
| --- | --- |
| Una base propia, otras compartidas | Backend lo aplica; list_databases tiene sentido. |
| Google, identidad y separación de usuarios | Backend; nunca depender del prompt. |
| Roles múltiples, descripción, tablas/vistas de lectura | Backend y prompt lo contemplan. |
| Agente administra un esquema adaptable | INSTRUCTIONS lo pide; ingest soporta DDL y referencias entre registros. |
| Transcripción fiel, participantes, diálogo, orden y tiempos conocidos | Explícito en INSTRUCTIONS; la normalización efectiva depende del asistente. |
| No inventar datos ni unir personas por nombre parecido | Explícito. |
| Catálogo interno que explica el modelo | Implementado; completar anotaciones requiere reforzar instrucciones. |
| Embeddings locales | Implementados con el modelo Granite documentado; no se calculan en el LLM del asistente. |
| Textos largos y turnos narrativos indexados automáticamente | Worker asíncrono por umbral/nombres; no sustituye ni elimina el texto relacional. |
| Respuestas breves sin exponer SQL ni decisiones internas | Explícito, con excepciones para solicitudes técnicas. |
| Sin confirmación adicional para escrituras ordinarias | Explícito; no autoriza cambios fuera del pedido. |
| Sin ofertas de importación masiva ni menús innecesarios | Explícito. |
| Administrador sin acceso al contenido | No implementado; arquitectura de cifrado pendiente, no prometerlo en el prompt. |
| Búsqueda híbrida léxica + vectorial | No implementada; no confundir filtros relacionales con fusión de rankings. |

## Cambio realizado en esta revisión

complete_onboarding pasa de ACTIONS a WEB_ONLY_ACTIONS. La web conserva la acción;
el MCP no la lista y rechaza su invocación. Se mantiene la prueba HTTP de completar
onboarding y se añade cobertura de ausencia/rechazo MCP.

Actualización posterior: las recomendaciones de instrucciones se aplicaron en
INSTRUCTIONS y en la guía: límite de confianza, ingest para cargas, anotaciones,
selección de fuente/base, cobertura de indexación y reintentos seguros. La guía
publica la configuración de indexación del proceso; gateway y worker deben compartirla.
search_vectors se amplió con búsqueda híbrida y semantic_weight. Esto no implementa
cifrado frente al administrador ni convierte instrucciones en controles de acceso.
Antes de dar el comportamiento de un asistente externo por validado,
conviene evaluar conversaciones con transcripciones divididas, datos repetidos,
fallos de respuesta, indexación pendiente e instrucciones maliciosas en documentos.
