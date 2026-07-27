"""
Repair logic for contact history message events written with non-time-ordered UUIDs.

Migration 0299_backfill_msg_events wrote each message's history event to DynamoDB using the raw `msg.uuid` as the sort
key (`evt#<uuid>`). Contact history is ordered *only* by that sort key, so it assumes UUIDs are time-ordered (v7).
Messages created before the switch to v7 UUIDs still have random v4 UUIDs, whose sort keys land in an arbitrary
position - almost always *above* the v7 keys - making an old message show up as the most recent one.

This module re-keys those events using a v7 UUID derived from the message's `created_on` (matching every other history
backfill, e.g. flows/0395) and removes the old randomly-ordered items. It does NOT touch `msg.uuid` in Postgres, since
that value is exposed through the public API and may be referenced externally.
"""

from uuid import UUID

from temba.utils import dynamo
from temba.utils.uuid import is_uuid7

STATUS_FAILED = "F"

status_to_tag = {"W": "wired", "S": "sent", "D": "delivered", "R": "read", "E": "errored", "F": "failed"}

FAILED_NO_DESTINATION = "D"
FAILED_CONTACT = "C"
FAILED_SUSPENDED = "S"
FAILED_LOOPING = "L"
FAILED_ERROR_LIMIT = "E"
FAILED_TOO_OLD = "O"
FAILED_CHANNEL_REMOVED = "R"

failed_reason_to_unsendable = {
    FAILED_NO_DESTINATION: "no_route",
    FAILED_CONTACT: "contact_blocked",
    FAILED_SUSPENDED: "org_suspended",
    FAILED_LOOPING: "looping",
}

failed_reason_to_tag_reason = {
    FAILED_ERROR_LIMIT: "error_limit",
    FAILED_TOO_OLD: "too_old",
    FAILED_CHANNEL_REMOVED: "channel_removed",
}

VISIBILITY_DELETED_BY_USER = "D"
VISIBILITY_DELETED_BY_SENDER = "X"

# suffixes of every item 0299 could have written for a single message
OLD_ITEM_SUFFIXES = ("", "#del", "#sts")

_RFC_4122_VERSION_7_FLAGS = (7 << 76) | (0x8000 << 48)


def stable_uuid7(when, seed: UUID) -> UUID:
    """
    Builds a deterministic v7 UUID whose timestamp comes from `when` and whose remaining bits come from `seed`.

    Being time-ordered by `when` fixes the ordering, while sourcing the random bits from the (unique) original UUID
    keeps the result unique and deterministic - so re-running the repair produces the same key and never creates
    duplicates.
    """
    ts_ms = int(when.timestamp() * 1000) & 0xFFFF_FFFF_FFFF

    counter = (seed.int >> 32) & 0x3FF_FFFF_FFFF  # 42 bits
    tail = seed.int & 0xFFFF_FFFF  # 32 bits

    counter_hi = (counter >> 30) & 0x0FFF
    counter_lo = counter & 0x3FFF_FFFF

    int_uuid = (ts_ms << 80) | (counter_hi << 64) | (counter_lo << 32) | tail | _RFC_4122_VERSION_7_FLAGS

    hex = "%032x" % int_uuid
    return UUID(f"{hex[:8]}-{hex[8:12]}-{hex[12:16]}-{hex[16:20]}-{hex[20:]}")


def _channel(channel) -> dict:
    return {"uuid": str(channel.uuid), "name": channel.name}


def _base_msg(obj) -> dict:
    d = {
        "urn": obj.contact_urn.identity if obj.contact_urn else None,
        "channel": _channel(obj.channel) if obj.channel else None,
        "text": obj.text,
    }
    if obj.attachments:
        d["attachments"] = obj.attachments
    return d


def _msg_in(obj) -> dict:
    d = _base_msg(obj)
    if obj.external_id:
        d["external_id"] = obj.external_id
    return d


def _msg_out(obj) -> dict:
    d = _base_msg(obj)
    if obj.quick_replies:
        d["quick_replies"] = obj.quick_replies
    if obj.failed_reason in failed_reason_to_unsendable:
        d["unsendable_reason"] = failed_reason_to_unsendable[obj.failed_reason]
    return d


def event_items(msg) -> list[tuple[str, dict]]:
    """
    Returns the (sort-key suffix, Data) pairs for a message, identical to what migration 0299 produced.
    """
    items = []
    if msg.direction == "I":
        items.append(("", {"type": "msg_received", "created_on": msg.created_on.isoformat(), "msg": _msg_in(msg)}))

        if msg.visibility == VISIBILITY_DELETED_BY_SENDER:
            items.append(("#del", {"created_on": msg.modified_on.isoformat(), "by_contact": True}))
        elif msg.visibility == VISIBILITY_DELETED_BY_USER:
            items.append(("#del", {"created_on": msg.modified_on.isoformat()}))
    else:
        if msg.msg_type == "V":
            items.append(("", {"type": "ivr_created", "created_on": msg.created_on.isoformat(), "msg": _msg_out(msg)}))
        else:
            items.append(("", {"type": "msg_created", "created_on": msg.created_on.isoformat(), "msg": _msg_out(msg)}))

            if msg.status in status_to_tag and msg.failed_reason not in failed_reason_to_unsendable:
                data = {"created_on": msg.modified_on.isoformat(), "status": status_to_tag[msg.status]}
                if msg.status == STATUS_FAILED and msg.failed_reason in failed_reason_to_tag_reason:
                    data["reason"] = failed_reason_to_tag_reason[msg.failed_reason]
                items.append(("#sts", data))

    return items


def repair_msgs(msgs, *, dry_run: bool = False, batch_size: int = 100) -> int:
    """
    Repairs the history events of the given messages queryset, re-keying legacy (non-v7) events with a time-ordered
    UUID and removing the old randomly-ordered items. Returns the number of messages repaired.

    Idempotent and resumable: only messages whose old (bad) base item still exists are touched, and corrected items
    are written before the old ones are removed so nothing is lost if interrupted.
    """
    before_id = None
    num_repaired = 0

    while True:
        batch = msgs.order_by("-id")
        if before_id:
            batch = batch.filter(id__lt=before_id)

        batch = list(batch[:batch_size])
        if not batch:
            break

        last_id = batch[-1].id

        # only messages with non-v7 UUIDs were written with a random sort key by migration 0299
        legacy = [m for m in batch if not is_uuid7(m.uuid)]

        # only repair ones whose old (bad) base item still exists
        keys = [(f"con#{m.contact.uuid}", f"evt#{m.uuid}") for m in legacy]
        existing = {(it["PK"], it["SK"]) for it in dynamo.batch_get(dynamo.HISTORY, keys)}
        to_repair = [m for m in legacy if (f"con#{m.contact.uuid}", f"evt#{m.uuid}") in existing]

        if to_repair and not dry_run:
            # 1) write the corrected, time-ordered items first so nothing is lost if interrupted
            with dynamo.HISTORY.batch_writer() as writer:
                for msg in to_repair:
                    pk = f"con#{msg.contact.uuid}"
                    new_uuid = stable_uuid7(msg.created_on, msg.uuid)
                    for suffix, data in event_items(msg):
                        writer.put_item({"PK": pk, "SK": f"evt#{new_uuid}{suffix}", "OrgID": msg.org_id, "Data": data})

            # 2) then remove the old randomly-ordered items
            with dynamo.HISTORY.batch_writer() as writer:
                for msg in to_repair:
                    pk = f"con#{msg.contact.uuid}"
                    for suffix in OLD_ITEM_SUFFIXES:
                        writer.delete_item(Key={"PK": pk, "SK": f"evt#{msg.uuid}{suffix}"})

        num_repaired += len(to_repair)
        before_id = last_id

    return num_repaired
