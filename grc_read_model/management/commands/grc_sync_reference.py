from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from grc_read_model.grc_dwh_reader import (
    GRCDWHReadError,
    GRCDWHSettings,
    load_grc_reference_snapshot,
)
from grc_read_model.grc_reference_sync import publish_grc_reference_snapshot


class Command(BaseCommand):
    help = "Read a consistent GRC Gold reference snapshot and publish it into the GO serving cache"

    def add_arguments(self, parser):
        parser.add_argument("--source-system", default="grc_gold")
        parser.add_argument("--stream", default="country_district_event")
        parser.add_argument("--pipeline", default="grc_reference")

    def handle(self, *args, **options):
        if settings.DJANGO_READ_ONLY:
            raise CommandError(
                "grc_sync_reference requires a separately authorized process with DJANGO_READ_ONLY=false"
            )

        try:
            dwh_settings = GRCDWHSettings.from_env()
            snapshot = load_grc_reference_snapshot(dwh_settings)
            sync_run = publish_grc_reference_snapshot(
                snapshot,
                source_system=options["source_system"],
                stream=options["stream"],
                pipeline=options["pipeline"],
            )
        except (ValueError, GRCDWHReadError, DatabaseError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"GRC reference sync {sync_run.pk} succeeded: "
                f"seen={sync_run.rows_seen}, published={sync_run.rows_published}, "
                f"deleted={sync_run.rows_deleted}, watermark={sync_run.source_watermark_to.isoformat()}"
            )
        )
