# Indexación automática de textos y selección visible de tabla

Objetivo autorizado: corregir transcripciones guardadas sin vectores y hacer evidente
el cambio de tabla en Mis datos.

Diseño: conservar texto original relacional. Worker detecta columnas narrativas
(body/content/text/texto/contenido/transcript/transcription/transcripcion/dialogue/
utterance) con contenido y cualquier otra columna textual con valores >=500
caracteres (umbral configurable). Registra la columna idempotentemente y procesa
fragmentos con el modelo local existente. Incluye datos ya guardados. Evitar que la
regla dependa de una llamada voluntaria del asistente. Mantener permisos de objetos
y registro protegido. Una tabla sin clave escalar compatible requiere resolver la
identidad estable de sus filas sin sobreescribir claves existentes.

Hallazgo: turns.body contiene 53 intervenciones; longitud máxima324. El umbral de
500 por fila no representa el documento completo, por eso se incluyen las columnas
narrativas independientemente del tamaño de cada intervención.

- [x] Backend: autodetección, worker, pruebas reales PG, contratos/instrucciones.
- [x] Frontend: selector Tabla o vista en el contenido principal, sincronizado con
  sidebar y visible en móvil; pruebas de cambio de consulta y estados vacíos.
- [x] Validar, revisar, ejecutar backfill sin imprimir contenido privado, publicar
  assets y reiniciar worker/MCP. Informar estado real de embeddings.

Ajuste posterior solicitado: respuestas orientadas al usuario. Confirmar el guardado
en una o dos frases; ocultar esquemas, nombres de tablas, SQL, conteos y decisiones
internas salvo petición explícita. No ofrecer embeddings como paso opcional ni
pedir decisiones de modelado futuro para completar la carga actual. No proponer
importaciones masivas adicionales como cierre. Mantener fidelidad interna y exponer
solo errores o preguntas necesarios. Instrucciones técnicas siguen en el MCP.

Verificación final:366 backend pasan (1 omisión de endpoint opcional del modelo),
46 frontend y8 navegador pasan; luego14 pruebas del panel de vectores, build y
formato pasan tras actualizar su explicación. Revisión independiente del guard
contra índices parciales/de expresión aprobada y cubierta por regresiones. Corrida
final de backend aislada sin otros runners simultáneos.

Backfill existente completado con modelo local real:53 intervenciones y6 notas
listas, sin pendientes ni errores. Consulta semántica devolvió resultados; no se
imprimió contenido privado. Los textos originales se conservaron. MCP e indexer
reiniciados y assets publicados. Reabrir/reconectar el asistente para recibir nuevas
instrucciones de respuesta.
