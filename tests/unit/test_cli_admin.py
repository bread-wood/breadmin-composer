"""Unit tests for composer admin commands: health and cost."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from composer.cli import cost, health_cmd
from composer.health import CheckResult, HealthReport

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_ENV = {
    "ANTHROPIC_API_KEY": "sk-ant-test-key",
    "GITHUB_TOKEN": "ghp-test-token",
}


def _make_pass_report() -> HealthReport:
    """Return a HealthReport with all checks passing."""
    return HealthReport(
        checks=[
            CheckResult(name="Check A", status="pass", message="All good."),
            CheckResult(name="Check B", status="pass", message="Also good."),
        ],
        overall="pass",
        fatal=False,
    )


def _make_warn_report() -> HealthReport:
    """Return a HealthReport with a warning but no fatal."""
    return HealthReport(
        checks=[
            CheckResult(name="Check A", status="pass", message="Good."),
            CheckResult(
                name="Check B",
                status="warn",
                message="Something is off.",
                remediation="Fix it.",
            ),
        ],
        overall="warn",
        fatal=False,
    )


def _make_fatal_report() -> HealthReport:
    """Return a HealthReport with a fatal failure."""
    return HealthReport(
        checks=[
            CheckResult(
                name="Check A",
                status="fail",
                message="Critical failure.",
                remediation="Do something.",
            ),
        ],
        overall="fail",
        fatal=True,
    )


def _make_cost_entry(
    repo: str = "owner/repo",
    stage: str = "research",
    input_tokens: int = 1000,
    output_tokens: int = 200,
    total_cost_usd: float | None = 1.50,
) -> dict[str, Any]:
    return {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "session_id": "session-1",
        "run_id": "run-1",
        "repo": repo,
        "stage": stage,
        "issue_number": None,
        "model": "claude-sonnet-4-6",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "num_turns": 5,
        "duration_ms": 1000,
        "is_error": False,
        "error_subtype": None,
        "total_cost_usd": total_cost_usd,
        "auth_mode": "subscription",
        "web_search_requests": 0,
    }


# ---------------------------------------------------------------------------
# health_cmd tests
# ---------------------------------------------------------------------------


class TestHealthCmd:
    def test_exits_0_on_pass_report(self) -> None:
        """health_cmd exits 0 when the report has no fatal."""
        runner = CliRunner()
        with (
            patch.dict("os.environ", MINIMAL_ENV, clear=False),
            patch("composer.cli.load_config") as mock_load_config,
            patch("composer.cli.session.load", return_value=None),
            patch("composer.cli.health.check_all", return_value=_make_pass_report()),
            patch("composer.cli.health.format_report", return_value="All good."),
        ):
            mock_cfg = MagicMock()
            mock_cfg.checkpoint_dir.expanduser.return_value = Path("/tmp/checkpoints")
            mock_load_config.return_value = mock_cfg

            result = runner.invoke(health_cmd, [])

        assert result.exit_code == 0

    def test_exits_0_on_warn_report(self) -> None:
        """health_cmd exits 0 when the report has warnings but no fatal."""
        runner = CliRunner()
        with (
            patch.dict("os.environ", MINIMAL_ENV, clear=False),
            patch("composer.cli.load_config") as mock_load_config,
            patch("composer.cli.session.load", return_value=None),
            patch("composer.cli.health.check_all", return_value=_make_warn_report()),
            patch("composer.cli.health.format_report", return_value="Warnings present."),
        ):
            mock_cfg = MagicMock()
            mock_cfg.checkpoint_dir.expanduser.return_value = Path("/tmp/checkpoints")
            mock_load_config.return_value = mock_cfg

            result = runner.invoke(health_cmd, [])

        assert result.exit_code == 0

    def test_exits_1_on_fatal_report(self) -> None:
        """health_cmd exits 1 when the report has a fatal check."""
        runner = CliRunner()
        with (
            patch.dict("os.environ", MINIMAL_ENV, clear=False),
            patch("composer.cli.load_config") as mock_load_config,
            patch("composer.cli.session.load", return_value=None),
            patch("composer.cli.health.check_all", return_value=_make_fatal_report()),
            patch("composer.cli.health.format_report", return_value="Fatal failure."),
        ):
            mock_cfg = MagicMock()
            mock_cfg.checkpoint_dir.expanduser.return_value = Path("/tmp/checkpoints")
            mock_load_config.return_value = mock_cfg

            result = runner.invoke(health_cmd, [])

        assert result.exit_code == 1

    def test_json_flag_outputs_valid_json(self) -> None:
        """health_cmd --json outputs valid JSON containing the report fields."""
        runner = CliRunner()
        report = _make_pass_report()
        with (
            patch.dict("os.environ", MINIMAL_ENV, clear=False),
            patch("composer.cli.load_config") as mock_load_config,
            patch("composer.cli.session.load", return_value=None),
            patch("composer.cli.health.check_all", return_value=report),
        ):
            mock_cfg = MagicMock()
            mock_cfg.checkpoint_dir.expanduser.return_value = Path("/tmp/checkpoints")
            mock_load_config.return_value = mock_cfg

            result = runner.invoke(health_cmd, ["--json"])

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "checks" in parsed
        assert "overall" in parsed
        assert "fatal" in parsed
        assert parsed["overall"] == "pass"
        assert parsed["fatal"] is False
        assert len(parsed["checks"]) == 2

    def test_json_flag_outputs_fatal_field_true(self) -> None:
        """health_cmd --json sets fatal=true in JSON on fatal report."""
        runner = CliRunner()
        report = _make_fatal_report()
        with (
            patch.dict("os.environ", MINIMAL_ENV, clear=False),
            patch("composer.cli.load_config") as mock_load_config,
            patch("composer.cli.session.load", return_value=None),
            patch("composer.cli.health.check_all", return_value=report),
        ):
            mock_cfg = MagicMock()
            mock_cfg.checkpoint_dir.expanduser.return_value = Path("/tmp/checkpoints")
            mock_load_config.return_value = mock_cfg

            result = runner.invoke(health_cmd, ["--json"])

        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert parsed["fatal"] is True
        assert parsed["overall"] == "fail"

    def test_text_output_calls_format_report(self) -> None:
        """health_cmd without --json calls health.format_report and prints result."""
        runner = CliRunner()
        formatted = "breadmin-composer health check\n✓ Check A\nOverall: PASS"
        with (
            patch.dict("os.environ", MINIMAL_ENV, clear=False),
            patch("composer.cli.load_config") as mock_load_config,
            patch("composer.cli.session.load", return_value=None),
            patch("composer.cli.health.check_all", return_value=_make_pass_report()),
            patch("composer.cli.health.format_report", return_value=formatted),
        ):
            mock_cfg = MagicMock()
            mock_cfg.checkpoint_dir.expanduser.return_value = Path("/tmp/checkpoints")
            mock_load_config.return_value = mock_cfg

            result = runner.invoke(health_cmd, [])

        assert result.exit_code == 0
        assert formatted in result.output


# ---------------------------------------------------------------------------
# cost tests
# ---------------------------------------------------------------------------


class TestCostCmd:
    def test_no_entries_prints_message(self) -> None:
        """cost prints 'No cost entries found.' when ledger is empty."""
        runner = CliRunner()
        with (
            patch.dict("os.environ", MINIMAL_ENV, clear=False),
            patch("composer.cli.load_config") as mock_load_config,
            patch("composer.cli.logger.read_cost_ledger", return_value=[]),
        ):
            mock_cfg = MagicMock()
            mock_cfg.log_dir.expanduser.return_value = Path("/tmp/logs")
            mock_load_config.return_value = mock_cfg

            result = runner.invoke(cost, [])

        assert result.exit_code == 0
        assert "No cost entries found." in result.output

    def test_aggregates_entries_correctly(self) -> None:
        """cost aggregates input_tokens, output_tokens, and cost_usd by repo and stage."""
        runner = CliRunner()
        entries = [
            _make_cost_entry(
                repo="owner/repo",
                stage="research",
                input_tokens=1000,
                output_tokens=200,
                total_cost_usd=1.00,
            ),
            _make_cost_entry(
                repo="owner/repo",
                stage="research",
                input_tokens=500,
                output_tokens=100,
                total_cost_usd=0.50,
            ),
        ]
        with (
            patch.dict("os.environ", MINIMAL_ENV, clear=False),
            patch("composer.cli.load_config") as mock_load_config,
            patch("composer.cli.logger.read_cost_ledger", return_value=entries),
        ):
            mock_cfg = MagicMock()
            mock_cfg.log_dir.expanduser.return_value = Path("/tmp/logs")
            mock_load_config.return_value = mock_cfg

            result = runner.invoke(cost, [])

        assert result.exit_code == 0
        output = result.output
        # Should show 2 sessions aggregated
        assert "2" in output
        # Should show summed tokens
        assert "1,500" in output  # input_tokens sum
        assert "300" in output  # output_tokens sum
        # Should show summed cost
        assert "$1.50" in output
        # Grand total
        assert "Grand total" in output

    def test_multiple_repos_printed_separately(self) -> None:
        """cost prints a separate table per repo."""
        runner = CliRunner()
        entries = [
            _make_cost_entry(repo="owner/repo1", stage="research"),
            _make_cost_entry(repo="owner/repo2", stage="impl"),
        ]
        with (
            patch.dict("os.environ", MINIMAL_ENV, clear=False),
            patch("composer.cli.load_config") as mock_load_config,
            patch("composer.cli.logger.read_cost_ledger", return_value=entries),
        ):
            mock_cfg = MagicMock()
            mock_cfg.log_dir.expanduser.return_value = Path("/tmp/logs")
            mock_load_config.return_value = mock_cfg

            result = runner.invoke(cost, [])

        assert result.exit_code == 0
        assert "Repo: owner/repo1" in result.output
        assert "Repo: owner/repo2" in result.output

    def test_repo_filter_applies(self) -> None:
        """cost --repo filters entries to that repo only."""
        runner = CliRunner()
        # read_cost_ledger already handles filtering, but we pass the flag
        entries = [
            _make_cost_entry(repo="owner/repo1", stage="research"),
        ]
        with (
            patch.dict("os.environ", MINIMAL_ENV, clear=False),
            patch("composer.cli.load_config") as mock_load_config,
            patch("composer.cli.logger.read_cost_ledger", return_value=entries) as mock_read,
        ):
            mock_cfg = MagicMock()
            mock_cfg.log_dir.expanduser.return_value = Path("/tmp/logs")
            mock_load_config.return_value = mock_cfg

            result = runner.invoke(cost, ["--repo", "owner/repo1"])

        assert result.exit_code == 0
        # Verify read_cost_ledger was called with repo filter
        mock_read.assert_called_once_with(
            Path("/tmp/logs"),
            repo="owner/repo1",
            stage=None,
        )
        assert "Repo: owner/repo1" in result.output

    def test_stage_filter_applies(self) -> None:
        """cost --stage filters entries to that stage only."""
        runner = CliRunner()
        entries = [
            _make_cost_entry(repo="owner/repo", stage="research"),
        ]
        with (
            patch.dict("os.environ", MINIMAL_ENV, clear=False),
            patch("composer.cli.load_config") as mock_load_config,
            patch("composer.cli.logger.read_cost_ledger", return_value=entries) as mock_read,
        ):
            mock_cfg = MagicMock()
            mock_cfg.log_dir.expanduser.return_value = Path("/tmp/logs")
            mock_load_config.return_value = mock_cfg

            result = runner.invoke(cost, ["--stage", "research"])

        assert result.exit_code == 0
        # Verify read_cost_ledger was called with stage filter
        mock_read.assert_called_once_with(
            Path("/tmp/logs"),
            repo=None,
            stage="research",
        )

    def test_null_cost_treated_as_zero(self) -> None:
        """cost treats total_cost_usd=None as 0 in aggregation."""
        runner = CliRunner()
        entries = [
            _make_cost_entry(
                repo="owner/repo",
                stage="research",
                total_cost_usd=None,
            ),
        ]
        with (
            patch.dict("os.environ", MINIMAL_ENV, clear=False),
            patch("composer.cli.load_config") as mock_load_config,
            patch("composer.cli.logger.read_cost_ledger", return_value=entries),
        ):
            mock_cfg = MagicMock()
            mock_cfg.log_dir.expanduser.return_value = Path("/tmp/logs")
            mock_load_config.return_value = mock_cfg

            result = runner.invoke(cost, [])

        assert result.exit_code == 0
        assert "$0.00" in result.output

    def test_grand_total_line_present(self) -> None:
        """cost prints a grand total line at the bottom."""
        runner = CliRunner()
        entries = [
            _make_cost_entry(repo="owner/repo", stage="research", total_cost_usd=2.50),
        ]
        with (
            patch.dict("os.environ", MINIMAL_ENV, clear=False),
            patch("composer.cli.load_config") as mock_load_config,
            patch("composer.cli.logger.read_cost_ledger", return_value=entries),
        ):
            mock_cfg = MagicMock()
            mock_cfg.log_dir.expanduser.return_value = Path("/tmp/logs")
            mock_load_config.return_value = mock_cfg

            result = runner.invoke(cost, [])

        assert result.exit_code == 0
        assert "Grand total across 1 repo(s): $2.50" in result.output

    def test_no_startup_sequence_called(self) -> None:
        """cost does NOT call startup_sequence (no orchestrator lock for admin cmd)."""
        runner = CliRunner()
        with (
            patch.dict("os.environ", MINIMAL_ENV, clear=False),
            patch("composer.cli.load_config") as mock_load_config,
            patch("composer.cli.logger.read_cost_ledger", return_value=[]),
            patch("composer.cli.startup_sequence") as mock_startup,
        ):
            mock_cfg = MagicMock()
            mock_cfg.log_dir.expanduser.return_value = Path("/tmp/logs")
            mock_load_config.return_value = mock_cfg

            runner.invoke(cost, [])

        mock_startup.assert_not_called()
