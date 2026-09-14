"""Offline packaging checks with dummy values; no database, pytest or deployment."""

# ruff: noqa: INP001, S603, S607, PLR2004, S105, T201 - standalone offline validation

import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
DUMMY = {
    "OPENDB_DOMAIN": "opendb.example.test",
    "DJANGO_SECRET_KEY": "dummy-validation-only-" * 4,
    "DATABASE_URL": "postgres://control:dummy@rds.example.test/control",
    "OPENDB_ADMIN_DSN": "postgres://admin:dummy@rds.example.test/postgres",
    "OPENDB_DB_CREDENTIAL_KEY": "dummy-validation-only-" * 4,
    "OPENDB_GOOGLE_CLIENT_ID": "dummy.apps.googleusercontent.com",
    "OPENDB_GOOGLE_CLIENT_SECRET": "dummy",
    "OPENDB_MCP_JWT_SIGNING_KEY": "dummy-validation-only-" * 4,
    "OPENDB_MCP_STORAGE_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    "OPENDB_MCP_ALLOWED_CLIENT_REDIRECT_URIS": "http://localhost:*/*",
    "DJANGO_ADMIN_URL": "admin/",
    "DJANGO_SETTINGS_MODULE": "config.settings.dokploy",
    "DJANGO_READ_DOT_ENV_FILE": "false",
}


def main():
    # Do not inherit deployment credentials or a local Compose .env file.
    environment = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], **DUMMY}
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            str(ROOT / "docker-compose.dokploy.yml"),
            "config",
            "--format",
            "json",
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    services = json.loads(result.stdout)["services"]
    assert all(not service.get("ports") for service in services.values())
    assert [
        name
        for name, service in services.items()
        if "dokploy-network" in service["networks"]
    ] == ["gateway"]
    assert [name for name, service in services.items() if "build" in service] == ["web"]
    for name in ("mcp", "indexer"):
        assert services[name]["image"] == services["web"]["image"]
        assert services[name]["depends_on"]["web"]["condition"] == "service_healthy"
    for name in ("embeddings", "indexer"):
        assert (
            services[name]["depends_on"]["model-init"]["condition"]
            == "service_completed_successfully"
        )
        assert next(v for v in services[name]["volumes"] if v["target"] == "/models")[
            "read_only"
        ]
    for path in [
        ROOT / "config/settings/dokploy.py",
        *Path(__file__).parent.glob("*.py"),
    ]:
        ast.parse(path.read_text())

    spec = importlib.util.spec_from_file_location(
        "model_init", Path(__file__).with_name("model-init.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory() as directory:
        module.MODEL = Path(directory) / "model.gguf"
        payload = b"fixture model bytes"
        module.SHA256 = hashlib.sha256(payload).hexdigest()
        secret_url = "https://example.test/private?signature=DO-NOT-LOG"
        with patch.dict(os.environ, {"OPENDB_MODEL_URL": secret_url}):
            response = io.BytesIO(payload)
            response.url = secret_url
            with patch.object(module, "urlopen", return_value=response):
                assert module.main() == 0
            assert module.MODEL.read_bytes() == payload
            assert module.MODEL.stat().st_mode & 0o777 == 0o444
            with patch.object(module, "urlopen", side_effect=AssertionError):
                assert module.main() == 0  # Verified cache requires no URL/network.
            module.MODEL.unlink()
            response = io.BytesIO(b"wrong checksum")
            response.url = secret_url
            error = io.StringIO()
            with (
                patch.object(module, "urlopen", return_value=response),
                contextlib.redirect_stderr(error),
            ):
                assert module.main() == 1
            assert not module.MODEL.exists()
            assert not module.MODEL.with_suffix(".download").exists()
            with (
                patch.object(module, "urlopen", side_effect=ValueError(secret_url)),
                contextlib.redirect_stderr(error),
            ):
                assert module.main() == 1
            assert secret_url not in error.getvalue()
            assert "DO-NOT-LOG" not in error.getvalue()
    print(
        "PASS: Compose isolation/dependencies, Python syntax, "
        "model download/cache/checksum/redaction"
    )

    if len(sys.argv) == 2:
        command = ["docker", "run", "--rm", "--network", "none", "-i"]
        for key, value in DUMMY.items():
            command.extend(["-e", f"{key}={value}"])
        command.extend([sys.argv[1], "python", "-"])
        subprocess.run(
            command,
            input=Path(__file__).with_name("check-runtime.py").read_text(),
            text=True,
            check=True,
        )


if __name__ == "__main__":
    main()
