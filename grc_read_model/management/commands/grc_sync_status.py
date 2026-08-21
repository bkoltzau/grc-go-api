import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from grc_read_model.models import GRCReadModelState, GRCSyncRun


def _timestamp(value):
    return value.isoformat() if value is not None else None


class Command(BaseCommand):
    help = "Report GRC read-model watermarks and recent publication runs without modifying data"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10, help="Number of recent runs to include")
        parser.add_argument("--json", action="store_true", dest="as_json", help="Emit machine-readable JSON")

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit <= 0 or limit > 100:
            raise CommandError("--limit must be between 1 and 100")

        states = list(
            GRCReadModelState.objects.select_related("last_successful_run").order_by(
                "source_system",
                "stream",
            )
        )
        runs = list(GRCSyncRun.objects.order_by("-started_at")[:limit])
        payload = {
            "generated_at": timezone.now().isoformat(),
            "states": [
                {
                    "last_successful_run_id": (
                        str(state.last_successful_run_id)
                        if state.last_successful_run_id is not None
                        else None
                    ),
                    "last_successful_watermark": _timestamp(state.last_successful_watermark),
                    "source_system": state.source_system,
                    "stream": state.stream,
                    "updated_at": state.updated_at.isoformat(),
                }
                for state in states
            ],
            "runs": [
                {
                    "completed_at": _timestamp(run.completed_at),
                    "error_message": run.error_message,
                    "id": str(run.pk),
                    "pipeline": run.pipeline,
                    "rows_deleted": run.rows_deleted,
                    "rows_published": run.rows_published,
                    "rows_seen": run.rows_seen,
                    "source_watermark_from": _timestamp(run.source_watermark_from),
                    "source_watermark_to": _timestamp(run.source_watermark_to),
                    "started_at": run.started_at.isoformat(),
                    "status": run.status,
                }
                for run in runs
            ],
        }

        if options["as_json"]:
            self.stdout.write(json.dumps(payload, sort_keys=True))
            return

        self.stdout.write(f"GRC read-model states: {len(payload['states'])}")
        for state in payload["states"]:
            watermark = state["last_successful_watermark"] or "never"
            run_id = state["last_successful_run_id"] or "none"
            self.stdout.write(
                f"- {state['source_system']}/{state['stream']}: watermark={watermark}, run={run_id}"
            )

        self.stdout.write(f"Recent GRC sync runs: {len(payload['runs'])}")
        for run in payload["runs"]:
            self.stdout.write(
                f"- {run['started_at']} {run['pipeline']} {run['status']} "
                f"seen={run['rows_seen']} published={run['rows_published']} "
                f"deleted={run['rows_deleted']} id={run['id']}"
            )
