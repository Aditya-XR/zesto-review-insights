"""Background worker: picks up queued analysis runs and finds their themes.

Postgres is the queue (no Redis/Celery to run): a run row with status=queued is a job, claimed
with SELECT ... FOR UPDATE SKIP LOCKED so two workers never take the same run.
"""
import logging
import time

from django.core.management.base import BaseCommand
from django.db import DatabaseError, transaction

from reviews import llm
from reviews.models import AnalysisRun
from reviews.pipeline import process_run

log = logging.getLogger(__name__)
S = AnalysisRun.Status


def claim_next_run():
    with transaction.atomic():
        run = (
            AnalysisRun.objects.select_for_update(skip_locked=True)
            .filter(status=S.QUEUED)
            .order_by("created_at")
            .first()
        )
        if run:
            run.status = S.DISCOVERING
            run.save(update_fields=["status"])
        return run


def requeue_interrupted():
    """On start-up: resume runs a previous worker was killed in the middle of, and runs that
    were waiting for an LLM key if one is configured now."""
    stuck = [S.DISCOVERING, S.CLASSIFYING] + ([S.NEEDS_KEY] if llm.is_configured() else [])
    n = AnalysisRun.objects.filter(status__in=stuck).update(status=S.QUEUED, error="")
    if n:
        log.info("re-queued %d run(s)", n)


class Command(BaseCommand):
    help = "Process queued review analysis runs."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="process what's queued, then exit")

    def handle(self, *args, once=False, **options):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s worker %(levelname)s %(message)s")
        while True:  # wait for the backend container to finish migrating
            try:
                requeue_interrupted()
                break
            except DatabaseError as exc:
                log.info("database not ready yet (%s), retrying", exc.__class__.__name__)
                time.sleep(2)
        cfg = llm.get_config()
        log.info("worker started; LLM: %s", f"{cfg.key_name} / {cfg.model}" if cfg else "NOT CONFIGURED")

        while True:
            run = claim_next_run()
            if run is None:
                if once:
                    return
                time.sleep(1)
                continue
            log.info("processing run %s (%d reviews)", run.pk, run.total_reviews)
            try:
                process_run(run)
            except Exception as exc:  # keep the worker alive whatever happens in one run
                log.exception("run %s crashed", run.pk)
                AnalysisRun.objects.filter(pk=run.pk).update(status=S.FAILED, error=f"Unexpected error: {exc}")
            run.refresh_from_db()
            log.info("run %s finished: %s %s", run.pk, run.status, run.error)
