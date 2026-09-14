"""Rollout: run after control migrations, before restarting ingestion services.

    python manage.py upgrade_catalog --all-ready

The installer is idempotent and preserves annotations, sources and embeddings.
Failed databases remain retryable; no database is provisioned by this command.
"""

from uuid import UUID

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from opendb.catalog import install
from opendb.databases.connections import privileged_connection
from opendb.databases.models import PersonalDatabase


class Command(BaseCommand):
    help = (
        "Upgrade protected catalog SQL in ready personal databases after migrations "
        "and before restarting ingestion services. Preserves existing data. "
        "Use --all-ready on deployment; rerun safely after resolving failures."
    )

    def add_arguments(self, parser):
        targets = parser.add_mutually_exclusive_group(required=True)
        targets.add_argument("--database-id", type=UUID)
        targets.add_argument("--all-ready", action="store_true")

    def handle(self, *args, **options):
        databases = PersonalDatabase.objects.filter(status="ready")
        if options["database_id"]:
            databases = databases.filter(pk=options["database_id"])
            if not databases.exists():
                msg = "Requested database is not ready or does not exist."
                raise CommandError(msg)
        upgraded = failed = 0
        for database in databases.iterator():
            try:
                with privileged_connection(database.id) as conn, conn.transaction():
                    conn.execute("SET LOCAL lock_timeout='5s'")
                    conn.execute("SET LOCAL statement_timeout='30s'")
                    install(conn, database.role_name)
            except Exception:  # noqa: BLE001 -- Isolate failures; do not expose DSNs/data.
                failed += 1
                self.stderr.write(f"{database.id}: catalog upgrade failed")
            else:
                upgraded += 1
        self.stdout.write(f"Catalogs upgraded: {upgraded}; failed: {failed}")
        if failed:
            msg = "Catalog upgrade incomplete; resolve failures and rerun."
            raise CommandError(msg)
