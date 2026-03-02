# Research: Skill File Adaptation for -p Mode

**Issue**: #7
**Milestone**: M1: Foundation
**Status**: Complete
**Date**: 2026-03-02

---

## Executive Summary

The current `issue-worker.md` and `research-worker.md` skill files were written for
interactive Claude Code sessions, where slash-command invocation (`/issue-worker`) and
user-confirmation gates are natural. Adapting them to headless `-p` mode requires six
concrete changes: (1) removal of user-confirmation gates and replacement with auto-resolve
policies, (2) restructuring them as prompt content rather than slash-command skill files,
(3) explicit MCP tool grants in `--allowedTools`, (4) ensuring the Notion MCP server is
configured and reachable from the conductor's subprocess environment, (5) understanding
how CLAUDE.md is resolved and whether the orchestrator-level instructions load
automatically, and (6) using `--append-system-prompt-file` to inject conductor-level
context while preserving Claude Code defaults.

The most important architectural finding: **skill files as currently written are
slash-command-style skills intended for interactive mode**. In `-p` mode, user-invokable
slash commands are unavailable. The skill content must be passed directly as the `claude
-p` prompt or via `--append-system-prompt-file`, not invoked as a skill name.

---

## 1. Skill Files vs. -p Mode: Fundamental Incompatibility

### 1.1 How Skills Are Invoked Today

The official Claude Code documentation confirms:

> "User-invoked skills like `/commit` and built-in commands are only available in
> interactive mode. In `-p` mode, describe the task you want to accomplish instead."
>
> — [Run Claude Code programmatically](https://code.claude.com/docs/en/headless)

When a user types `/issue-worker` in interactive mode, Claude Code's Skill tool expands
the skill's `SKILL.md` body into the conversation context on demand. In `-p` mode, there
is no REPL, no slash-command menu, and no Skill tool invocation path. Passing
`claude -p "/issue-worker"` will cause Claude to interpret the string literally rather
than invoking the skill.

The current `issue-worker.md` and `research-worker.md` live in
`src/conductor/skills/` but they are **plain markdown files** (not in a `SKILL.md`
inside a named skill directory). They function effectively as prose instruction sets,
not as structured Claude Code skill packages with frontmatter.

### 1.2 Two Paths Forward

There are two architectural choices for adapting the skill content to `-p` mode:

**Path A — Prompt injection** (recommended for conductor): Pass the skill content
directly as the orchestrator's `-p` prompt string, supplemented by
`--append-system-prompt-file` for stable instructions. The conductor reads the skill
file at dispatch time and passes its content as the prompt to each orchestrator
`claude -p` invocation.

**Path B — Structured skill package**: Convert each skill file into a proper SKILL.md
package with YAML frontmatter (`context: fork`, `disable-model-invocation: true`) so
it can be discovered and preloaded by sub-agent definitions. This is more elegant
for interactive use but does not solve the `-p` invocation problem — skills cannot be
slash-invoked in headless mode regardless of their structure.

**Recommendation**: Use Path A. The conductor already dispatches workers with explicit
prompt strings. The skill file content should be the blueprint for that prompt string.

---

## 2. Required Changes to the Skill Files for -p Mode

### 2.1 "Ask User" Gate Removal (Critical)

Both skill files contain the same startup gate:

```markdown
- If orphaned work is detected (stale worktrees, in-progress issues without PRs),
  ask the user before cleaning up — this is the only confirmation gate.
```

In `-p` mode, there is no user to ask. Claude Code has no mechanism to pause, emit
a question to stdout, and wait for stdin input during a headless session. If an
agent reaches this gate and generates a user-question prompt, two outcomes are possible:
(a) it waits indefinitely (hanging the agent), or (b) it auto-resolves to some
in-context assumption that may not be correct.

**Required change**: Replace the "ask user" gate with a deterministic auto-resolve
policy. The policy must be encoded directly in the skill content.

**Recommended auto-resolve policy for the orchestrator:**

```markdown
3. Run startup checks per the Orchestrator-Dispatch Protocol:
   - Check for active worktrees: `git worktree list`
   - Check for in-progress issues: `gh issue list --state open --label in-progress`
   - Check for open PRs: `gh pr list --state open`
   - Orphaned work auto-resolve policy (headless mode — no user available):
     a. Stale worktree with NO corresponding open PR: remove automatically.
        (`git worktree remove --force <path>`)
     b. In-progress issue with open PR: leave it; it is active work.
     c. In-progress issue with NO open PR, branch has commits: log a warning
        and abandon the issue (remove in-progress label, do NOT delete branch —
        preserve the work for human review).
     d. In-progress issue with NO open PR, branch has NO commits: abandon the
        issue and delete the branch.
   - Proceed automatically after applying the above rules.
```

**Rationale for the policy**:
- Removing stale worktrees with no PR is safe — no committed work exists.
- In-progress + open PR = the sub-agent completed its work; safe to proceed.
- In-progress + no PR + commits = ambiguous; preserve the work, escalate via log.
- In-progress + no PR + no commits = failed or abandoned agent; clean up safely.

This eliminates the blocking gate while keeping the correctness guarantees of the
original check.

### 2.2 Full Diff / Annotation for `issue-worker.md`

**Before (lines 19-21)**:
```markdown
   - If orphaned work is detected (stale worktrees, in-progress issues without PRs),
     ask the user before cleaning up — this is the only confirmation gate.
   - If no issues detected, proceed automatically.
```

**After**:
```markdown
   - If orphaned work is detected (stale worktrees, in-progress issues without PRs),
     apply the headless auto-resolve policy (see below). No user confirmation is
     available in -p mode.
   - Auto-resolve policy:
     * Stale worktree + no open PR → `git worktree remove --force <path>`
     * In-progress issue + open PR → leave alone (active work)
     * In-progress issue + no PR + branch has commits → abandon issue
       (remove in-progress label, preserve branch for human review)
     * In-progress issue + no PR + branch has no commits → abandon issue,
       delete branch
   - Proceed automatically after applying all rules.
```

**Before (line 57)**:
```markdown
Launch sub-agents in parallel using `Agent(isolation: "worktree")`.
```

**After** (per the subprocess pattern established in `01-agent-tool-in-p-mode.md`):
```markdown
Launch sub-agents in parallel by dispatching `claude -p` subprocesses via Bash.
Each sub-agent runs in a pre-created git worktree
(`git worktree add .claude/worktrees/<N>-<slug> <N>-<slug>`).
Pass the sub-agent instructions template as the `-p` prompt, setting CWD to the
worktree path.
```

### 2.3 Full Diff / Annotation for `research-worker.md`

The same "ask user" gate (lines 22-25) needs identical replacement per Section 2.2.

**Before (line 73)**:
```markdown
Research agents are dispatched with `Agent(isolation: "worktree")` and given:
```

**After**:
```markdown
Research agents are dispatched via `claude -p` subprocesses (Bash tool). Each agent
runs in a pre-created worktree. The conductor passes the research prompt template as
the `-p` argument and sets CWD to the worktree path.
```

**Before (line 109)**:
```markdown
Launch sub-agents in parallel using `Agent(isolation: "worktree")`.
```

**After**: Same replacement as for issue-worker — use subprocess spawning.

### 2.4 Notion Report Section Applicability

Both skill files require posting a session report to Notion using
`mcp__notion__API-post-page`. This is not a user-facing interaction, so it does not
need to be removed. However, it **does** require Notion MCP to be available in the
orchestrator's session (see Section 4).

---

## 3. "Ask User" Gate Handling Strategy: Full Policy

### 3.1 Taxonomy of Confirmation Gates

The skill files contain exactly one class of interactive gate: "ask the user before
cleaning up." This gate is procedural, not architectural — it exists to prevent
accidental destruction of in-progress work when a human is watching. In headless mode,
the equivalent guard is a **conservative deterministic policy** that errs on the side
of preservation over deletion.

### 3.2 Gate Disposition Table

| Detected Condition | Original Action | Headless Policy |
|---|---|---|
| Stale worktree, no open PR | Ask before removing | Auto-remove (`git worktree remove --force`) |
| Stale worktree, open PR exists | Ask before removing | Leave worktree; PR is active |
| In-progress label, open PR | No gate (safe to proceed) | Proceed automatically |
| In-progress label, no PR, branch has commits | Ask before abandoning | Abandon label; preserve branch; log warning |
| In-progress label, no PR, no commits | Ask before cleaning | Abandon label; delete branch; log info |

### 3.3 Logging Requirements

All auto-resolve decisions must be logged as structured events with:
- `event_type: "orphan_auto_resolved"`
- `condition`: one of the five conditions above
- `action_taken`: description of what was changed
- `issue_number`: if applicable
- `branch_name`: the branch affected
- `worktree_path`: if applicable

This provides an audit trail for human review after sessions.

### 3.4 Future Gate Types to Watch For

If new confirmation gates are added to the skill files in the future, they must be
classified as:
- **Procedural gates** (like the orphan check): convert to deterministic policy
- **Architectural gates** (e.g., "should I take a different approach?"): the agent
  should not be writing these; if it does, the skill prompt is under-specified

---

## 4. Notion MCP in Headless Mode

### 4.1 MCP Server Availability in -p Mode

Claude Code's `-p` mode inherits MCP server configuration from the user-scope
`~/.claude.json` (user-scoped MCP config) and project-scoped `.mcp.json` at the CWD.
When conductor spawns an orchestrator as a subprocess, the MCP servers registered in
the user's `~/.claude.json` under the user scope are automatically available —
**provided** the subprocess inherits the correct `HOME` and the `~/.claude.json`
file has the Notion server configured at user scope.

Key distinction from `04-configuration.md` (Section 5.4): when conductor uses a
**custom `CLAUDE_CONFIG_DIR`** for subprocess isolation, MCP server configuration from
`~/.claude.json` is NOT inherited. The conductor must explicitly pass MCP config in
this case.

### 4.2 Ensuring Notion MCP Availability: Three Approaches

**Approach A — Rely on user-scope config (simplest)**:
The user registers the Notion MCP server at user scope via:
```bash
claude mcp add --transport http notion https://mcp.notion.com/mcp --scope user
```
When conductor spawns orchestrator subprocesses without a custom `CLAUDE_CONFIG_DIR`,
this config is automatically inherited. No additional CLI flags are needed.

**Approach B — Pass via `--mcp-config` (explicit, reproducible)**:
Conductor generates or ships a `notion-mcp.json` file and passes it:
```bash
claude -p "..." \
  --mcp-config ~/.conductor/mcp-configs/notion.json \
  --allowedTools "mcp__notion__*,Bash,Read,Edit,..."
```

Where `~/.conductor/mcp-configs/notion.json` contains:
```json
{
  "mcpServers": {
    "notion": {
      "type": "http",
      "url": "https://mcp.notion.com/mcp"
    }
  }
}
```

**Approach C — Strict MCP isolation with `--strict-mcp-config`**:
For maximum reproducibility, use `--strict-mcp-config` to ignore all user-configured
MCP servers and only use those provided via `--mcp-config`:
```bash
claude -p "..." \
  --strict-mcp-config \
  --mcp-config ~/.conductor/mcp-configs/notion.json \
  --allowedTools "mcp__notion__*,Bash,Read,Edit,..."
```

This is the most defensive option and removes the dependency on user-level
`~/.claude.json` configuration.

**Recommendation**: Use Approach B for the initial implementation. It is explicit
(reproducible regardless of user config) without requiring strict isolation that would
break other inherited settings. Upgrade to Approach C if the full `CLAUDE_CONFIG_DIR`
isolation pattern from `04-configuration.md` is adopted.

### 4.3 Notion MCP Authentication in Headless Context

The official Notion MCP uses OAuth 2.0. The `https://mcp.notion.com/mcp` endpoint
requires an authenticated session. In interactive mode, users complete the OAuth flow
via `/mcp` and tokens are stored securely.

In headless mode, OAuth interactive flows cannot be completed. The conductor must
ensure:
1. The user has pre-authenticated in interactive mode (`/mcp` → Authenticate → Notion)
   before running conductor headlessly
2. OAuth tokens are stored in the same config location that the headless subprocess will
   read (user-scope `~/.claude.json` or the `CLAUDE_CONFIG_DIR` if overridden)

**If `CLAUDE_CONFIG_DIR` is used for subprocess isolation**, the OAuth tokens stored
in the user's `~/.claude.json` will NOT be available to the subprocess. This is a
critical interaction between the isolation pattern and Notion MCP authentication.
Options:
- Pre-copy the relevant token from `~/.claude.json` into the isolated config dir
- Use a pre-shared static Bearer token instead of OAuth (requires Notion API key, not
  OAuth client)
- Scope the CLAUDE_CONFIG_DIR isolation to only cover session history, not MCP tokens
  (not currently supported — CLAUDE_CONFIG_DIR is all-or-nothing)

**Practical recommendation**: Do NOT use `CLAUDE_CONFIG_DIR` isolation for orchestrator
sessions (only for isolated worker agents). Orchestrators need access to user-scope
MCP tokens. Worker agents do not post to Notion, so they can safely run in isolation.

### 4.4 `--allowedTools` for Notion MCP

The Notion MCP server exposes tools with the naming pattern `mcp__notion__API-*`.
The tool used in both skill files is `mcp__notion__API-post-page`. To allow it:

```bash
# Specific (minimal-permission):
--allowedTools "mcp__notion__API-post-page"

# Wildcard (allow all Notion tools — simpler):
--allowedTools "mcp__notion__*"
```

The wildcard form is appropriate for the orchestrator, which may use additional Notion
tools in the future (e.g., reading existing session reports).

---

## 5. CLAUDE.md Resolution in -p Mode

### 5.1 Resolution Order (from `04-configuration.md` Section 1.2)

Cross-referencing the findings from `04-configuration.md`: CLAUDE.md is resolved
hierarchically from the CWD upward. For a conductor spawning an orchestrator in
`/path/to/repo/.claude/worktrees/main-session/` (or the repo root itself):

| Scope | File | Loads in -p? |
|---|---|---|
| Managed policy | `/Library/Application Support/ClaudeCode/CLAUDE.md` (macOS) | Yes — cannot exclude |
| User | `~/.claude/CLAUDE.md` | Yes — always loaded |
| Project (conductor repo) | `{cwd}/CLAUDE.md` or `{cwd}/.claude/CLAUDE.md` | Yes — if CWD is in conductor repo |
| Local project | `{cwd}/CLAUDE.local.md` | Yes — if present |

### 5.2 What Loads for the Orchestrator

The orchestrator is the `claude -p` process that runs the `issue-worker` or
`research-worker` instructions. Its CWD is typically the repo root or a worktree of
the target repo.

For the **conductor repo** itself (when conductor is running against its own repo):
- `~/.claude/CLAUDE.md` loads → the user's Orchestrator-Dispatch Protocol rules load
- `src/conductor/CLAUDE.md` or top-level `CLAUDE.md` loads → repo-specific module
  isolation rules load

For a **managed repo** (conductor managing `breadwinner-mcp` or `moot`):
- `~/.claude/CLAUDE.md` loads (always)
- The target repo's own `CLAUDE.md` loads (if the orchestrator's CWD is that repo)

**Key insight**: The `~/.claude/CLAUDE.md` that contains the Orchestrator-Dispatch
Protocol rules **does load automatically** in `-p` mode when the subprocess uses the
default user-scope configuration. This means the orchestrator inherits the user's
global rules — including the "Sub-agents NEVER merge" and "Only orchestrator merges"
rules — without needing to pass them explicitly.

This is **beneficial** for correctness: the user's `~/.claude/CLAUDE.md` contains
the behavioral rules the orchestrator should follow. The project-level
`CLAUDE.md` (this repo) adds repo-specific constraints (module isolation, testing
commands). Together, they compose correctly.

### 5.3 Controlling CLAUDE.md Loading

To prevent the user's global `~/.claude/CLAUDE.md` from loading (e.g., to run an
isolated sub-agent that should not inherit orchestrator rules), use:

```bash
claude -p "..." --setting-sources project,local
```

This loads project and local settings but not user-scope settings, which suppresses
`~/.claude/CLAUDE.md`.

Alternatively, `CLAUDE_CONFIG_DIR` isolation (from `04-configuration.md` Section 5.4)
prevents all user-scope files from loading. For **worker sub-agents** (which should not
inherit the orchestrator-level CLAUDE.md), this is the recommended isolation pattern.

### 5.4 Reference Resolution: `~/.claude/CLAUDE.md` in Skill Content

Both skill files reference `~/.claude/CLAUDE.md` by path:
```
per the Orchestrator-Dispatch Protocol in ~/.claude/CLAUDE.md
```

This reference is informational — it tells the model where to look for rules. In `-p`
mode, since the file loads automatically, this reference is valid and accurate. No
change is needed to the file path reference.

---

## 6. `--append-system-prompt-file` Best Practices and Format

### 6.1 What the Flag Does

From the CLI reference:

| Flag | Behavior | Modes |
|---|---|---|
| `--append-system-prompt-file` | Appends file contents to the default system prompt | Print only |

This flag preserves all of Claude Code's built-in instructions while adding additional
text at the end of the system prompt. It is the correct choice for injecting
conductor-level instructions without losing Claude Code defaults (tool descriptions,
output formatting rules, etc.).

The alternative `--system-prompt-file` **replaces** the entire system prompt — this
would remove Claude Code's built-in tool knowledge and is inappropriate for conductor
worker agents.

### 6.2 Format: Plain Text vs. Markdown

`--append-system-prompt-file` accepts any text file — there is no documented format
requirement. However, since Claude Code itself is trained on markdown and its existing
system prompt uses markdown formatting extensively, **markdown is strongly preferred**
for clarity and consistency.

Empirically, Anthropic's own examples (CLI reference) use `.txt` extension but the
content in those examples is plain prose. For conductor's use, use `.md` extension and
full markdown formatting (headings, bullet points, code blocks) for:
- Improved model comprehension of hierarchical instructions
- Visual distinction between sections (role, constraints, steps)
- Code examples formatted as fenced code blocks

### 6.3 Preamble: Not Required, But Helpful

There is no required preamble. However, a brief role declaration at the top of the
appended file helps Claude understand the context of the appended instructions relative
to the existing system prompt:

```markdown
## Conductor Sub-Agent Instructions

You are a sub-agent dispatched by breadmin-conductor. The following rules
supplement Claude Code's standard behavior for this session:

...
```

### 6.4 Recommended Structure for Conductor's Append File

The conductor should maintain a stable `~/.conductor/agent-base-instructions.md` file
that contains the sub-agent protocol extracted from `~/.claude/CLAUDE.md`. This avoids
redundancy (since `~/.claude/CLAUDE.md` already loads in `-p` mode) but provides an
explicit anchor for conductor-specific overrides.

For orchestrator sessions (issue-worker, research-worker), a lightweight append file
should inject only per-session context that is NOT in any CLAUDE.md:

```markdown
## Session Context

- **Repository**: {repo_owner}/{repo_name}
- **Active Milestone**: {milestone_name}
- **Session ID**: {session_id}
- **Dispatch Time**: {ISO8601 timestamp}

This orchestrator session manages {issue_type} issues in {repo_owner}/{repo_name}.
Apply the Orchestrator-Dispatch Protocol from ~/.claude/CLAUDE.md.
```

This is deliberately minimal because the project CLAUDE.md and `~/.claude/CLAUDE.md`
already provide the protocol rules.

### 6.5 File Size Limit Considerations

There is no documented hard limit on `--append-system-prompt-file` size, but the
appended content counts toward the context window. Keep append files under 2,000 tokens
(roughly 8,000 characters of plain text or 6,000 characters of markdown with
formatting overhead). The actual skill instructions (issue-worker / research-worker)
should be passed as the prompt, not in the append file.

---

## 7. Complete `--allowedTools` Lists

### 7.1 Tool Availability in -p Mode (Default)

By default in `-p` mode with `--dangerously-skip-permissions`, all standard built-in
tools are available:
- `Bash`, `Read`, `Edit`, `Write`, `Glob`, `Grep` — file system and shell operations
- `Agent` (formerly `Task`) — sub-agent spawning (if `CLAUDE_CODE_ENABLE_TASKS=true`
  or Claude Code >= v2.1.50)
- `WebFetch` — web content retrieval (if not disabled)
- `WebSearch` — web search (available on Sonnet 4+ with API key auth)
- MCP tools — only if servers are configured AND the tool is listed in `--allowedTools`

MCP tools are **not available by default** even if the server is running; they require
explicit listing in `--allowedTools`.

### 7.2 `--allowedTools` for the Orchestrator (issue-worker invocation)

```bash
claude -p "..." \
  --dangerously-skip-permissions \
  --allowedTools \
    "Bash,Read,Edit,Write,Glob,Grep,\
     mcp__notion__API-post-page,\
     mcp__github__*,\
     Agent"
```

Tool-by-tool rationale:
| Tool | Purpose |
|---|---|
| `Bash` | Run `git`, `gh`, sub-agent `claude -p` subprocess dispatch |
| `Read` | Read CLAUDE.md, skill files, issue body, test output |
| `Edit` | Fix conflicts during rebase |
| `Write` | Write worktree setup scripts, temp files |
| `Glob` | Discover files in repo |
| `Grep` | Search for patterns across files |
| `mcp__notion__API-post-page` | Post session report to Notion |
| `mcp__github__*` | Wildcard: interact with GitHub issues, PRs, labels (if GitHub MCP is configured) |
| `Agent` | Spawn sub-agents in-process (optional — only if using Agent tool pattern) |

**If using subprocess-based dispatch** (recommended per `01-agent-tool-in-p-mode.md`),
`Agent` is not needed — `Bash` covers the `claude -p` subprocess invocations.

### 7.3 `--allowedTools` for the Orchestrator (research-worker invocation)

Identical to issue-worker with one addition:
```bash
--allowedTools \
  "Bash,Read,Edit,Write,Glob,Grep,\
   WebSearch,WebFetch,\
   mcp__notion__API-post-page,\
   mcp__github__*,\
   Agent"
```

The research-worker orchestrator needs `WebSearch` and `WebFetch` to do gap analysis
and completeness checks on the research docs it receives. It may also need these to
look up issue references.

Note: Research **agents** (sub-processes) will need their own `--allowedTools` list
that includes `WebSearch` and `WebFetch` for their document research.

### 7.4 `--allowedTools` for Research Sub-Agents (worker processes)

```bash
claude -p "Research issue #N..." \
  --dangerously-skip-permissions \
  --allowedTools \
    "Bash(git *),Bash(gh *),\
     Read,Edit,Write,Glob,Grep,\
     WebSearch,WebFetch"
```

Research agents should NOT have `mcp__notion__*` — only the orchestrator posts to
Notion. The Bash restriction to `git *` and `gh *` prevents a worker from accidentally
running arbitrary system commands.

### 7.5 `--allowedTools` for Implementation Sub-Agents (issue-worker workers)

```bash
claude -p "Implement issue #N..." \
  --dangerously-skip-permissions \
  --allowedTools \
    "Bash(git *),Bash(gh *),Bash(npm *),Bash(uv *),\
     Read,Edit,Write,Glob,Grep"
```

Implementation workers need test runner access. Include the language-specific test
commands for the target repo. No MCP tools needed — workers should not post to Notion
or interact with GitHub via MCP (they use `gh` CLI via Bash).

---

## 8. How Conductor Assembles the Final Invocation

### 8.1 Invocation Assembly Pattern

The conductor assembles each orchestrator invocation from four layers:

```
Layer 1: claude -p <prompt>
         └── The full skill file content (issue-worker or research-worker),
             rendered with runtime substitutions:
             - {REPO}: owner/repo slug
             - {MILESTONE}: active milestone name
             - {SESSION_DATE}: YYYY-MM-DD

Layer 2: --append-system-prompt-file <path>
         └── Minimal session context (repo, milestone, session ID)
             Does NOT duplicate skill instructions (those are in Layer 1)

Layer 3: --allowedTools "..."
         └── Per-worker-type tool list (see Section 7)
             MCP tools listed explicitly

Layer 4: --mcp-config <path>  (if using Approach B from Section 4.2)
         └── Notion MCP server config
             Generated once at conductor startup, reused per invocation
```

### 8.2 Complete Orchestrator Invocation Template

```bash
claude -p \
  "$(conductor render-skill issue-worker \
       --repo "${REPO}" \
       --milestone "${MILESTONE}" \
       --date "$(date +%Y-%m-%d)")" \
  --dangerously-skip-permissions \
  --output-format stream-json \
  --max-turns 200 \
  --append-system-prompt-file "${CONDUCTOR_HOME}/session-context.md" \
  --mcp-config "${CONDUCTOR_HOME}/mcp-configs/notion.json" \
  --allowedTools "Bash,Read,Edit,Write,Glob,Grep,mcp__notion__API-post-page"
```

Where `conductor render-skill` is a conductor CLI command that:
1. Reads `src/conductor/skills/issue-worker.md`
2. Applies the auto-resolve policy substitutions (Section 2.2)
3. Substitutes `{REPO}`, `{MILESTONE}`, `{SESSION_DATE}` runtime values
4. Outputs the rendered markdown to stdout

### 8.3 Separate System Prompt vs. Append for Per-Invocation Context

The question from the issue: should conductor inject per-invocation context (current
repo, milestone) as `--system-prompt` or `--append-system-prompt-file`?

**Use `--append-system-prompt-file`, not `--system-prompt`.**

`--system-prompt` replaces all Claude Code defaults, including tool descriptions and
built-in behavioral guidance. The resulting session would have no knowledge of how to
use Bash, Read, Edit, etc. without those being re-described.

`--append-system-prompt-file` preserves all built-in Claude Code instructions and
adds the conductor-specific context on top. This is the correct layering.

**However**: per-invocation dynamic context (repo name, milestone, date) that changes
per-run should go in the `-p` prompt itself (the rendered skill content), NOT in the
append file. The append file should contain only static conductor-level policies that
apply across all invocations. This separation keeps the append file cacheable and
avoids regenerating it per-run.

### 8.4 Milestone and Repo Injection

The research-worker skill specifically requires milestone scoping. This context should
be injected at the top of the rendered skill prompt:

```markdown
## Session Parameters

- **Repository**: {REPO}
- **Active Milestone**: {MILESTONE}
- **Session Date**: {SESSION_DATE}

---

Start the research worker orchestrator...
[rest of research-worker.md content]
```

The conductor template engine substitutes `{REPO}`, `{MILESTONE}`, and
`{SESSION_DATE}` before passing the rendered string to `-p`.

---

## 9. Summary of Required Changes

| File | Change | Priority |
|---|---|---|
| `issue-worker.md` | Replace "ask user" gate with auto-resolve policy | Critical |
| `issue-worker.md` | Replace `Agent(isolation: "worktree")` with subprocess pattern | High |
| `research-worker.md` | Replace "ask user" gate with auto-resolve policy | Critical |
| `research-worker.md` | Replace `Agent(isolation: "worktree")` with subprocess pattern | High |
| `research-worker.md` | Remove argument-passing via slash command (`--milestone "M2"`) — replace with prompt template substitution | Medium |
| Conductor config | Add `CONDUCTOR_NOTION_MCP_CONFIG` path to settings schema | High |
| Conductor CLI | Add `render-skill` command that substitutes runtime values and applies headless policy | High |
| Conductor config | Document `--allowedTools` per worker type in `CONDUCTOR_ALLOWED_TOOLS` | Medium |

---

## 10. Cross-References

- **`01-agent-tool-in-p-mode.md`**: Section 3 (Subprocess Spawning) establishes the
  subprocess-based worker dispatch pattern. This document adopts that pattern
  throughout and specifies which `--allowedTools` are needed for each subprocess type.
  The `Agent` tool (Section 1.3) would replace some Bash subprocess calls if adopted,
  but the subprocess pattern remains the recommendation.

- **`04-configuration.md`**: Section 1.2 defines the CLAUDE.md resolution order used
  in Section 5 of this document. Section 2 defines `--append-system-prompt-file`
  semantics used in Section 6. Section 5.4 (`CLAUDE_CONFIG_DIR` isolation) informs the
  MCP authentication concern in Section 4.3.

---

## 11. Follow-Up Research Recommendations

### 11.1 Empirical Test: Notion MCP OAuth Tokens Across Config Isolation Boundaries

The interaction between `CLAUDE_CONFIG_DIR` subprocess isolation and Notion MCP OAuth
token availability is documented here as theoretical. An empirical test is needed:
1. Authenticate Notion MCP interactively
2. Spawn a `claude -p` subprocess with a custom `CLAUDE_CONFIG_DIR`
3. Verify whether `mcp__notion__API-post-page` succeeds or fails

**Suggested issue**: `Research: Notion MCP OAuth token availability with CLAUDE_CONFIG_DIR
subprocess isolation`

### 11.2 `--strict-mcp-config` Isolation for Worker Sub-Agents

This document recommends that worker sub-agents NOT have Notion MCP access. The `--strict-mcp-config` flag (combined with a minimal `--mcp-config` file) can enforce this.
Research should confirm:
- Does `--strict-mcp-config` prevent user-scope MCP servers from loading?
- Can it be combined with `CLAUDE_CONFIG_DIR` isolation?
- Is there a performance benefit to not loading unnecessary MCP servers in workers?

Issue #10 (`Research: --settings flag isolation and MCP config injection`) covers related ground — coordinate with that doc to avoid duplication.

### 11.3 `disable-slash-commands` and Skill Discovery in -p Mode

The CLI reference lists `--disable-slash-commands` as a flag that "disables all skills
and commands for this session." In `-p` mode, skill invocation is already unavailable
— but skill **descriptions** may still be loaded into the context window (per the
skills documentation: "Skill descriptions are loaded into context so Claude knows
what's available"). This consumes tokens unnecessarily.

Research question: Does `--disable-slash-commands` prevent skill descriptions from
loading into context in `-p` mode? If so, it should be included in all conductor
invocations to reduce token overhead.

**Suggested issue**: `Research: Token overhead of skill description loading in -p mode
and --disable-slash-commands mitigation`

### 11.4 Structured Skill Package Conversion (Path B)

This document recommends Path A (prompt injection) over Path B (SKILL.md package
conversion). However, Path B would enable more elegant skill management: skills could
be loaded by sub-agents using the `skills:` field in agent definitions (per the sub-agents
documentation: `skills: ["issue-worker"]`).

Research question: Is there a hybrid approach where skills are structured as proper
`SKILL.md` packages with frontmatter but their content is also accessible to conductor
for prompt injection?

**Suggested issue**: `Research: Hybrid skill architecture — SKILL.md package + conductor
prompt injection compatibility`

---

## 12. Sources

- [Run Claude Code programmatically — Claude Code Docs](https://code.claude.com/docs/en/headless) — Confirmation that user-invoked skills are unavailable in `-p` mode; `--append-system-prompt` and `--append-system-prompt-file` semantics
- [CLI reference — Claude Code Docs](https://code.claude.com/docs/en/cli-reference) — Complete flag reference including `--append-system-prompt-file` (print only), `--system-prompt-file` (print only), `--allowedTools`, `--strict-mcp-config`, `--mcp-config`, `--disable-slash-commands`; system prompt flags table
- [Extend Claude with skills — Claude Code Docs](https://code.claude.com/docs/en/skills) — Skill file format, frontmatter fields (`disable-model-invocation`, `context: fork`, `allowed-tools`); how skill descriptions load into context; confirmation that built-in commands are not available through the Skill tool in `-p` mode
- [Connect Claude Code to tools via MCP — Claude Code Docs](https://code.claude.com/docs/en/mcp) — MCP server scope hierarchy; `--strict-mcp-config`; MCP allowedTools naming convention (`mcp__server__tool`); wildcard patterns; `--mcp-config` flag
- [Connect to external tools with MCP — Claude API Docs](https://platform.claude.com/docs/en/agent-sdk/mcp) — MCP allowedTools wildcard syntax; `mcp__server-name__tool-name` pattern; `.mcp.json` automatic loading; MCP authentication patterns
- [Modifying system prompts — Claude API Docs](https://platform.claude.com/docs/en/agent-sdk/modifying-system-prompts) — `--append-system-prompt-file` vs. `--system-prompt-file` distinction; CLAUDE.md loading in `-p` mode vs. SDK
- [docs/research/01-agent-tool-in-p-mode.md](01-agent-tool-in-p-mode.md) — Subprocess spawning pattern; `Agent` tool availability; `--allowedTools` for orchestrators; worktree cleanup behavior in headless mode
- [docs/research/04-configuration.md](04-configuration.md) — CLAUDE.md resolution order; `--append-system-prompt-file` semantics; `CLAUDE_CONFIG_DIR` isolation; `CLAUDECODE=1` nesting issue; `--setting-sources` flag for suppressing user-scope CLAUDE.md
- [Claude Code GitHub Issue #5037: MCP servers in .claude/.mcp.json not loading properly](https://github.com/anthropics/claude-code/issues/5037) — Confirmed that `.mcp.json` at project root is the correct location; `.claude/.mcp.json` has known loading issues
- [Inside Claude Code Skills — Mikhail Shilkov](https://mikhail.io/2025/10/claude-code-skills/) — Internal mechanics of skill invocation: the Skill tool, on-demand prompt expansion, base path injection
- [Claude Skills Compared to Slash Commands — egghead.io](https://egghead.io/claude-skills-compared-to-slash-commands~lhdor) — Invocation model differences between skills and slash commands
- [Connecting to Notion MCP — Notion Docs](https://developers.notion.com/guides/mcp/get-started-with-mcp) — Notion MCP server endpoint, OAuth authentication requirements
- [Notion's hosted MCP server: an inside look — Notion Blog](https://www.notion.com/blog/notions-hosted-mcp-server-an-inside-look) — Notion MCP architecture and auth flow
- [GitHub Issue #26251: Skill with disable-model-invocation: true cannot be invoked by user via slash command](https://github.com/anthropics/claude-code/issues/26251) — Known issue with skill invocation control; context for understanding skill behavior
- [GitHub Issue #19751: context: fork in a skill breaks AskUserQuestion](https://github.com/anthropics/claude-code/issues/19751) — Bug where `context: fork` skills cannot use interactive prompts; confirms that forked skill contexts are headless-like
