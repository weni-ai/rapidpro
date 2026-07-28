from django.core.management.base import BaseCommand, CommandError

from temba.contacts.models import Contact
from temba.msgs.history_repair import repair_contact_partition
from temba.orgs.models import Org


class Command(BaseCommand):
    help = (
        "Repairs contact history events that were written with non-time-ordered (v4) UUIDs, which makes an "
        "old message show up as the most recent one. Scoped to a single org, and optionally a single contact."
    )

    def add_arguments(self, parser):
        parser.add_argument("--org", type=int, dest="org_id", required=True, help="ID of the org to repair")
        parser.add_argument("--contact", type=str, dest="contact_uuid", help="UUID of a single contact to repair")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            dest="dry_run",
            help="Only report how many events would be repaired, without writing anything",
        )

    def handle(self, org_id: int, contact_uuid: str, dry_run: bool, *args, **kwargs):
        org = Org.objects.filter(id=org_id, is_active=True).first()
        if not org:
            raise CommandError(f"no active org with id {org_id}")

        contacts = org.contacts.filter(is_active=True)

        if contact_uuid:
            contact = contacts.filter(uuid=contact_uuid).first()
            if not contact:
                raise CommandError(f"no active contact with uuid {contact_uuid} in org #{org.id}")
            contacts = contacts.filter(id=contact.id)
            scope = f"contact {contact_uuid}"
        else:
            scope = "all contacts"

        mode = "DRY RUN - " if dry_run else ""
        self.stdout.write(f"{mode}Repairing msg history for '{org.name}' (#{org.id}), {scope}...")

        num_repaired = 0
        for i, contact in enumerate(contacts.iterator(), 1):
            num_repaired += repair_contact_partition(f"con#{contact.uuid}", dry_run=dry_run)
            if i % 100 == 0:
                self.stdout.write(f"  ... {i} contacts processed, {num_repaired:,} event groups so far")

        verb = "would be repaired" if dry_run else "repaired"
        self.stdout.write(f"Done. {num_repaired:,} legacy history event groups {verb}.")
