"""Unit tests for monitor.repair_repo()."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from brimstone.beads import (
    BEAD_SCHEMA_VERSION,
    BeadStore,
    PRBead,
    WorkBead,
)
from brimstone.monitor import repair_repo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO = "owner/repo"


def _make_store(tmp_path: Path) -> BeadStore:
    return BeadStore(tmp_path)


def _make_work_bead(
    issue_number: int,
    state: str = "merge_ready",
    branch: str = "42-fix",
    pr_id: str | None = None,
) -> WorkBead:
    return WorkBead(
        v=BEAD_SCHEMA_VERSION,
        issue_number=issue_number,
        title=f"Issue #{issue_number}",
        milestone="v1.0",
        stage="impl",
        module="cli",
        priority="P2",
        state=state,
        branch=branch,
        pr_id=pr_id,
    )


def _make_pr_bead(pr_number: int, issue_number: int, state: str, branch: str = "42-fix") -> PRBead:
    return PRBead(
        v=BEAD_SCHEMA_VERSION,
        pr_number=pr_number,
        issue_number=issue_number,
        branch=branch,
        state=state,
    )


def _gh_result(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.stdout = stdout
    r.returncode = returncode
    return r


# ---------------------------------------------------------------------------
# Pass 1: orphaned_merge with missing pr_id — PR found on GitHub
# ---------------------------------------------------------------------------


def test_repair_orphaned_merge_links_pr_and_enqueues(tmp_path):
    store = _make_store(tmp_path)
    bead = _make_work_bead(42, state="merge_ready", branch="42-fix", pr_id=None)
    store.write_work_bead(bead)

    # No MergeQueue entry yet
    mq = store.read_merge_queue()
    assert not mq.queue

    pr_list_json = json.dumps(
        [{"number": 99, "mergeable": "MERGEABLE", "statusCheckRollup": [{"conclusion": "SUCCESS"}]}]
    )

    def _side_effect(args, **kwargs):
        if "list" in args:
            return _gh_result(pr_list_json)
        return _gh_result("[]")

    with patch("brimstone.monitor._gh", side_effect=_side_effect):
        fixes = repair_repo(store, REPO)

    assert any("linked pr-99" in f and "enqueued" in f for f in fixes)

    # WorkBead updated
    updated = store.read_work_bead(42)
    assert updated.pr_id == "pr-99"

    # PRBead created
    pr = store.read_pr_bead(99)
    assert pr is not None
    assert pr.state == "merge_ready"

    # MergeQueue entry added
    mq2 = store.read_merge_queue()
    assert any(e.issue_number == 42 for e in mq2.queue)


# ---------------------------------------------------------------------------
# Pass 1: orphaned_merge with missing pr_id — no PR on GitHub
# ---------------------------------------------------------------------------


def test_repair_orphaned_merge_no_pr_skips(tmp_path):
    store = _make_store(tmp_path)
    bead = _make_work_bead(42, state="merge_ready", branch="42-fix", pr_id=None)
    store.write_work_bead(bead)

    def _side_effect(args, **kwargs):
        return _gh_result("[]")

    with patch("brimstone.monitor._gh", side_effect=_side_effect):
        fixes = repair_repo(store, REPO)

    assert any("no open PR found" in f for f in fixes)

    # WorkBead unchanged
    updated = store.read_work_bead(42)
    assert updated.pr_id is None


# ---------------------------------------------------------------------------
# Pass 2: orphaned_merge with pr_id already set
# ---------------------------------------------------------------------------


def test_repair_orphaned_merge_pr_id_set_enqueues(tmp_path):
    store = _make_store(tmp_path)
    bead = _make_work_bead(42, state="merge_ready", branch="42-fix", pr_id="pr-77")
    store.write_work_bead(bead)
    store.write_pr_bead(_make_pr_bead(77, 42, "merge_ready"))

    def _side_effect(args, **kwargs):
        return _gh_result("[]")

    with patch("brimstone.monitor._gh", side_effect=_side_effect):
        fixes = repair_repo(store, REPO)

    assert any("pr_id=pr-77" in f and "enqueued" in f for f in fixes)
    mq = store.read_merge_queue()
    assert any(e.issue_number == 42 for e in mq.queue)


# ---------------------------------------------------------------------------
# Pass 3: state_regression on closed bead → wont_fix
# ---------------------------------------------------------------------------


def test_repair_state_regression_closed_bead_marks_wont_fix(tmp_path):
    store = _make_store(tmp_path)
    bead = _make_work_bead(10, state="closed")
    store.write_work_bead(bead)
    # Manually inject a bad event (merge_ready → open)
    events_path = tmp_path / "events" / "work-10.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text(
        json.dumps(
            {
                "ts": "2026-01-01T00:00:00+00:00",
                "bead_type": "work",
                "bead_id": "10",
                "from": "merge_ready",
                "to": "open",
                "meta": {},
            }
        )
        + "\n"
    )

    def _side_effect(args, **kwargs):
        return _gh_result("[]")

    with patch("brimstone.monitor._gh", side_effect=_side_effect):
        fixes = repair_repo(store, REPO)

    assert any("wont_fix" in f and "#10" in f for f in fixes)

    # AnomalyBead written
    from brimstone.monitor import Anomaly, _anomaly_id

    anomaly = Anomaly(
        kind="state_regression",
        severity="critical",
        description="",
        details={
            "issue_number": 10,
            "from_state": "merge_ready",
            "to_state": "open",
            "ts": "2026-01-01T00:00:00+00:00",
        },
    )
    aid = _anomaly_id(anomaly)
    abead = store.read_anomaly_bead(aid)
    assert abead is not None
    assert abead.state == "wont_fix"


# ---------------------------------------------------------------------------
# Pass 3: state_regression on active bead → manual investigation message
# ---------------------------------------------------------------------------


def test_repair_state_regression_active_bead_reports_manual(tmp_path):
    store = _make_store(tmp_path)
    bead = _make_work_bead(11, state="claimed")
    store.write_work_bead(bead)
    events_path = tmp_path / "events" / "work-11.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text(
        json.dumps(
            {
                "ts": "2026-01-01T00:00:00+00:00",
                "bead_type": "work",
                "bead_id": "11",
                "from": "merge_ready",
                "to": "open",
                "meta": {},
            }
        )
        + "\n"
    )

    def _side_effect(args, **kwargs):
        return _gh_result("[]")

    with patch("brimstone.monitor._gh", side_effect=_side_effect):
        fixes = repair_repo(store, REPO)

    assert any("needs manual investigation" in f and "#11" in f for f in fixes)


# ---------------------------------------------------------------------------
# Pass 4: stale PRBead conflict → refreshed to merge_ready
# ---------------------------------------------------------------------------


def test_repair_stale_prbead_conflict_refreshed(tmp_path):
    store = _make_store(tmp_path)
    pr = _make_pr_bead(55, 20, "conflict")
    store.write_pr_bead(pr)

    pr_view_json = json.dumps(
        {
            "state": "OPEN",
            "mergeable": "MERGEABLE",
            "statusCheckRollup": [{"conclusion": "SUCCESS"}],
        }
    )

    def _side_effect(args, **kwargs):
        if "view" in args:
            return _gh_result(pr_view_json)
        return _gh_result("[]")

    with patch("brimstone.monitor._gh", side_effect=_side_effect):
        fixes = repair_repo(store, REPO)

    assert any("pr-55" in f and "conflict" in f and "merge_ready" in f for f in fixes)
    updated = store.read_pr_bead(55)
    assert updated.state == "merge_ready"


# ---------------------------------------------------------------------------
# Nothing to fix → empty list
# ---------------------------------------------------------------------------


def test_repair_nothing_to_fix(tmp_path):
    store = _make_store(tmp_path)

    def _side_effect(args, **kwargs):
        return _gh_result("[]")

    with patch("brimstone.monitor._gh", side_effect=_side_effect):
        fixes = repair_repo(store, REPO)

    assert fixes == []


# ---------------------------------------------------------------------------
# dry_run=True → no writes, messages returned
# ---------------------------------------------------------------------------


def test_repair_dry_run_no_writes(tmp_path):
    store = _make_store(tmp_path)
    bead = _make_work_bead(42, state="merge_ready", branch="42-fix", pr_id=None)
    store.write_work_bead(bead)

    pr_list_json = json.dumps([{"number": 99, "mergeable": "MERGEABLE", "statusCheckRollup": []}])

    def _side_effect(args, **kwargs):
        if "list" in args:
            return _gh_result(pr_list_json)
        return _gh_result("[]")

    with patch("brimstone.monitor._gh", side_effect=_side_effect):
        fixes = repair_repo(store, REPO, dry_run=True)

    # Messages returned
    assert any("pr-99" in f for f in fixes)
    assert all("(dry-run)" in f for f in fixes)

    # No writes happened
    updated = store.read_work_bead(42)
    assert updated.pr_id is None

    mq = store.read_merge_queue()
    assert not mq.queue
