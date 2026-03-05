"""Unit tests for src/brimstone/sync.py.

Verifies the bead-first invariant: every GitHubSync mutation writes/flushes
the bead *before* the corresponding GitHub API call.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from brimstone.beads import BEAD_SCHEMA_VERSION, WorkBead
from brimstone.sync import (
    GitHubSync,
    _extract_module,
    _extract_priority,
    _extract_stage,
    _parse_dependencies,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cp(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    r: subprocess.CompletedProcess = subprocess.CompletedProcess(args=[], returncode=returncode)
    r.stdout = stdout
    r.stderr = ""
    return r


def _bead(issue_number: int = 1, state: str = "open", **kwargs: Any) -> WorkBead:
    defaults: dict[str, Any] = dict(
        v=BEAD_SCHEMA_VERSION,
        issue_number=issue_number,
        title="Test issue",
        milestone="v1",
        stage="impl",
        module="cli",
        priority="P2",
        state=state,
        branch="",
        retry_count=0,
        blocked_by=[],
    )
    defaults.update(kwargs)
    return WorkBead(**defaults)


class FakeStore:
    """Minimal BeadStore stand-in that records calls in an events list."""

    def __init__(self, bead: WorkBead | None = None, events: list | None = None) -> None:
        self._bead = bead
        self.events: list = events if events is not None else []

    def read_work_bead(self, issue_number: int) -> WorkBead | None:
        if self._bead is not None and self._bead.issue_number == issue_number:
            return self._bead
        return None

    def write_work_bead(self, bead: WorkBead) -> None:
        self._bead = bead
        self.events.append(("write", bead.state))

    def flush(self, message: str) -> None:
        self.events.append(("flush", message))

    def list_work_beads(self, **kwargs: Any) -> list[WorkBead]:
        if self._bead is None:
            return []
        # Apply simple filter by milestone/stage if passed
        bead = self._bead
        for key, val in kwargs.items():
            if getattr(bead, key, None) != val:
                return []
        return [bead]


def _gh_factory(events: list, responses: dict[str, str] | None = None):
    """Return a fake GhFn that appends ("gh", args) to events."""
    if responses is None:
        responses = {}

    def fake_gh(args: list[str]) -> subprocess.CompletedProcess:
        events.append(("gh", args))
        key = " ".join(args[:3])
        stdout = responses.get(key, "")
        return _cp(stdout)

    return fake_gh


# ---------------------------------------------------------------------------
# claim_issue
# ---------------------------------------------------------------------------


class TestClaimIssue:
    def test_bead_written_before_gh_call(self) -> None:
        events: list = []
        store = FakeStore(bead=_bead(1, "open"), events=events)
        sync = GitHubSync("owner/repo", store, gh=_gh_factory(events))

        sync.claim_issue(1, "1-branch")

        write_idx = next(i for i, (t, *_) in enumerate(events) if t == "write")
        gh_idx = next(i for i, (t, *_) in enumerate(events) if t == "gh")
        assert write_idx < gh_idx, "bead write must precede gh call"

    def test_bead_state_set_to_claimed(self) -> None:
        events: list = []
        store = FakeStore(bead=_bead(1, "open"), events=events)
        sync = GitHubSync("owner/repo", store, gh=_gh_factory(events))

        sync.claim_issue(1, "1-branch")

        written = next(b for t, b in events if t == "write")
        assert written == "claimed"

    def test_branch_set_on_bead(self) -> None:
        store = FakeStore(bead=_bead(1, "open"))
        sync = GitHubSync("owner/repo", store, gh=_gh_factory([]))

        sync.claim_issue(1, "1-my-branch")

        assert store._bead is not None
        assert store._bead.branch == "1-my-branch"

    def test_creates_bead_from_issue_dict_when_none_exists(self) -> None:
        events: list = []
        store = FakeStore(bead=None, events=events)
        issue = {
            "number": 42,
            "title": "New issue",
            "body": "Depends on: #10",
            "labels": [{"name": "stage/impl"}, {"name": "feat:runner"}, {"name": "P1"}],
            "milestone": {"title": "v2"},
        }
        sync = GitHubSync("owner/repo", store, gh=_gh_factory(events))

        sync.claim_issue(42, "42-new-issue", issue=issue)

        assert store._bead is not None
        b = store._bead
        assert b.issue_number == 42
        assert b.state == "claimed"
        assert b.branch == "42-new-issue"
        assert b.stage == "impl"
        assert b.module == "runner"
        assert b.priority == "P1"
        assert b.blocked_by == [10]

    def test_flush_called_before_gh(self) -> None:
        events: list = []
        store = FakeStore(bead=_bead(5, "open"), events=events)
        sync = GitHubSync("owner/repo", store, gh=_gh_factory(events))

        sync.claim_issue(5, "5-branch")

        flush_idx = next(i for i, (t, *_) in enumerate(events) if t == "flush")
        gh_idx = next(i for i, (t, *_) in enumerate(events) if t == "gh")
        assert flush_idx < gh_idx

    def test_no_bead_write_when_store_is_none(self) -> None:
        gh_calls: list = []
        sync = GitHubSync("owner/repo", None, gh=_gh_factory(gh_calls))

        sync.claim_issue(99, "99-branch")

        assert any(t == "gh" for t, *_ in gh_calls)


# ---------------------------------------------------------------------------
# unclaim_issue
# ---------------------------------------------------------------------------


class TestUnclaimIssue:
    def test_bead_written_before_gh_edit(self) -> None:
        events: list = []
        store = FakeStore(bead=_bead(2, "claimed"), events=events)
        responses = {"issue view 2": json.dumps({"assignees": [{"login": "bot"}]})}
        sync = GitHubSync("owner/repo", store, gh=_gh_factory(events, responses))

        sync.unclaim_issue(2)

        write_idx = next(i for i, (t, *_) in enumerate(events) if t == "write")
        # gh view is the first gh call; gh edit is the second
        gh_edit_idx = next(i for i, (t, args) in enumerate(events) if t == "gh" and "edit" in args)
        assert write_idx < gh_edit_idx

    def test_skips_bead_write_when_terminal_abandoned(self) -> None:
        events: list = []
        store = FakeStore(bead=_bead(3, "abandoned"), events=events)
        responses = {"issue view 3": json.dumps({"assignees": []})}
        sync = GitHubSync("owner/repo", store, gh=_gh_factory(events, responses))

        sync.unclaim_issue(3)

        writes = [e for e in events if e[0] == "write"]
        assert not writes, "must not write bead when state is terminal"

    def test_skips_bead_write_when_terminal_closed(self) -> None:
        events: list = []
        store = FakeStore(bead=_bead(4, "closed"), events=events)
        responses = {"issue view 4": json.dumps({"assignees": []})}
        sync = GitHubSync("owner/repo", store, gh=_gh_factory(events, responses))

        sync.unclaim_issue(4)

        writes = [e for e in events if e[0] == "write"]
        assert not writes

    def test_bead_state_set_to_open(self) -> None:
        store = FakeStore(bead=_bead(6, "claimed"))
        responses = {"issue view 6": json.dumps({"assignees": []})}
        sync = GitHubSync("owner/repo", store, gh=_gh_factory([], responses))

        sync.unclaim_issue(6)

        assert store._bead is not None
        assert store._bead.state == "open"


# ---------------------------------------------------------------------------
# exhaust_issue
# ---------------------------------------------------------------------------


class TestExhaustIssue:
    def test_bead_set_to_abandoned_before_gh_calls(self) -> None:
        events: list = []
        store = FakeStore(bead=_bead(7, "claimed"), events=events)
        responses = {"issue view 7": json.dumps({"assignees": []})}
        sync = GitHubSync("owner/repo", store, gh=_gh_factory(events, responses))

        sync.exhaust_issue(7, "agent_error")

        write_idx = next(i for i, (t, *_) in enumerate(events) if t == "write")
        first_gh_idx = next(i for i, (t, *_) in enumerate(events) if t == "gh")
        assert write_idx < first_gh_idx

    def test_bead_state_set_to_abandoned(self) -> None:
        store = FakeStore(bead=_bead(8, "claimed"))
        responses = {"issue view 8": json.dumps({"assignees": []})}
        sync = GitHubSync("owner/repo", store, gh=_gh_factory([], responses))

        sync.exhaust_issue(8, "timeout")

        assert store._bead is not None
        assert store._bead.state == "abandoned"

    def test_flush_before_gh_comment(self) -> None:
        events: list = []
        store = FakeStore(bead=_bead(9, "claimed"), events=events)
        responses = {"issue view 9": json.dumps({"assignees": []})}
        sync = GitHubSync("owner/repo", store, gh=_gh_factory(events, responses))

        sync.exhaust_issue(9, "reason")

        flush_idx = next(i for i, (t, *_) in enumerate(events) if t == "flush")
        comment_idx = next(
            i for i, (t, args) in enumerate(events) if t == "gh" and "comment" in args
        )
        assert flush_idx < comment_idx


# ---------------------------------------------------------------------------
# close_issue
# ---------------------------------------------------------------------------


class TestCloseIssue:
    def test_bead_written_before_gh_close(self) -> None:
        events: list = []
        store = FakeStore(bead=_bead(10, "claimed"), events=events)
        sync = GitHubSync("owner/repo", store, gh=_gh_factory(events))

        sync.close_issue(10, "brimstone: #10 closed")

        write_idx = next(i for i, (t, *_) in enumerate(events) if t == "write")
        gh_idx = next(i for i, (t, *_) in enumerate(events) if t == "gh")
        assert write_idx < gh_idx

    def test_bead_state_set_to_closed(self) -> None:
        store = FakeStore(bead=_bead(11, "claimed"))
        sync = GitHubSync("owner/repo", store, gh=_gh_factory([]))

        sync.close_issue(11)

        assert store._bead is not None
        assert store._bead.state == "closed"

    def test_skips_write_when_already_closed(self) -> None:
        events: list = []
        store = FakeStore(bead=_bead(12, "closed"), events=events)
        sync = GitHubSync("owner/repo", store, gh=_gh_factory(events))

        sync.close_issue(12)

        writes = [e for e in events if e[0] == "write"]
        assert not writes

    def test_flush_called_when_message_provided(self) -> None:
        events: list = []
        store = FakeStore(bead=_bead(13, "claimed"), events=events)
        sync = GitHubSync("owner/repo", store, gh=_gh_factory(events))

        sync.close_issue(13, "brimstone: flush message")

        flushes = [e for e in events if e[0] == "flush"]
        assert flushes, "flush must be called when flush_message is non-empty"

    def test_no_flush_when_no_message(self) -> None:
        events: list = []
        store = FakeStore(bead=_bead(14, "claimed"), events=events)
        sync = GitHubSync("owner/repo", store, gh=_gh_factory(events))

        sync.close_issue(14)

        flushes = [e for e in events if e[0] == "flush"]
        assert not flushes


# ---------------------------------------------------------------------------
# prune_dependency
# ---------------------------------------------------------------------------


class TestPruneDependency:
    def test_bead_written_before_gh_edit(self) -> None:
        events: list = []
        store = FakeStore(bead=_bead(15, "open", blocked_by=[5]), events=events)
        sync = GitHubSync("owner/repo", store, gh=_gh_factory(events))

        sync.prune_dependency(15, 5, "new body")

        write_idx = next(i for i, (t, *_) in enumerate(events) if t == "write")
        gh_idx = next(i for i, (t, *_) in enumerate(events) if t == "gh")
        assert write_idx < gh_idx

    def test_dep_removed_from_blocked_by(self) -> None:
        store = FakeStore(bead=_bead(16, "open", blocked_by=[7, 8]))
        sync = GitHubSync("owner/repo", store, gh=_gh_factory([]))

        sync.prune_dependency(16, 7, "new body")

        assert store._bead is not None
        assert 7 not in store._bead.blocked_by
        assert 8 in store._bead.blocked_by

    def test_no_write_when_dep_not_in_blocked_by(self) -> None:
        events: list = []
        store = FakeStore(bead=_bead(17, "open", blocked_by=[]), events=events)
        sync = GitHubSync("owner/repo", store, gh=_gh_factory(events))

        sync.prune_dependency(17, 99, "new body")

        writes = [e for e in events if e[0] == "write"]
        assert not writes


# ---------------------------------------------------------------------------
# migrate_issue
# ---------------------------------------------------------------------------


class TestMigrateIssue:
    def test_bead_written_before_gh_edit(self) -> None:
        events: list = []
        store = FakeStore(bead=_bead(18, "open", milestone="v1"), events=events)
        sync = GitHubSync("owner/repo", store, gh=_gh_factory(events))

        sync.migrate_issue(18, "v2")

        write_idx = next(i for i, (t, *_) in enumerate(events) if t == "write")
        gh_idx = next(i for i, (t, *_) in enumerate(events) if t == "gh")
        assert write_idx < gh_idx

    def test_bead_milestone_updated(self) -> None:
        store = FakeStore(bead=_bead(19, "open", milestone="v1"))
        sync = GitHubSync("owner/repo", store, gh=_gh_factory([]))

        sync.migrate_issue(19, "v2")

        assert store._bead is not None
        assert store._bead.milestone == "v2"

    def test_returns_true_on_success(self) -> None:
        store = FakeStore(bead=_bead(20, "open"))
        sync = GitHubSync("owner/repo", store, gh=_gh_factory([]))

        result = sync.migrate_issue(20, "v2")

        assert result is True

    def test_returns_false_on_gh_failure(self) -> None:
        def fail_gh(args: list[str]) -> subprocess.CompletedProcess:
            return _cp(returncode=1)

        store = FakeStore(bead=_bead(21, "open"))
        sync = GitHubSync("owner/repo", store, gh=fail_gh)

        result = sync.migrate_issue(21, "v2")

        assert result is False


# ---------------------------------------------------------------------------
# create_issue_if_missing
# ---------------------------------------------------------------------------


class TestCreateIssueIfMissing:
    def test_deduplicates_by_title_set(self) -> None:
        gh_calls: list = []
        store = FakeStore()
        sync = GitHubSync("owner/repo", store, gh=_gh_factory(gh_calls))

        result = sync.create_issue_if_missing(
            "Existing title",
            "v1",
            "stage/design",
            "body",
            stage="design",
            dedup_titles={"Existing title"},
        )

        assert result is None
        creates = [args for _, args in gh_calls if "create" in args]
        assert not creates, "must not call gh issue create when title already exists"

    def test_creates_when_title_not_in_dedup_set(self) -> None:
        def fake_gh(args: list[str]) -> subprocess.CompletedProcess:
            if "create" in args:
                return _cp("https://github.com/owner/repo/issues/42")
            return _cp()

        store = FakeStore()
        sync = GitHubSync("owner/repo", store, gh=fake_gh)

        result = sync.create_issue_if_missing(
            "New title",
            "v1",
            "stage/design",
            "body",
            stage="design",
            dedup_titles=set(),
        )

        assert result == 42

    def test_deduplicates_by_bead_store(self) -> None:
        gh_calls: list = []
        existing = _bead(99, "open", title="HLD for v1", milestone="v1", stage="design")
        store = FakeStore(bead=existing)
        sync = GitHubSync("owner/repo", store, gh=_gh_factory(gh_calls))

        result = sync.create_issue_if_missing(
            "HLD for v1",
            "v1",
            "stage/design",
            "body",
            stage="design",
        )

        assert result is None
        creates = [args for _, args in gh_calls if "create" in args]
        assert not creates

    def test_returns_none_on_gh_failure(self) -> None:
        def fail_gh(args: list[str]) -> subprocess.CompletedProcess:
            return _cp(returncode=1)

        store = FakeStore()
        sync = GitHubSync("owner/repo", store, gh=fail_gh)

        result = sync.create_issue_if_missing(
            "New issue",
            "v1",
            "stage/design",
            "body",
            stage="design",
            dedup_titles=set(),
        )

        assert result is None


# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------


class TestParseDependencies:
    def test_single_dep(self) -> None:
        assert _parse_dependencies("Depends on: #42") == [42]

    def test_multiple_deps(self) -> None:
        assert _parse_dependencies("Depends on: #10, #20") == [10, 20]

    def test_no_deps(self) -> None:
        assert _parse_dependencies("No dependencies here.") == []

    def test_case_insensitive(self) -> None:
        assert _parse_dependencies("depends on: #5") == [5]


class TestExtractHelpers:
    def _issue(self, labels: list[str]) -> dict:
        return {"labels": [{"name": lbl} for lbl in labels]}

    def test_extract_module_feat_prefix(self) -> None:
        assert _extract_module(self._issue(["feat:runner"])) == "runner"

    def test_extract_module_no_feat_label(self) -> None:
        assert _extract_module(self._issue(["stage/impl"])) == "none"

    def test_extract_stage_research(self) -> None:
        assert _extract_stage(self._issue(["stage/research"])) == "research"

    def test_extract_stage_impl(self) -> None:
        assert _extract_stage(self._issue(["stage/impl"])) == "impl"

    def test_extract_stage_design(self) -> None:
        assert _extract_stage(self._issue(["stage/design"])) == "design"

    def test_extract_stage_unknown(self) -> None:
        assert _extract_stage(self._issue(["other"])) == ""

    def test_extract_priority_p0(self) -> None:
        assert _extract_priority(self._issue(["P0"])) == "P0"

    def test_extract_priority_default(self) -> None:
        assert _extract_priority(self._issue(["stage/impl"])) == "P2"
