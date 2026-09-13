# OpenDB

Base Django para una base de datos personal privada con futuro acceso MCP.
Generada con [Cookiecutter Django](https://github.com/cookiecutter/cookiecutter-django).
Opciones y versión de la plantilla: [docs/scaffolding.json](docs/scaffolding.json).

## Desarrollo local

Requiere Docker Engine y Docker Compose v2. Desde este directorio:

```bash
mkdir -p .envs/.local
# Solo en la primera instalación; no sobrescribir configuraciones existentes.
cp -n env.example/django .envs/.local/.django
cp -n env.example/postgres .envs/.local/.postgres
docker compose -f docker-compose.local.yml up --build
```

Aplicación: http://localhost:8000. Correo local: http://localhost:8025.
El arranque aplica las migraciones. Para crear un administrador:

```bash
docker compose -f docker-compose.local.yml run --rm django python manage.py createsuperuser
```

## Verificación

```bash
docker compose -f docker-compose.local.yml run --rm django python manage.py check
docker compose -f docker-compose.local.yml run --rm django python manage.py makemigrations --check --dry-run
docker compose -f docker-compose.local.yml run --rm django pytest
docker compose -f docker-compose.local.yml run --rm django ruff check .
docker compose -f docker-compose.local.yml down
```

`down` conserva los datos. Los archivos `.envs/` no se versionan. Los ejemplos son
solo locales. El stack de producción generado necesita su propia configuración.

## Alcance

Incluye autenticación por email, administración, PostgreSQL, ASGI, Docker, pruebas
y GitHub Actions. MCP, pgvector, ingesta y aislamiento de las bases personales se
implementarán después de revisar el sistema de la Raspberry. Esta plantilla no
representa todavía el MVP funcional.

Modelo comercial: open source + cloud pago. Licencia concreta por definir.
Los canvas se conservan fuera del repositorio, en `../canvas/`.
