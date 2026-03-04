# brimstone

Headless Claude Code orchestrator for automated GitHub issue workstreams.

Runs a multi-stage pipeline (plan → research → design → scoping → implementation)
against a target GitHub repo by invoking `claude -p` headlessly. Each stage dispatches
sub-agents in parallel, monitors their output, and merges results back to the default branch.

## Installation

```bash
uv sync
```

Requires Python 3.11+ and the following tools on `$PATH`:

- `claude` — Claude Code CLI (`claude -p` headless mode)
- `gh` — GitHub CLI (authenticated)
- `git`

## Pipeline

```
spec → init → plan → research → design → scoping → implementation
```

Each stage is triggered via `brimstone run --<stage>`.
See `CLAUDE.md` for the full orchestration protocol.

## Commands

```
brimstone init    Create a GitHub repo and set it up for the pipeline
brimstone run     Run one or more pipeline stages for a milestone
brimstone health  Preflight checks (credentials, repo state, active worktrees)
brimstone cost    Cost ledger summary
brimstone adopt   Adopt an existing repo (not yet implemented)
```

### `brimstone init`

Creates the GitHub repo (if it does not already exist), clones it locally, adds
`yeast-bot` as a collaborator, installs the CI workflow, creates issue labels, and
sets branch protection.

```bash
brimstone init OWNER/REPO
brimstone init OWNER/REPO --dry-run
```

Run once per new repository, then use `brimstone run --plan` to seed research issues.

### `brimstone run --plan`

Seeds a spec into the repo and decomposes it into `stage/research` GitHub issues.
The milestone name is inferred from the spec filename stem.

```bash
# Single milestone — milestone inferred from filename (v0.1.0-cold-start)
brimstone run --plan --repo OWNER/REPO \
              --spec /path/to/v0.1.0-cold-start.md

# Multiple milestones in one invocation
brimstone run --plan --repo OWNER/REPO \
              --spec /path/to/v0.1.0.md \
              --spec /path/to/v0.2.0.md \
              --spec /path/to/v0.3.0.md

# Explicit milestone name (overrides stem inference)
brimstone run --plan --repo OWNER/REPO \
              --spec /path/to/spec.md --milestone v0.1.0-cold-start
```

### `brimstone run`

```bash
# Research stage
brimstone run --research --repo OWNER/REPO --milestone "v0.1.0-cold-start"

# Design stage (after research completes)
brimstone run --design --repo OWNER/REPO --milestone "v0.1.0-cold-start"

# Implementation stage
brimstone run --impl --repo OWNER/REPO --milestone "v0.1.0-cold-start"

# All stages in order (research → design → impl)
brimstone run --all --repo OWNER/REPO --milestone "v0.1.0-cold-start"
```

Common flags: `--dry-run`, `--model <model-id>`, `--max-budget <usd>`.

### `--repo` resolution

| Invocation | Behaviour |
|---|---|
| *(no flag)* | Operates on the current working directory. Fails if cwd is not a git repo. |
| `--repo owner/name` | Clones the remote repo to a temp dir and operates on it. |
| `--repo path/to/local/dir` | Operates on the local directory. Fails if not a git repo. |

## Module Listing

```
src/brimstone/
├── cli.py          ← Click entry point: brimstone (subcommands: run, init, health, cost, adopt)
├── config.py       ← Config pydantic-settings model; env/flag resolution; subprocess env builder
├── runner.py       ← claude -p subprocess invocation; stream-json capture; RunResult
├── session.py      ← Session ID persistence and --resume logic
├── logger.py       ← Per-session JSONL logging and cost ledger
├── health.py       ← Preflight checks (claude, gh, git, credentials, worktrees)
└── skills/
    ├── impl-worker.md      ← Bundled system prompt for the implementation stage
    ├── research-worker.md  ← Bundled system prompt for the research stage
    ├── design-worker.md    ← Bundled system prompt for the design stage
    └── plan-milestones.md  ← Bundled system prompt for brimstone run --plan
```

## Key Types

- `Config` (`config.py`) — validated configuration; loaded from environment + CLI flags
- `Checkpoint` (`session.py`) — persisted session state for `--resume`
- `RunResult` (`runner.py`) — result of a single `claude -p` invocation
- `HealthReport` (`health.py`) — preflight check outcome with fatal/warn status
- `UsageGovernor` (`cli.py`) — enforces concurrency limits and rate-limit backoff

## Configuration

Set environment variables or create a `.env` file in the working directory:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key |
| `BRIMSTONE_GH_TOKEN` or `GH_TOKEN` | Yes | — | GitHub token passed to sub-agents |
| `BRIMSTONE_MODEL` | No | `claude-opus-4-6` | Claude model ID |
| `BRIMSTONE_MAX_BUDGET_USD` | No | `5.00` | USD budget cap per session |
| `BRIMSTONE_MAX_CONCURRENCY` | No | `5` | Max parallel sub-agents |
| `BRIMSTONE_AGENT_TIMEOUT_MINUTES` | No | `30` | Timeout per sub-agent |
| `BRIMSTONE_DEFAULT_BRANCH` | No | — | Enforce a specific default branch name |
| `BRIMSTONE_LOG_DIR` | No | `~/.brimstone/logs` | Session logs and cost ledger |
| `BRIMSTONE_CHECKPOINT_DIR` | No | `~/.brimstone/checkpoints` | Session checkpoints |

## Dependencies

- `click>=8.1` — CLI framework
- `pydantic>=2.0` — data validation
- `pydantic-settings>=2.0` — environment-based configuration
