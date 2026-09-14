# Búsqueda híbrida y prompt DBA

Pedido autorizado: corregir los hallazgos del prompt y permitir al agente elegir
consulta y peso de búsqueda. Interpretación: vectorial=semántica, contraparte léxica.

- Mantener search_vectors y añadir semantic_weight (0..100, default 50).
- Combinar ranking vectorial por coseno y léxico PostgreSQL simple/websearch con
  reciprocal-rank fusion ponderada; 0 solo léxico sin llamar al modelo, 100 vectorial.
- Calcular ambos sobre el mismo conjunto autorizado/filtrado y vigente; la rama de
  vistas debe respetar texto proyectado y ejecutar como usuario restringido.
- Registrar la semántica de score (ranking, no probabilidad), límites y corpus
  indexado. No afirmar acceso léxico a textos todavía no indexados.
- Reforzar INSTRUCTIONS y guía: datos no son instrucciones, ingest para cargas,
  anotaciones, fuentes/destino correctos, indexación por campo, reintentos seguros
  y elección de peso sin molestar al usuario. Mantener respuestas breves.
- Validar inversión de rankings, extremos, pesos inválidos, permisos/vistas/filtros,
  contratos MCP y ruta HTTP. Revisar cambios y ejecutar checks pertinentes.
- Actualizar SQL interno de bases existentes con instalador idempotente, sin borrar
  fuentes ni regenerar embeddings; reiniciar MCP tras validación.

## Validación completada

423 pruebas backend pasaron, 1 omitida (modelo vivo en suite); 50 frontend y
10 navegador pasaron. Pre-commit completo y revisión de código aprobados.
Se actualizó el catálogo vectorial de la base activa con el instalador idempotente.
Smoke de lectura con el modelo local real y pesos 0/50/100 devolvió resultados;
los estados de indexación antes/después fueron iguales. MCP reiniciado.

La evaluación conversacional de un LLM externo permanece como ejercicio manual
(documentado en docs/reviews/2026-09-14-assistant-evaluation-cases.md); no se afirma
que un prompt por sí mismo impida toda inyección de instrucciones.
