# Research: Git Worktree Isolation

**Consolidated from:** #1, #14, #20
**Status:** Current
**Date:** 2026-03-04

---

## 1. Worktree Isolation Pattern

brimstone uses `Agent(isolation:"worktree")` to give each sub-agent its own git worktree.
The main checkout is never touched by agents.

### 1.1 How It Works

When `isolation:"worktree"` is set in an agent definition (or requested via the Agent tool),
Claude Code:

1. Creates a new git worktree under `.claude/worktrees/<branch-name>/`.
2. Checks out the specified branch (or creates a new one) in that worktree.
3. Runs the agent with the worktree as its working directory.
4. After the agent exits:
   - If no changes were committed: removes the worktree and branch automatically.
   - If changes exist: leaves the worktree (must be cleaned up by the orchestrator).

### 1.2 Manual Worktree Management (Subprocess Pattern)

When using the subprocess spawning pattern (orchestrator uses Bash to call `claude -p`),
the orchestrator manages worktrees manually:

```bash
# Create worktree before dispatch
git worktree add .claude/worktrees/<branch> -b <branch> origin/mainline
git -C .claude/worktrees/<branch> push -u origin <branch>

# Clean up after merge
git worktree remove --force .claude/worktrees/<branch>
git push origin --delete <branch>
```

---

## 2. Cleanup

### 2.1 After Successful Merge

After `gh pr merge --squash --delete-branch`:

```bash
git worktree remove --force .claude/worktrees/<branch>
# --delete-branch in gh pr merge also removes the remote branch
# Remove the local tracking ref:
git fetch --prune
```

### 2.2 After Agent Failure

If an agent exits with an error before creating a PR:

```bash
git worktree remove --force .claude/worktrees/<branch>
git push origin --delete <branch>
gh issue edit <N> --remove-assignee @me --remove-label in-progress
```

### 2.3 Stale Worktree Detection

Check the worktree's latest commit time:

```python
import subprocess, time

def is_stale_worktree(path: str, threshold_days: int = 3) -> bool:
    result = subprocess.run(
        ["git", "-C", path, "log", "-1", "--format=%ct"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        return True
    try:
        last_commit_ts = int(result.stdout.strip())
    except ValueError:
        return True
    age_seconds = time.time() - last_commit_ts
    return age_seconds > threshold_days * 86400
```

A worktree with no commits beyond the base branch and no recent activity is safe to remove.

---

## 3. Conflict Patterns

### 3.1 Rebase Conflicts on Merge

When two PRs both modify the same file, the second PR to reach the MergeQueue will have
a rebase conflict after the first is merged. Resolution:

```bash
git fetch origin
git rebase origin/mainline
# If conflict in agent's scope: resolve + git add + git rebase --continue
# If conflict outside scope: git rebase --abort; escalate to human
git push --force-with-lease origin <branch>
```

### 3.2 `_process_merge_queue` Rebase-Before-Merge

`_process_merge_queue()` always rebases before squash merging. This catches conflicts
early (before the CI re-runs) and maintains a linear commit history.

### 3.3 Concurrent Worktree Access

Each agent has its own worktree. Agents never share a worktree. The orchestrator's main
checkout is not used for agent work. This prevents concurrent file modification conflicts.

---

## 4. Worktree Listing and Health Checks

```bash
# List all worktrees
git worktree list --porcelain

# Check for worktrees under .claude/worktrees/
git worktree list --porcelain | grep ".claude/worktrees/"
```

The `brimstone health` check scans for orphaned worktrees under `.claude/worktrees/` and
reports them as warnings. Worktrees older than 3 days with no recent commits are flagged.

---

## 5. Sources

- Research files: #1, #14, #20
- Claude Code documentation: built-in git worktree support (Boris Cherny, Threads)
- Claude Code Worktrees guide (claudefa.st)
