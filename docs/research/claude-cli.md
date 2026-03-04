# Research: Claude CLI Headless Mode

**Consolidated from:** #1, #2, #7, #12, #18, #31, #53, #73, #81
**Status:** Current
**Date:** 2026-03-04

---

## 1. Headless `-p` Mode

`claude -p "<prompt>"` runs Claude Code in headless (print) mode. No interactive terminal
is presented; the process reads the prompt, executes tools, and exits.

### 1.1 Core Invocation Pattern

```bash
claude \
  -p "<prompt>" \
  --output-format stream-json \
  --allowedTools "Bash,Read,Edit,Write,Glob,Grep" \
  --max-turns <N> \
  --dangerously-skip-permissions
```

### 1.2 `--output-format` Values

| Value | Description |
|-------|-------------|
| `stream-json` | Each event emitted as a JSON object on its own line (newline-delimited JSON stream) |
| `json` | Single JSON object on process exit (buffered; not recommended for long runs) |
| `text` | Plain text output only (no structured events) |

`stream-json` is required for cost accounting and error classification. The `result` event
in this format is the sole authoritative source for token counts and cost.

### 1.3 Exit Codes

| Code | Meaning | Recovery |
|------|---------|---------|
| `0` | Success | Proceed |
| `1` | General error (overloaded: rate limit, auth, execution, max-turns) | Inspect `result.subtype` |
| `2` | Authentication error | Fatal; check API key |
| `124` | `timeout(1)` killed the process | Retry with longer timeout |
| `130` | SIGINT | Operator interrupted; clean up |
| `137` | SIGKILL (OOM or forced kill) | Clean up; retry if transient |
| `143` | SIGTERM (watchdog kill) | Clean up; retry |

Exit code `1` is overloaded. Always read `result.subtype` from stream-json to classify the
actual error before choosing a recovery action.

### 1.4 `--session-id` Flag

`--session-id <UUID>` sets the session ID for the run. Without it, Claude Code generates a
random UUID. Useful for correlating logs across retries of the same logical task.

> Note: `--session-id` is documented but its interaction with `CLAUDE_CONFIG_DIR` isolation
> is version-dependent. Do not rely on it for cross-restart session continuity.
>
> Needs verification as of v0.1.0

### 1.5 `--disable-slash-commands`

Disables slash command processing (e.g., `/clear`, `/help`). Recommended for headless use
to prevent prompt injection via slash commands embedded in issue bodies.

### 1.6 `--no-session-persistence`

Prevents the session from being written to Claude Code's session store. Recommended for
isolated per-agent runs that should not accumulate history in `~/.claude/`.

### 1.7 Agent Tool in `-p` Mode

The `Agent` tool (formerly `Task`) is available in headless mode as of Claude Code v2.1.50+.
The `CLAUDE_CODE_ENABLE_TASKS=true` env var was required on older versions.

Key constraint: **sub-agents cannot spawn sub-agents**. Only one level of delegation is
supported by the in-process `Agent` tool.

`isolation:"worktree"` in agent definitions works in headless mode. Each sub-agent gets
its own git worktree, auto-cleaned if no changes were committed.

### 1.8 Subprocess Token Overhead

Under default conditions, each `claude -p` subprocess loads approximately 50,000 tokens of
context overhead (CLAUDE.md files from ancestor directories, plugin descriptions, MCP tool
catalogs).

**Mitigation:** Use `CLAUDE_CONFIG_DIR` set to a clean temp directory, plus explicit
`--allowedTools` to disable unnecessary MCP servers. Reduces overhead to ~5,000 tokens.

> Needs verification as of v0.1.0

### 1.9 CLAUDEMD Opt-Out in Headless Subprocesses

Setting `claudeMdExcludes` in `.claude/settings.json` suppresses CLAUDE.md files from
ancestor directories. Use this when sub-agents are working in a worktree whose parent
directory tree contains CLAUDE.md files intended only for the operator.

> Needs verification as of v0.1.0 — `claudeMdExcludes` behavior in headless mode

### 1.10 `--allowedTools` Reliability

`--allowedTools` is reliable in non-`bypassPermissions` mode. In `--dangerously-skip-permissions`
mode, the allowed tools list is advisory only — Claude Code may use additional tools not on
the list if the model decides to. Use `--permission-mode acceptEdits` for a stricter but less
permissive alternative.

### 1.11 Disallowed Tools Inheritance in Sub-Agents

When an orchestrator launches a sub-agent via `Agent(isolation:"worktree")`, the sub-agent
inherits the orchestrator's `disallowedTools` list from `.claude/settings.json`. This can
prevent expected tools from working in sub-agents if the orchestrator's settings are
restrictive.

Workaround: use `--allowedTools` explicitly on the sub-agent prompt rather than relying
on inherited settings.

### 1.12 `hasCompletedOnboarding` Bypass

New Claude Code installations present an onboarding flow on first run. In CI/headless
environments, set `"hasCompletedOnboarding": true` in `~/.claude/settings.json` (or the
`CLAUDE_CONFIG_DIR` settings file) to skip this.

> Needs verification as of v0.1.0

---

## 2. Sources

- Claude Code CLI reference (code.claude.com/docs/en/cli-reference)
- Claude Code Subagents documentation
- GitHub Issue #20463: Task tools not available in headless mode
- GitHub Issue #7091: Sub-agent ask user to approve an edit gets stuck
- GitHub Issue #28482: Agent hangs indefinitely mid-task
- Research files: #1, #2, #7, #12, #18, #31, #53, #73, #81
