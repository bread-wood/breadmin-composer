# Research: Multi-Repo Orchestration from a Single Conductor Instance

**Issue:** #39
**Milestone:** v2
**Feature:** feat:multi-repo
**Status:** Complete
**Date:** 2026-03-02

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Isolation Requirements Per Repo](#isolation-requirements-per-repo)
3. [Token and Identity Model](#token-and-identity-model)
4. [Global vs. Per-Repo Concurrency Accounting](#global-vs-per-repo-concurrency-accounting)
5. [CLAUDE.md Multiplexing Mechanism](#claudemd-multiplexing-mechanism)
6. [Config Schema for Multi-Repo Setup](#config-schema-for-multi-repo-setup)
7. [Prioritization Policy](#prioritization-policy)
8. [Follow-Up Research Recommendations](#follow-up-research-recommendations)
9. [Sources](#sources)

---

## Executive Summary

A single conductor instance can orchestrate multiple repositories simultaneously with
proper isolation at the filesystem, credential, and scheduling layers. The key design
decisions are: (1) per-repo working directories with separate git worktrees, (2) a shared
global concurrency pool (not per-repo) to prevent starvation, (3) `cwd`-based
CLAUDE.md multiplexing (no code changes needed), and (4) a TOML config schema that
defines repos as a list with per-repo overrides.

**Key findings:**

1. **Filesystem isolation** is already solved by conductor's worktree pattern. Each
   sub-agent gets its own git worktree with `cwd` pointing to that worktree. For
   multi-repo, conductor maintains a separate checkout directory per repo:
   `~/.conductor/repos/<owner>/<repo>/`. [INFERRED, architecturally straightforward]

2. **Token model**: A single fine-grained GitHub PAT can cover all target repos within
   one organization. Cross-organization deployments require per-org tokens. The existing
   credential proxy design (`17-credential-proxy.md`) extends naturally to per-repo token
   injection. [DOCUMENTED]

3. **Concurrency**: A global cap of 5 concurrent agents across all repos is the correct
   default. Per-repo caps (e.g., max 2 agents per repo) can be added as overrides. Global
   scheduling prevents one active repo from starving others. [INFERRED]

4. **CLAUDE.md multiplexing** requires no new mechanism — setting `cwd` per sub-agent
   to the target repo's checkout directory causes `claude -p` to discover that repo's
   `CLAUDE.md` automatically (per `04-configuration.md`). The conductor's own
   `~/.claude/CLAUDE.md` is also loaded at all times; this is unavoidable. [DOCUMENTED]

5. **Config schema**: A `conductor.toml` with a `[[repos]]` array is the most natural
   representation. Per-repo overrides (max_concurrency, module labels, build commands)
   are supported under each `[[repos]]` entry. [INFERRED]

6. **Prioritization**: A token-bucket fairness policy per repo with a global FIFO queue
   for dispatch — the simplest design that prevents starvation. [INFERRED]

---

## Isolation Requirements Per Repo

### Filesystem Isolation

Each repo needs its own:
1. **Base checkout directory**: `~/.conductor/repos/<owner>/<repo>/` — conductor clones
   or pulls the default branch here.
2. **Worktrees**: `~/.conductor/repos/<owner>/<repo>/.claude/worktrees/<N>-<slug>/` —
   consistent with the single-repo pattern; worktrees are created under the repo's
   base checkout.
3. **CLAUDE.md context**: Automatically isolated by setting `cwd` to the worktree path.

### Process Environment Isolation

Each sub-agent subprocess needs:
- `GH_TOKEN` or `GITHUB_TOKEN` scoped to its target repo (see Token Model section)
- `CLAUDE_CONFIG_DIR` pointing to a per-job temp directory to prevent credential conflicts
  (per `04-configuration.md` and `38-ci-server-deployment.md`)
- `ANTHROPIC_API_KEY` — shared across all sub-agents (one API key, all repos use the same
  Anthropic account)

### Branch Namespace Isolation

Each repo has its own branch namespace, so no conflict exists between repos at the git
level. A branch `7-fix-bug` in repo A and `7-fix-bug` in repo B are independent.

**No special handling needed** for branch naming across repos. The `gh` CLI is always
invoked with an explicit `--repo owner/name` flag to target the correct repo, preventing
cross-repo operations.

### Module Lock Isolation

The "one agent per module at a time" rule in the CLAUDE.md orchestrator protocol applies
**per repo** — not globally. Repo A's `module/runner` and repo B's `module/runner` can
run concurrently. The module lock table is keyed by `(repo, module)` pairs.

---

## Token and Identity Model

### GitHub Token Options

| Token Type | Multi-repo Support | Max Repos | Expiry | Security |
|------------|-------------------|-----------|--------|----------|
| Fine-grained PAT (single-repo) | No | 1 | Configurable (1–365 days) | Best isolation |
| Fine-grained PAT (selected repos) | Yes | Many | Configurable | Good |
| Fine-grained PAT (all repos) | Yes | Unlimited | Configurable | Least isolation |
| Classic PAT (repo scope) | Yes | Unlimited | Never (unless manually expired) | Poor |
| GitHub App installation token | Yes | Per installation | 1 hour | Best for orgs |

**Recommended for v2: Fine-grained PAT scoped to selected repos.**

Create one fine-grained PAT with read/write access to all conductor-managed repos.
Required permissions per repo:
- Contents: Read and write (for branch creation, commits)
- Pull requests: Read and write
- Issues: Read and write
- Metadata: Read-only (required)
- Actions: Read-only (for CI status checks)

**Cross-organization limitation**: A fine-grained PAT can only access repos within a
single GitHub account/organization. For cross-org deployments, each org requires its own
PAT. Conductor's config schema must support per-repo or per-org token overrides.

### Token Distribution to Sub-Agents

The existing credential proxy pattern (`17-credential-proxy.md`) handles this:

1. Conductor holds the token(s) — not the sub-agents
2. Per sub-agent dispatch, conductor injects a scoped token (or uses the credential proxy)
3. For multi-repo, conductor selects the correct token based on `repo.owner` from config

**Implementation:**

```python
@dataclass
class RepoConfig:
    owner: str
    name: str
    github_token_env_var: str = "GITHUB_TOKEN"  # env var to read token from

def get_repo_token(repo: RepoConfig) -> str:
    return os.environ[repo.github_token_env_var]
```

This allows per-repo token env vars (e.g., `GITHUB_TOKEN_REPO_A`, `GITHUB_TOKEN_REPO_B`)
while defaulting to a shared token.

---

## Global vs. Per-Repo Concurrency Accounting

### Problem Statement

With a global concurrency cap of 5 agents, an orchestrator managing 3 repos could
monopolize all 5 slots for Repo A (most active), starving Repos B and C.

### Recommended: Global Pool with Per-Repo Soft Cap

```
Global concurrency cap: 5 (default, configurable)
Per-repo soft cap: min(global_cap, repo.max_agents) where repo.max_agents defaults to 3
```

**Algorithm:**

```python
def can_dispatch(repo: str, active_agents: dict[str, int], global_cap: int) -> bool:
    global_used = sum(active_agents.values())
    repo_used = active_agents.get(repo, 0)
    repo_cap = config.repos[repo].max_agents  # default 3

    return global_used < global_cap and repo_used < repo_cap
```

This ensures:
- No single repo consumes all global slots (per-repo cap)
- Global cap is respected (sum check)
- Repos with fewer active issues naturally yield capacity to busier repos

### Why Not Per-Repo Pools?

Per-repo pools (e.g., each repo gets 2 of 10 global slots) waste capacity when a repo
has no ready work. The soft-cap-per-repo + global-pool design allows burst capacity for
repos with many ready issues while maintaining fairness.

### Usage Quota Accounting

For Pro/Max subscription users, the 5-hour usage window (`08-usage-scheduling.md`) applies
to all sub-agents collectively — the Anthropic account doesn't distinguish which repo
generated the usage. Multi-repo deployments must treat the usage window as a global
resource, shared across all repos.

**Implication**: The conductor's usage governor (throttling based on remaining quota)
must be implemented at the global orchestrator level, not per-repo.

---

## CLAUDE.md Multiplexing Mechanism

### How CWD-Based Multiplexing Works

From `04-configuration.md`: `claude -p` discovers `CLAUDE.md` by walking up the directory
tree from the process CWD. Setting `cwd` to `/path/to/repo/` when spawning the sub-agent
causes it to load that repo's `CLAUDE.md` (plus ancestors, including `~/.claude/CLAUDE.md`).

No code change is needed for multi-repo CLAUDE.md multiplexing — it works automatically
when each sub-agent's CWD is set to the correct repo checkout.

### What Cannot Be Isolated

The user-level `~/.claude/CLAUDE.md` is **always loaded** for all sub-agents, regardless
of CWD. This file contains the global Orchestrator-Dispatch Protocol. It applies to all
repos. This is a feature, not a bug — the global protocol rules are intentionally
universal.

The managed policy CLAUDE.md (`/Library/Application Support/ClaudeCode/CLAUDE.md` on
macOS) is also always loaded. This cannot be excluded.

### Per-Repo CLAUDE.md Content

Each repo's `CLAUDE.md` (at project root) should contain only repo-specific overrides:
- Module label table
- Build commands
- Default branch name
- Repo-specific notes

The global protocol rules live in `~/.claude/CLAUDE.md` and do not need to be duplicated
per repo.

**Pattern for multi-repo CLAUDE.md structure:**

```
~/.claude/CLAUDE.md            # Global protocol (orchestrator rules, pipeline stages)
<repo-A>/CLAUDE.md             # Repo A module table, build commands
<repo-B>/CLAUDE.md             # Repo B module table, build commands
```

---

## Config Schema for Multi-Repo Setup

### Proposed `conductor.toml` Schema

```toml
# conductor.toml — located at ~/.conductor/conductor.toml or passed via --config

[global]
max_agents = 5          # Global concurrency cap
default_model = "claude-sonnet-4-6"
default_branch_strategy = "origin"   # Always branch from origin/<default_branch>

[[repos]]
owner = "bread-wood"
name = "breadmin-composer"
default_branch = "main"
max_agents = 3          # Per-repo soft cap; overrides global.max_agents / 2
github_token_env_var = "GITHUB_TOKEN"
priority = 1            # Lower = higher priority (1 is highest)
milestone = "v2"        # Active milestone; selects issues within this milestone

[[repos]]
owner = "bread-wood"
name = "breadwinner-mcp"
default_branch = "main"
max_agents = 2
github_token_env_var = "GITHUB_TOKEN_BREADWINNER"
priority = 2
milestone = "v1"

[[repos]]
owner = "bread-wood"
name = "breadmin-conductor"
default_branch = "main"
max_agents = 2
github_token_env_var = "GITHUB_TOKEN"
priority = 1
milestone = "v2"
```

### Python Data Model

```python
from pydantic import BaseModel
from typing import Optional

class RepoConfig(BaseModel):
    owner: str
    name: str
    default_branch: str = "main"
    max_agents: int = 3
    github_token_env_var: str = "GITHUB_TOKEN"
    priority: int = 1
    milestone: Optional[str] = None

class GlobalConfig(BaseModel):
    max_agents: int = 5
    default_model: str = "claude-sonnet-4-6"
    default_branch_strategy: str = "origin"

class ConductorConfig(BaseModel):
    global_: GlobalConfig = GlobalConfig()
    repos: list[RepoConfig] = []

    class Config:
        fields = {"global_": "global"}
```

This integrates with the existing pydantic-settings pattern from `04-configuration.md`.

### CLI Usage

```bash
# Run research-worker across all configured repos
conductor research-worker --config ~/.conductor/conductor.toml

# Run for a specific repo only
conductor research-worker --config ~/.conductor/conductor.toml \
  --repo bread-wood/breadmin-composer

# Override milestone
conductor research-worker --config ~/.conductor/conductor.toml \
  --milestone v3
```

---

## Prioritization Policy

### Dispatch Order Algorithm

When multiple repos have ready (unblocked, unassigned) issues, conductor dispatches in
this order:

1. **Highest priority repos first** (lower priority number = higher priority)
2. **Within same priority: oldest open issue first** (ascending by issue number)
3. **Respect per-repo soft cap** (do not dispatch to a repo already at its cap)

```python
def select_next_issues(
    ready_issues: dict[str, list[Issue]],  # repo -> list of ready issues
    active_agents: dict[str, int],         # repo -> active agent count
    config: ConductorConfig,
) -> list[tuple[str, Issue]]:
    """Select up to global_cap issues to dispatch, respecting per-repo caps."""
    selected = []
    global_used = sum(active_agents.values())

    # Sort repos by priority
    sorted_repos = sorted(
        config.repos,
        key=lambda r: (r.priority, r.owner + "/" + r.name)
    )

    for repo_cfg in sorted_repos:
        repo_key = f"{repo_cfg.owner}/{repo_cfg.name}"
        if global_used >= config.global_.max_agents:
            break  # Global cap reached

        repo_cap = repo_cfg.max_agents
        repo_used = active_agents.get(repo_key, 0)
        available_slots = min(
            config.global_.max_agents - global_used,
            repo_cap - repo_used
        )

        issues = ready_issues.get(repo_key, [])
        for issue in issues[:available_slots]:
            selected.append((repo_key, issue))
            global_used += 1

    return selected
```

### Anti-Starvation Guarantee

With per-repo soft caps, a high-priority repo with many ready issues cannot indefinitely
block lower-priority repos. The soft cap limits its slot consumption, leaving room for
lower-priority repos to dispatch.

For stricter fairness, a token-bucket approach can be implemented in v3: each repo
accumulates dispatch tokens at a rate proportional to its priority, and can only dispatch
when it has a token. This prevents burst patterns from permanently favoring active repos.

---

## Follow-Up Research Recommendations

**[WONT_RESEARCH] Kubernetes-based multi-repo conductor scheduling**
Out of scope for v2. A single-process orchestrator with asyncio is sufficient.

**[WONT_RESEARCH] Cross-organization GitHub App token management**
Complex (requires GitHub App installation per org) and not needed for the current use
case (all repos under one organization/account). File as v3 research if cross-org support
is requested.

**[V2_RESEARCH] Config discovery for multi-repo: auto-detect repos from GitHub organization**
Should conductor support `--org bread-wood` to auto-discover all repos in an org rather
than requiring explicit `[[repos]]` entries? What filtering (topic-based, label-based)
prevents conductor from picking up repos that don't have the conductor config?

---

## Sources

- [Introducing Fine-Grained Personal Access Tokens — GitHub Blog](https://github.blog/security/application-security/introducing-fine-grained-personal-access-tokens-for-github/)
- [Permissions for Fine-Grained PATs — GitHub Docs](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens)
- [Managing Personal Access Tokens — GitHub Docs](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)
- [CI/CD Orchestration with Scheduler Design — prachub.com](https://prachub.com/interview-questions/design-a-ci-cd-pipeline-with-scheduler)
- [Sokovan Orchestrator: Reliable Session Scheduling — Backend.AI](https://www.backend.ai/blog/2025-12-how-we-solved-scheduling-problem-with-sokovan-orchestrator)
- [Multi-Agent AI Orchestration: Enterprise Strategy for 2025-2026 — onabout.ai](https://www.onabout.ai/p/mastering-multi-agent-orchestration-architectures-patterns-roi-benchmarks-for-2025-2026)
- [GitHub Actions Scheduling and Concurrency — AWS in Plain English](https://aws.plainenglish.io/how-github-actions-actually-schedules-and-runs-your-ci-cd-pipelines-b6be53caf955)

**Cross-references:**
- `04-configuration.md` — CWD-based CLAUDE.md discovery, environment isolation, pydantic-settings config model
- `08-usage-scheduling.md` — usage window limits (global Anthropic account, not per-repo)
- `17-credential-proxy.md` — GitHub token isolation per sub-agent
- `38-ci-server-deployment.md` — API key auth, CLAUDE_CONFIG_DIR isolation per job
