# Scaffolding Implementation Plan

**Goal:** Base Django reproducible generada desde Cookiecutter.
**Architecture:** Aplicación ASGI y PostgreSQL, con Mailpit local.
**Tech Stack:** Django 6, Python 3.14, PostgreSQL 17, Docker y uv.
**Spec:** ../specs/2026-09-13-scaffolding-design.md

## Restricciones
Conservar origin y los canvas externos. No desplegar ni modificar la Raspberry.
No añadir funcionalidades de dominio antes de inspeccionar el sistema actual.

## Tarea: Generación y verificación de la base
- [x] Generar Cookiecutter con opciones registradas en docs/scaffolding.json.
- [x] Copiar al repositorio vacío conservando .git.
- [x] Documentar comandos locales y separar ejemplos de credenciales reales.
- [x] Construir Docker y ejecutar check, migrate, makemigrations --check y pytest.
- [x] Ejecutar Ruff y comprobar que Git ignore .envs.

## Resultado
Docker build correcto; Django check sin incidencias; migraciones aplicadas;
makemigrations sin cambios; 26 pruebas aprobadas y Ruff correcto.
Pytest emite 4 avisos por staticfiles aún no recolectados en desarrollo.
