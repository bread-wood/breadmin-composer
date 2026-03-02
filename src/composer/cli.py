"""CLI entry points for breadmin-composer.

Entry points:
  impl-worker      → impl_worker()
  research-worker  → research_worker()
  design-worker    → design_worker()
  plan-issues      → plan_issues()
  composer         → composer()
"""

from __future__ import annotations

import os
from pathlib import Path

import click

from composer import health, logger, session
from composer.config import (
    Config,
    OrchestratorNestingError,
    load_config,
)
from composer.health import FatalHealthCheckError
from composer.session import Checkpoint

# ---------------------------------------------------------------------------
# Startup sequence
# ---------------------------------------------------------------------------


def startup_sequence(
    config: Config,
    checkpoint_path: Path,
    milestone: str = "",
    stage: str = "",
    resume_run_id: str | None = None,
) -> tuple[Config, Checkpoint]:
    """Shared startup sequence run by every worker before entering its main loop.

    Steps:
      1. CLAUDECODE nesting guard
      2. Health checks (abort on fatal, print warn and continue)
      3. Load or create checkpoint
      4. Validate resume_run_id if supplied
      5. Acquire orchestrator lock
      6. Log stage_start event
      7. Return (config, checkpoint)

    Args:
        config:           Validated Config instance.
        checkpoint_path:  Path to the checkpoint JSON file.
        milestone:        Active milestone name (forwarded to session.new).
        stage:            Pipeline stage (forwarded to session.new).
        resume_run_id:    If provided, validate the checkpoint run_id matches.

    Returns:
        A (Config, Checkpoint) tuple ready for the worker loop.

    Raises:
        OrchestratorNestingError: If CLAUDECODE=1 is set in the environment.
        FatalHealthCheckError:    If any health check is fatal.
        ValueError:               If resume_run_id is provided and does not
                                  match the checkpoint's run_id.
    """
    # Step 1: Nesting guard (also checked by load_config, but be explicit here)
    if os.environ.get("CLAUDECODE") == "1":
        raise OrchestratorNestingError(
            "Cannot nest orchestrator invocations.\n\n"
            "CLAUDECODE=1 is set in the current environment, which means this process is\n"
            "already running inside a Claude Code session.\n\n"
            "To run the conductor, open a plain terminal (not a Claude Code session) and\n"
            "invoke it from there."
        )

    # Step 2: Health checks
    report = health.check_all(config)
    if report.fatal:
        click.echo(health.format_report(report))
        raise FatalHealthCheckError("Fatal health check failure — see report above.")
    if report.overall == "warn":
        click.echo(health.format_report(report))

    # Step 3: Load or create checkpoint
    chk = session.load(checkpoint_path)
    if chk is None:
        chk = session.new(
            repo=config.github_repo or "",
            default_branch=config.default_branch,
            milestone=milestone,
            stage=stage,
        )

    # Step 4: Resume validation
    if resume_run_id is not None and chk.run_id != resume_run_id:
        raise ValueError(
            f"Checkpoint run_id mismatch: checkpoint has {chk.run_id!r}, "
            f"but --resume specified {resume_run_id!r}."
        )

    # Step 5: Acquire orchestrator lock
    health.acquire_orchestrator_lock(config, chk.run_id)

    # Step 6: Log stage_start event
    logger.log_conductor_event(
        run_id=chk.run_id,
        phase="init",
        event_type="stage_start",
        payload={
            "worker_type": stage,
            "milestone": milestone,
            "stage": stage,
        },
        log_dir=config.log_dir.expanduser(),
    )

    # Step 7: Return
    return config, chk


# ---------------------------------------------------------------------------
# Skill injection
# ---------------------------------------------------------------------------


def inject_skill(skill_name: str, base_prompt: str) -> str:
    """Read skills/<skill_name>.md, apply headless policy, and prepend to base_prompt.

    Args:
        skill_name:  Filename stem without extension (e.g. "research-worker").
        base_prompt: Session-specific context assembled by the caller. Runtime
                     values (repo, milestone, issue number, date) must already
                     be substituted by the caller.

    Returns:
        Full prompt string ready to pass to runner.run() as the -p argument.

    Raises:
        FileNotFoundError: If skills/<skill_name>.md does not exist.
    """
    skill_path = Path(__file__).parent / "skills" / f"{skill_name}.md"
    skill_text = skill_path.read_text(encoding="utf-8")
    skill_text = _apply_headless_policy(skill_text)
    return base_prompt + "\n\n---\n\n" + skill_text


def _apply_headless_policy(text: str) -> str:
    """Replace interactive confirmation gates with auto-resolve directives.

    Applies a best-effort set of string substitutions so that skill prompts
    work correctly in headless ``claude -p`` sessions where no user is present.

    Args:
        text: Raw skill markdown text.

    Returns:
        Text with interactive gates replaced.
    """
    replacements = [
        ("Ask the user", "Auto-resolve:"),
        ("ask the user", "Auto-resolve:"),
        ("confirm with user", "Auto-resolve:"),
        ("Wait for approval", "Proceed automatically:"),
        ("await user confirmation", "Proceed automatically:"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


# ---------------------------------------------------------------------------
# UsageGovernor
# ---------------------------------------------------------------------------


class UsageGovernor:
    """Enforces concurrency limits and manages rate-limit backoff for agent dispatch.

    Sits between the issue queue and the dispatch loop. Instantiated once per
    worker invocation. Three dispatch gates are checked in order:

      1. Backoff gate   — if session.is_backing_off() is True, return False.
      2. Concurrency    — active + n > limit, return False.
      3. Budget         — total_cost_usd >= max_budget_usd (when set), return False.

    Concurrency limits by subscription tier:
        pro     -> 2
        max     -> 3
        max20x  -> 5
    """

    def __init__(self, config: Config, checkpoint: Checkpoint) -> None:
        self.config = config
        self.checkpoint = checkpoint
        self._active_agents: int = 0
        self._total_cost_usd: float = 0.0

    def can_dispatch(self, n_agents: int = 1) -> bool:
        """Return True if it is safe to dispatch n_agents new agents right now.

        Checks three gates in order: backoff, concurrency, budget.

        Args:
            n_agents: Number of agents about to be dispatched.

        Returns:
            True if all gates pass; False if any gate blocks.
        """
        # Gate 1: backoff
        if session.is_backing_off(self.checkpoint):
            return False

        # Gate 2: concurrency
        limits: dict[str, int] = {"pro": 2, "max": 3, "max20x": 5}
        limit = self.config.max_concurrency or limits.get(self.config.subscription_tier, 3)
        if self._active_agents + n_agents > limit:
            return False

        # Gate 3: budget (only applies when max_budget_usd is set)
        if self.config.max_budget_usd and self._total_cost_usd >= self.config.max_budget_usd:
            return False

        return True

    def record_dispatch(self, n: int = 1) -> None:
        """Record that n agents have been dispatched."""
        self._active_agents += n

    def record_completion(self, n: int = 1) -> None:
        """Record that n agents have completed."""
        self._active_agents = max(0, self._active_agents - n)

    def record_429(self, attempt: int) -> None:
        """Record a rate-limit (429) response and set exponential backoff.

        Args:
            attempt: Zero-indexed retry attempt number.
        """
        session.set_backoff(
            self.checkpoint,
            attempt,
            self.config.backoff_base_seconds,
            self.config.backoff_max_minutes * 60,
        )

    def record_result(self, run_result: object) -> None:
        """Update running cost total from a RunResult.

        Args:
            run_result: A RunResult instance from runner.run(). Uses the
                        total_cost_usd attribute when present.
        """
        cost = getattr(run_result, "total_cost_usd", None)
        if cost:
            self._total_cost_usd += cost


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


@click.command("impl-worker")
@click.option("--repo", required=True, help="Target repo in OWNER/REPO format")
@click.option("--milestone", default=None, help="Milestone name to process")
@click.option("--model", default=None, help="Override Claude model")
@click.option("--max-budget", type=float, default=None, help="USD budget cap")
@click.option("--max-turns", type=int, default=None, help="Max turns per invocation")
@click.option("--dry-run", is_flag=True, help="Print invocation without executing")
@click.option("--resume", default=None, help="Resume a previous session by ID")
def impl_worker(
    repo: str,
    milestone: str | None,
    model: str | None,
    max_budget: float | None,
    max_turns: int | None,
    dry_run: bool,
    resume: str | None,
) -> None:
    """Process implementation issues headlessly via claude -p."""
    overrides: dict = {"github_repo": repo}
    if model:
        overrides["model"] = model
    if max_budget is not None:
        overrides["max_budget_usd"] = max_budget
    if max_turns is not None:
        overrides["max_turns"] = max_turns

    config = load_config(**overrides)
    checkpoint_path = config.checkpoint_dir.expanduser() / "current.json"

    _config, _checkpoint = startup_sequence(
        config=config,
        checkpoint_path=checkpoint_path,
        milestone=milestone or "",
        stage="impl",
        resume_run_id=resume,
    )
    click.echo("Not yet implemented")


@click.command("research-worker")
@click.option("--repo", required=True, help="Target repo in OWNER/REPO format")
@click.option("--milestone", required=True, help="Milestone name to process")
@click.option("--model", default=None, help="Override Claude model")
@click.option("--max-budget", type=float, default=None, help="USD budget cap")
@click.option("--max-turns", type=int, default=None, help="Max turns per invocation")
@click.option("--dry-run", is_flag=True, help="Print invocation without executing")
@click.option("--resume", default=None, help="Resume a previous session by ID")
def research_worker(
    repo: str,
    milestone: str,
    model: str | None,
    max_budget: float | None,
    max_turns: int | None,
    dry_run: bool,
    resume: str | None,
) -> None:
    """Process research issues for a milestone headlessly via claude -p."""
    overrides: dict = {"github_repo": repo}
    if model:
        overrides["model"] = model
    if max_budget is not None:
        overrides["max_budget_usd"] = max_budget
    if max_turns is not None:
        overrides["max_turns"] = max_turns

    config = load_config(**overrides)
    checkpoint_path = config.checkpoint_dir.expanduser() / "current.json"

    _config, _checkpoint = startup_sequence(
        config=config,
        checkpoint_path=checkpoint_path,
        milestone=milestone,
        stage="research",
        resume_run_id=resume,
    )
    click.echo("Not yet implemented")


@click.command("design-worker")
@click.option("--repo", required=True, help="Target repo in OWNER/REPO format")
@click.option(
    "--research-milestone", required=True, help="Completed research milestone to translate"
)
@click.option("--model", default=None, help="Override Claude model")
@click.option("--dry-run", is_flag=True, help="Print planned issues without creating them")
def design_worker(
    repo: str,
    research_milestone: str,
    model: str | None,
    dry_run: bool,
) -> None:
    """Translate research docs into scoped implementation issues."""
    overrides: dict = {"github_repo": repo}
    if model:
        overrides["model"] = model

    config = load_config(**overrides)
    checkpoint_path = config.checkpoint_dir.expanduser() / "current.json"

    _config, _checkpoint = startup_sequence(
        config=config,
        checkpoint_path=checkpoint_path,
        milestone=research_milestone,
        stage="design",
    )
    click.echo("Not yet implemented")


@click.command("plan-issues")
@click.option("--repo", required=True, help="Target repo in OWNER/REPO format")
@click.option("--model", default=None, help="Override Claude model")
@click.option("--dry-run", is_flag=True, help="Print planned milestones without creating them")
def plan_issues(
    repo: str,
    model: str | None,
    dry_run: bool,
) -> None:
    """Plan milestones and seed research issues for the next version."""
    overrides: dict = {"github_repo": repo}
    if model:
        overrides["model"] = model

    config = load_config(**overrides)
    checkpoint_path = config.checkpoint_dir.expanduser() / "current.json"

    _config, _checkpoint = startup_sequence(
        config=config,
        checkpoint_path=checkpoint_path,
        milestone="",
        stage="plan-issues",
    )
    click.echo("Not yet implemented")


@click.group()
def composer() -> None:
    """Composer admin commands."""


@composer.command("health")
@click.option("--repo", default=None, help="Repo to check (OWNER/REPO)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def health_cmd(repo: str | None, as_json: bool) -> None:
    """Run preflight checks."""
    config = load_config(**({} if not repo else {"github_repo": repo}))
    checkpoint_path = config.checkpoint_dir.expanduser() / "current.json"
    chk = session.load(checkpoint_path)
    report = health.check_all(config, chk)
    if as_json:
        import dataclasses
        import json as _json

        click.echo(_json.dumps(dataclasses.asdict(report), indent=2))
    else:
        click.echo(health.format_report(report))
    raise SystemExit(0 if not report.fatal else 1)


@composer.command("cost")
def cost() -> None:
    """Show cost ledger summary."""
    config = load_config()
    checkpoint_path = config.checkpoint_dir.expanduser() / "current.json"
    _chk = session.load(checkpoint_path)
    _config, _checkpoint = startup_sequence(
        config=config,
        checkpoint_path=checkpoint_path,
        milestone="",
        stage="cost",
    )
    click.echo("Not yet implemented")
