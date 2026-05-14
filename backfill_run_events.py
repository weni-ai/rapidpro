#!/usr/bin/env python3
"""
Backfill resumable de run_started/run_ended events no DynamoDB a partir
de flows_flowrun. Equivalente à migration 0395, mas:

- Persiste checkpoint no Postgres (sobrevive a recriação de pod)
- Mostra progresso a cada batch
- Pode ser interrompido e retomado a qualquer momento

Uso:
    python backfill_run_events.py             # roda a partir do checkpoint
    python backfill_run_events.py --reset     # apaga checkpoint e roda do zero
    python backfill_run_events.py --status    # mostra estado atual sem rodar
"""

import argparse
import os
import sys
import time
from datetime import datetime

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "temba.settings")

import django

django.setup()

from django.db import connection
from django.db.models import Prefetch

from temba.contacts.models import Contact
from temba.flows.models import Flow, FlowRun
from temba.utils import dynamo
from temba.utils.uuid import uuid7

BATCH_SIZE = 100
JOB_NAME = "backfill_run_events"

STATUS_MAP = {
    "A": "active",
    "W": "waiting",
    "C": "completed",
    "I": "interrupted",
    "X": "expired",
    "F": "failed",
}


def ensure_checkpoint_table():
    with connection.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS _migration_checkpoints (
                job_name TEXT PRIMARY KEY,
                last_id BIGINT,
                num_written BIGINT NOT NULL DEFAULT 0,
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                finished_at TIMESTAMPTZ
            )
            """
        )


def load_checkpoint():
    with connection.cursor() as cur:
        cur.execute(
            "SELECT last_id, num_written, started_at, finished_at "
            "FROM _migration_checkpoints WHERE job_name = %s",
            [JOB_NAME],
        )
        row = cur.fetchone()
    return row


def save_checkpoint(last_id, num_written):
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO _migration_checkpoints (job_name, last_id, num_written)
            VALUES (%s, %s, %s)
            ON CONFLICT (job_name) DO UPDATE SET
                last_id = EXCLUDED.last_id,
                num_written = EXCLUDED.num_written,
                updated_at = NOW()
            """,
            [JOB_NAME, last_id, num_written],
        )


def mark_finished():
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE _migration_checkpoints SET finished_at = NOW() WHERE job_name = %s",
            [JOB_NAME],
        )


def reset_checkpoint():
    with connection.cursor() as cur:
        cur.execute("DELETE FROM _migration_checkpoints WHERE job_name = %s", [JOB_NAME])


def show_status():
    ensure_checkpoint_table()
    cp = load_checkpoint()
    if not cp:
        print("Sem checkpoint registrado.")
        return
    last_id, num_written, started_at, finished_at = cp
    print(f"Job:           {JOB_NAME}")
    print(f"Iniciado em:   {started_at}")
    print(f"Last id:       {last_id}")
    print(f"Itens escritos: {num_written:,}")
    print(f"Finalizado em: {finished_at if finished_at else 'EM ANDAMENTO'}")


def run():
    ensure_checkpoint_table()

    cp = load_checkpoint()
    if cp and cp[3] is not None:
        print(f"Job '{JOB_NAME}' já foi finalizado em {cp[3]}.")
        print("Use --reset se precisar reexecutar do zero.")
        return

    before_id = cp[0] if cp else None
    num_written = cp[1] if cp else 0

    if before_id:
        print(f"Retomando do checkpoint: before_id={before_id}, já escritos={num_written:,}")
    else:
        print("Iniciando do zero (sem checkpoint anterior)")

    qs = (
        FlowRun.objects.filter(
            org__is_active=True,
            contact__is_active=True,
            flow__is_active=True,
            flow__is_system=False,
        )
        .prefetch_related(
            Prefetch("contact", Contact.objects.only("uuid")),
            Prefetch("flow", Flow.objects.only("uuid", "name")),
        )
    )

    start_time = time.time()
    batch_num = 0

    while True:
        batch_qs = qs.order_by("-id")
        if before_id is not None:
            batch_qs = batch_qs.filter(id__lt=before_id)

        batch = list(batch_qs[:BATCH_SIZE])
        if not batch:
            break

        batch_num += 1

        try:
            with dynamo.HISTORY.batch_writer() as writer:
                for run in batch:
                    flow_ref = {"uuid": str(run.flow.uuid), "name": run.flow.name}

                    writer.put_item(
                        {
                            "PK": f"con#{run.contact.uuid}",
                            "SK": f"evt#{uuid7(run.created_on)}",
                            "OrgID": run.org_id,
                            "Data": {
                                "type": "run_started",
                                "created_on": run.created_on.isoformat(),
                                "run_uuid": str(run.uuid),
                                "flow": flow_ref,
                            },
                        }
                    )
                    num_written += 1

                    if run.exited_on:
                        writer.put_item(
                            {
                                "PK": f"con#{run.contact.uuid}",
                                "SK": f"evt#{uuid7(run.exited_on)}",
                                "OrgID": run.org_id,
                                "Data": {
                                    "type": "run_ended",
                                    "created_on": run.exited_on.isoformat(),
                                    "run_uuid": str(run.uuid),
                                    "flow": flow_ref,
                                    "status": STATUS_MAP[run.status],
                                },
                            }
                        )
                        num_written += 1

                    before_id = run.id
        except Exception as e:
            print(f"\nERRO no batch {batch_num}: {e}")
            print(f"Checkpoint salvo em before_id={before_id}, num_written={num_written:,}")
            save_checkpoint(before_id, num_written)
            sys.exit(1)

        save_checkpoint(before_id, num_written)

        last_id = batch[-1].id
        last_created_on = batch[-1].created_on
        elapsed = time.time() - start_time
        rate = num_written / elapsed if elapsed > 0 else 0

        print(
            f"[{datetime.utcnow().isoformat(timespec='seconds')}Z] "
            f"Batch {batch_num} | "
            f"escritos: {num_written:,} | "
            f"last id: {last_id} | "
            f"created_on: {last_created_on.isoformat()} | "
            f"taxa: {rate:,.0f} items/s",
            flush=True,
        )

    mark_finished()
    elapsed = time.time() - start_time
    print(f"\nConcluído em {elapsed/60:.1f} min | Total escrito: {num_written:,}")


def main():
    parser = argparse.ArgumentParser(description="Backfill resumable de run events")
    parser.add_argument("--reset", action="store_true", help="Apaga checkpoint e começa do zero")
    parser.add_argument("--status", action="store_true", help="Mostra estado do checkpoint sem rodar")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    if args.reset:
        ensure_checkpoint_table()
        reset_checkpoint()
        print("Checkpoint apagado.")

    run()


if __name__ == "__main__":
    main()
