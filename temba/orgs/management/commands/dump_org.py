import glob
import json
import os
import re
import shlex
import subprocess
import sys
import time

from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from temba.orgs.models import Org

_BAR_WIDTH = 30

# Logical groupings of models. Each group name maps to the set of model labels it contains.
# Groups are used with --groups; individual models can be picked with --models.
MODEL_GROUPS: dict[str, set[str]] = {
    "core": {
        "orgs.org",
        "orgs.orgmembership",
        "orgs.invitation",
        "orgs.orgimport",
        "orgs.export",
        "orgs.itemcount",
        "orgs.dailycount",
    },
    "contacts": {
        "contacts.contactfield",
        "contacts.contactgroup",
        "contacts.contact",
        "contacts.contacturn",
        "contacts.contactgroupcount",
        "contacts.contactnote",
        "contacts.contactfire",
        "contacts.contactimport",
        "contacts.contactimportbatch",
    },
    "channels": {
        "channels.channel",
        "channels.channelevent",
        "channels.syncevent",
        "channels.channelcount",
    },
    "flows": {
        "flows.flowlabel",
        "flows.flow",
        "flows.flowrevision",
        "flows.flowstart",
        "flows.flowstartcount",
        "flows.flowsession",
        "flows.flowrun",
        "flows.flowactivitycount",
        "flows.flowresultcount",
    },
    "msgs": {
        "msgs.label",
        "msgs.labelcount",
        "msgs.optin",
        "msgs.media",
        "msgs.broadcast",
        "msgs.broadcastmsgcount",
        "msgs.msg",
    },
    "campaigns": {
        "campaigns.campaign",
        "campaigns.campaignevent",
    },
    "tickets": {
        "tickets.topic",
        "tickets.team",
        "tickets.shortcut",
        "tickets.ticket",
        "tickets.ticketevent",
    },
    "triggers": {"triggers.trigger"},
    "templates": {"templates.template"},
    "api": {
        "api.resthook",
        "api.resthooksubscriber",
        "api.webhookevent",
        "api.apitoken",
    },
    "globals": {"globals.global"},
    "schedules": {"schedules.schedule"},
    "classifiers": {"classifiers.classifier"},
    "ai": {"ai.llm"},
    "notifications": {
        "notifications.incident",
        "notifications.notification",
    },
    "archives": {"archives.archive"},
    "airtime": {"airtime.airtimetransfer"},
    "ivr": {"ivr.call"},
    "request_logs": {"request_logs.httplog"},
}


def _progress_bar(done: int, total: int) -> str:
    if total == 0:
        filled, pct = _BAR_WIDTH, 100
    else:
        filled = int(_BAR_WIDTH * done / total)
        pct = int(100 * done / total)
    bar = "█" * filled + "░" * (_BAR_WIDTH - filled)
    return f"[{bar}] {done:,}/{total:,} ({pct}%)"


def get_model_specs(org):
    """
    Returns the full ordered list of (label, queryset) pairs for an org.
    Models without a direct 'org' FK are filtered through their relation chain.
    Order respects FK dependencies so loaddata works correctly.
    """
    from temba.ai.models import LLM
    from temba.airtime.models import AirtimeTransfer
    from temba.api.models import APIToken, Resthook, ResthookSubscriber, WebHookEvent
    from temba.archives.models import Archive
    from temba.campaigns.models import Campaign, CampaignEvent
    from temba.channels.models import Channel, ChannelCount, ChannelEvent, SyncEvent
    from temba.classifiers.models import Classifier
    from temba.contacts.models import (
        Contact,
        ContactField,
        ContactFire,
        ContactGroup,
        ContactGroupCount,
        ContactImport,
        ContactImportBatch,
        ContactNote,
        ContactURN,
    )
    from temba.flows.models import (
        Flow,
        FlowActivityCount,
        FlowLabel,
        FlowResultCount,
        FlowRevision,
        FlowRun,
        FlowSession,
        FlowStart,
        FlowStartCount,
    )
    from temba.globals.models import Global
    from temba.ivr.models import Call
    from temba.msgs.models import Broadcast, BroadcastMsgCount, Label, LabelCount, Media, Msg, OptIn
    from temba.notifications.models import Incident, Notification
    from temba.orgs.models import DailyCount, Export, Invitation, ItemCount, OrgImport, OrgMembership
    from temba.request_logs.models import HTTPLog
    from temba.schedules.models import Schedule
    from temba.templates.models import Template
    from temba.tickets.models import Shortcut, Team, Ticket, TicketEvent, Topic
    from temba.triggers.models import Trigger

    return [
        # Tier 0: the org itself
        ("orgs.org",             Org.objects.filter(id=org.id)),
        # Tier 1: direct org FK, no intra-org dependencies
        ("orgs.orgmembership",   OrgMembership.objects.filter(org=org)),
        ("orgs.invitation",      Invitation.objects.filter(org=org)),
        ("orgs.orgimport",       OrgImport.objects.filter(org=org)),
        ("ai.llm",               LLM.objects.filter(org=org)),
        ("classifiers.classifier", Classifier.objects.filter(org=org)),
        ("contacts.contactfield", ContactField.objects.filter(org=org)),
        ("flows.flowlabel",      FlowLabel.objects.filter(org=org)),
        ("globals.global",       Global.objects.filter(org=org)),
        ("msgs.label",           Label.objects.filter(org=org)),
        ("msgs.optin",           OptIn.objects.filter(org=org)),
        ("schedules.schedule",   Schedule.objects.filter(org=org)),
        ("tickets.topic",        Topic.objects.filter(org=org)),
        ("tickets.team",         Team.objects.filter(org=org)),
        ("tickets.shortcut",     Shortcut.objects.filter(org=org)),
        ("api.resthook",         Resthook.objects.filter(org=org)),
        ("api.apitoken",         APIToken.objects.filter(org=org)),
        # Tier 2: depends on Tier 1
        ("contacts.contactgroup", ContactGroup.objects.filter(org=org)),
        ("channels.channel",     Channel.objects.filter(org=org)),
        ("campaigns.campaign",   Campaign.objects.filter(org=org)),
        ("templates.template",   Template.objects.filter(org=org)),
        # Tier 3: depends on Tier 2
        ("contacts.contact",     Contact.objects.filter(org=org)),
        ("contacts.contactimport", ContactImport.objects.filter(org=org)),
        ("channels.syncevent",   SyncEvent.objects.filter(channel__org=org)),
        ("channels.channelcount", ChannelCount.objects.filter(channel__org=org)),
        ("campaigns.campaignevent", CampaignEvent.objects.filter(campaign__org=org)),
        ("api.resthooksubscriber", ResthookSubscriber.objects.filter(resthook__org=org)),
        ("api.webhookevent",     WebHookEvent.objects.filter(org=org)),
        # Tier 4: depends on Tier 3
        ("contacts.contacturn",       ContactURN.objects.filter(org=org)),
        ("contacts.contactgroupcount", ContactGroupCount.objects.filter(group__org=org)),
        ("contacts.contactnote",      ContactNote.objects.filter(contact__org=org)),
        ("contacts.contactfire",      ContactFire.objects.filter(org=org)),
        ("contacts.contactimportbatch", ContactImportBatch.objects.filter(contact_import__org=org)),
        ("channels.channelevent",     ChannelEvent.objects.filter(org=org)),
        ("flows.flow",                Flow.objects.filter(org=org)),
        # Tier 5: depends on Tier 4
        ("flows.flowrevision",     FlowRevision.objects.filter(flow__org=org)),
        ("flows.flowactivitycount", FlowActivityCount.objects.filter(flow__org=org)),
        ("flows.flowresultcount",  FlowResultCount.objects.filter(flow__org=org)),
        ("flows.flowstart",        FlowStart.objects.filter(org=org)),
        ("flows.flowsession",      FlowSession.objects.filter(contact__org=org)),
        ("msgs.broadcast",         Broadcast.objects.filter(org=org)),
        ("msgs.media",             Media.objects.filter(org=org)),
        ("triggers.trigger",       Trigger.objects.filter(org=org)),
        ("request_logs.httplog",   HTTPLog.objects.filter(org=org)),
        ("notifications.incident", Incident.objects.filter(org=org)),
        ("airtime.airtimetransfer", AirtimeTransfer.objects.filter(org=org)),
        ("tickets.ticket",         Ticket.objects.filter(org=org)),
        ("ivr.call",               Call.objects.filter(org=org)),
        # Tier 6: depends on Tier 5
        ("flows.flowrun",          FlowRun.objects.filter(org=org)),
        ("flows.flowstartcount",   FlowStartCount.objects.filter(start__org=org)),
        ("msgs.broadcastmsgcount", BroadcastMsgCount.objects.filter(broadcast__org=org)),
        ("msgs.labelcount",        LabelCount.objects.filter(label__org=org)),
        ("msgs.msg",               Msg.objects.filter(org=org)),
        ("tickets.ticketevent",    TicketEvent.objects.filter(org=org)),
        ("notifications.notification", Notification.objects.filter(org=org)),
        # Tier 7: final
        ("archives.archive",   Archive.objects.filter(org=org)),
        ("orgs.export",        Export.objects.filter(org=org)),
        ("orgs.itemcount",     ItemCount.objects.filter(org=org)),
        ("orgs.dailycount",    DailyCount.objects.filter(org=org)),
    ]


def _is_complete_fixture(filepath: str) -> bool:
    """
    Returns True if the file exists and ends with the closing bytes of a valid fixture
    (i.e. the writer finished normally). Avoids loading the entire file into memory.
    """
    try:
        size = os.path.getsize(filepath)
    except FileNotFoundError:
        return False

    if size == 0:
        return False

    # We always write "\n]\n" as the last bytes. Check for that suffix.
    tail_size = min(size, 8)
    with open(filepath, "rb") as f:
        f.seek(-tail_size, os.SEEK_END)
        tail = f.read()

    return tail.rstrip().endswith(b"]")


def _read_last_pk(filepath: str):
    """
    Returns the last pk value written in a fixture file by scanning only the tail.
    The pk can be an integer or a string (UUID). Returns None if it cannot be determined.
    """
    try:
        size = os.path.getsize(filepath)
        read_size = min(size, 8192)
        with open(filepath, "rb") as f:
            f.seek(-read_size, os.SEEK_END)
            tail = f.read().decode("utf-8", errors="replace")
        # Fixture records: {"model": "...", "pk": 12345, "fields": {...}}
        # pk is either an integer or a quoted string (UUID).
        matches = re.findall(r'"pk":\s*(?:"([^"]+)"|(\d+))', tail)
        if matches:
            str_pk, int_pk = matches[-1]
            return str_pk if str_pk else int(int_pk)
    except (OSError, ValueError):
        pass
    return None


_CHECKPOINT_FILE = "checkpoint.json"


def _checkpoint_path(out_dir: str) -> str:
    return os.path.join(out_dir, _CHECKPOINT_FILE)


def _load_checkpoint(out_dir: str) -> dict:
    """
    Loads the checkpoint file from out_dir. Returns an empty dict if missing or corrupt.
    Format:
        {
            "models": {
                "msgs.msg":      {"last_pk": 23000000, "next_part": 47},
                "flows.flowrun": {"done": true}
            }
        }
    """
    path = _checkpoint_path(out_dir)
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_checkpoint(out_dir: str, models: dict):
    """
    Atomically writes checkpoint.json. Existing entries are merged so that
    a call for one model never clobbers another model's data.
    """
    path = _checkpoint_path(out_dir)
    # Load existing data to merge (another process / interrupted write)
    try:
        with open(path) as f:
            existing = json.load(f)
    except (OSError, json.JSONDecodeError):
        existing = {}

    merged = existing.copy()
    merged.setdefault("models", {}).update(models)

    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(merged, f, indent=2)
    os.replace(tmp, path)  # atomic on POSIX


def _resolve_selection(groups: list[str], models: list[str]) -> set[str] | None:
    """
    Returns the set of model labels to include, or None meaning 'all'.
    Validates group and model names, raising ValueError on unknown names.
    """
    if not groups and not models:
        return None  # dump everything

    selected: set[str] = set()

    for g in groups:
        if g not in MODEL_GROUPS:
            available = ", ".join(sorted(MODEL_GROUPS))
            raise ValueError(f"Unknown group '{g}'. Available groups: {available}")
        selected.update(MODEL_GROUPS[g])

    all_labels = {label for labels in MODEL_GROUPS.values() for label in labels}
    for m in models:
        m = m.lower()
        if m not in all_labels:
            raise ValueError(f"Unknown model '{m}'. Use --list-groups to see all available models.")
        selected.add(m)

    return selected


class Command(BaseCommand):
    help = "Dumps all data for a single org to JSON fixture files, with optional model/group filtering"

    def add_arguments(self, parser):
        parser.add_argument("org_id", type=int, nargs="?", help="ID of the org to dump")
        parser.add_argument(
            "--output", "-o",
            default=None,
            help="Output file path (default: org_<id>_dump.json). Use '-' for stdout. "
                 "In --split mode, sets the output directory.",
        )
        parser.add_argument(
            "--groups", "-g",
            nargs="+",
            default=[],
            metavar="GROUP",
            help=(
                "Only dump these logical groups, e.g. --groups contacts flows msgs. "
                "Use --list-groups to see all available groups."
            ),
        )
        parser.add_argument(
            "--models", "-m",
            nargs="+",
            default=[],
            metavar="APP.MODEL",
            help="Only dump these specific models, e.g. --models msgs.msg contacts.contact",
        ),
        parser.add_argument(
            "--exclude", "-e",
            nargs="+",
            default=[],
            metavar="APP.MODEL",
            help="Exclude these models from the dump, e.g. --exclude msgs.msg flows.flowrun",
        )
        parser.add_argument(
            "--split",
            action="store_true",
            default=False,
            help=(
                "Write one fixture file per model instead of a single file. "
                "Files are named <NNN>_<app>.<model>.json inside the output directory."
            ),
        )
        parser.add_argument(
            "--indent",
            type=int,
            default=2,
            help="JSON indentation level (default: 2). Use 0 for compact output.",
        )
        parser.add_argument(
            "--counts",
            action="store_true",
            default=False,
            help="Show row counts per model during the scan phase",
        )
        parser.add_argument(
            "--list-groups",
            action="store_true",
            default=False,
            help="List all available groups and their models, then exit",
        )
        parser.add_argument(
            "--on-success",
            default=None,
            metavar="CMD",
            help=(
                "Shell command to run after a successful dump. "
                "Placeholders: {output}, {org_id}. "
                "Example: --on-success './scripts/upload_org_fixtures.sh {output} s3://my-bucket'"
            ),
        )
        parser.add_argument(
            "--on-chunk",
            default=None,
            metavar="CMD",
            help=(
                "Shell command to run after each chunk file is written (requires --chunk-size). "
                "The command runs synchronously before the next chunk starts. "
                "Placeholders: {chunk_file} (full path to chunk), {filename} (basename), "
                "{label} (app.model), {part} (part number), {org_id}, {output} (output dir), "
                "{checkpoint_file} (full path to checkpoint.json). "
                "Example: --on-chunk 'aws s3 cp {chunk_file} s3://my-bucket/fixtures/ "
                "&& aws s3 cp {checkpoint_file} s3://my-bucket/fixtures/'"
            ),
        )
        parser.add_argument(
            "--resume",
            action="store_true",
            default=False,
            help=(
                "Resume an interrupted --split dump. Files that already exist and are "
                "complete (valid JSON ending with ']') are skipped; incomplete or missing "
                "files are (re)written. Requires --split and --output."
            ),
        )
        parser.add_argument(
            "--chunk-size",
            type=int,
            default=0,
            metavar="N",
            help=(
                "Maximum rows per file in --split mode. Models with more rows than N are "
                "split into multiple files named {NNN}_{app}.{model}_part{MMMM}.json. "
                "When combined with --resume, completed chunks are skipped using keyset "
                "pagination (pk__gt=last_pk) — no expensive COUNT() per chunk. "
                "Recommended for large tables like msgs.msg and flows.flowrun. "
                "Example: --chunk-size 500000"
            ),
        )

    def handle(self, *args, **options):
        if options["list_groups"]:
            self._print_groups()
            return

        if not options["org_id"]:
            raise CommandError("org_id is required unless --list-groups is used.")

        org_id = options["org_id"]
        output_path = options["output"]
        excludes = {e.lower() for e in options["exclude"]}
        indent = options["indent"] or None
        show_counts = options["counts"]
        split = options["split"]
        resume = options["resume"]
        on_success = options["on_success"]
        on_chunk = options["on_chunk"]
        chunk_size = options["chunk_size"]

        if resume and not split:
            raise CommandError("--resume requires --split.")
        if resume and not output_path:
            raise CommandError("--resume requires --output <directory> so the existing files can be found.")
        if chunk_size < 0:
            raise CommandError("--chunk-size must be a positive integer.")
        if chunk_size > 0 and not split:
            raise CommandError("--chunk-size requires --split.")
        if on_chunk and not chunk_size:
            raise CommandError("--on-chunk requires --chunk-size.")

        try:
            selected = _resolve_selection(options["groups"], options["models"])
        except ValueError as e:
            raise CommandError(str(e))

        try:
            org = Org.objects.get(id=org_id)
        except Org.DoesNotExist:
            raise CommandError(f"Org with id={org_id} does not exist.")

        self.stderr.write(f'Dumping org: {org.name} (id={org.id}, slug="{org.slug}")\n')

        if selected:
            group_names = ", ".join(sorted(options["groups"])) or "—"
            model_names = ", ".join(sorted(options["models"])) or "—"
            self.stderr.write(f"  groups : {group_names}\n")
            self.stderr.write(f"  models : {model_names}\n")

        self.stderr.write("\n")

        specs = get_model_specs(org)

        # Canonical index: position of each label in the full specs list.
        # Used as the file number in --split so filenames are stable across partial runs.
        canonical_idx = {label: idx for idx, (label, _) in enumerate(specs, start=1)}

        # In resume+split mode, pre-scan the output dir to find already-complete files/chunks.
        #
        # resume_done      : set of labels fully done — their COUNT() is skipped entirely.
        #                    Detected via a .done sentinel file (written when all chunks finish)
        #                    or a complete single-file (NNN_label.json, no _partNNNN suffix).
        # resume_last_pk   : label → last pk of the highest complete chunk for partially-done
        #                    chunked models. Used to resume via pk__gt keyset pagination.
        resume_done: set[str] = set()
        resume_last_pk: dict[str, object] = {}  # label → last pk of last complete chunk
        if resume and split:
            out_dir_pre = output_path  # --output is required with --resume

            # 1) .done sentinels → model fully complete (all chunks written)
            for done_path in glob.glob(os.path.join(out_dir_pre, "*.done")):
                basename = os.path.basename(done_path)
                label = basename[4:].removesuffix(".done")  # strip "NNN_"
                resume_done.add(label)

            # 2) Scan .json files: either a complete single file or a complete chunk file
            for path in glob.glob(os.path.join(out_dir_pre, "*.json")):
                if path == _checkpoint_path(out_dir_pre):
                    continue  # checkpoint.json is not a fixture file
                if not _is_complete_fixture(path):
                    continue
                basename = os.path.basename(path)
                name = basename[4:].removesuffix(".json")  # strip "NNN_" prefix
                part_match = re.match(r"^(.+)_part(\d+)$", name)
                if part_match:
                    # Chunk file: track the last pk of the highest complete chunk
                    label = part_match.group(1)
                    if label not in resume_done:
                        last_pk = _read_last_pk(path)
                        if last_pk is not None:
                            # Keep the last_pk from the chunk with the highest part number.
                            # Basenames sort alphabetically → "part0003" > "part0002" → correct.
                            if basename > resume_last_pk.get(f"__file__{label}", ""):
                                resume_last_pk[label] = last_pk
                                resume_last_pk[f"__file__{label}"] = basename
                else:
                    # Single complete file (no chunking)
                    resume_done.add(name)

            # 3) Checkpoint file — used when chunk files were deleted after upload.
            #    Disk files always take priority; checkpoint only fills the gaps.
            checkpoint = _load_checkpoint(out_dir_pre)
            for lbl, state in checkpoint.get("models", {}).items():
                if lbl in resume_done:
                    continue  # already detected as done from disk
                if state.get("done"):
                    resume_done.add(lbl)
                elif "last_pk" in state and lbl not in resume_last_pk:
                    # Only use checkpoint if no disk chunk was found for this label
                    resume_last_pk[lbl] = state["last_pk"]
                    resume_last_pk[f"__next_part__{lbl}"] = state.get("next_part", 1)

        if resume_done or resume_last_pk:
            partial = {k for k in resume_last_pk if not k.startswith("__file__")}
            self.stderr.write(
                f"  Resume: {len(resume_done)} fully done, {len(partial)} partially done"
                f" — skipping COUNT() for done models.\n\n"
            )

        # Collect (label, qs, count) once — count() is never called again after this.
        # count == -1 is a sentinel meaning "already done, skip without writing".
        all_objects: list[tuple[str, object, int]] = []
        total = 0

        for label, qs in specs:
            if selected is not None and label not in selected:
                continue
            if label in excludes:
                self.stderr.write(f"  [SKIP] {label}\n")
                continue

            if label in resume_done:
                self.stderr.write(f"  {self.style.WARNING('–')} {label:<40}  (done)\n")
                all_objects.append((label, qs, -1))
                continue

            count = qs.count()
            total += count

            if label in resume_last_pk:
                suffix = f"  (partial — resuming from pk {resume_last_pk[label]})"
            else:
                suffix = ""

            if show_counts or count > 0:
                self.stderr.write(f"  {label:<40} {count:>8,} rows{suffix}\n")

            if count > 0:
                all_objects.append((label, qs, count))

        self.stderr.write(f"\nTotal objects: {total:,}\n\n")

        t0 = time.monotonic()

        if split:
            out_dir = output_path or f"org_{org_id}_fixtures"
            os.makedirs(out_dir, exist_ok=True)
            action = "Resuming" if resume else "Writing"
            self.stderr.write(f"{action} split fixtures to: {out_dir}/\n\n")
            written = skipped = 0
            for label, qs, count in all_objects:
                cidx = canonical_idx[label]

                # count == -1 means the file was already complete at scan time (resume mode)
                if count == -1:
                    skipped += 1
                    filename = f"{cidx:03d}_{label}.json"
                    self.stderr.write(
                        f"  {self.style.WARNING('–')} {filename:<45}"
                        f"   [{written:,}/{total:,}]  (skipped)\n"
                    )
                    continue

                use_chunks = chunk_size > 0 and count > chunk_size
                if use_chunks:
                    # Determine start_pk and start_part for resume.
                    # Priority: disk chunk files > checkpoint (for when files were deleted).
                    start_pk = resume_last_pk.get(label)
                    if start_pk is not None and f"__file__{label}" not in resume_last_pk:
                        # Came from checkpoint (no disk files found); use checkpoint's next_part.
                        start_part = resume_last_pk.get(f"__next_part__{label}", 1)
                    else:
                        # Derived from disk: count complete chunk files present.
                        existing = sorted(
                            p for p in glob.glob(os.path.join(out_dir, f"{cidx:03d}_{label}_part*.json"))
                            if _is_complete_fixture(p)
                        )
                        start_part = len(existing) + 1
                    rows_written = self._write_model_chunked(
                        out_dir, cidx, label, qs, count, chunk_size, indent,
                        start_pk, start_part, t0, written, total,
                        on_chunk=on_chunk, org_id=org_id,
                    )
                else:
                    filename = f"{cidx:03d}_{label}.json"
                    filepath = os.path.join(out_dir, filename)
                    with open(filepath, "w", encoding="utf-8") as f:
                        self._write_fixture_with_progress(f, label, qs, count, indent)
                    rows_written = count
                    elapsed = time.monotonic() - t0
                    self.stderr.write(
                        f"  {self.style.SUCCESS('✓')} {filename:<45} {count:>6,} rows"
                        f"   [{written:,}/{total:,}]  {elapsed:.1f}s\n"
                    )

                written += rows_written

            elapsed = time.monotonic() - t0
            done_count = len(all_objects) - skipped
            self._update_manifest(out_dir, canonical_idx)
            self.stderr.write(
                f"\n{self.style.SUCCESS('Done!')} {done_count} written, {skipped} skipped"
                f"  →  {out_dir}/  ({total:,} objects, {elapsed:.1f}s)\n"
                f"Manifest updated: {os.path.join(out_dir, 'manifest.txt')}\n"
            )
            self._run_on_success(on_success, output=out_dir, org_id=org_id)
        elif output_path == "-":
            self._write_fixture_with_progress(sys.stdout, "all", None, total, indent, all_objects=all_objects)
        else:
            output_path = output_path or f"org_{org_id}_dump.json"
            self.stderr.write(f"Writing to: {output_path}\n\n")
            with open(output_path, "w", encoding="utf-8") as f:
                self._write_fixture_with_progress(f, "all", None, total, indent, all_objects=all_objects)
            elapsed = time.monotonic() - t0
            self.stderr.write(
                f"\n{self.style.SUCCESS('Done!')} {output_path}"
                f"  ({total:,} objects, {elapsed:.1f}s)\n"
            )
            self._run_on_success(on_success, output=output_path, org_id=org_id)

    def _run_on_success(self, cmd_template: str | None, *, output: str, org_id: int):
        """
        Runs the --on-success command, replacing {output} and {org_id} placeholders.
        Streams stdout/stderr of the subprocess directly to the terminal.
        Raises CommandError if the subprocess exits with a non-zero code.
        """
        if not cmd_template:
            return

        cmd_str = cmd_template.format(output=output, org_id=org_id)
        self.stderr.write(f"\n{self.style.MIGRATE_HEADING('Running --on-success:')}\n")
        self.stderr.write(f"  $ {cmd_str}\n\n")

        try:
            result = subprocess.run(shlex.split(cmd_str), check=False)
        except FileNotFoundError as e:
            raise CommandError(f"--on-success command not found: {e}")

        if result.returncode != 0:
            raise CommandError(
                f"--on-success command exited with code {result.returncode}: {cmd_str}"
            )

        self.stderr.write(self.style.SUCCESS("--on-success completed successfully.\n"))

    def _update_manifest(self, out_dir: str, canonical_idx: dict[str, int]):
        """
        Writes/updates manifest.txt in out_dir listing all complete fixture files
        in canonical FK-safe load order.

        Sorting key: (canonical_index, part_number) where part_number is 0 for
        single files and the actual part number for chunk files. This guarantees
        that all parts of a model are listed consecutively and in order.
        """
        complete = []
        for path in glob.glob(os.path.join(out_dir, "*.json")):
            if _is_complete_fixture(path):
                complete.append(path)

        def _sort_key(p):
            basename = os.path.basename(p)
            # Strip leading "NNN_" prefix to get the label (+ optional _partNNNN)
            name = basename[4:].removesuffix(".json")
            part_match = re.match(r"^(.+)_part(\d+)$", name)
            if part_match:
                label, part = part_match.group(1), int(part_match.group(2))
            else:
                label, part = name, 0
            # Fall back to filename sort if label is unknown (shouldn't happen)
            cidx = canonical_idx.get(label, 9999)
            return (cidx, part)

        complete.sort(key=_sort_key)

        manifest_path = os.path.join(out_dir, "manifest.txt")
        with open(manifest_path, "w") as f:
            for path in complete:
                f.write(os.path.basename(path) + "\n")

    def _print_groups(self):
        self.stdout.write("Available groups (use with --groups):\n\n")
        for group, labels in sorted(MODEL_GROUPS.items()):
            self.stdout.write(f"  {self.style.SUCCESS(group)}\n")
            for label in sorted(labels):
                self.stdout.write(f"    {label}\n")
            self.stdout.write("\n")

    def _write_model_chunked(
        self,
        out_dir: str,
        cidx: int,
        label: str,
        qs,
        count: int,
        chunk_size: int,
        indent,
        start_pk,
        start_part: int,
        t0: float,
        written_global: int,
        total_global: int,
        *,
        on_chunk: str | None = None,
        org_id: int = 0,
    ) -> int:
        """
        Writes a model's queryset in chunks of at most `chunk_size` rows.

        Uses keyset pagination (pk__gt=last_pk ORDER BY pk) so each chunk is a
        single efficient indexed range scan — no OFFSET drift on huge tables.

        Each chunk becomes its own file:
            {cidx:03d}_{label}_part{part:04d}.json

        If `on_chunk` is set, runs that shell command synchronously after each chunk
        is written before moving to the next one. Placeholders:
            {chunk_file}  full path to the just-written file
            {filename}    basename of the file
            {label}       app.model label
            {part}        part number (integer)
            {org_id}      org id
            {output}      output directory

        When all chunks finish, a sentinel file {cidx:03d}_{label}.done is created
        so a subsequent --resume can skip the entire model without a COUNT() query.

        Returns the total number of rows written in this invocation.
        """
        is_tty = hasattr(self.stderr, "isatty") and self.stderr.isatty()

        last_pk = start_pk
        part = start_part
        total_written_now = 0

        while True:
            # Keyset pagination: start after the last pk written in the previous chunk.
            # ORDER BY pk is required for consistent pagination and relies on the PK index.
            if last_pk is not None:
                chunk_qs = qs.filter(pk__gt=last_pk).order_by("pk")[:chunk_size]
            else:
                chunk_qs = qs.order_by("pk")[:chunk_size]

            filename = f"{cidx:03d}_{label}_part{part:04d}.json"
            filepath = os.path.join(out_dir, filename)

            rows = 0
            first_record = True
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("[\n")
                for obj in chunk_qs.iterator(chunk_size=500):
                    data = serializers.serialize("python", [obj])
                    json_str = json.dumps(
                        data[0], indent=indent, ensure_ascii=False, cls=DjangoJSONEncoder
                    )
                    if not first_record:
                        f.write(",\n")
                    f.write(json_str)
                    first_record = False
                    rows += 1
                    last_pk = obj.pk

                    if rows % 500 == 0:
                        done_so_far = written_global + total_written_now + rows
                        bar = _progress_bar(done_so_far, total_global)
                        if is_tty:
                            self.stderr.write(f"\r  {filename:<50} {bar}", ending="")
                        else:
                            self.stderr.write(f"  {filename}: {rows:,} rows\n")
                f.write("\n]\n")

            if rows == 0:
                # Empty chunk means we've exhausted the queryset — remove empty file.
                os.remove(filepath)
                break

            total_written_now += rows
            done_so_far = written_global + total_written_now
            elapsed = time.monotonic() - t0
            self.stderr.write(
                f"\r  {self.style.SUCCESS('✓')} {filename:<50}"
                f" {rows:>8,} rows   [{done_so_far:,}/{total_global:,}]  {elapsed:.1f}s\n"
            )

            if on_chunk:
                self._run_on_chunk(
                    on_chunk,
                    chunk_file=filepath,
                    filename=filename,
                    label=label,
                    part=part,
                    org_id=org_id,
                    output=out_dir,
                    checkpoint_file=_checkpoint_path(out_dir),
                )

            is_last = rows < chunk_size
            next_part = part + 1

            # Update checkpoint AFTER on_chunk so that if on_chunk fails the chunk
            # is retried on the next --resume (the file is still on disk at that point).
            # This also enables resume when files are deleted by on_chunk.
            _save_checkpoint(out_dir, {label: {"last_pk": last_pk, "next_part": next_part}})

            if is_last:
                # Last chunk — fewer rows than requested means no more data.
                break

            part = next_part

        # Write .done sentinel so --resume can skip the entire model without COUNT().
        done_path = os.path.join(out_dir, f"{cidx:03d}_{label}.done")
        with open(done_path, "w") as f:
            f.write(str(count))

        # Mark model as fully done in checkpoint too (covers the case where .done is deleted).
        _save_checkpoint(out_dir, {label: {"done": True}})

        return total_written_now

    def _run_on_chunk(
        self,
        cmd_template: str,
        *,
        chunk_file: str,
        filename: str,
        label: str,
        part: int,
        org_id: int,
        output: str,
        checkpoint_file: str,
    ):
        """
        Runs the --on-chunk command for a single completed chunk file.
        Raises CommandError on non-zero exit so the dump is aborted early
        rather than silently leaving chunks un-processed.
        """
        cmd_str = cmd_template.format(
            chunk_file=chunk_file,
            filename=filename,
            label=label,
            part=part,
            org_id=org_id,
            output=output,
            checkpoint_file=checkpoint_file,
        )
        self.stderr.write(f"  → on-chunk: {cmd_str}\n")
        try:
            result = subprocess.run(shlex.split(cmd_str), check=False)
        except FileNotFoundError as e:
            raise CommandError(f"--on-chunk command not found: {e}")

        if result.returncode != 0:
            raise CommandError(
                f"--on-chunk command exited with code {result.returncode}: {cmd_str}"
            )

    def _write_fixture_with_progress(self, out, label, qs, total, indent, *, all_objects=None):
        """
        Serializes querysets to a JSON fixture stream with an inline progress bar on stderr.

        - Split mode: pass qs + count as total (single model).
        - Single-file mode: pass all_objects as list of (label, qs, count) tuples.
        """
        # Normalise sources to (label, qs) — count was already used to set total
        if all_objects is not None:
            sources = [(lbl, q) for lbl, q, _count in all_objects]
        else:
            sources = [(label, qs)]

        is_tty = hasattr(self.stderr, "isatty") and self.stderr.isatty()

        out.write("[\n")
        first_record = True
        written = 0

        for src_label, src_qs in sources:
            for obj in src_qs.iterator(chunk_size=500):
                data = serializers.serialize("python", [obj])
                json_str = json.dumps(data[0], indent=indent, ensure_ascii=False, cls=DjangoJSONEncoder)

                if not first_record:
                    out.write(",\n")
                out.write(json_str)
                first_record = False
                written += 1

                if written % 100 == 0 or written == total:
                    bar = _progress_bar(written, total)
                    if is_tty:
                        self.stderr.write(f"\r  {src_label:<35} {bar}", ending="")
                    else:
                        self.stderr.write(f"  {src_label}: {written:,}/{total:,}\n")

        out.write("\n]\n")

        if is_tty and total > 0:
            bar = _progress_bar(written, total)
            self.stderr.write(f"\r  {label:<35} {bar}", ending="")
