import glob
import json
import os
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
        )
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
                "The placeholders {output} and {org_id} are replaced at runtime. "
                "Example: --on-success './scripts/upload_org_fixtures.sh {output} s3://my-bucket'"
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

        if resume and not split:
            raise CommandError("--resume requires --split.")
        if resume and not output_path:
            raise CommandError("--resume requires --output <directory> so the existing files can be found.")

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

        # In resume+split mode, pre-scan the output dir to find already-complete files.
        # Files are named {canonical_idx:03d}_{label}.json, so we can reverse-map by label.
        resume_done: set[str] = set()
        if resume and split:
            out_dir_pre = output_path  # --output is required with --resume
            for path in glob.glob(os.path.join(out_dir_pre, "*.json")):
                if _is_complete_fixture(path):
                    # filename is NNN_app.model.json — strip the leading "NNN_"
                    basename = os.path.basename(path)
                    label_part = basename[4:].removesuffix(".json")
                    resume_done.add(label_part)

        if resume_done:
            self.stderr.write(f"  {len(resume_done)} complete files found — skipping their COUNT().\n\n")

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

            if show_counts or count > 0:
                self.stderr.write(f"  {label:<40} {count:>8,} rows\n")

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
                filename = f"{cidx:03d}_{label}.json"
                filepath = os.path.join(out_dir, filename)

                # count == -1 means the file was already complete at scan time (resume mode)
                if count == -1:
                    skipped += 1
                    self.stderr.write(
                        f"  {self.style.WARNING('–')} {filename:<45}"
                        f"   [{written:,}/{total:,}]  (skipped)\n"
                    )
                    continue

                with open(filepath, "w", encoding="utf-8") as f:
                    self._write_fixture_with_progress(f, label, qs, count, indent)
                written += count
                elapsed = time.monotonic() - t0
                self.stderr.write(
                    f"  {self.style.SUCCESS('✓')} {filename:<45} {count:>6,} rows"
                    f"   [{written:,}/{total:,}]  {elapsed:.1f}s\n"
                )
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
        in canonical FK-safe load order (sorted by canonical index).
        """
        complete = []
        for path in glob.glob(os.path.join(out_dir, "*.json")):
            if _is_complete_fixture(path):
                complete.append(path)

        complete.sort(key=lambda p: int(os.path.basename(p).split("_", 1)[0]))

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
