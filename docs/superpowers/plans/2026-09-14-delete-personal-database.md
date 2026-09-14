# Borrar base personal y simplificar Mis datos

Pedido: botón para borrar la base y quitar el selector nuevo de tablas del contenido.

Diseño: botón exclusivo del dueño al pie del sidebar, diálogo de confirmación,
acción web con sesión Google y CSRF (sin tool MCP). Eliminación física con bloqueo
administrativo por base, comprobación de propiedad de recursos y limpieza de roles.
Conservar registro eliminado para impedir recreación automática en login. Permitir
recreación explícita vacía. Un fallo deja estado reintentable y descarta datos en
caché. La navegación de tablas permanece en el sidebar, también en móvil.

Validación: bases PostgreSQL desechables para permisos, eliminación, reintento,
recreación y aislamiento; frontend con confirmación/cancelación, errores, recarga
y navegación; navegador con APIs simuladas sin borrar información real.

Implementado y verificado: 392 pruebas backend pasaron (1 omitida), 50 frontend,
10 navegador; build, Ruff y formato correctos. Revisión de permisos y fallos
completada. Pruebas reales de borrado ejecutadas solo en PostgreSQL desechable.
Interfaz compilada y servicios MCP/indexador reiniciados; /app/ devuelve HTTP 200.
