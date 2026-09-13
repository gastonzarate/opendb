"""Run with python -m opendb.vectors.worker; credentials remain inside the backend."""

# Peer model imports must happen after django.setup().
# ruff: noqa: PLC0415

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from uuid import UUID

from .embeddings import MODEL_SHA256
from .embeddings import EmbeddingError
from .indexing import MAX_ROWS


def verify_model(path):
    with Path(path).open("rb") as artifact:
        digest = hashlib.file_digest(artifact, "sha256").hexdigest()
    if digest != MODEL_SHA256:
        msg = "Model checksum does not match the pinned Pi artifact"
        raise EmbeddingError(msg)


def run_once(database_id=None, limit=10):
    # Peer imports happen after Django setup, never at application import time.
    from opendb.databases.connections import privileged_connection
    from opendb.databases.models import PersonalDatabase
    from opendb.vectors import process_pending

    databases = PersonalDatabase.objects.filter(status="ready")
    if database_id:
        databases = databases.filter(pk=database_id)
    exit_code = 0
    for database in databases.iterator():
        summary = {"database_id": str(database.id)}
        try:
            with privileged_connection(database.id) as conn:
                summary.update(process_pending(conn, limit=limit))
        except Exception:  # noqa: BLE001 - isolate unavailable personal databases.
            summary["error_code"] = "database_processing_failed"
            exit_code = 1
        sys.stdout.write(json.dumps(summary) + "\n")
        sys.stdout.flush()
    return exit_code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once", action="store_true", help="Process one bounded batch and exit"
    )
    parser.add_argument(
        "--database-id", type=UUID, help="Only process this ready personal database"
    )
    parser.add_argument(
        "--limit", type=int, default=10, help="Rows per database per pass (1-1000)"
    )
    parser.add_argument(
        "--poll-interval", type=float, default=5, help="Seconds between passes"
    )
    parser.add_argument(
        "--model-file",
        type=Path,
        help="Verify this local GGUF checksum before starting",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= MAX_ROWS or args.poll_interval <= 0:
        parser.error("limit must be 1-1000 and poll-interval must be positive")
    if args.model_file:
        verify_model(args.model_file)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
    import django

    django.setup()
    try:
        while True:
            result = run_once(args.database_id, args.limit)
            if args.once:
                return result
            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
