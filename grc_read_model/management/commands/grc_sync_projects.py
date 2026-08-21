from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from grc_read_model.grc_dwh_reader import GRCDWHReadError, GRCDWHSettings
from grc_read_model.grc_project_dwh_reader import load_grc_project_snapshot
from grc_read_model.grc_project_sync import publish_grc_project_snapshot


class Command(BaseCommand):
    help = "Publish one complete GRC Gold Project snapshot into the existing GO read model"

    def add_arguments(self, parser):
        parser.add_argument("--source-system", default="grc_gold")
        parser.add_argument("--stream", default="project")
        parser.add_argument("--pipeline", default="grc_project")

    def handle(self, *args, **options):
        if settings.DJANGO_READ_ONLY:
            raise CommandError("grc_sync_projects requires a write-enabled publisher process")

        try:
            dwh_settings = GRCDWHSettings.from_env()
            snapshot = load_grc_project_snapshot(dwh_settings)
            sync_run = publish_grc_project_snapshot(
                snapshot,
                source_system=options["source_system"],
                stream=options["stream"],
                pipeline=options["pipeline"],
            )
        except (ValueError, GRCDWHReadError, DatabaseError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                "Published GRC Gold Project snapshot "
                f"(seen={sync_run.rows_seen}, published={sync_run.rows_published}, "
                f"deleted={sync_run.rows_deleted}, watermark={sync_run.source_watermark_to.isoformat()})"
            )
        )
