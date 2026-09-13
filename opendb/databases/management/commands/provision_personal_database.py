from django.core.management.base import BaseCommand

from opendb.databases.provisioning import provision_personal_database


class Command(BaseCommand):
    help = "Provision a personal database for an existing Django user ID."

    def add_arguments(self, parser):
        parser.add_argument("owner_id", type=int)

    def handle(self, *args, **options):
        db = provision_personal_database(options["owner_id"])
        self.stdout.write(f"{db.id} {db.status}")
