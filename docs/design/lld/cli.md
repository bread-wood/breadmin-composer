# LLD: CLI Module

**Module:** `cli` + `skills/`
**Files:** `src/brimstone/cli.py`, `src/brimstone/skills/`
**Status:** Current
**Date:** 2026-03-04

---

## 1. Module Overview

`cli.py` is the entry-point layer for `brimstone`. It owns the Click command definitions,
flag wiring, startup sequence, worker loop orchestration, skill injection, Watchdog, and
MergeQueue drain. It imports from `config`, `health`, `runner`, `session`, `logger`, and
`beads`; it contains no business logic that belongs to those modules.

`skills/` contains markdown files that serve as prompt blueprints for each worker type.
At dispatch time `cli.py` reads the appropriate skill file and passes the rendered string
as the `-p` prompt to `runner.run()`.

**Prior Art:** The worker loop with persistent pool, the sequential MergeQueue drain, and
the Watchdog zombie recovery loop were inspired by concepts from Steve Yegge's Gas Town
project (github.com/steveyegge/gastown, SE Daily interview February 2026):

- Gas Town's Refinery -> `_process_merge_queue()` (concept borrowed, renamed)
- Gas Town's Deacon -> `_watchdog_scan()` (name borrowed, renamed to Watchdog in brimstone)
- Gas Town's Mayor/Polecats -> orchestrator/sub-agent split (developed independently)

---

## 2. Entry Point Map

| Command | Function | Purpose |
|---------|----------|---------|
| `brimstone run` | `_run_impl_worker()` | Implementation pipeline |
| `brimstone run --stage research` | `_run_research_worker()` | Research pipeline |
| `brimstone run --stage design` | `_run_design_worker()` | Design pipeline |
| `brimstone health` | `health_command()` | Preflight health check |
| `brimstone cost` | `cost_command()` | Cost ledger summary |
| `brimstone init` | `init_command()` | Repo scaffolding |

---

## 3. `startup_sequence()`

```python
def startup_sequence(
    repo: str,
    milestone: str,
    stage: str,
    **cli_overrides,
) -> tuple[Config, Checkpoint, BeadStore]:
```

Returns a 3-tuple. Called once at the start of every worker command.

**Steps:**

1. `config = load_config(**cli_overrides)` - resolve env vars + CLI flags.
2. `report = health.check_all(config)` - run preflight checks. Abort on `report.fatal`.
3. `checkpoint_path = config.checkpoint_dir / f"{stage}.checkpoint.json"`
4. `checkpoint = session.load(checkpoint_path)` - load or create via `session.new()`.
5. `session.save(checkpoint, checkpoint_path)` - persist (includes migration if older schema).
6. `store = make_bead_store(config, repo_slug)` - create BeadStore for this repo.
7. `_resume_stale_issues(store, config)` - re-dispatch any claimed-but-stale issues.
8. Return `(config, checkpoint, store)`.

### `_resume_stale_issues(store, config)`

Scans `store.list_work_beads(state="claimed")`. For each claimed work bead without an
associated open PR bead, re-dispatches the agent. If `store` is `None`, returns early.

---

## 4. Worker Loop: `_run_persistent_pool()`

The persistent pool loop drives all three worker types (research, design, impl). It:

1. Selects open issues from the milestone (filtered by `stage/*` label).
2. Claims issues sequentially (writes WorkBead + flushes).
3. Dispatches agents in parallel via `Agent(isolation:"worktree")`.
4. On each iteration:
   - Calls `_watchdog_tick()` every `WATCHDOG_INTERVAL=5` iterations.
   - Calls `_process_merge_queue()` after each batch.
5. Continues until no open issues remain in the milestone.

---

## 5. `_claim_issue(issue_number, store, config)`

```python
def _claim_issue(issue_number: int, store: BeadStore, config: Config) -> WorkBead:
```

1. `gh issue edit <N> --add-assignee @me --add-label in-progress`
2. Create branch: `git checkout -b <N>-<slug> origin/<default>` + `git push -u origin ...`
3. `bead = WorkBead(issue_number=N, state="claimed", claimed_at=now_utc())`
4. `store.write_work_bead(bead)` + `store.flush()`
5. Return the bead.

---

## 6. `_monitor_pr(pr_number, store)`

Monitors a PR from creation to `merge_ready`. Called per PR after the dispatched agent
writes `Done.` and exits.

```
_monitor_pr(pr_number, store)
  |
  +- write PRBead(state="open")
  |
  +- poll loop (gh pr checks pr_number):
  |     +- pending -> write PRBead(state="ci_running") -> sleep + retry
  |     +- failing -> write PRBead(state="ci_failing") -> (Watchdog handles)
  |     +- conflict -> write PRBead(state="conflict") -> (Watchdog handles)
  |     +- pass -> write PRBead(state="merge_ready") -> enqueue MergeQueue
  |
  +- store.flush()
```

Key state transitions written by `_monitor_pr`:

| Trigger | PRBead state written |
|---------|---------------------|
| PR created | `open` |
| CI started | `ci_running` |
| CI failed | `ci_failing` |
| Rebase conflict detected | `conflict` |
| All CI checks pass | `merge_ready` |

---

## 7. `_process_merge_queue(store, config)`

Drains the MergeQueue sequentially to prevent rebase conflicts.

```python
def _process_merge_queue(store: BeadStore, config: Config) -> None:
```

```
while queue = store.read_merge_queue() and queue.entries:
    entry = queue.entries[0]      # pop front
    |
    +- git fetch origin
    +- git rebase origin/<default>
    |     +- on conflict -> write PRBead(state="conflict") -> skip -> continue
    |
    +- gh pr merge <pr_number> --squash --delete-branch
    +- write WorkBead(state="closed")
    +- write PRBead(state="merged")
    +- gh issue edit <N> --remove-label in-progress
    +- remove entry from queue
    +- store.flush()
```

Called after each pool iteration batch completes.

---

## 8. `_watchdog_tick()` and `_watchdog_scan()`

### 8.1 `_watchdog_tick(store, config, iteration)`

```python
def _watchdog_tick(store: BeadStore, config: Config, iteration: int) -> None:
    if iteration % WATCHDOG_INTERVAL == 0:
        _watchdog_scan(store, config)
```

`WATCHDOG_INTERVAL = 5` (every 5 pool iterations).

### 8.2 `_watchdog_scan(store, config)`

Scans all open PRBead files. For each PR bead in `ci_failing` or `conflict` state that
has been stuck for longer than `WATCHDOG_TIMEOUT_MINUTES=45`:

```
for bead in store.list_pr_beads(state=["ci_failing", "conflict"]):
    if age_minutes(bead) > WATCHDOG_TIMEOUT_MINUTES:
        if bead.fix_attempts < WATCHDOG_MAX_FIX_ATTEMPTS:
            _dispatch_recovery_agent(bead, store, config)
        else:
            _exhaust_issue(bead, store, config)
```

`WATCHDOG_MAX_FIX_ATTEMPTS = 3`.

### 8.3 `_dispatch_recovery_agent(pr_bead, store, config)`

Dispatches a recovery sub-agent to inspect the PR, fix CI or review issues, and re-push.

1. Increment `pr_bead.fix_attempts`.
2. Write updated PRBead.
3. `store.flush()`.
4. Dispatch `Agent(isolation:"worktree")` with a recovery prompt referencing the PR number
   and the triage feedback from `pr_bead.feedback`.

### 8.4 `_exhaust_issue(pr_bead, store, config)`

Called when `fix_attempts >= WATCHDOG_MAX_FIX_ATTEMPTS`.

1. Write `PRBead(state="abandoned")`.
2. Write `WorkBead(state="abandoned")`.
3. `gh issue edit <N> --remove-label in-progress --add-label abandoned`.
4. Post a comment on the issue explaining exhaustion.
5. `store.flush()`.

---

## 9. Skill Injection

Each worker type has a corresponding skill file in `skills/`. The skill file is read and
rendered with runtime substitutions at dispatch time:

```python
def inject_skill(skill_name: str, substitutions: dict) -> str:
    skill_path = Path(__file__).parent / "skills" / f"{skill_name}.md"
    template = skill_path.read_text()
    return template.format_map(substitutions)
```

Skill files:

| File | Worker |
|------|--------|
| `skills/impl-worker.md` | Implementation stage agents |
| `skills/research-worker.md` | Research stage agents |
| `skills/design-worker.md` | Design stage agents |
| `skills/scope-worker.md` | Scope stage agents |

---

## 10. Interface Summary

### 10.1 Key Functions

| Function | Signature | Purpose |
|----------|-----------|---------|
| `startup_sequence` | `(repo, milestone, stage, **kw) -> (Config, Checkpoint, BeadStore)` | One-time startup |
| `_claim_issue` | `(issue_number, store, config) -> WorkBead` | Claim + write bead |
| `_monitor_pr` | `(pr_number, store) -> None` | Write PRBead states |
| `_process_merge_queue` | `(store, config) -> None` | Drain merge queue |
| `_watchdog_tick` | `(store, config, iteration) -> None` | Conditional watchdog |
| `_watchdog_scan` | `(store, config) -> None` | Zombie detection + recovery |
| `_dispatch_recovery_agent` | `(pr_bead, store, config) -> None` | Recovery dispatch |
| `_exhaust_issue` | `(pr_bead, store, config) -> None` | Mark abandoned |
| `inject_skill` | `(skill_name, substitutions) -> str` | Prompt construction |

### 10.2 Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `WATCHDOG_INTERVAL` | `5` | Pool iterations between Watchdog scans |
| `WATCHDOG_TIMEOUT_MINUTES` | `45` | PR age threshold for zombie detection |
| `WATCHDOG_MAX_FIX_ATTEMPTS` | `3` | Max recovery dispatches per PR |

---

## 11. Cross-References

- `docs/design/lld/beads.md` - BeadStore, WorkBead, PRBead, MergeQueue structures
- `docs/design/lld/session.md` - Checkpoint schema v3; `startup_sequence()` callee
- `docs/design/HLD.md` - System overview, Watchdog and MergeQueue design
- `src/brimstone/beads.py` - BeadStore implementation
- `src/brimstone/session.py` - Checkpoint load/save
