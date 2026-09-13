# Private Databases Implementation Plan

> **For agentic workers:** Use executing-plans to implement this scoped increment.

**Goal:** Aprovisionar una base PostgreSQL privada por dueño con roles aislados.
**Architecture:** Django mantiene el registro de bases. Un comando administrativo
aprovisiona recursos con una conexión privilegiada separada; las conexiones de datos
usan credenciales limitadas por base. No se expone el aprovisionador a SQL del agente.
**Tech Stack:** Django 6, Python 3.14, PostgreSQL 17, psycopg 3, Docker.
**Spec:** ../specs/2026-09-13-mvp-design.md
**Estado:** plan propuesto, sin ejecutar.

## Global Constraints

- Una base separada por dueño; las tablas personales no son modelos Django.
- El dueño no es superusuario PostgreSQL ni propietario del esquema interno.
- No modificar la Raspberry ni copiar sus datos.
- pgvector pertenece al MVP; su integración se verifica en el incremento vectorial.
- El usuario debe poder modificar libremente sus tablas; las credenciales técnicas
  quedan en el backend, no en las respuestas de API o MCP.

## Estructura y contratos

Crear app `opendb/databases/` con `apps.py`, `models.py`, `provisioning.py`,
`connections.py`, `credentials.py`, `exceptions.py` y migraciones.
Registrar la app en `config/settings/base.py`.

- `PersonalDatabase`: UUID, owner OneToOne al usuario Django, nombre PostgreSQL
  único, nombre de rol único, estado `pending|provisioning|ready|failed` y código de error.
- `provision_personal_database(owner_id: int) -> PersonalDatabase`: uso administrativo.
- `owner_connection(owner_id: int, database_id: UUID)`: context manager psycopg.
- `DatabaseAccessDenied`, `DatabaseNotReady`: errores de aplicación sin detalles secretos.
- `derive_database_password(database_id: UUID) -> str`: HMAC-SHA256 hexadecimal con
  clave exclusiva de despliegue `OPENDB_DB_CREDENTIAL_KEY`; nunca Django SECRET_KEY.
  Esta decisión simplifica el primer incremento. Rotación requiere actualizar roles y
  reiniciar conexiones coordinadamente; documentarlo, no cambiar la clave silenciosamente.

Los nombres se generan del UUID: `odb_<hex>` y `odb_owner_<hex>`; nunca de un email.
La identidad PostgreSQL dueña de la base y del catálogo será administrativa.
El rol personal solo será propietario de su esquema `data` y los objetos que cree allí.

## Task 1: Registro de base e identidad de dueño

**Files:** app databases, configuración y `opendb/databases/tests/test_models.py`.
**Consumes:** modelo de usuarios existente.
**Produces:** registro único por dueño y nombres no controlados por el solicitante.

- [ ] Crear primero el test siguiente y ejecutarlo; debe fallar porque falta la app.

```python
import pytest
from django.db import IntegrityError, transaction
from opendb.databases.models import PersonalDatabase
from opendb.users.tests.factories import UserFactory

@pytest.mark.django_db
def test_one_database_per_owner():
    owner = UserFactory()
    PersonalDatabase.objects.create(owner=owner)
    with pytest.raises(IntegrityError), transaction.atomic():
        PersonalDatabase.objects.create(owner=owner)
```

- [ ] Crear modelo/migración con UUID por defecto, owner OneToOne, nombres derivados
  antes del primer save y estado pending. Agregar tests para nombres y dos dueños.
- [ ] Ejecutar migrate y makemigrations --check --dry-run.
- [ ] Ejecutar tests de app, Ruff y registrar un commit del incremento probado.

## Task 2: Aprovisionamiento recuperable

**Files:** provisioning.py, credentials.py, exceptions.py,
`management/commands/provision_personal_database.py`, tests/test_provisioning.py.
**Consumes:** PersonalDatabase; configuración privilegiada solo disponible al comando.
**Produces:** base lista y rol personal; reintentos seguros.

- [ ] Crear prueba de integración con PostgreSQL real, no SQLite:

```python
@pytest.mark.django_db(transaction=True)
def test_repeated_provisioning_returns_same_database():
    owner = UserFactory()
    first = provision_personal_database(owner.pk)
    second = provision_personal_database(owner.pk)
    assert first.pk == second.pk
    assert second.status == "ready"
```

Importar UserFactory y provision_personal_database de los módulos definidos arriba.
La fixture del módulo registra los recursos que crea y los elimina al finalizar;
solo opera sobre un clúster desechable exclusivo de tests.

- [ ] Implementar contraseña determinista con HMAC y exigir clave no vacía antes de
  conectar. El servidor PostgreSQL la almacena con SCRAM; no persistirla en el modelo.
- [ ] Serializar el aprovisionamiento por UUID con advisory lock de sesión mantenido
  por el comando; CREATE DATABASE requiere autocommit. Registrar estados en Django.
- [ ] Crear rol primero como NOLOGIN y base con plantilla template0. Identificadores
  con psycopg.sql.Identifier y valores con parámetros donde PostgreSQL lo admite.
- [ ] Dentro de la base, configurar permisos usando esta secuencia conceptual:

```sql
REVOKE ALL ON DATABASE personal_db FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA data AUTHORIZATION personal_owner;
CREATE SCHEMA opendb_catalog AUTHORIZATION provisioner;
REVOKE ALL ON SCHEMA opendb_catalog FROM PUBLIC;
GRANT CONNECT ON DATABASE personal_db TO personal_owner;
ALTER ROLE personal_owner SET search_path = data, pg_catalog;
```

`personal_db`, `personal_owner` y `provisioner` representan los identificadores
calculados/configurados que se componen con Identifier, no strings interpolados.
El rol personal es NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS.
No conceder CREATE a nivel de base ni membresía en roles administrativos.

- [ ] Configurar LOGIN y contraseña al final y marcar ready solo tras comprobar la
  conexión. Reintentos inspeccionan propietario y privilegios existentes antes de
  reutilizar recursos. Si los nombres pertenecen a recursos ajenos, fallar sin borrarlos.
- [ ] En errores, marcar failed y conservar estado recuperable; no declarar atomicidad
  entre CREATE DATABASE y el registro Django. Probar fallos después de crear rol,
  después de crear base y antes de marcar ready, reintentando cada caso.
- [ ] Bootstrap del clúster de tests/desarrollo: revocar CONNECT/TEMP de PUBLIC en
  bases de control y mantenimiento y conceder solo a roles administrativos necesarios.
  No ejecutar este bootstrap automáticamente contra un clúster externo compartido.
- [ ] Documentar comando y variables en env.example y README; no valores reales.

## Task 3: Conexión restringida y pruebas de aislamiento

**Files:** connections.py, tests/test_isolation.py, configuración Docker de tests.
**Consumes:** registro ready, contraseña derivada y host/puerto del clúster de datos.
**Produces:** conexión autenticada realmente como personal_owner, sin SET ROLE desde admin.

- [ ] Escribir este test con dos dueños y sus bases, aprovisionadas por fixture:

```python
@pytest.mark.django_db(transaction=True)
def test_owner_cannot_select_other_database(two_owners):
    alice, bob, alice_db, bob_db = two_owners
    with pytest.raises(DatabaseAccessDenied):
        with owner_connection(alice.pk, bob_db.pk):
            pass
```

- [ ] Implementar lookup por owner y UUID, exigir ready y abrir conexión con rol y
  contraseña personales. Cerrar siempre; sin pooling compartido en este incremento.
- [ ] Añadir tests reales de PostgreSQL, no solo rechazo en Python: credenciales de
  Alice contra la base de Bob y la de control deben fallar. Alice puede crear,
  insertar, consultar, alterar y borrar sus propias tablas en `data`.
- [ ] Probar rechazo de CREATE ROLE, CREATE DATABASE, SET ROLE administrativo,
  creación/modificación en opendb_catalog y lectura de archivos del servidor.
- [ ] Probar reuso secuencial Alice/Bob sin contaminación de conexiones.
- [ ] Probar creación concurrente para el mismo owner: un registro y una base lista.
- [ ] Ejecutar suite existente y nueva, Ruff, check, migraciones y registrar resultados.

## Comandos de validación

```bash
docker compose -f docker-compose.local.yml run --rm django python manage.py check
docker compose -f docker-compose.local.yml run --rm django python manage.py makemigrations --check --dry-run
docker compose -f docker-compose.local.yml run --rm django pytest opendb/databases/tests -v
docker compose -f docker-compose.local.yml run --rm django pytest
docker compose -f docker-compose.local.yml run --rm django ruff check .
```

El clúster de integración debe ser un servicio dedicado dentro del entorno de tests;
no utilizar la base de la Pi, una base cloud ni la de control de desarrollo como destino
para los tests de bootstrap. Las fixtures pasan las conexiones de ese servicio al código.

## Fuentes y alcance de las garantías

- [CREATE DATABASE](https://www.postgresql.org/docs/17/sql-createdatabase.html):
  requiere privilegios específicos y no admite transacción; permisos de base no se
  heredan de la plantilla, por lo que se configuran explícitamente.
- [Privileges](https://www.postgresql.org/docs/17/ddl-priv.html): revisar PUBLIC y
  grants de CONNECT/TEMP; separar propiedad de objetos de permisos de uso.
- [CREATE ROLE](https://www.postgresql.org/docs/17/sql-createrole.html): definir
  privilegios del rol personal explícitamente.

Este incremento no garantiza todavía seguridad de SQL arbitrario expuesto por MCP,
confidencialidad de todos los metadatos del clúster ni aislamiento de recursos físicos.
Esas interfaces necesitan restricciones y pruebas antes de su exposición al agente.
