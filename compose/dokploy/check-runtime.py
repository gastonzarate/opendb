"""Run inside the built image with dummy settings; no database access."""

# ruff: noqa: INP001, S607, S310, PLR2004, T201, PT017 - isolated smoke check

import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import django
from django.core.checks import run_checks
from django.core.management import call_command
from psycopg.conninfo import conninfo_to_dict


def main():
    django.setup()
    from django.conf import settings  # noqa: PLC0415

    assert os.getuid() == 10001
    assert Path(settings.RDS_CA).is_file()
    assert not Path("/app/.envs").exists()
    assert not Path("/app/.models").exists()
    assert settings.DATABASES["default"]["OPTIONS"]["sslmode"] == "verify-full"
    assert conninfo_to_dict(settings.OPENDB_ADMIN_DSN)["sslmode"] == "verify-full"
    assert [os.environ["OPENDB_DOMAIN"]] == settings.ALLOWED_HOSTS
    issues = run_checks(include_deployment_checks=True)
    assert {issue.id for issue in issues} <= {"security.W005", "security.W021"}
    call_command("check", deploy=True, fail_level="ERROR")
    call_command("collectstatic", interactive=False, verbosity=0)
    # Bootstrap inherits ATOMIC_REQUESTS and therefore opens a DB transaction.
    # Exercise HTTP without contacting RDS using a disposable SQLite override.
    smoke_directory = Path(tempfile.mkdtemp(prefix="opendb-smoke-"))
    (smoke_directory / "smoke_settings.py").write_text(
        "from config.settings.dokploy import *\n"
        "DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', "
        "'NAME': ':memory:', 'ATOMIC_REQUESTS': True}}\n"
    )
    os.environ["DJANGO_SETTINGS_MODULE"] = "smoke_settings"
    os.environ["PYTHONPATH"] = f"{smoke_directory}:/app"
    process = subprocess.Popen(
        [
            "gunicorn",
            "config.asgi:application",
            "--worker-class",
            "uvicorn_worker.UvicornWorker",
            "--bind",
            "127.0.0.1:8000",
            "--workers",
            "1",
        ],
    )
    try:
        for _ in range(40):
            result = subprocess.run(
                ["python", "/app/deploy/healthcheck.py"],
                capture_output=True,
                check=False,
            )
            if result.returncode == 0:
                break
            time.sleep(0.25)
        else:
            message = "ASGI bootstrap healthcheck did not pass"
            raise AssertionError(message)
        for path in ("/", "/static/app/main.js"):
            request = urllib.request.Request(
                "http://127.0.0.1:8000" + path,
                headers={
                    "Host": os.environ["OPENDB_DOMAIN"],
                    "X-Forwarded-Proto": "https",
                },
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                assert response.status == 200
                assert response.read()
        request = urllib.request.Request(
            "http://127.0.0.1:8000/api/bootstrap/",
            headers={"Host": "untrusted.example.test", "X-Forwarded-Proto": "https"},
        )
        try:
            urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            assert error.code == 400
        else:
            message = "Invalid Host was accepted"
            raise AssertionError(message)
    finally:
        process.terminate()
        process.wait(timeout=15)
    print(
        "PASS: deployment checks, nonroot image, static collection, "
        "ASGI health/HTML/JS with temporary SQLite, Host rejection"
    )


if __name__ == "__main__":
    main()
