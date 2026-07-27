from datetime import datetime, timezone as tzone
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError

from temba.msgs.models import Msg
from temba.tests import TembaTest, cleanup
from temba.tests.dynamo import dynamo_scan_all
from temba.utils import dynamo
from temba.utils.uuid import is_uuid7, uuid4


class RepairMsgHistoryTest(TembaTest):
    def _write_bad_event(self, contact, uuid, data):
        """Simulates what migration 0299 wrote for a legacy (v4 UUID) message."""
        dynamo.HISTORY.put_item(
            Item={"PK": f"con#{contact.uuid}", "SK": f"evt#{uuid}", "OrgID": self.org.id, "Data": data}
        )

    @cleanup(dynamodb=True)
    def test_command(self):
        contact = self.create_contact("Ann", phone="+16305550123")
        other = self.create_contact("Bob", phone="+16305550124")

        # a legacy incoming message with a v4 UUID, created back in early 2025
        msg_in = self.create_incoming_msg(
            contact, "old message", created_on=datetime(2025, 1, 15, 10, 0, tzinfo=tzone.utc)
        )
        msg_in.uuid = uuid4()
        msg_in.save(update_fields=["uuid"])

        # a legacy outgoing (sent) message with a v4 UUID
        msg_out = self.create_outgoing_msg(
            contact, "old reply", status=Msg.STATUS_SENT, created_on=datetime(2025, 2, 20, 11, 0, tzinfo=tzone.utc)
        )
        msg_out.uuid = uuid4()
        msg_out.save(update_fields=["uuid"])

        # a recent message that already has a v7 UUID and must be left untouched
        msg_recent = self.create_incoming_msg(
            contact, "recent message", created_on=datetime(2026, 1, 1, 12, 0, tzinfo=tzone.utc)
        )

        # a legacy message belonging to a different contact - must be untouched when scoping by contact
        other_msg = self.create_incoming_msg(
            other, "other contact", created_on=datetime(2025, 3, 1, 9, 0, tzinfo=tzone.utc)
        )
        other_msg.uuid = uuid4()
        other_msg.save(update_fields=["uuid"])

        # seed DynamoDB with what 0299 would have written
        self._write_bad_event(
            contact,
            msg_in.uuid,
            {"type": "msg_received", "created_on": "2025-01-15T10:00:00+00:00", "msg": {"text": "old message"}},
        )
        self._write_bad_event(
            contact,
            msg_out.uuid,
            {"type": "msg_created", "created_on": "2025-02-20T11:00:00+00:00", "msg": {"text": "old reply"}},
        )
        dynamo.HISTORY.put_item(
            Item={
                "PK": f"con#{contact.uuid}",
                "SK": f"evt#{msg_out.uuid}#sts",
                "OrgID": self.org.id,
                "Data": {"created_on": "2025-02-20T11:00:00+00:00", "status": "sent"},
            }
        )
        dynamo.HISTORY.put_item(
            Item={
                "PK": f"con#{contact.uuid}",
                "SK": f"evt#{msg_recent.uuid}",
                "OrgID": self.org.id,
                "Data": {"type": "msg_received", "created_on": "2026-01-01T12:00:00+00:00", "msg": {"text": "recent"}},
            }
        )
        self._write_bad_event(
            other,
            other_msg.uuid,
            {"type": "msg_received", "created_on": "2025-03-01T09:00:00+00:00", "msg": {"text": "other contact"}},
        )

        # unknown org / contact are rejected
        with self.assertRaises(CommandError):
            call_command("repair_msg_history", org_id=99999999)
        with self.assertRaises(CommandError):
            call_command("repair_msg_history", org_id=self.org.id, contact_uuid=str(uuid4()))

        # dry run scoped to one contact reports but writes nothing
        out = StringIO()
        call_command("repair_msg_history", org_id=self.org.id, contact_uuid=str(contact.uuid), dry_run=True, stdout=out)
        self.assertIn("2 legacy msg events would be repaired", out.getvalue())
        self.assertIn(f"evt#{msg_in.uuid}", {i["SK"] for i in dynamo_scan_all(dynamo.HISTORY)})

        # real run scoped to a single contact
        out = StringIO()
        call_command("repair_msg_history", org_id=self.org.id, contact_uuid=str(contact.uuid), stdout=out)
        self.assertIn("2 legacy msg events repaired", out.getvalue())

        items = dynamo_scan_all(dynamo.HISTORY)
        sks = {i["SK"] for i in items}

        # the scoped contact's old v4 keys are gone
        self.assertNotIn(f"evt#{msg_in.uuid}", sks)
        self.assertNotIn(f"evt#{msg_out.uuid}", sks)
        self.assertNotIn(f"evt#{msg_out.uuid}#sts", sks)

        # its already-v7 event is untouched, and the other contact's legacy event was left alone
        self.assertIn(f"evt#{msg_recent.uuid}", sks)
        self.assertIn(f"evt#{other_msg.uuid}", sks)

        # for the scoped contact every event is now time-ordered (v7) and sorts chronologically
        contact_events = sorted(
            (i for i in items if i["PK"] == f"con#{contact.uuid}" and "#" not in i["SK"][4:]),
            key=lambda i: i["SK"],
        )
        for e in contact_events:
            self.assertTrue(is_uuid7(e["SK"][4:40]))
        self.assertEqual(
            ["old message", "old reply", "recent"],
            [e["Data"]["msg"]["text"] for e in contact_events],
        )

        # re-running is idempotent - nothing left to repair
        out = StringIO()
        call_command("repair_msg_history", org_id=self.org.id, contact_uuid=str(contact.uuid), stdout=out)
        self.assertIn("0 legacy msg events repaired", out.getvalue())
