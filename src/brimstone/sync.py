"""GitHub↔Bead mirroring — centralized mutation layer.

All GitHub issue mutations flow through ``GitHubSync``, which enforces the
invariant: bead state is updated and flushed *before* the corresponding
GitHub API call.

Import graph (no circularity):
  beads.py  (pure storage)
    ↑
  sync.py   (GitHubSync + moved helpers)
    ↑
  cli.py    (thin delegators; existing patch points preserved)
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from brimstone.beads import BEAD_SCHEMA_VERSION, BeadStore, WorkBead

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BRIMSTONE_BOT: str = "yeast-bot"
_FEAT_PREFIX: str = "feat:"

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

GhFn = Callable[[list[str]], subprocess.CompletedProcess]

# ---------------------------------------------------------------------------
# Pure helpers (moved from cli.py; re-exported for cli.py callers)
# ---------------------------------------------------------------------------


def _parse_dependencies(body: str) -> list[int]:
    """Parse ``Depends on: #N`` references from an issue body.

    Matches patterns like ``Depends on: #42`` or ``Depends on: #42, #43``.

    Args:
        body: Raw issue body text.

    Returns:
        List of referenced issue numbers as integers.
    """
    deps: list[int] = []
    for match in re.finditer(r"[Dd]epends\s+on\s*:?\s*((?:#\d+(?:\s*,\s*)?)+)", body):
        for num_match in re.finditer(r"#(\d+)", match.group(1)):
            deps.append(int(num_match.group(1)))
    return deps


def _extract_module(issue: dict[str, Any]) -> str:
    """Extract the module name from a feat:* label on an issue.

    Scans the issue's ``labels`` list for a label whose name starts with
    ``feat:``.  Returns the part after the prefix (e.g. ``"config"`` from
    ``"feat:config"``).  Returns ``"none"`` when no matching label is found.

    Args:
        issue: Issue dict with a ``labels`` key (list of label dicts with ``name``).

    Returns:
        Module name string, or ``"none"`` if no feat:* label is present.
    """
    for label in issue.get("labels", []):
        name = label.get("name", "")
        if name.startswith(_FEAT_PREFIX):
            return name[len(_FEAT_PREFIX) :]
    return "none"


def _extract_stage(issue: dict[str, Any]) -> str:
    """Return the stage string from issue labels ('research', 'impl', 'design', or '')."""
    labels = {lbl["name"] for lbl in issue.get("labels", [])}
    if "stage/research" in labels:
        return "research"
    if "stage/impl" in labels:
        return "impl"
    if "stage/design" in labels:
        return "design"
    return ""


def _extract_priority(issue: dict[str, Any]) -> str:
    """Return the priority label from issue labels (defaults to 'P2')."""
    labels = {lbl["name"] for lbl in issue.get("labels", [])}
    for p in ("P0", "P1", "P2", "P3", "P4"):
        if p in labels:
            return p
    return "P2"


# ---------------------------------------------------------------------------
# GitHubSync
# ---------------------------------------------------------------------------


class GitHubSync:
    """Centralized GitHub↔bead mirroring.

    Every mutating method writes/flushes the bead *first*, then calls GitHub.
    This enforces the invariant that bead state is always ahead of GitHub state.

    Args:
        repo:  Repository in ``owner/repo`` format.
        store: Active BeadStore instance, or None (legacy no-store path).
        gh:    Callable that runs a gh subcommand: ``gh(args) -> CompletedProcess``.
               Injected for testability; defaults to a subprocess wrapper.
    """

    def __init__(
        self,
        repo: str,
        store: BeadStore | None,
        gh: GhFn | None = None,
    ) -> None:
        self._repo = repo
        self._store = store
        if gh is not None:
            self._gh = gh
        else:

            def _default_gh(args: list[str]) -> subprocess.CompletedProcess:
                cmd = ["gh", "--repo", repo] + args
                return subprocess.run(cmd, capture_output=True, text=True, check=False)

            self._gh = _default_gh

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def claim_issue(
        self,
        issue_number: int,
        branch: str,
        issue: dict | None = None,
    ) -> None:
        """Write WorkBead(state='claimed') + flush, then gh issue edit.

        Args:
            issue_number: GitHub issue number.
            branch:       Branch name created for this issue.
            issue:        Full issue dict (from GitHub API). Used when creating a new bead.
        """
        if self._store is not None:
            existing = self._store.read_work_bead(issue_number)
            if existing is not None:
                existing.state = "claimed"
                existing.branch = branch
                existing.claimed_at = datetime.now(UTC).isoformat()
                self._store.write_work_bead(existing)
            elif issue is not None:
                milestone_title = (issue.get("milestone") or {}).get("title", "")
                body = issue.get("body") or ""
                bead = WorkBead(
                    v=BEAD_SCHEMA_VERSION,
                    issue_number=issue_number,
                    title=issue.get("title", ""),
                    milestone=milestone_title,
                    stage=_extract_stage(issue),
                    module=_extract_module(issue),
                    priority=_extract_priority(issue),
                    state="claimed",
                    branch=branch,
                    retry_count=0,
                    blocked_by=_parse_dependencies(body),
                    claimed_at=datetime.now(UTC).isoformat(),
                )
                self._store.write_work_bead(bead)
            self._store.flush(f"brimstone: claim #{issue_number}")
        self._gh(
            [
                "issue",
                "edit",
                str(issue_number),
                "--add-assignee",
                _BRIMSTONE_BOT,
                "--add-label",
                "in-progress",
            ]
        )

    def unclaim_issue(self, issue_number: int) -> None:
        """Write WorkBead(state='open') if not terminal, then gh issue edit.

        Fetches current assignees so that legacy assignments (e.g. from a run
        before the bot account was configured) are also cleared.
        """
        if self._store is not None:
            bead = self._store.read_work_bead(issue_number)
            if bead is not None and bead.state not in ("abandoned", "closed", "merge_ready"):
                bead.state = "open"
                self._store.write_work_bead(bead)
        info = self._gh(["issue", "view", str(issue_number), "--json", "assignees"])
        try:
            assignees = [a["login"] for a in json.loads(info.stdout).get("assignees", [])]
        except (json.JSONDecodeError, KeyError):
            assignees = [_BRIMSTONE_BOT]
        if not assignees:
            assignees = [_BRIMSTONE_BOT]
        args = ["issue", "edit", str(issue_number), "--remove-label", "in-progress"]
        for login in assignees:
            args += ["--remove-assignee", login]
        self._gh(args)

    def exhaust_issue(self, issue_number: int, reason: str, max_retries: int = 3) -> None:
        """Write WorkBead(state='abandoned') + flush, then comment/unclaim/close.

        Args:
            issue_number: GitHub issue number.
            reason:       Short failure description.
            max_retries:  Number of retries shown in the comment.
        """
        if self._store is not None:
            bead = self._store.read_work_bead(issue_number)
            if bead is not None:
                bead.state = "abandoned"
                self._store.write_work_bead(bead)
                self._store.flush(f"brimstone: #{issue_number} abandoned — {reason}")
        self._gh(
            [
                "issue",
                "comment",
                str(issue_number),
                "--body",
                (
                    f"brimstone: agent exhausted all {max_retries} retries without success.\n"
                    f"Failure reason: `{reason}`\n\n"
                    "Manual investigation required. Reopen this issue to retry on the next run."
                ),
            ]
        )
        self.unclaim_issue(issue_number)
        self._gh(["issue", "edit", str(issue_number), "--add-label", "bug"])
        self._gh(["issue", "close", str(issue_number)])

    def close_issue(self, issue_number: int, flush_message: str = "") -> None:
        """Write WorkBead(state='closed') [+ flush if message], then gh issue close.

        Args:
            issue_number:  GitHub issue number.
            flush_message: If non-empty, flush the store with this message after writing.
        """
        if self._store is not None:
            bead = self._store.read_work_bead(issue_number)
            if bead is not None and bead.state not in ("closed", "abandoned"):
                bead.state = "closed"
                bead.closed_at = datetime.now(UTC).isoformat()
                self._store.write_work_bead(bead)
                if flush_message:
                    self._store.flush(flush_message)
        self._gh(["issue", "close", str(issue_number)])

    def prune_dependency(self, issue_number: int, dep_number: int, new_body: str) -> None:
        """Remove dep_number from WorkBead.blocked_by + write, then gh issue edit --body.

        Args:
            issue_number: Issue whose dependency is being removed.
            dep_number:   Dependency issue number to remove.
            new_body:     Updated issue body (stale dep already removed from text).
        """
        if self._store is not None:
            bead = self._store.read_work_bead(issue_number)
            if bead is not None and dep_number in bead.blocked_by:
                bead.blocked_by.remove(dep_number)
                self._store.write_work_bead(bead)
        self._gh(["issue", "edit", str(issue_number), "--body", new_body])

    def migrate_issue(self, issue_number: int, next_milestone: str) -> bool:
        """Write WorkBead(milestone=next_milestone), then gh issue edit --milestone.

        Returns:
            True on success, False if the gh call fails.
        """
        if self._store is not None:
            bead = self._store.read_work_bead(issue_number)
            if bead is not None:
                bead.milestone = next_milestone
                self._store.write_work_bead(bead)
        result = self._gh(["issue", "edit", str(issue_number), "--milestone", next_milestone])
        return result.returncode == 0

    def create_issue_if_missing(
        self,
        title: str,
        milestone: str,
        label: str,
        body: str,
        stage: str,
        dedup_titles: set[str] | None = None,
    ) -> int | None:
        """Dedup by title, then gh issue create if not a duplicate.

        Does NOT create a bead — caller must call ``_seed_work_beads`` after.

        Args:
            title:        Issue title.
            milestone:    Milestone name.
            label:        Label(s) for the issue (comma-separated for multiple).
            body:         Issue body text.
            stage:        Stage name used for bead-based dedup (e.g. ``"design"``).
            dedup_titles: Optional pre-computed set of existing titles.  When
                          provided, the store/gh dedup queries are skipped.

        Returns:
            Issue number on success, ``None`` if it already exists or creation fails.
        """
        # Bead-first dedup
        if dedup_titles is not None:
            if title in dedup_titles:
                return None
        elif self._store is not None:
            existing_titles = {
                b.title for b in self._store.list_work_beads(milestone=milestone, stage=stage)
            }
            if title in existing_titles:
                return None
        else:
            # Fall back to GitHub query
            result = self._gh(
                [
                    "issue",
                    "list",
                    "--state",
                    "all",
                    "--milestone",
                    milestone,
                    "--limit",
                    "500",
                    "--json",
                    "title",
                ]
            )
            if result.returncode == 0:
                try:
                    existing = {i["title"] for i in json.loads(result.stdout)}
                    if title in existing:
                        return None
                except (json.JSONDecodeError, KeyError):
                    pass

        result = self._gh(
            [
                "issue",
                "create",
                "--title",
                title,
                "--label",
                label,
                "--milestone",
                milestone,
                "--body",
                body,
            ]
        )
        if result.returncode != 0:
            return None
        url = result.stdout.strip()
        try:
            return int(url.split("/")[-1])
        except (ValueError, IndexError):
            return None
