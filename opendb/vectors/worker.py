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

from .discovery import configuration
from .discovery import discover
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
                summary.update(discover(conn))
                summary.update(process_pending(conn, limit=limit))
                if summary["discovery_failed"] or summary["failed"]:
                    exit_code = 1
        except Exception as exc:  # noqa: BLE001 - isolate unavailable personal databases.
            # Only the exception class: driver messages may carry credentials or rows.
            summary["error_code"] = "database_processing_failed"
            summary["error_type"] = type(exc).__name__
            exit_code = 1
        sys.stdout.write(json.dumps(summary) + "\n")
        sys.stdout.flush()
    return exit_code


MAX_BACKOFF_SECONDS = 300


def backoff(poll_interval, failures):
    """Poll normally while healthy; slow down exponentially while passes fail.

    An unreachable administrative connection otherwise produces a tight loop of
    identical failures (hundreds of thousands of log lines per week).
    """
    if failures <= 0:
        return poll_interval
    return min(poll_interval * 2**failures, MAX_BACKOFF_SECONDS)


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
    try:
        configuration()
    except ValueError as exc:
        parser.error(str(exc))
    if args.model_file:
        verify_model(args.model_file)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
    import django

    django.setup()
    try:
        failures = 0
        while True:
            result = run_once(args.database_id, args.limit)
            if args.once:
                return result
            failures = failures + 1 if result else 0
            time.sleep(backoff(args.poll_interval, failures))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
