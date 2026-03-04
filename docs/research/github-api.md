# Research: GitHub API and gh CLI

**Consolidated from:** #6, #11, #19, #38, #39, #79
**Status:** Current
**Date:** 2026-03-04

---

## 1. `gh` CLI Flag Behavior

### 1.1 Issue Operations

```bash
# Claim an issue
gh issue edit <N> --add-assignee @me --add-label in-progress --repo OWNER/REPO

# Release an issue
gh issue edit <N> --remove-assignee @me --remove-label in-progress --repo OWNER/REPO

# List open issues in milestone with label filter
gh issue list \
  --state open \
  --milestone "v0.2.0" \
  --label "stage/impl" \
  --assignee "" \
  --json number,title,labels,body \
  --limit 200

# Create an issue
gh issue create \
  --title "..." \
  --label "stage/impl,feat:cli" \
  --milestone "v0.2.0" \
  --body "..."
```

### 1.2 PR Operations

```bash
# Create PR
gh pr create \
  --title "..." \
  --body "Closes #N" \
  --head <branch> \
  --base mainline

# Check CI status
gh pr checks <PR-number> --watch

# View PR with reviews
gh pr view <PR-number> --json reviews,comments,statusCheckRollup

# Squash merge
gh pr merge <PR-number> --squash --delete-branch

# Get inline review comments
gh api repos/OWNER/REPO/pulls/<PR-number>/comments
```

### 1.3 Worktree Isolation Pattern

```bash
# Create worktree for agent
git checkout -b <N>-<slug> origin/mainline
git push -u origin <N>-<slug>
# Agent checks out this branch in its worktree
```

The orchestrator creates the branch before dispatching the agent. The agent pushes to
the existing remote branch (never creates a new branch).

---

## 2. CI Check States

`gh pr checks` returns check states via `statusCheckRollup`. Key states:

| State | Description | Action |
|-------|-------------|--------|
| `PENDING` / `IN_PROGRESS` | CI running | Poll again |
| `SUCCESS` | All checks passed | Enqueue MergeQueue |
| `FAILURE` | One or more checks failed | Classify failure type |
| `CANCELLED` | Check was cancelled | Re-trigger or escalate |
| `NEUTRAL` | Check passed but with warnings | Treat as pass |

### 2.1 Polling Pattern

```python
while True:
    result = gh_pr_checks(pr_number)
    if result.all_pass:
        break
    if result.any_fail:
        handle_failure(result)
        break
    sleep(30)
```

---

## 3. PR Review States

`gh pr view --json reviews` returns a list of review objects. Key `state` values:

| State | Description |
|-------|-------------|
| `APPROVED` | Reviewer approved |
| `CHANGES_REQUESTED` | Reviewer requested changes |
| `COMMENTED` | Reviewer left comments only |
| `DISMISSED` | Previous review was dismissed |

`reviewDecision` (from `--json reviewDecision`) is the aggregate:

| `reviewDecision` | Description |
|-----------------|-------------|
| `APPROVED` | Required reviewers approved |
| `CHANGES_REQUESTED` | At least one required reviewer requested changes |
| `REVIEW_REQUIRED` | Reviews required but not yet submitted |
| `null` | No review policy configured |

---

## 4. Branch Naming Convention

Format: `<issue-number>-<short-slug>`

- `slug` is the issue title, lowercased, spaces replaced with hyphens, max 40 chars
- Example: `16-add-beadstore-flush` for issue #16

One issue per branch. One branch per issue. Never reuse branches across issues.

---

## 5. Label Schema

All issues carry labels from orthogonal families. See `CLAUDE.md` for the full label table.

Key labels managed by orchestrator:
- `in-progress` — added on claim, removed on merge/abandon
- `abandoned` — added when max fix attempts exhausted

Agents must never modify labels — only the orchestrator does.

---

## 6. GHA Deployment

> Needs verification as of v0.1.0 — GHA issue event trigger pattern

GitHub Actions can trigger `brimstone run` on `issues` events (e.g., when a label is
applied). The brimstone binary must be installed in the GHA runner environment.

The `GITHUB_TOKEN` available in GHA is sufficient for most `gh` CLI operations within
the same repo. For cross-repo operations, a PAT or GitHub App installation token is
required.

---

## 7. Multi-Repo Orchestration

> Needs verification as of v0.1.0 — multi-repo patterns

When operating across multiple repos, the orchestrator should:
1. Use a single `BeadStore` with separate `<owner>/<repo>/` subdirectories per repo.
2. Authenticate with a GitHub App installation token scoped to each target repo.
3. Run one `brimstone run` invocation per repo (not one invocation for all repos).

---

## 8. Sources

- Research files: #6, #11, #19, #38, #39, #79
- gh CLI documentation (cli.github.com)
- GitHub REST API documentation
