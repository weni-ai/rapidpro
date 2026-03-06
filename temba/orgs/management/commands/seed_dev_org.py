"""
Populates the database with a realistic org for local development and testing.

Usage:
    python manage.py seed_dev_org
    python manage.py seed_dev_org --org-name "My Test Org" --contacts 50 --msgs 200
"""

from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand
from django.utils import timezone

from temba.campaigns.models import Campaign, CampaignEvent
from temba.channels.models import Channel
from temba.contacts.models import URN, Contact, ContactField, ContactGroup, ContactURN
from temba.flows.models import Flow, FlowLabel, FlowRevision
from temba.globals.models import Global
from temba.msgs.models import Label, Msg, OptIn
from temba.orgs.models import Org, OrgRole
from temba.tickets.models import Team, Ticket, TicketEvent, Topic
from temba.users.models import User
from temba.utils.uuid import uuid4


NAMES = [
    "Alice Oliveira", "Bob Santos", "Clara Lima", "Diego Costa", "Elena Ferreira",
    "Felipe Souza", "Gabriela Alves", "Henrique Rocha", "Isabela Mendes", "João Carvalho",
    "Karen Martins", "Lucas Pereira", "Mariana Silva", "Nuno Barbosa", "Olívia Gomes",
    "Paulo Ribeiro", "Quinn Azevedo", "Rafaela Torres", "Sávio Neves", "Tatiane Pinto",
    "Ulisses Cardoso", "Verônica Moreira", "Wagner Lopes", "Xiomara Fonseca", "Yuri Nascimento",
]


class Command(BaseCommand):
    help = "Seeds the database with a realistic dev org for testing"

    def add_arguments(self, parser):
        parser.add_argument("--org-name", default="Dev Org", help="Name of the org to create")
        parser.add_argument("--contacts", type=int, default=25, help="Number of contacts to create")
        parser.add_argument("--msgs", type=int, default=50, help="Number of messages to create per contact (approx)")
        parser.add_argument("--superuser-email", default="admin@example.com", help="Superuser email")
        parser.add_argument("--password", default="Qwerty123", help="Password for all created users")

    def handle(self, *args, **options):
        org_name = options["org_name"]
        num_contacts = options["contacts"]
        num_msgs = options["msgs"]
        superuser_email = options["superuser_email"]
        password = options["password"]

        self._log(f"Seeding dev org: {org_name}\n\n")

        # ── Users ──────────────────────────────────────────────────────────────
        self._log("Creating users... ")
        superuser = self._get_or_create_user(superuser_email, password, is_superuser=True)
        admin = self._get_or_create_user("admin@devorg.com", password, first_name="Admin")
        editor = self._get_or_create_user("editor@devorg.com", password, first_name="Editor")
        agent = self._get_or_create_user("agent@devorg.com", password, first_name="Agent")
        self._ok()

        # ── Org ────────────────────────────────────────────────────────────────
        self._log(f'Creating org "{org_name}"... ')
        org, created = Org.objects.get_or_create(
            name=org_name,
            defaults=dict(
                timezone=ZoneInfo("America/Sao_Paulo"),
                flow_languages=["por", "eng"],
                created_by=admin,
                modified_by=admin,
            ),
        )
        if created:
            org.initialize(sample_flows=False)
            org.add_user(admin, OrgRole.ADMINISTRATOR)
            org.add_user(editor, OrgRole.EDITOR)
            org.add_user(agent, OrgRole.AGENT)
        self._ok(f"id={org.id}")

        # ── Channel ────────────────────────────────────────────────────────────
        self._log("Creating channel... ")
        channel, _ = Channel.objects.get_or_create(
            org=org,
            name="WhatsApp Dev",
            defaults=dict(
                channel_type="WA",
                address="+5511900000000",
                schemes=["whatsapp"],
                role="SR",
                config={},
                uuid=uuid4(),
                created_by=admin,
                modified_by=admin,
            ),
        )
        self._ok(f"id={channel.id}")

        # ── Contact Fields ─────────────────────────────────────────────────────
        self._log("Creating contact fields... ")
        ContactField.objects.get_or_create(
            org=org, key="age", is_active=True,
            defaults=dict(name="Age", value_type=ContactField.TYPE_NUMBER, is_system=False,
                          created_by=admin, modified_by=admin),
        )
        ContactField.objects.get_or_create(
            org=org, key="city", is_active=True,
            defaults=dict(name="City", value_type=ContactField.TYPE_TEXT, is_system=False,
                          created_by=admin, modified_by=admin),
        )
        field_joined_on, _ = ContactField.objects.get_or_create(
            org=org, key="joined_on", is_active=True,
            defaults=dict(name="Joined On", value_type=ContactField.TYPE_DATETIME, is_system=False,
                          created_by=admin, modified_by=admin),
        )
        self._ok()

        # ── Globals ────────────────────────────────────────────────────────────
        self._log("Creating globals... ")
        Global.objects.get_or_create(
            org=org, key="support_phone",
            defaults=dict(name="Support Phone", value="+5511000000000",
                          created_by=admin, modified_by=admin),
        )
        self._ok()

        # ── Labels ─────────────────────────────────────────────────────────────
        self._log("Creating labels... ")
        label_promo = self._get_or_create_label(org, admin, "Promotional")
        label_support = self._get_or_create_label(org, admin, "Support")
        self._ok()

        # ── Flow Labels ────────────────────────────────────────────────────────
        self._log("Creating flow labels... ")
        flow_label, _ = FlowLabel.objects.get_or_create(
            org=org, name="Onboarding", defaults=dict(created_by=admin, modified_by=admin),
        )
        self._ok()

        # ── Opt-ins ────────────────────────────────────────────────────────────
        self._log("Creating opt-ins... ")
        optin = self._get_or_create_optin(org, admin, "Newsletter")
        self._ok()

        # ── Topics & Teams ─────────────────────────────────────────────────────
        self._log("Creating topics and teams... ")
        topic_sales, _ = Topic.objects.get_or_create(
            org=org, name="Sales", defaults=dict(created_by=admin, modified_by=admin),
        )
        topic_support, _ = Topic.objects.get_or_create(
            org=org, name="Support", defaults=dict(created_by=admin, modified_by=admin),
        )
        team_sales, _ = Team.objects.get_or_create(
            org=org, name="Sales Team", defaults=dict(created_by=admin, modified_by=admin),
        )
        self._ok()

        # ── Contact Groups ─────────────────────────────────────────────────────
        self._log("Creating contact groups... ")
        group_all = self._get_or_create_manual_group(org, admin, "All Contacts")
        group_vip = self._get_or_create_manual_group(org, admin, "VIP")
        group_newsletter = self._get_or_create_manual_group(org, admin, "Newsletter")
        self._ok()

        # ── Flows ──────────────────────────────────────────────────────────────
        self._log("Creating flows... ")
        flow_welcome = self._get_or_create_flow(org, admin, "Welcome Flow")
        flow_support = self._get_or_create_flow(org, admin, "Support Flow")
        flow_survey = self._get_or_create_flow(org, admin, "Survey Flow")
        self._ok()

        # ── Contacts ───────────────────────────────────────────────────────────
        self._log(f"Creating {num_contacts} contacts...\n")
        contacts = self._create_contacts(org, admin, channel, num_contacts)
        self._log(f"  Created {len(contacts)} contacts\n")

        # ── Add contacts to groups ─────────────────────────────────────────────
        self._log("Adding contacts to groups... ")
        group_all.contacts.set(contacts)
        vip_contacts = contacts[: max(1, len(contacts) // 5)]
        group_vip.contacts.set(vip_contacts)
        newsletter_contacts = contacts[: max(1, len(contacts) // 2)]
        group_newsletter.contacts.set(newsletter_contacts)
        self._ok()

        # ── Campaigns ─────────────────────────────────────────────────────────
        self._log("Creating campaigns... ")
        campaign, _ = Campaign.objects.get_or_create(
            org=org, name="Onboarding Campaign",
            defaults=dict(group=group_newsletter, is_active=True, created_by=admin, modified_by=admin),
        )
        if not campaign.events.exists():
            CampaignEvent.create_flow_event(
                org, admin, campaign, field_joined_on,
                offset=1, unit=CampaignEvent.UNIT_DAYS,
                flow=flow_welcome, start_mode=CampaignEvent.MODE_INTERRUPT,
            )
        self._ok()

        # ── Messages ───────────────────────────────────────────────────────────
        self._log(f"Creating messages (≈{num_msgs} total)... ")
        msg_count = self._create_messages(org, admin, channel, contacts[:min(10, len(contacts))], num_msgs, label_support)
        self._ok(f"{msg_count} msgs")

        # ── Tickets ────────────────────────────────────────────────────────────
        self._log("Creating tickets... ")
        ticket_count = self._create_tickets(org, admin, contacts[:min(5, len(contacts))], topic_support, team_sales)
        self._ok(f"{ticket_count} tickets")

        self._log("\n")
        self._log(self.style.SUCCESS("Done!") + f"\n\n")
        self._log(f"  Org:      {org.name} (id={org.id})\n")
        self._log(f"  Contacts: {Contact.objects.filter(org=org).count()}\n")
        self._log(f"  Msgs:     {Msg.objects.filter(org=org).count()}\n")
        self._log(f"  Flows:    {Flow.objects.filter(org=org).count()}\n")
        self._log(f"  Tickets:  {Ticket.objects.filter(org=org).count()}\n")
        self._log(f"\nNow run:\n  python manage.py dump_org {org.id} --counts\n\n")

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _log(self, msg):
        self.stderr.write(msg)

    def _ok(self, extra=""):
        suffix = f" ({extra})" if extra else ""
        self.stderr.write(self.style.SUCCESS("OK") + suffix + "\n")

    def _get_or_create_user(self, email, password, is_superuser=False, **kwargs):
        user, created = User.objects.get_or_create(
            email=email,
            defaults=dict(is_superuser=is_superuser, is_staff=is_superuser, **kwargs),
        )
        if created:
            user.set_password(password)
            user.save(update_fields=("password",))
        return user

    def _get_or_create_label(self, org, user, name):
        existing = Label.objects.filter(org=org, name=name, is_active=True).first()
        if existing:
            return existing
        return Label.create(org, user, name)

    def _get_or_create_optin(self, org, user, name):
        existing = OptIn.objects.filter(org=org, name=name).first()
        if existing:
            return existing
        return OptIn.create(org, user, name)

    def _get_or_create_manual_group(self, org, user, name):
        existing = ContactGroup.objects.filter(org=org, name=name, is_active=True).first()
        if existing:
            return existing
        return ContactGroup.create_manual(org, user, name)

    def _get_or_create_flow(self, org, user, name):
        existing = Flow.objects.filter(org=org, name=name, is_active=True).first()
        if existing:
            return existing

        flow = Flow.objects.create(
            org=org,
            name=name,
            flow_type=Flow.TYPE_MESSAGE,
            created_by=user,
            modified_by=user,
            saved_by=user,
        )
        definition = {
            "uuid": str(uuid4()),
            "name": name,
            "type": "messaging",
            "revision": 1,
            "spec_version": Flow.CURRENT_SPEC_VERSION,
            "expire_after_minutes": 10080,
            "language": "por",
            "nodes": [
                {
                    "uuid": str(uuid4()),
                    "actions": [
                        {"uuid": str(uuid4()), "type": "send_msg", "text": f"Hello from {name}!"}
                    ],
                    "exits": [{"uuid": str(uuid4())}],
                }
            ],
        }
        flow.version_number = definition["spec_version"]
        flow.save(update_fields=("version_number",))
        flow.save_revision(user, definition)
        return flow

    def _create_contacts(self, org, user, channel, count):
        contacts = list(Contact.objects.filter(org=org, status=Contact.STATUS_ACTIVE)[:count])
        existing = len(contacts)

        phones_used = set(
            ContactURN.objects.filter(org=org, scheme="tel").values_list("path", flat=True)
        )

        for i in range(existing, count):
            name = NAMES[i % len(NAMES)] + (f" {i}" if i >= len(NAMES) else "")
            phone = f"+5511{90000000 + i:08d}"

            if phone in phones_used:
                continue
            phones_used.add(phone)

            contact = Contact.objects.create(
                org=org,
                name=name,
                language="por",
                created_by=user,
                modified_by=user,
                created_on=timezone.now(),
                status=Contact.STATUS_ACTIVE,
            )
            ContactURN.objects.create(
                org=org,
                contact=contact,
                identity=f"tel:{phone}",
                scheme="tel",
                path=phone,
                priority=50,
            )
            contacts.append(contact)

        return contacts

    def _create_messages(self, org, user, channel, contacts, total, label):
        if not contacts:
            return 0

        per_contact = max(1, total // len(contacts))
        count = 0

        for contact in contacts:
            urn = ContactURN.objects.filter(contact=contact).first()
            if not urn:
                continue

            for j in range(per_contact):
                direction = Msg.DIRECTION_IN if j % 2 == 0 else Msg.DIRECTION_OUT
                text = f"Incoming message {j}" if direction == Msg.DIRECTION_IN else f"Outgoing reply {j}"
                status = Msg.STATUS_HANDLED if direction == Msg.DIRECTION_IN else Msg.STATUS_SENT

                msg = Msg.objects.create(
                    org=org,
                    direction=direction,
                    contact=contact,
                    contact_urn=urn,
                    text=text,
                    channel=channel,
                    status=status,
                    msg_type=Msg.TYPE_TEXT,
                    is_android=False,
                    created_on=timezone.now(),
                    modified_on=timezone.now(),
                    sent_on=timezone.now() if direction == Msg.DIRECTION_OUT else None,
                )
                if j % 5 == 0:
                    msg.labels.add(label)
                count += 1

        return count

    def _create_tickets(self, org, user, contacts, topic, team):
        count = 0
        for i, contact in enumerate(contacts):
            is_open = i % 2 == 0
            ticket = Ticket.objects.create(
                org=org,
                contact=contact,
                topic=topic,
                assignee=user if i % 3 == 0 else None,
                status=Ticket.STATUS_OPEN if is_open else Ticket.STATUS_CLOSED,
                opened_on=timezone.now(),
                closed_on=None if is_open else timezone.now(),
            )
            TicketEvent.objects.create(
                org=org,
                contact=contact,
                ticket=ticket,
                event_type=TicketEvent.TYPE_OPENED,
                created_by=user,
                created_on=timezone.now(),
            )
            if not is_open:
                TicketEvent.objects.create(
                    org=org,
                    contact=contact,
                    ticket=ticket,
                    event_type=TicketEvent.TYPE_CLOSED,
                    created_by=user,
                    created_on=timezone.now(),
                )
            count += 1
        return count
