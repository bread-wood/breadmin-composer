# LLD: Session Module

**Module:** `session`
**File:** `src/brimstone/session.py`
**Status:** Current
**Date:** 2026-03-04

---

## 1. Module Overview

The `session` module provides durable orchestration state for the brimstone orchestrator. Its single responsibility is to serialize and recover run metadata and backoff state across restarts, crashes, and rate-limit waits. It does not dispatch agents, manage GitHub labels, or track issue/PR lifecycle — those concerns live in `beads.py` and `cli.py`. The session module purely owns the checkpoint lifecycle: create, load, save, migrate, and backoff control.

**File path:** `src/brimstone/session.py`

**Exports:**

| Symbol | Kind | Consumers |
|--------|------|-----------|
| `Checkpoint` | dataclass | `cli`, `health` |
| `new` | function | `cli` (worker startup) |
| `load` | function | `cli` (worker startup) |
| `save` | function | `cli` |
| `set_backoff` | function | `cli` (on 429) |
| `is_backing_off` | function | `cli` (pre-dispatch guard) |
| `clear_backoff` | function | `cli` (post-backoff dispatch) |

**Removed functions (not in current codebase, do not document as current):**
- `record_dispatch` — moved to BeadStore / dispatch_times
- `is_agent_hung` — moved to Watchdog in `cli.py`
- `classify_orphaned_issue` — removed; Watchdog handles via PRBead states
- `recover` — startup recovery now handled by `_resume_stale_issues()` in `cli.py`

---

## 2. Checkpoint Schema (v3)

### 2.1 Current Fields

The checkpoint is a single JSON file written atomically to `~/.brimstone/current.json`. Schema v3 is slim: it stores only run metadata and backoff state. Issue/PR lifecycle is in BeadStore.

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | `int` | Current value: `3`. Loader migrates older files forward. |
| `run_id` | `str` | UUID (v4) generated once at orchestrator startup. Stable for the entire run — survives restarts by being reloaded from the checkpoint. Used as the filename base for the conductor JSONL log. |
| `timestamp` | `str` | ISO 8601 UTC, updated on every `save()` call. Used to detect stale checkpoints at startup. |
| `backoff_until` | `str \| None` | ISO 8601 UTC timestamp until which all dispatch is suspended, or `None` when not in backoff. Set by `set_backoff()`, cleared by `clear_backoff()`. |
| `backoff_reason` | `str \| None` | Human-readable reason for the current backoff (e.g., `"rate_limit: 429 on issue #7"`). `None` when not in backoff. |

### 2.2 Fields Removed in v3

These fields were in earlier schemas and are now tracked in BeadStore instead:

| Removed field | Now tracked in |
|---------------|---------------|
| `claimed_issues` | `WorkBead(state="claimed")` files |
| `open_prs` | `PRBead(state="open")` files |
| `completed_prs` | `PRBead(state="merged")` files |
| `retry_counts` | `PRBead.fix_attempts` |
| `dispatch_times` | `PRBead.created_at` + Watchdog timeout |
| `active_worktrees` | Removed entirely (Watchdog uses BeadStore) |

### 2.3 Example: Current Schema

```json
{
  "schema_version": 3,
  "run_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "timestamp": "2026-03-04T15:12:44Z",
  "backoff_until": null,
  "backoff_reason": null
}
```

Example during active rate-limit backoff:

```json
{
  "schema_version": 3,
  "run_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "timestamp": "2026-03-04T15:10:00Z",
  "backoff_until": "2026-03-04T15:42:00Z",
  "backoff_reason": "rate_limit: 429 received on issue #16 dispatch"
}
```

---

## 3. `Checkpoint` Dataclass

```python
from dataclasses import dataclass

@dataclass
class Checkpoint:
    schema_version: int
    run_id: str
    timestamp: str
    backoff_until: str | None = None
    backoff_reason: str | None = None
```

---

## 4. Read/Write API

### 4.1 `new() -> Checkpoint`

Creates a fresh checkpoint with a new `run_id`. Called once at orchestrator startup when no existing checkpoint is found.

```python
def new() -> Checkpoint:
```

**Behaviour:**
1. Generates `run_id = str(uuid.uuid4())`.
2. Constructs `Checkpoint` with `backoff_until = None`, `backoff_reason = None`.
3. Sets `timestamp = datetime.now(timezone.utc).isoformat()`.
4. Does not write to disk — caller must call `save()`.

### 4.2 `load(path: Path) -> Checkpoint | None`

Reads and deserialises the checkpoint file.

```python
def load(path: Path) -> Checkpoint | None:
```

**Behaviour:**
1. If `path` does not exist, returns `None`. Normal first-run case — not an error.
2. Opens and parses the file as JSON.
3. If `schema_version` in the file is greater than `SCHEMA_VERSION=3`, raises `CheckpointVersionError`.
4. If `schema_version` is less than `3`, applies forward migration (see Section 4.5).
5. Constructs and returns the `Checkpoint` dataclass.
6. On corrupt JSON: logs the error, raises `CheckpointCorruptError`. Do not silently overwrite.

### 4.3 `save(checkpoint: Checkpoint, path: Path) -> None`

Atomically writes the checkpoint to disk.

```python
def save(checkpoint: Checkpoint, path: Path) -> None:
```

**Behaviour:**
1. Updates `checkpoint.timestamp = datetime.now(timezone.utc).isoformat()`.
2. Serialises to a JSON string (indent=2).
3. Writes to a temporary file at `path.with_suffix(".tmp")`.
4. Calls `os.replace(tmp_path, path)` — atomic rename on POSIX.
5. Calls `log_conductor_event` with `checkpoint_write`.

### 4.4 Schema Migration Chain

| From version | To version | Migration |
|--------------|------------|-----------|
| v0 | v1 | Add `dispatch_times: {}` field |
| v1 | v2 | No-op (internal reorganisation, no field changes) |
| v2 | v3 | Drop `active_worktrees` field |
| v3 | v3 | No migration needed |

Migration function pattern:

```python
def _migrate_v0_to_v1(data: dict) -> dict:
    data.setdefault("dispatch_times", {})
    return data

def _migrate_v2_to_v3(data: dict) -> dict:
    data.pop("active_worktrees", None)
    return data
```

Migrated checkpoints are not written back automatically — the caller's next `save()` persists the migrated state.

### 4.5 Corrupt Checkpoint: Human Escalation

When `load()` raises `CheckpointCorruptError`:

```
ERROR: Checkpoint at <path> is corrupt and cannot be parsed.

  To inspect: cat <path>

  If unrecoverable, delete and restart:
      rm <path>

  WARNING: Review in-progress issues before deleting:
      gh issue list --state open --label in-progress --repo <repo>
```

The CLI exits with code 2. The corrupt file is never silently overwritten.

---

## 5. Backoff State

Rate-limit backoff suspends all agent dispatch until the deadline expires.

### 5.1 `set_backoff(checkpoint, duration_seconds, attempt, reason)`

```python
def set_backoff(
    checkpoint: Checkpoint,
    duration_seconds: int,
    attempt: int,
    reason: str,
) -> None:
```

Exponential backoff formula: `wait = min(base * 2^attempt, max_seconds)`

| Attempt | Base=60s | Effective Wait |
|---------|----------|----------------|
| 0 | 60 s | 60 s |
| 1 | 60 s | 120 s |
| 2 | 60 s | 240 s |
| 3 | 60 s | 480 s |
| 4+ | 60 s | capped at `backoff_max_minutes * 60` (default 1920 s) |

### 5.2 `is_backing_off(checkpoint) -> bool`

Returns `True` if `checkpoint.backoff_until` is in the future.

### 5.3 `clear_backoff(checkpoint)`

Sets `backoff_until = None` and `backoff_reason = None`.

Called after a successful agent dispatch following a backoff period.

---

## 6. Interface Summary

| Symbol | Kind | Signature |
|--------|------|-----------|
| `SCHEMA_VERSION` | `int` | Module constant; value `3` |
| `CheckpointVersionError` | exception | Raised when file `schema_version > SCHEMA_VERSION` |
| `CheckpointCorruptError` | exception | Raised when checkpoint JSON is unparseable |
| `Checkpoint` | dataclass | See Section 3 |
| `new` | function | `() -> Checkpoint` |
| `load` | function | `(path: Path) -> Checkpoint \| None` |
| `save` | function | `(checkpoint: Checkpoint, path: Path) -> None` |
| `set_backoff` | function | `(checkpoint, duration_seconds, attempt, reason) -> None` |
| `is_backing_off` | function | `(checkpoint: Checkpoint) -> bool` |
| `clear_backoff` | function | `(checkpoint: Checkpoint) -> None` |

### Consumer Call Map

| Consumer | Function | When |
|----------|----------|------|
| `cli.py` (startup) | `load` | First action of `startup_sequence()` |
| `cli.py` (startup) | `new` | When `load` returns `None` |
| `cli.py` (startup) | `save` | After `new()` or after migration |
| `cli.py` (dispatch) | `set_backoff` | On 429 result from runner |
| `cli.py` (dispatch) | `is_backing_off` | At top of each pool iteration |
| `cli.py` (dispatch) | `clear_backoff` | After first successful post-backoff dispatch |
| `health.py` | `load` | Reads `backoff_until` for health check #8 |

### Checkpoint File Location

```python
checkpoint_path = config.checkpoint_dir / "current.json"
```

`Config.checkpoint_dir` defaults to `~/.brimstone/` and is overridden by `BRIMSTONE_CHECKPOINT_DIR`.

---

## 7. What This Module Does NOT Do

- Does not call `gh`, `git`, or any subprocess directly.
- Does not track issue or PR lifecycle — that is BeadStore's responsibility.
- Does not implement Watchdog or hang detection — that is in `cli.py`.
- Does not manage the conductor JSONL log. It calls `log_conductor_event` for checkpoint events, but does not own the log file.
- Does not store sub-agent session IDs. Those are written to the per-session JSONL log by `logger.py`.

---

## 8. Cross-References

- `docs/design/lld/beads.md` — Issue/PR lifecycle state that was removed from Checkpoint in v3
- `docs/design/lld/cli.md` — `startup_sequence()` calls `session.load()` / `session.new()`; `_watchdog_scan()` handles hang detection
- `docs/design/HLD.md` — System overview, state model section
- `src/brimstone/beads.py` — BeadStore implementation (owns `claimed_issues`, `open_prs`, etc.)
