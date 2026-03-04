# LLD: Beads Module

**Module:** `beads`
**File:** `src/brimstone/beads.py`
**Status:** Current
**Date:** 2026-03-04

---

## 1. Prior Art: Gas Town / Beads

The name "beads" and the core insight of persisting agent work state as git-backed files
come from Steve Yegge's Gas Town multi-agent workspace manager
(github.com/steveyegge/gastown, SE Daily interview February 2026).

Gas Town's core insight: make agent work durable by writing state to files that survive
process crashes and restarts. brimstone borrowed the name and the persistence pattern.

**What brimstone did differently:** three typed structures (WorkBead, PRBead, MergeQueue)
stored in `~/.brimstone/beads/<owner>/<repo>/` with atomic POSIX rename (not git-native
writes), tuned to the GitHub PR lifecycle rather than a general workspace model.

### Gas Town Inheritance Map

| Gas Town concept | brimstone equivalent | Status |
|---|---|---|
| Mayor | Orchestrator (cli.py / `brimstone run`) | Developed independently |
| Polecats | Sub-agents via `Agent(isolation:"worktree")` | Developed independently |
| Beads | WorkBead, PRBead, MergeQueue files | Name + persistence insight borrowed; structure diverged |
| The Refinery | `_process_merge_queue()` + MergeQueue | Concept borrowed; renamed |
| The Deacon | `_watchdog_scan()` (renamed to Watchdog) | Name borrowed → renamed |
| Git worktrees | `isolation:"worktree"` | Developed independently |

---

## 2. File Layout

```
~/.brimstone/beads/<owner>/<repo>/
  work/
    <N>.json              ← WorkBead per issue
  prs/
    pr-<N>.json           ← PRBead per PR
  merge-queue.json        ← MergeQueue
```

Example:

```
~/.brimstone/beads/bread-wood/brimstone/
  work/
    10.json
    16.json
    31.json
  prs/
    pr-42.json
    pr-55.json
  merge-queue.json
```

---

## 3. WorkBead

Tracks the lifecycle of a single GitHub issue from open to closed.

### 3.1 Dataclass

```python
from dataclasses import dataclass, field

@dataclass
class WorkBead:
    v: int = 1                          # schema version
    issue_number: int = 0
    state: str = "open"                 # see state enum below
    claimed_at: str | None = None       # ISO 8601 UTC
    pr_id: int | None = None            # PR number, set when PR is created
    retry_count: int = 0                # number of recovery attempts
```

### 3.2 State Enum

| State | Description |
|-------|-------------|
| `open` | Issue exists in the milestone, not yet claimed |
| `claimed` | Assigned `@me`, labelled `in-progress`, branch pushed |
| `pr_open` | Agent created a PR; CI running |
| `merge_ready` | PR CI passing, in MergeQueue |
| `closed` | PR squash-merged; issue resolved |
| `abandoned` | Exhausted `WATCHDOG_MAX_FIX_ATTEMPTS`; human escalation required |

### 3.3 State Transitions

```
open → claimed          _claim_issue() in cli.py
claimed → pr_open       _monitor_pr() detects open PR
pr_open → merge_ready   _monitor_pr() detects CI pass → enqueue MergeQueue
merge_ready → closed    _process_merge_queue() squash merges
pr_open → abandoned     _exhaust_issue() after max fix attempts
```

---

## 4. PRBead

Tracks the lifecycle of a GitHub PR from creation to merge.

### 4.1 Dataclass

```python
@dataclass
class PRBead:
    v: int = 1                          # schema version
    pr_number: int = 0
    issue_number: int = 0
    state: str = "open"                 # see state enum below
    fix_attempts: int = 0               # number of Watchdog recovery dispatches
    feedback: list[FeedbackItem] = field(default_factory=list)
```

### 4.2 State Enum

| State | Description |
|-------|-------------|
| `open` | PR created; awaiting CI |
| `ci_running` | CI checks in progress |
| `ci_failing` | One or more CI checks failed |
| `reviewing` | CI passes; awaiting review approval |
| `conflict` | Rebase conflict detected |
| `merge_ready` | CI pass + reviews approved; in MergeQueue |
| `merged` | PR squash-merged |
| `abandoned` | Exhausted fix attempts; human escalation required |

### 4.3 State Transitions

```
open → ci_running       first CI poll
ci_running → ci_failing CI check fails
ci_running → reviewing  CI passes; review pending
ci_running → merge_ready CI passes; no review required
reviewing → merge_ready  review approved
ci_failing → ci_running  recovery agent pushes fix
conflict → ci_running    recovery agent resolves conflict + rebases
merge_ready → merged     _process_merge_queue() squash merges
ci_failing → abandoned   _exhaust_issue()
conflict → abandoned     _exhaust_issue()
```

---

## 5. FeedbackItem

Records triage decisions for CI and review feedback items.

```python
@dataclass
class FeedbackItem:
    comment_id: str
    author: str
    is_bot: bool
    triage: str = "pending"             # pending | fix_now | filed_issue | skipped
    filed_issue: int | None = None      # GitHub issue number if triage=="filed_issue"
    triage_reason: str | None = None    # explanation for skipped or filed_issue
```

`FeedbackItem` entries are written into `PRBead.feedback` by the sub-agent as it processes
CI failures and review comments. The Watchdog reads these entries to determine whether
a recovery dispatch is warranted.

---

## 6. MergeQueue

Ordered list of PRs that are ready to merge. Processed sequentially by
`_process_merge_queue()` to prevent rebase conflicts.

### 6.1 Dataclasses

```python
@dataclass
class MergeQueueEntry:
    pr_number: int
    issue_number: int
    branch: str
    enqueued_at: str                    # ISO 8601 UTC

@dataclass
class MergeQueue:
    v: int = 1                          # schema version
    entries: list[MergeQueueEntry] = field(default_factory=list)
```

### 6.2 Operations

| Operation | How |
|-----------|-----|
| Enqueue | Append `MergeQueueEntry` to `entries`; `store.write_merge_queue(queue)` |
| Dequeue | `entries.pop(0)` after successful merge; `store.write_merge_queue(queue)` |
| Peek | `queue.entries[0]` without removing |

---

## 7. BeadStore

`BeadStore` is the I/O layer for all bead files. It provides atomic read/write operations
and an optional git-backed flush to a remote `state_repo`.

### 7.1 Constructor

```python
class BeadStore:
    def __init__(self, beads_dir: Path, repo_slug: str, config: Config):
        self.base = beads_dir / repo_slug.replace("/", "/")
        self.config = config
```

`repo_slug` is `"owner/repo"` format. The base directory is created on first write.

### 7.2 Read/Write API

```python
# Work beads
def read_work_bead(self, issue_number: int) -> WorkBead | None
def write_work_bead(self, bead: WorkBead) -> None
def list_work_beads(self, state: str | None = None) -> list[WorkBead]
def delete_work_bead(self, issue_number: int) -> None

# PR beads
def read_pr_bead(self, pr_number: int) -> PRBead | None
def write_pr_bead(self, bead: PRBead) -> None
def list_pr_beads(self, state: str | list[str] | None = None) -> list[PRBead]
def delete_pr_bead(self, pr_number: int) -> None

# Merge queue
def read_merge_queue(self) -> MergeQueue
def write_merge_queue(self, queue: MergeQueue) -> None

# Flush
def flush(self) -> None
```

### 7.3 Atomic Write Pattern

Every bead write uses `.tmp` + `os.replace` to guarantee that readers never see a partial
file:

```python
def _atomic_write(self, path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)
```

On POSIX (Linux, macOS), `os.replace` is atomic within the same filesystem.

### 7.4 `flush()`

Commits bead changes to the optional `state_repo`:

```python
def flush(self) -> None:
    if not self.config.state_repo:
        return
    repo_dir = self.config.state_repo_dir
    subprocess.run(["git", "-C", str(repo_dir), "add", str(self.base)], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-m",
                    f"brimstone: bead flush {datetime.now(timezone.utc).isoformat()}"],
                   check=True)
    subprocess.run(["git", "-C", str(repo_dir), "push"], check=True)
```

If `config.state_repo` is not set, `flush()` is a no-op (bead files are still written
atomically to disk; they just are not pushed to a remote).

### 7.5 Flush Points

`store.flush()` is called after:

| Event | Reason |
|-------|--------|
| `_claim_issue()` completes | Persist claimed state before dispatching agent |
| `_exhaust_issue()` completes | Persist abandoned state |
| `_process_merge_queue()` merges a PR | Persist closed state |
| Watchdog marks issue abandoned | Persist abandoned state |

---

## 8. `make_bead_store(config, repo_slug)` Factory

```python
def make_bead_store(config: Config, repo_slug: str) -> BeadStore:
    beads_dir = config.beads_dir or Path.home() / ".brimstone" / "beads"
    return BeadStore(beads_dir=beads_dir, repo_slug=repo_slug, config=config)
```

Called by `startup_sequence()` in `cli.py`.

---

## 9. Schema Version

Each bead type includes `v: int = 1` as the schema version field. Breaking field changes
bump this value. The `BeadStore` read methods check `v` on load and log a warning (but
do not abort) if the version is newer than expected.

---

## 10. Cross-References

- `docs/design/HLD.md` — §3 State Model, §5 MergeQueue, §6 Watchdog
- `docs/design/lld/cli.md` — `_claim_issue`, `_monitor_pr`, `_process_merge_queue`, `_watchdog_scan`
- `src/brimstone/config.py` — `Config.beads_dir`, `Config.state_repo`, `Config.state_repo_dir`
