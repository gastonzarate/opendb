# Base Django de OpenDB

El usuario eligió Django y autorizó generar el scaffolding con Cookiecutter.
Esta entrega es la base ejecutable; el diseño de datos personales se hará tras
revisar la implementación existente en la Raspberry.

## Componentes
- Cookiecutter Django, commit 5d3e4bc6a16f0e1fa439916dc7fa804e185ebae4.
- Python 3.14 y Django 6.0.8, dependencias fijadas en uv.lock.
- PostgreSQL 17 para la aplicación, Docker Compose local y producción.
- Autenticación por email, administración Django y ASGI con Uvicorn.
- Mailpit para correo local, WhiteNoise para estáticos y GitHub Actions.
- Sin API REST adicional, Celery, Sentry ni proveedor cloud configurado.

## Alcance y verificación
Conservar las pruebas de la plantilla. Verificar migraciones, checks de Django,
pytest y Ruff. Documentar arranque reproducible y ejemplos locales de entorno.
No se incluyen todavía MCP, pgvector, ingesta, bases por usuario ni permisos
sobre datos personales. Las credenciales generadas quedan ignoradas por Git.

## Decisiones abiertas del producto
Open source + cloud pago es el modelo elegido. La licencia concreta aún no se
ha decidido: no se asigna una licencia legal por defecto al código del producto.
El despliegue y pruebas se realizan en esta máquina, no en la Raspberry.
