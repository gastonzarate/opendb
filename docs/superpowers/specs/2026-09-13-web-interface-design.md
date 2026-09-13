# Interfaz web de OpenDB

Alcance solicitado: login Google con el cliente de Hermes y UI para explorar bases,
tablas, vistas y accesos. Se mantiene Django como autoridad de identidad/permisos.

Frontend separado React + TypeScript, construido con Vite y servido por Django en
/app/ bajo el mismo origen. Evita credenciales OAuth en JavaScript y reutiliza CSRF.
Un bootstrap público entrega token CSRF, disponibilidad de Google y URL pública MCP;
la identidad y los datos se obtienen únicamente por los endpoints autenticados.

Diseño: navegación lateral oscura, superficie clara, acento verde; tablas de datos
como contenido principal. Adaptación a móvil, controles con etiquetas y teclado.
Pantallas: login; base vacía con creación; explorador de datos/esquema/SQL; roles e
invitados para dueños; indexación y registro de columnas; conexión MCP. Invitados
ven solo objetos autorizados y no disponen de administración. Sin datos ficticios.

Las consultas paginan con LIMIT/OFFSET e identificadores citados. Estados de carga,
error y vacío explícitos; no mostrar respuestas de una base al cambiar a otra.
Las mutaciones van al dispatcher existente y conservan sus garantías y límites.

Google: credenciales copiadas solo a .envs/.local/.django (ignorado, modo0600).
Google responde redirect_uri_mismatch hasta que se agreguen ambos callbacks locales.
No modificar ni reemplazar los callbacks que ya usa Hermes.
