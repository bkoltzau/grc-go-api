from django.core.management.base import BaseCommand, CommandError

from grc_read_model.grc_dwh_contract import GRCDWHContractError, check_grc_gold_contract
from grc_read_model.grc_dwh_reader import GRCDWHConfigurationError, GRCDWHReadError, GRCDWHSettings


class Command(BaseCommand):
    help = "Check the read-only GRC Gold schema contract used by implemented sync commands"

    def add_arguments(self, parser):
        parser.add_argument(
            "--scope",
            action="append",
            choices=("reference", "project"),
            help="Contract scope to inspect; may be repeated (default: both)",
        )

    def handle(self, *args, **options):
        scopes = options["scope"] or ("reference", "project")
        try:
            dwh_settings = GRCDWHSettings.from_env()
            report = check_grc_gold_contract(dwh_settings, scopes=scopes)
            report.raise_for_errors()
        except (GRCDWHConfigurationError, GRCDWHReadError, GRCDWHContractError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"GRC Gold contract is compatible for scope(s): {', '.join(report.scopes)}"
            )
        )
