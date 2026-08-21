from io import StringIO
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from grc_read_model.grc_dwh_contract import GRCDWHContractError


class GRCDWHContractCommandTest(SimpleTestCase):
    @patch("grc_read_model.management.commands.grc_check_dwh_contract.check_grc_gold_contract")
    @patch("grc_read_model.management.commands.grc_check_dwh_contract.GRCDWHSettings.from_env")
    def test_checks_selected_scopes_without_requiring_go_write_mode(self, load_settings, check_contract):
        dwh_settings = Mock()
        report = Mock(scopes=("project",))
        load_settings.return_value = dwh_settings
        check_contract.return_value = report
        stdout = StringIO()

        call_command("grc_check_dwh_contract", scope=["project"], stdout=stdout)

        check_contract.assert_called_once_with(dwh_settings, scopes=["project"])
        report.raise_for_errors.assert_called_once_with()
        self.assertIn("project", stdout.getvalue())

    @patch("grc_read_model.management.commands.grc_check_dwh_contract.check_grc_gold_contract")
    @patch("grc_read_model.management.commands.grc_check_dwh_contract.GRCDWHSettings.from_env")
    def test_reports_contract_failure_as_command_error(self, load_settings, check_contract):
        load_settings.return_value = Mock()
        report = Mock()
        report.raise_for_errors.side_effect = GRCDWHContractError(
            "missing column: factproject.projectname"
        )
        check_contract.return_value = report

        with self.assertRaisesRegex(CommandError, "factproject.projectname"):
            call_command("grc_check_dwh_contract")
