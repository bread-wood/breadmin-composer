# Research: `--permission-prompt-tool` MCP Schema and Implementation for Headless Permission Handling

**Issue:** #31
**Milestone:** M1: Foundation
**Status:** Research Complete
**Date:** 2026-03-02
**Spawned From:** #14 (R-HANG-B)

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Background and Motivation](#background-and-motivation)
3. [What `--permission-prompt-tool` Does](#what---permission-prompt-tool-does)
4. [MCP Tool Schema: Request Format](#mcp-tool-schema-request-format)
5. [MCP Tool Schema: Response Format](#mcp-tool-schema-response-format)
6. [Permission Evaluation Order](#permission-evaluation-order)
7. [MCP Server Implementation: Architecture](#mcp-server-implementation-architecture)
8. [Minimal Python MCP Server Implementation](#minimal-python-mcp-server-implementation)
9. [Policy Design for Conductor](#policy-design-for-conductor)
10. [The `--permission-prompt-tool stdio` Control Protocol](#the---permission-prompt-tool-stdio-control-protocol)
11. [Interaction with `--allowedTools`, `--disallowedTools`, and `--dangerously-skip-permissions`](#interaction-with---allowedtools---disallowedtools-and---dangerously-skip-permissions)
12. [Integration with the OS Sandbox (doc #20)](#integration-with-the-os-sandbox-doc-20)
13. [Known Bugs and Limitations](#known-bugs-and-limitations)
14. [Comparison with Existing Defense Layers](#comparison-with-existing-defense-layers)
15. [Recommended Integration Pattern for Conductor](#recommended-integration-pattern-for-conductor)
16. [Contradictions with Other Docs](#contradictions-with-other-docs)
17. [Follow-Up Research Recommendations](#follow-up-research-recommendations)
18. [Sources](#sources)

---

## Executive Summary

The `--permission-prompt-tool` flag in Claude Code's `-p` (headless) mode delegates per-tool permission decisions to a named MCP tool rather than requiring `--dangerously-skip-permissions` or interactive input. The flag is **officially documented** in the CLI reference as of Claude Code v2.x but lacks a minimal worked example in the official documentation (GitHub Issue #1175, open as of March 2026).

Key findings:

- **The flag is functional** [DOCUMENTED] and has been independently verified at Claude Code v2.0.76+. It is listed in the official CLI reference table.
- **The MCP tool schema is clear** [DOCUMENTED via community research]: the tool receives `{tool_use_id, tool_name, input}` and must return `{"behavior": "allow", "updatedInput": {...}}` or `{"behavior": "deny", "message": "..."}` as a JSON string in the MCP tool response text.
- **The flag is only invoked for tools that pass through the static rule layers** [DOCUMENTED]: `--allowedTools`, `--disallowedTools`, and settings.json `allow`/`ask`/`deny` rules are evaluated first. The MCP tool is called only when no static rule matches.
- **The flag does NOT supersede `--dangerously-skip-permissions`** [DOCUMENTED]: the two flags operate at different layers of the permission evaluation stack. They can be combined, though combining them is redundant — `--dangerously-skip-permissions` auto-approves everything at Step 3 before the MCP tool would be called anyway.
- **The `updatedInput` field allows input sanitization** [DOCUMENTED]: the MCP tool can return a modified version of the tool input, enabling pre-execution sanitization (e.g., stripping unsafe flags from a git command, or redirecting a write to a safer path).
- **A critical protocol bug exists** [DOCUMENTED, GitHub Issue #320 on claude-agent-sdk-python]: if `updatedInput` is included in the response as an empty object `{}`, Claude CLI interprets this as a directive to replace the tool's arguments with an empty object. `updatedInput` must be omitted entirely (not set to `{}`) when the original input should be passed through unchanged.
- **The `--permission-prompt-tool stdio` variant** is a separate internal mechanism used by the Agent SDK's `canUseTool` callback — it is not the same as the MCP server approach and has its own bug (Issue #469: `can_use_tool` callbacks never fire in CLI v2.1.6+).
- **For breadmin-conductor**, the practical recommendation is: prefer `--disallowedTools` + PreToolUse hooks as the primary permission layer (per docs #19 and #06) with the OS sandbox as enforcement (doc #20). The `--permission-prompt-tool` MCP approach is best suited as a future upgrade path for per-tool policy granularity, but its current reliability issues (especially the control protocol mismatch and the empty `updatedInput` bug) mean it should be treated as experimental until a minimal smoke test confirms it works in the target environment.

---

## Background and Motivation

`14-hang-detection.md` (Section 8, R-HANG-B) identified `--permission-prompt-tool` as an open research gap:

> "This flag does not solve the hang problem for breadmin-conductor at this time. The recommended approach remains `--dangerously-skip-permissions` with a scoped `--allowedTools` allowlist... If Anthropic provides documentation for `--permission-prompt-tool`, it could eliminate permission-prompt hangs (Pattern P1) for tools outside the allowlist rather than requiring `--dangerously-skip-permissions`."

Pattern P1 (from doc #14) is the core motivation: in headless mode without `--dangerously-skip-permissions`, Claude Code hangs indefinitely when it encounters a tool call that requires interactive permission. The `--permission-prompt-tool` flag is designed to handle those permission decisions programmatically, substituting the interactive prompt with a call to a policy MCP server.

`06-security-threat-model.md` (T4 Bash Tool Scope Creep) identifies the security risk of using `--dangerously-skip-permissions` even alongside `--allowedTools` and `--disallowedTools`, given confirmed bugs where `--allowedTools` is ignored under bypassPermissions mode (Issue #12232, confirmed in doc #19).

The `--permission-prompt-tool` flag is a potential alternative to bypass mode that preserves per-decision granularity while still enabling fully headless operation.

---

## What `--permission-prompt-tool` Does

The `--permission-prompt-tool` flag is officially documented in the Claude Code CLI reference table [DOCUMENTED]:

```
--permission-prompt-tool    Specify an MCP tool to handle permission prompts
                            in non-interactive mode
```

**Usage:**
```bash
claude -p "implement feature X" \
  --mcp-config '{"mcpServers": {"conductor-policy": {"command": "python", "args": ["/path/to/policy_server.py"]}}}' \
  --permission-prompt-tool mcp__conductor-policy__check_permission \
  "implement feature X"
```

**What it replaces:** In interactive mode, Claude Code pauses and presents a terminal prompt when it wants to use a tool. In headless `-p` mode, there is no terminal for prompts. `--dangerously-skip-permissions` bypasses all prompts. `--permission-prompt-tool` provides a third option: redirect permission decisions to a named MCP tool that can apply policy logic without human interaction.

**The key insight:** The MCP tool is your policy engine. It receives every tool call that makes it through the static rules without a definitive allow/deny decision, and it returns a programmatic allow or deny based on whatever logic you implement.

---

## MCP Tool Schema: Request Format

When Claude Code needs a permission decision and routes it to the `--permission-prompt-tool`, it calls your MCP tool with the following input: [DOCUMENTED, verified via community research and UnknownJoe796/claude-code-mcp-permission]

```json
{
  "tool_use_id": "toolu_01AbCdEfGhIjKlMn",
  "tool_name": "Bash",
  "input": {
    "command": "git push -u origin 31-permission-prompt-tool",
    "description": "Push the feature branch to remote"
  }
}
```

**Field descriptions:**

| Field | Type | Description |
|-------|------|-------------|
| `tool_use_id` | string | Unique identifier for this tool invocation. Correlates with the Claude model's internal tool_use block. |
| `tool_name` | string | The name of the built-in tool being invoked. Known values: `Bash`, `Edit`, `Write`, `Read`, `Glob`, `Grep`, `WebFetch`, `mcp__<server>__<tool>`. |
| `input` | object | The complete parameters Claude is passing to the tool. Schema varies by tool — see below. |

**Common `input` schemas by tool type:** [DOCUMENTED, Agent SDK user-input reference]

| `tool_name` | Key `input` fields |
|-------------|-------------------|
| `Bash` | `command` (string), `description` (string, optional), `timeout` (number, optional) |
| `Write` | `file_path` (string), `content` (string) |
| `Edit` | `file_path` (string), `old_string` (string), `new_string` (string) |
| `Read` | `file_path` (string), `offset` (number, optional), `limit` (number, optional) |
| `WebFetch` | `url` (string), `prompt` (string) |
| `mcp__*__*` | Varies by MCP tool — tool-specific parameters |

---

## MCP Tool Schema: Response Format

Your MCP tool must return a JSON string as its text response content. The two valid response shapes are: [DOCUMENTED via Agent SDK user-input reference, confirmed by community implementations]

**Allow (no input modification):**
```json
{
  "behavior": "allow"
}
```

**Allow (with input modification):**
```json
{
  "behavior": "allow",
  "updatedInput": {
    "command": "git push -u origin 31-permission-prompt-tool",
    "description": "Push the feature branch to remote"
  }
}
```

**Deny:**
```json
{
  "behavior": "deny",
  "message": "Bash command blocked by conductor policy: git push --force is not permitted for sub-agents"
}
```

**Critical bug warning regarding `updatedInput`** [DOCUMENTED, Issue #320]:

If `updatedInput` is present as an empty object `{}`, Claude CLI replaces the tool's arguments with `{}`, causing the tool to receive no arguments at all. The correct behavior is:

- **To pass input through unchanged:** omit `updatedInput` entirely (`{"behavior": "allow"}`)
- **To modify input:** include `updatedInput` with the complete modified input object
- **Never include** `updatedInput: {}` — this empties the tool arguments

The `updatedInput` field is the mechanism for pre-execution sanitization. For example, a policy server could:
- Receive `Bash(command="git diff HEAD~1")` and allow it unchanged
- Receive `Bash(command="git push --force origin main")` and deny it with a message
- Receive `Bash(command="git push -u origin my-feature")` and allow it, but strip the `-u` flag if needed

---

## Permission Evaluation Order

[DOCUMENTED, official permissions page and Agent SDK permissions reference]

When Claude requests a tool, Claude Code evaluates permissions in this order:

```
Step 1: PreToolUse hooks
  → Can allow, deny, or continue to the next step
  → deny at this step prevents the tool call entirely

Step 2: Static permission rules (settings.json / --allowedTools / --disallowedTools)
  → Evaluated in order: deny rules first, then allow rules, then ask rules
  → First matching rule wins — deny takes absolute precedence
  → If a deny rule matches → tool is blocked immediately
  → If an allow rule matches → tool executes immediately
  → If an ask rule matches → escalate to next step

Step 3: Permission mode
  → bypassPermissions: auto-approves (--dangerously-skip-permissions)
  → dontAsk: auto-denies
  → acceptEdits: auto-approves file edits; other tools escalate
  → default: escalate to next step

Step 4: --permission-prompt-tool (MCP tool call)
  → Only reached if no rule matched and permission mode did not auto-resolve
  → MCP tool receives the request and returns allow/deny

Step 5: canUseTool callback (interactive fallback)
  → Used by the Agent SDK; not available in raw -p mode
  → In headless mode, this step is absent
```

**Critical implication:** The `--permission-prompt-tool` is called at **Step 4 only**. If `--dangerously-skip-permissions` is active (Step 3), the MCP tool is never called — bypass mode resolves the decision before reaching Step 4. If a `--disallowedTools` deny rule matches (Step 2), the MCP tool is never called. The MCP tool only fires for tool calls that make it through all prior layers without a definitive decision.

---

## MCP Server Implementation: Architecture

A `--permission-prompt-tool` MCP server is a lightweight JSON-RPC 2.0 process that communicates over stdio with Claude Code. It must:

1. Implement the MCP protocol (capability negotiation, tool listing, tool invocation)
2. Expose a single tool (e.g., `check_permission`) that receives the permission request and returns allow/deny JSON
3. Be stateless and fast — it runs synchronously, blocking Claude's execution until it responds
4. Handle errors gracefully — an exception or crash in the server causes the permission decision to fail, which defaults to deny [INFERRED from MCP error handling patterns]

**Transport:** stdio (JSON-RPC 2.0 over stdin/stdout), the standard MCP transport for local servers.

**Server naming convention:** The MCP tool name in `--permission-prompt-tool` must follow the format `mcp__<server-name>__<tool-name>`. [DOCUMENTED] The server name matches the key used in `--mcp-config`, and the tool name is the tool defined in the server's tools list.

Example: if the MCP config declares:
```json
{
  "mcpServers": {
    "conductor-policy": {
      "command": "python",
      "args": ["/conductor/policy_server.py"]
    }
  }
}
```

Then the correct `--permission-prompt-tool` value is `mcp__conductor-policy__check_permission` (where `check_permission` is the tool name defined inside `policy_server.py`).

---

## Minimal Python MCP Server Implementation

The following is a minimal working pattern for a Python stdio MCP server that implements the `--permission-prompt-tool` interface. This is synthesized from the documented schema and community examples, but **has not been empirically tested against the Claude Code CLI** at the time of writing (see R-31-A for the recommended smoke test).

[INFERRED from documented schema + community patterns — MCP SDK Python usage]

```python
#!/usr/bin/env python3
"""
conductor-policy-server.py
Minimal MCP server implementing --permission-prompt-tool for breadmin-conductor.

Usage:
  claude -p "$PROMPT" \
    --mcp-config '{"mcpServers": {"conductor-policy": {"command": "python", "args": ["/conductor/policy_server.py"]}}}' \
    --permission-prompt-tool mcp__conductor-policy__check_permission \
    --disallowedTools "Bash(env),Bash(printenv),Bash(curl *),Bash(wget *)" \
    "$PROMPT"

The MCP tool receives:
  {tool_use_id: string, tool_name: string, input: object}

And returns JSON string with either:
  {"behavior": "allow"}
  {"behavior": "allow", "updatedInput": {...}}
  {"behavior": "deny", "message": "..."}

IMPORTANT: omit updatedInput entirely when passing input unchanged.
Including "updatedInput": {} empties the tool arguments (Issue #320 bug).
"""

import asyncio
import json
import re
import sys
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# ---------------------------------------------------------------------------
# Policy configuration — customize for each worker type
# ---------------------------------------------------------------------------

# Issue-worker policy: explicit allowlist for bash commands
BASH_ALLOWLIST_PATTERNS: list[re.Pattern] = [
    re.compile(r'^git\s+(status|diff|add|commit|push|checkout|fetch|rebase|log|branch)'),
    re.compile(r'^gh\s+(issue|pr)\s+(view|list|create|checks)'),
    re.compile(r'^uv\s+(run|add)'),
    re.compile(r'^python'),
]

# Bash commands that are always denied regardless of other rules
BASH_DENYLIST_PATTERNS: list[re.Pattern] = [
    re.compile(r'\benv\b'),
    re.compile(r'\bprintenv\b'),
    re.compile(r'\bcurl\b'),
    re.compile(r'\bwget\b'),
    re.compile(r'\bnc\b(?:\s|$)'),
    re.compile(r'\beval\b'),
    re.compile(r'\bexec\b'),
    re.compile(r'rm\s+-rf'),
    re.compile(r'git\s+push\s+.*--force'),
    re.compile(r'git\s+push\s+.*origin\s+main'),
    re.compile(r'gh\s+pr\s+merge'),
    re.compile(r'gh\s+issue\s+edit'),
    re.compile(r'cat\s+~/'),
    re.compile(r'cat\s+\.env'),
    re.compile(r'--no-verify'),
]

# Tool-level allowlist: tools always allowed regardless of bash policy
TOOL_ALLOWLIST = {"Read", "Glob", "Grep"}

# Tool-level denylist: tools always denied (belt-and-suspenders)
TOOL_DENYLIST = {"WebFetch"}


def evaluate_permission(tool_name: str, tool_input: dict) -> tuple[str, str | None]:
    """
    Evaluate whether a tool call should be allowed or denied.

    Returns:
        ("allow", None) — allow the tool call, pass input unchanged
        ("allow", modified_input_json) — allow with modified input (JSON string)
        ("deny", reason) — deny the tool call, reason shown to model
    """
    # Tool-level allowlist
    if tool_name in TOOL_ALLOWLIST:
        return ("allow", None)

    # Tool-level denylist
    if tool_name in TOOL_DENYLIST:
        return ("deny", f"Tool {tool_name} is not permitted for this worker type")

    # Bash-specific policy
    if tool_name == "Bash":
        command = tool_input.get("command", "")

        # Check denylist first
        for pattern in BASH_DENYLIST_PATTERNS:
            if pattern.search(command):
                return ("deny", f"Bash command blocked by denylist pattern '{pattern.pattern}': {command[:100]}")

        # Check allowlist
        for pattern in BASH_ALLOWLIST_PATTERNS:
            if pattern.match(command):
                return ("allow", None)

        # Default: deny unknown bash commands (allowlist-first policy)
        return ("deny", f"Bash command not in allowlist: {command[:100]}")

    # Edit/Write tool: scope check
    if tool_name in ("Edit", "Write"):
        file_path = tool_input.get("file_path", "")
        # Block writes outside allowed scope (worktree path check)
        if any(forbidden in file_path for forbidden in [".claude/", ".github/", "~/"]):
            return ("deny", f"Write to {file_path} is outside allowed scope")
        return ("allow", None)

    # Default: deny unknown tool types
    return ("deny", f"Unknown tool type {tool_name}: not permitted")


async def main():
    """Start the MCP server with stdio transport."""
    server = Server("conductor-policy")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="check_permission",
                description="Evaluate whether a Claude Code tool call should be allowed or denied based on conductor policy",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "tool_use_id": {
                            "type": "string",
                            "description": "Unique identifier for this tool invocation"
                        },
                        "tool_name": {
                            "type": "string",
                            "description": "Name of the tool requesting permission"
                        },
                        "input": {
                            "type": "object",
                            "description": "Complete parameters for the tool call"
                        }
                    },
                    "required": ["tool_use_id", "tool_name", "input"]
                }
            )
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        if name != "check_permission":
            return [types.TextContent(
                type="text",
                text=json.dumps({"behavior": "deny", "message": f"Unknown tool: {name}"})
            )]

        tool_use_id = arguments.get("tool_use_id", "unknown")
        tool_name = arguments.get("tool_name", "")
        tool_input = arguments.get("input", {})

        decision, extra = evaluate_permission(tool_name, tool_input)

        if decision == "deny":
            response = {"behavior": "deny", "message": extra or "Denied by conductor policy"}
        elif extra is not None:
            # extra is a JSON string of modified input
            response = {"behavior": "allow", "updatedInput": json.loads(extra)}
        else:
            # IMPORTANT: omit updatedInput when passing input unchanged
            # Including "updatedInput": {} causes the CLI to empty the tool's arguments (Issue #320)
            response = {"behavior": "allow"}

        # Log the decision to stderr (not stdout, which is the JSON-RPC channel)
        print(
            f"POLICY [{tool_use_id[:8]}] {tool_name}: {decision}"
            + (f" | {extra[:80] if extra else ''}" if decision == "deny" else ""),
            file=sys.stderr
        )

        return [types.TextContent(type="text", text=json.dumps(response))]

    # Run the server with stdio transport
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
```

**Dependency:** requires `mcp` Python package (`uv add mcp`).

**Notes on this implementation:**
- The `evaluate_permission` function implements an allowlist-first policy for Bash (deny anything not explicitly allowed) and a passthrough policy for Read/Glob/Grep.
- Logging goes to stderr only — stdout is reserved for JSON-RPC protocol messages.
- The `updatedInput` field is deliberately omitted when passing input unchanged, per the Issue #320 bug fix.
- This server is stateless and synchronous from the caller's perspective — it blocks Claude until it responds.

---

## Policy Design for Conductor

The conductor spawns two worker types, each with different permission needs. The policy server should accept a worker type parameter (via environment variable or command-line argument) and apply the appropriate policy.

### Issue-Worker Policy

Issue workers implement GitHub issues: they read and edit source files, run tests, push branches, and create PRs.

**Bash allowlist (allow if matches ANY):**
```
git status
git diff *
git add *
git commit *
git push -u origin *          # allow push to feature branch only
git checkout *
git fetch origin
git rebase *
git log *
git branch *
gh issue view *
gh pr create *
gh pr view *
gh pr checks *
uv run pytest *
uv run ruff *
uv add *
python -m pytest *
```

**Bash denylist (deny if matches ANY — checked first):**
```
env | *                        # environment dump
printenv *                     # environment dump
curl *                         # network exfiltration
wget *                         # network exfiltration
nc *                           # network exfiltration
eval *                         # arbitrary code injection
exec *                         # arbitrary code injection
bash -c *                      # shell injection
sh -c *                        # shell injection
python -c *                    # inline code execution
node -e *                      # inline code execution
rm -rf *                       # destructive
git push --force *             # force push
git push * origin main *       # push to main
git push * --no-verify *       # bypass hooks
git commit * --no-verify *     # bypass hooks
gh pr merge *                  # merge (orchestrator only)
gh issue edit *                # label management (orchestrator only)
```

**Tool allowlist:**
```
Read, Glob, Grep
Edit(/src/**), Edit(/tests/**), Edit(/docs/**)
Write(/src/**), Write(/tests/**), Write(/docs/**)
```

**Tool denylist:**
```
WebFetch
Edit(.github/**), Edit(.claude/**)
Write(.github/**), Write(.claude/**)
```

### Research-Worker Policy

Research workers fetch web content, read broadly, and write only to `docs/research/`.

**Bash allowlist:**
```
git status
git add docs/research/*
git commit *
git push -u origin *
gh issue view *
gh issue list *
gh issue create *
gh pr create *
gh pr checks *
```

**Bash denylist:** (same as issue-worker)

**Tool allowlist:**
```
Read
Glob
Grep
Edit(/docs/research/**)
Write(/docs/research/**)
WebFetch(domain:github.com)
WebFetch(domain:anthropic.com)
WebFetch(domain:code.claude.com)
WebFetch(domain:platform.claude.com)
WebFetch(domain:owasp.org)
WebFetch(domain:genai.owasp.org)
WebFetch(domain:arxiv.org)
```

**Tool denylist:**
```
Edit(/src/**), Edit(/tests/**)
Write(/src/**), Write(/tests/**)
Edit(.github/**), Edit(.claude/**)
WebFetch (without domain restriction — catch-all deny for unspecified domains)
```

---

## The `--permission-prompt-tool stdio` Control Protocol

[DOCUMENTED, Issue #469 on claude-agent-sdk-python; partially INFERRED]

There are **two different mechanisms** that share similar flag names but are architecturally distinct:

### Mechanism A: MCP Server (`--permission-prompt-tool mcp__<server>__<tool>`)

This is the mechanism described in sections above. Claude Code calls a named MCP tool via JSON-RPC over a subprocess pipe. The MCP server processes the permission request and returns a JSON response. This is the mechanism applicable to breadmin-conductor.

### Mechanism B: stdio Control Protocol (`--permission-prompt-tool stdio`)

This is an internal mechanism used by the Claude Agent SDK (Python and TypeScript) when a `canUseTool` callback is provided. When the SDK sets `--permission-prompt-tool stdio`, the CLI is supposed to emit structured `control_request` events over stdout with `subtype: "can_use_tool"`, which the SDK intercepts and routes to the callback.

**Known bug with Mechanism B** [DOCUMENTED, Issue #469, open as of March 2026]: In CLI v2.1.6+, the `can_use_tool` control callbacks are never emitted even when `--permission-prompt-tool stdio` is set. Tool execution succeeds, but no `can_use_tool` events fire. This is a regression or protocol mismatch in the CLI.

**The control_request event schema (Mechanism B):**
```json
{
  "type": "control_request",
  "subtype": "can_use_tool",
  "tool_name": "Write",
  "input": {"file_path": "/tmp/test.txt", "content": "hello"},
  "request_id": "bd66e5e1-a64f-4e68-acbc-538583bb94bf"
}
```

**The corresponding control_response expected by the CLI:**
```json
{
  "type": "control_response",
  "response": {
    "subtype": "success",
    "request_id": "bd66e5e1-a64f-4e68-acbc-538583bb94bf",
    "response": {
      "behavior": "allow"
      // updatedInput omitted to preserve original arguments
    }
  }
}
```

**For conductor:** Mechanism B (stdio control protocol) is the SDK's internal approach and is currently broken in CLI 2.1.6+. Conductor should use Mechanism A (explicit MCP server) via `mcp__<server>__<tool>` naming.

---

## Interaction with `--allowedTools`, `--disallowedTools`, and `--dangerously-skip-permissions`

[DOCUMENTED from official permissions page and CLI reference; confirmed by community research]

### Interaction Summary

| Scenario | `--permission-prompt-tool` called? |
|----------|-----------------------------------|
| Tool matches `--disallowedTools` denylist | No — denied at Step 2 before MCP call |
| Tool matches `--allowedTools` allowlist | No — allowed at Step 2 before MCP call |
| `--dangerously-skip-permissions` active | No — auto-approved at Step 3 before MCP call |
| Tool matches `settings.json allow` rule | No — allowed at Step 2 |
| Tool matches `settings.json deny` rule | No — denied at Step 2 |
| No rule matches, no bypass mode | Yes — MCP tool called at Step 4 |

### Does `--permission-prompt-tool` supersede `--dangerously-skip-permissions`?

**No.** [DOCUMENTED] These flags operate at different layers:
- `--dangerously-skip-permissions` activates at Step 3 (permission mode)
- `--permission-prompt-tool` operates at Step 4 (dynamic resolution)

If both are set simultaneously, `--dangerously-skip-permissions` resolves all undecided tool calls at Step 3, so the MCP tool at Step 4 is never reached. Combining them is equivalent to using `--dangerously-skip-permissions` alone.

### Can `--permission-prompt-tool` replace `--dangerously-skip-permissions`?

**Potentially yes**, for headless operation — but only if the MCP policy server approves all tool calls that the agent legitimately needs. If the policy server does not approve a tool call, Claude hangs waiting for a decision (Pattern P1 from doc #14). The policy server must be exhaustive for the tools the agent uses.

In practice, the safest approach is:
1. Pre-approve known-safe tools via `--allowedTools` (avoid using in conjunction with `--dangerously-skip-permissions` due to Issue #12232 bug — see doc #19)
2. Pre-deny known-dangerous tools via `--disallowedTools` (this works under all modes)
3. Route remaining tool calls through the MCP policy server
4. Do NOT use `--dangerously-skip-permissions` — this is the point of using `--permission-prompt-tool`

However, note the confirmed bug in doc #19 (Issue #12232): `--allowedTools` is ignored under `bypassPermissions`. Since `--permission-prompt-tool` does not activate bypass mode, this bug should not affect the MCP approach. **This is a significant advantage of `--permission-prompt-tool` over `--dangerously-skip-permissions`** — static `--allowedTools` rules should function correctly.

**Caveat** [INFERRED]: This advantage is contingent on Issue #12232 being specific to `bypassPermissions` mode. If the bug affects allowlist enforcement at Step 2 regardless of permission mode, then `--allowedTools` remains unreliable. This should be verified empirically (see R-31-A).

---

## Integration with the OS Sandbox (doc #20)

[Cross-reference: `20-os-sandbox.md`]

The OS sandbox (Claude Code's native sandboxed Bash tool using macOS Seatbelt / Linux bubblewrap) and `--permission-prompt-tool` are **complementary layers** [DOCUMENTED from sandboxing docs]:

```
Layer 1: Input Sanitization
  ├── CLAUDE.md hash check
  ├── Issue body sanitization and XML delimiting
  └── Pre-run security scan checklist

Layer 2: Static Permission Rules (Step 2 of evaluation order)
  ├── --disallowedTools: deny known-dangerous tools/commands
  ├── settings.json deny rules: belt-and-suspenders
  └── PreToolUse hooks: runtime regex validation for Bash

Layer 3: Dynamic Permission Decisions (Step 4)
  ├── --permission-prompt-tool: MCP policy server
  │   ├── Allowlist-first policy for Bash
  │   ├── Tool-level allow/deny for Edit, Write, Read
  │   └── updatedInput for input sanitization before execution
  └── (No bypassPermissions — intentionally omitted)

Layer 4: OS-Level Sandbox (enforcement below the Claude process)
  ├── Filesystem: write restricted to worktree, read-only for system dirs
  ├── Network: domain-allowlisted proxy
  └── OS primitives: Seatbelt (macOS) / bubblewrap (Linux)
```

The OS sandbox is the **enforcement fallback** — if the MCP policy server makes an incorrect allow decision (e.g., due to a novel command pattern not in the allowlist), the OS sandbox still prevents the command from accessing disallowed filesystem paths or making unauthorized network connections.

**Key difference from the `--dangerously-skip-permissions` architecture (doc #06):**

| Aspect | `--dangerously-skip-permissions` | `--permission-prompt-tool` |
|--------|----------------------------------|----------------------------|
| Layer 2 (`--allowedTools`) | Broken due to Issue #12232 | Should work (not in bypass mode) |
| Layer 3 decision | Auto-approve everything | Policy server decides per-call |
| Granularity | Binary (all or nothing) | Per-tool, per-command |
| MCP hook failure risk | N/A (bypass mode skips hooks) | MCP crash → denied by default |
| OS sandbox complementary? | Yes (mandatory) | Yes (defense-in-depth) |

---

## Known Bugs and Limitations

### Bug 1: `updatedInput: {}` empties tool arguments

**Severity: CRITICAL** [DOCUMENTED, Issue #320 on claude-agent-sdk-python]

If the policy server returns `{"behavior": "allow", "updatedInput": {}}`, Claude CLI replaces the tool's arguments with an empty object. Tools receiving empty arguments will fail or behave unexpectedly.

**Mitigation:** Omit `updatedInput` entirely when passing input unchanged. Include it only when explicitly modifying the input. The minimal response for "allow with no changes" is `{"behavior": "allow"}`.

### Bug 2: `--permission-prompt-tool stdio` control protocol broken in CLI 2.1.6+

**Severity: HIGH** [DOCUMENTED, Issue #469 on claude-agent-sdk-python, open as of March 2026]

The `--permission-prompt-tool stdio` mechanism (used by the Agent SDK's `canUseTool` callback) does not emit `control_request` events in current CLI versions. Tool calls succeed without invoking the callback.

**Mitigation:** Use the explicit MCP server mechanism (`mcp__<server>__<tool>`) instead of `stdio`. This is the correct approach for conductor.

### Bug 3: No minimal documented example

**Severity: MEDIUM** [DOCUMENTED, Issue #1175 on anthropics/claude-code, open as of March 2026]

Anthropic's official documentation lists the flag but provides no worked example of implementing the MCP server. Community implementations exist (UnknownJoe796/claude-code-mcp-permission, CCO-MCP) but are JavaScript/TypeScript; no Python minimal example is officially provided.

**Mitigation:** The implementation in this document provides a Python pattern based on the documented schema.

### Bug 4: MCP server crash behavior

**Severity: MEDIUM** [INFERRED from MCP error handling patterns]

If the policy MCP server process crashes or fails to respond, the permission decision defaults to deny. In headless mode, an unrecoverable deny causes Claude Code to hang (Pattern P1 from doc #14) waiting for a decision that never comes, or to abort the tool call with an error.

**Mitigation:**
1. Add a health-check as part of the pre-flight startup sequence (confirm the server starts correctly before launching the claude -p process)
2. Use Python exception handling broadly in the server to ensure it always returns a valid JSON response, even on error:
   ```python
   try:
       response = evaluate_permission(tool_name, tool_input)
   except Exception as e:
       response = {"behavior": "deny", "message": f"Policy server error: {e}"}
   ```

### Bug 5: `--allowedTools` reliability under non-bypass mode

**Severity: UNKNOWN** [INFERRED; needs empirical verification]

Issue #12232 confirms `--allowedTools` is broken under `bypassPermissions` mode. Whether it works correctly in the non-bypass mode used with `--permission-prompt-tool` is not confirmed by existing research. If it is also broken in non-bypass mode, then the static allow rules at Step 2 don't fire, and all allow decisions fall through to the MCP tool.

**Mitigation:** Until verified empirically, treat `--allowedTools` as unreliable and implement the full allowlist logic in the MCP policy server. Rely on `--disallowedTools` for the highest-risk denials (confirmed working under all modes per doc #19).

### Limitation 1: Synchronous blocking

The MCP tool call is synchronous — Claude Code blocks until the policy server responds. A slow policy server (e.g., one that calls an external API for approval) directly adds latency to every tool call. [DOCUMENTED]

**Mitigation:** Keep the policy server in-process with fast regex matching (no I/O in the critical path). Async external approval workflows (Slack messages, email) are not suitable for this synchronous interface.

### Limitation 2: Subagent inheritance

[INFERRED from `bypassPermissions` subagent inheritance behavior, documented in Agent SDK]

The official documentation notes for `bypassPermissions`: "When using bypassPermissions, all subagents inherit this mode." It is unclear whether `--permission-prompt-tool` is inherited by Task/Agent tool subagents. If not inherited, subagents may bypass the policy server entirely.

**Mitigation:** Verify empirically whether subagents spawned by the main claude -p process use the same permission-prompt-tool setting.

---

## Comparison with Existing Defense Layers

[Cross-references: `06-security-threat-model.md`, `19-pretooluse-reliability.md`]

The existing architecture (from docs #06 and #19) uses:
- `--disallowedTools` for hard denials (confirmed working)
- PreToolUse hooks for Bash command validation (confirmed working for Bash)
- `--dangerously-skip-permissions` + `--disallowedTools` as the combined approach
- OS sandbox as enforcement layer

The `--permission-prompt-tool` approach would replace this with:
- `--disallowedTools` for hard denials (still needed; belt-and-suspenders)
- MCP policy server for per-call decisions (replaces `--dangerously-skip-permissions`)
- PreToolUse hooks for additional Bash validation (can still be layered)
- OS sandbox as enforcement layer (still mandatory)

**Advantages of `--permission-prompt-tool` over current approach:**
1. No `bypassPermissions` mode → `--allowedTools` should function correctly (Issue #12232 is bypass-mode-specific)
2. Per-call policy logic with access to the complete tool input (e.g., can inspect the actual Bash command, not just the tool name)
3. `updatedInput` enables input sanitization before execution (no equivalent in current approach)
4. Cleaner security posture: no "bypass everything" flag in the invocation

**Disadvantages:**
1. Additional process overhead (MCP server subprocess)
2. New failure mode: MCP server crash → permission hang
3. Unverified reliability — no smoke test has been run against the current CLI version
4. The `--permission-prompt-tool stdio` bug (Issue #469) suggests the broader permission control protocol has reliability issues

**Recommendation:**
- **Short term:** Keep the existing `--disallowedTools` + PreToolUse hooks + `--dangerously-skip-permissions` architecture (docs #06, #19). It is battle-tested and the failure modes are known.
- **Medium term:** Add the MCP policy server as a Layer 3 replacement for `--dangerously-skip-permissions`, after empirical verification confirms the MCP mechanism works correctly in the target CLI version (see R-31-A).

---

## Recommended Integration Pattern for Conductor

When `--permission-prompt-tool` is ready for production use (after R-31-A smoke test passes):

```bash
# Issue-worker invocation (with MCP policy server, no bypass mode)
claude -p "$PROMPT" \
  --mcp-config "/conductor/mcp-config.json" \
  --permission-prompt-tool "mcp__conductor-policy__check_permission" \
  --disallowedTools "Bash(env),Bash(printenv),Bash(curl *),Bash(wget *),Bash(nc *),Bash(eval *),Bash(exec *),Bash(rm -rf *),Bash(git push --force *),Bash(git push * origin main *),Bash(gh pr merge *),Bash(gh issue edit *),WebFetch" \
  --output-format stream-json \
  --max-turns "$CONDUCTOR_MAX_TURNS" \
  --max-budget-usd "$CONDUCTOR_MAX_BUDGET" \
  "$PROMPT"
```

Where `/conductor/mcp-config.json`:
```json
{
  "mcpServers": {
    "conductor-policy": {
      "command": "python",
      "args": ["/conductor/policy_server.py", "--worker-type", "issue"],
      "env": {
        "POLICY_LOG_DIR": "/var/log/conductor/policy"
      }
    }
  }
}
```

**Pre-flight check:** Before spawning the agent, verify the MCP server starts and responds:
```bash
echo '{"method": "tools/list", "id": 1, "jsonrpc": "2.0"}' | python /conductor/policy_server.py
# Expected: {"jsonrpc": "2.0", "id": 1, "result": {"tools": [...]}}
```

---

## Contradictions with Other Docs

### Contradiction with doc #06 (Security Threat Model)

Doc #06 states: "Note: `--dangerously-skip-permissions` is used here only because the explicit `--allowedTools` + `--disallowedTools` policy is in place. The bypass mode does not skip the deny rules — deny rules always take precedence."

This research confirms that `--allowedTools` is broken under `bypassPermissions` (Issue #12232, also documented in doc #19). The statement that `--allowedTools` provides an effective defense layer under bypass mode is **incorrect** based on the confirmed bug. Doc #06 should be updated to note that `--allowedTools` provides no protection under bypassPermissions and that the full security burden falls on `--disallowedTools` + PreToolUse hooks.

The `--permission-prompt-tool` approach actually improves on this by operating outside of bypass mode, where `--allowedTools` may work correctly.

### Consistent with doc #14

Doc #14 (Section 8) correctly identified this as an open research gap and assessed: "This flag does not solve the hang problem for breadmin-conductor at this time." This research confirms that assessment was correct at the time of writing, but the flag is now documented and has a working schema. The main remaining blocker is the lack of a Python smoke test and the open Issue #1175 requesting official examples.

### Consistent with doc #19

Doc #19 confirmed `--allowedTools` is broken under bypass mode (Issue #12232). This research adds: the `--permission-prompt-tool` approach avoids bypass mode entirely and therefore the allowlist breakage may not apply. This is an additive finding, not a contradiction.

---

## Empirical Verification (Issue #60)

**Issue:** #60
**Date:** 2026-03-02
**Research method:** Web-based research aggregation — no live CLI execution was performed. Findings synthesize community test reports, SDK issue trackers, official documentation, and published implementation references against the current CLI version (v2.1.63 as of 2026-02-27).

This section upgrades specific [INFERRED] and [DOCUMENTED-not-tested] claims from the sections above. Outstanding gaps requiring direct CLI execution are noted.

---

### V1: Basic Invocation — Does routing to the MCP tool actually happen?

**Finding: CONFIRMED** [DOCUMENTED — verified at CLI v2.0.76; corroborated by active production use in CLI 2.1.x]

Evidence:

- **UnknownJoe796/claude-code-mcp-permission** (December 26, 2025, CLI v2.0.76): The author built and tested a working MCP server. The `check_permission` tool was called by Claude Code for every tool request that made it through the static rules. This is the only reported direct CLI smoke test against a real `mcp__<server>__<tool>` endpoint.

- **toolprint/cco-mcp** (January–February 2026, CLI 2.1.x range): A production-level audit-and-approval MCP server built for `--permission-prompt-tool mcp__cco-mcp__approval_prompt`. This project would not be in active community use if routing were broken.

- **GitHub Issue #1175** (originally filed May 2025, closed as completed): The community confirmed the response schema `{"behavior": "allow", "updatedInput": {...}}` and `{"behavior": "deny", "message": "..."}` work. No regression has been reported against CLI 2.1.x.

**Upgraded from:** [DOCUMENTED-not-tested]
**Confidence level:** MEDIUM-HIGH — routing confirmed working; no regression filed against 2.1.x; a production implementation uses it without reported issues.

**Remaining gap:** No independent tester has published a log confirming `mcp__<server>__<tool>` routing fires in CLI 2.1.50+ with the `--settings` flag injection pattern conductor uses.

---

### V2: Deny Behavior — Does `{"behavior": "deny"}` block cleanly or hang?

**Finding: CONFIRMED CLEAN BLOCK** [DOCUMENTED via Agent SDK; INFERRED from community implementations]

The official Agent SDK user-input documentation (`platform.claude.com/docs/en/agent-sdk/user-input`) documents the deny response and states: "Claude sees this message and may adjust its approach." This implies clean error delivery, not a hang.

A P1 hang occurs only if the MCP server fails to respond at all (crash, timeout), not when it returns `{"behavior": "deny"}`. The server returns an immediate synchronous JSON-RPC response — there is no blocking wait inherent to the deny path. No GitHub issue or community report was found describing a P1 hang triggered specifically by a deny response from a permission-prompt-tool MCP server.

**Upgraded from:** [INFERRED] to **INFERRED-HIGH**

---

### V3: Allow Behavior (No `updatedInput`) — Does omitting `updatedInput` preserve original input?

**Finding: CONFIRMED at protocol level** [DOCUMENTED — Agent SDK docs; corroborated by Issue #320 fix analysis]

The official Agent SDK documentation states: when `updatedInput` is omitted, "the original input is preserved and used as-is." A commit titled `fix(daemon): omit updatedInput in permission allow response` was referenced in Issue #320 comments, confirming the CLI treats an absent `updatedInput` key as "use original arguments" and `"updatedInput": {}` as "replace with empty object." The omit-vs-empty distinction is enforced at the CLI level.

**Upgraded from:** [DOCUMENTED] — this verification adds protocol-level confirmation that the omit behavior is enforced by the CLI.

---

### V4: Allow Behavior (With `updatedInput`) — Does modified input reach the tool correctly?

**Finding: CONFIRMED** [DOCUMENTED — Agent SDK docs; confirmed working at v2.0.76]

The official documentation (`platform.claude.com/docs/en/agent-sdk/user-input`, "Approve with changes" tab) provides a working code example using `updatedInput` to sandbox Bash command paths. UnknownJoe796 explicitly confirmed input modification as a verified feature at CLI v2.0.76. No regression reports for `updatedInput` found in CLI 2.1.x.

**Critical note:** `updatedInput` is a complete replacement, not a merge.

**Upgraded from:** [DOCUMENTED] to confirmed in community testing.

---

### V5: Interaction with `--disallowedTools` — Does the denylist fire before the MCP call?

**Finding: CONFIRMED** [DOCUMENTED from multiple independent sources]

The official permissions page documents evaluation order: deny rules (Step 2) fire before the MCP tool (Step 4). LobeHub reference documentation states explicitly: "If a tool matches allowedTools or disallowedTools in settings.json or CLI flags, the MCP tool is never called." UnknownJoe796 confirms: "Static deny rules checked first; if matched, DENY immediately, skip remaining layers." No community report of `--disallowedTools` failing to intercept before the MCP call.

**Upgraded from:** [DOCUMENTED-not-tested] to CONFIRMED.

---

### V6: First-Launch Reliability — Is there a first-launch initialization bug for Mechanism A?

**Finding: NO ANALOGOUS BUG FOUND** [INFERRED from absence of reports]

No GitHub issue, community report, or blog post was found describing a first-launch initialization failure for `--permission-prompt-tool mcp__<server>__<tool>`. This contrasts with the hook initialization bug (Issue #10385), which was reported and confirmed by multiple users. Mechanistically, the MCP server initialization is architecturally different from hook initialization: the hook system reads from `settings.json` at startup; the MCP server mechanism starts a subprocess via `--mcp-config` at invocation time.

**Assessment:** Confidence level MEDIUM. Pre-flight health check (Bug 4 mitigation) covers this risk regardless.

---

### V7: Reliability vs. CLI 2.1.6+ stdio Breakage — Does It Affect Mechanism A?

**Finding: NO IMPACT ON MECHANISM A** [DOCUMENTED — the stdio breakage is specific to Mechanism B]

Issue #469 (filed January 13, 2026, OPEN): The `can_use_tool` callback never fires in CLI v2.1.6-2.1.7 when using `--permission-prompt-tool stdio` (Mechanism B). This bug is specific to the stdio control protocol. Mechanism A (MCP server via `mcp__` naming) communicates via JSON-RPC over a subprocess pipe. CCO-MCP uses Mechanism A and has been reported working in CLI 2.1.x without issues during the same period.

**Key implication:** The concern in the executive summary ("suggests the broader permission control protocol has reliability issues") is overstated. The reliability issue is localized to Mechanism B's stdout event emission, not to the MCP JSON-RPC path.

**Upgraded from:** [DOCUMENTED] to confirmed mechanistic independence.

---

### V8: Subagent Inheritance of `--permission-prompt-tool`

**Finding: NOT INHERITED** [DOCUMENTED + INFERRED-HIGH from corroborating bug reports]

See Section 19 for the full analysis (Issue #62 research). Summary: `--permission-prompt-tool` is a CLI-level flag that is not passed to in-process Task/Agent tool subagents. Issues #25000, #18950, and #21460 collectively confirm that subagents do not inherit the parent session's permission configuration. The subagent frontmatter schema has no `permissionPromptTool` field.

**Impact for conductor:** Since conductor uses subprocess spawning (external `claude -p` processes), each subprocess can receive its own `--permission-prompt-tool` flag. The limitation matters only for in-process Task/Agent tool delegation.

**Upgraded from:** [INFERRED] to [DOCUMENTED + INFERRED-HIGH].

---

### V9: `--allowedTools` Reliability in Non-Bypass Mode

**Finding: UNKNOWN — not resolved by this research** [needs empirical test]

No new evidence was found. Issue #12232 (broken under bypassPermissions) remains the only empirical data point. Issue #61 tracks this directly.

---

### Summary: Confidence Level Upgrades

| Claim | Original Status | Updated Status |
|-------|----------------|----------------|
| MCP tool routing fires when no static rule matches | [DOCUMENTED-not-tested] | [CONFIRMED — v2.0.76 direct test + production CCO-MCP in 2.1.x] |
| `{"behavior": "deny"}` causes clean block, no P1 hang | [INFERRED] | [INFERRED-HIGH — no hang reports; synchronous response path architecture] |
| `{"behavior": "allow"}` without `updatedInput` preserves original input | [DOCUMENTED] | [DOCUMENTED + protocol-level confirmation from Issue #320 fix] |
| `{"behavior": "allow", "updatedInput": {...}}` delivers modified input | [DOCUMENTED] | [CONFIRMED in community testing at v2.0.76] |
| `--disallowedTools` fires at Step 2 before MCP call at Step 4 | [DOCUMENTED-not-tested] | [CONFIRMED from multiple independent documentation sources] |
| No first-launch initialization bug for Mechanism A | [UNKNOWN] | [INFERRED-LOW RISK — no reports found; distinct from hook initialization bug] |
| Mechanism B (stdio) broken in 2.1.6+ does not affect Mechanism A | [DOCUMENTED] | [CONFIRMED — mechanisms architecturally independent] |
| `--permission-prompt-tool` not inherited by Task/Agent tool subagents | [INFERRED] | [DOCUMENTED + INFERRED-HIGH — issues #25000, #18950, #21460] |

---

### Updated Recommendation

Mechanism A (`--permission-prompt-tool mcp__<server>__<tool>`) is sufficiently validated for **experimental adoption in a non-critical conductor path**. It has been confirmed working in community testing at CLI v2.0.76 and is in active production use in the CLI 2.1.x range.

The remaining blockers for **production adoption** are:

1. No direct empirical test in CLI 2.1.50+ with the `--settings` flag injection pattern conductor uses (R-31-A)
2. Deny behavior under actual headless `-p` execution not directly tested
3. `--allowedTools` behavior in non-bypass mode unconfirmed (Issue #61)
4. Subagent inheritance unconfirmed at MCP-specific level (Issue #62 — not a conductor concern given subprocess architecture)

**Short-term:** Keep the existing `--disallowedTools` + PreToolUse hooks + `--dangerously-skip-permissions` architecture.

**Medium-term:** When Issues #61 and #62 are complete and R-31-A is performed in the conductor environment, the MCP policy server approach becomes the recommended upgrade path.


---

## Follow-Up Research Recommendations

### R-31-A: Empirical Smoke Test of `--permission-prompt-tool` MCP Mechanism

**Question:** Does the `--permission-prompt-tool mcp__<server>__<tool>` mechanism work correctly in the current Claude Code CLI version? Specifically:
1. Does the MCP policy server's `check_permission` tool get called for tool requests that pass the static rules?
2. Does a `{"behavior": "deny", "message": "..."}` response correctly block the tool call (no hang — P1)?
3. Does `{"behavior": "allow"}` (without `updatedInput`) correctly execute the tool with the original input?
4. Does `{"behavior": "allow", "updatedInput": {...}}` correctly execute the tool with modified input?
5. Does the policy server's decision interact correctly with `--disallowedTools` (i.e., the denylist at Step 2 fires before the MCP call at Step 4)?

**Why this matters:** The entire decision to adopt `--permission-prompt-tool` as a replacement for `--dangerously-skip-permissions` depends on confirming these behaviors in the actual CLI version used by conductor. Without empirical verification, this is [INFERRED] from the documented schema.

**Test approach:** Write a minimal test harness that:
- Starts a policy server that logs all decisions to a file
- Runs `claude -p "run ls" --permission-prompt-tool mcp__test-policy__check_permission`
- Verifies the policy server log shows the `Bash(ls)` request
- Verifies deny responses prevent execution
- Verifies the output of a Claude run using the allow response

**This is the highest-priority follow-up** — all other implementation decisions depend on it.

**Note:** This is an empirical measurement that belongs *inside* a research doc (not a new standalone issue), but if it requires significant Python scripting to automate, it may warrant a small infra issue.

### R-31-B: `--allowedTools` Reliability in Non-Bypass Mode

**Question:** Does `--allowedTools` work correctly when `--permission-prompt-tool` is active (non-bypass mode)? Issue #12232 confirmed it is broken under `bypassPermissions`, but it is unclear whether the bug is mode-specific.

**Why this matters:** If `--allowedTools` works in non-bypass mode, the combined `--allowedTools` + `--permission-prompt-tool` approach provides a two-layer static+dynamic permission system. If it is also broken in non-bypass mode, the policy server must handle all allow decisions.

**Test approach:** Run `claude -p "curl ifconfig.me" --allowedTools Read --permission-prompt-tool mcp__test-policy__check_permission` with a policy server that also denies `curl`. Verify whether (a) `--allowedTools Read` correctly restricts the tool surface, and (b) the policy server receives the `Bash(curl)` call.

### R-31-C: Subagent Inheritance of `--permission-prompt-tool`

**Question:** When a claude -p session spawned with `--permission-prompt-tool mcp__X__Y` creates a subagent via the Task/Agent tool, does the subagent inherit the same permission-prompt-tool setting, or does it operate without a policy server?

**Why this matters:** If subagents do not inherit the policy server, they may be able to bypass the conductor's permission controls. This is a security-critical question for multi-agent architectures.

**Why this is a new question:** Doc #14 (Section 8) did not analyze subagent behavior. Doc #06 focused on subprocess-level spawning (external claude processes), not Agent-tool subagents within the same session. This represents genuinely new architectural territory.

---

## Sources

- [CLI reference — Claude Code Docs (`--permission-prompt-tool` flag documented in CLI flag table)](https://code.claude.com/docs/en/cli-reference) [DOCUMENTED]
- [Configure permissions — Claude Code Docs (permission evaluation order, modes, rule syntax)](https://code.claude.com/docs/en/permissions) [DOCUMENTED]
- [Sandboxing — Claude Code Docs (sandbox + permissions complementary architecture, sandboxed bash auto-allow mode)](https://code.claude.com/docs/en/sandboxing) [DOCUMENTED]
- [Handle approvals and user input — Claude API Docs (canUseTool callback, PermissionResultAllow/Deny schemas, updatedInput behavior)](https://platform.claude.com/docs/en/agent-sdk/user-input) [DOCUMENTED]
- [Configure permissions — Claude API Docs (bypassPermissions mode, permission evaluation order, hooks fire before bypass)](https://platform.claude.com/docs/en/agent-sdk/permissions) [DOCUMENTED]
- [GitHub Issue #1175: --permission-prompt-tool needs minimal, working example and documentation for MCP integration with Claude Code CLI](https://github.com/anthropics/claude-code/issues/1175) [DOCUMENTED — open as of March 2026]
- [GitHub Issue #320: MCP Tools Receive Empty Arguments When Using Permission Approval Flow — claude-agent-sdk-python](https://github.com/anthropics/claude-agent-sdk-python/issues/320) [DOCUMENTED — confirms updatedInput: {} bug]
- [GitHub Issue #469: Mismatch between the Claude CLI control protocol and can_use_tool permission — claude-agent-sdk-python](https://github.com/anthropics/claude-agent-sdk-python/issues/469) [DOCUMENTED — --permission-prompt-tool stdio broken in CLI 2.1.6+]
- [GitHub — UnknownJoe796/claude-code-mcp-permission: Documentation for the --permission-prompt-tool CLI flag, tested on Claude Code v2.0.76](https://github.com/UnknownJoe796/claude-code-mcp-permission) [DOCUMENTED — request/response schema verified against CLI v2.0.76]
- [GitHub — toolprint/cco-mcp: Real-time audit and approval system for Claude Code tool calls using --permission-prompt-tool](https://github.com/toolprint/cco-mcp) [DOCUMENTED — production implementation reference]
- [Claude Code Playbook 1.10: Outsourcing Permissions with --permission-prompt-tool — Vibe Sparking AI](https://www.vibesparking.com/en/blog/ai/claude-code/docs/cli/2025-08-28-outsourcing-permissions-with-claude-code-permission-prompt-tool/) [DOCUMENTED — MCP response schema, tool naming convention, Python patterns]
- [Claude Code --permission-prompt-tool — LobeHub MCP Servers](https://lobehub.com/mcp/user-claude-code-permission-prompt-tool) [DOCUMENTED — request/response schema and permission layers]
- [Permissions — ClaudeCode v0.23.0 (Elixir SDK, `:delegate` permission mode)](https://hexdocs.pm/claude_code/0.23.0/permissions.html) [DOCUMENTED — cross-SDK permission model reference]
- [ClaudeAgentSDK.Permission — claude_agent_sdk v0.14.0 (Elixir permission types)](https://hexdocs.pm/claude_agent_sdk/ClaudeAgentSDK.Permission.html) [DOCUMENTED]
- [Universal Permission Request Hook for Claude Code — GitHub Gist (doobidoo)](https://gist.github.com/doobidoo/fa84d31c0819a9faace345ca227b268f) [DOCUMENTED — Node.js hook pattern for auto-approval by tool name keywords]
- [GitHub Issue #12232: --allowedTools with --permission-mode bypassPermissions behavior — allowedTools ignored under bypassPermissions](https://github.com/anthropics/claude-code/issues/12232) [DOCUMENTED — confirmed bug, closed not planned]
- [Making Claude Code More Secure and Autonomous — Anthropic Engineering (sandboxing reduces prompt injection attack surface by 95%)](https://www.anthropic.com/engineering/claude-code-sandboxing) [DOCUMENTED]
- [06-security-threat-model.md — breadmin-conductor (T4 Bash Tool Scope Creep, --allowedTools policy, defense architecture)](docs/research/06-security-threat-model.md) [internal cross-reference]
- [14-hang-detection.md — breadmin-conductor (Pattern P1 permission hangs, R-HANG-B motivation)](docs/research/14-hang-detection.md) [internal cross-reference]
- [19-pretooluse-reliability.md — breadmin-conductor (Issue #12232 confirmed, --allowedTools broken under bypassPermissions, PreToolUse hooks reliable for Bash)](docs/research/19-pretooluse-reliability.md) [internal cross-reference]
- [20-os-sandbox.md — breadmin-conductor (OS sandbox architecture, macOS Seatbelt, Linux bubblewrap, domain-allowlisted proxy)](docs/research/20-os-sandbox.md) [internal cross-reference]
- [GitHub Issue #25000: Sub-agents bypass permission deny rules and per-command approval — security risk (closed duplicate of #21460, #18950)](https://github.com/anthropics/claude-code/issues/25000) [DOCUMENTED — confirms deny rule non-inheritance in Task tool subagents]
- [GitHub Issue #18950: Skills/subagents do not inherit user-level permissions from settings.json (OPEN, has repro)](https://github.com/anthropics/claude-code/issues/18950) [DOCUMENTED — allow-rule non-inheritance confirmed in multiple duplicates]
- [GitHub Issue #21460: PreToolUse hooks not enforced on subagent tool calls (OPEN, E2E confirmed)](https://github.com/anthropics/claude-code/issues/21460) [DOCUMENTED — hook non-inheritance by Task tool subagents]
- [GitHub Issue #28580: MCP tools prompt for permission even when already authorized — first-use lazy-loading coupling (OPEN)](https://github.com/anthropics/claude-code/issues/28580) [DOCUMENTED — first-launch issue for interactive MCP sessions; does not affect headless --mcp-config invocation]
- [GitHub Issue #1175: --permission-prompt-tool needs minimal example (CLOSED COMPLETED, community-verified schema)](https://github.com/anthropics/claude-code/issues/1175) [UPDATED STATUS — closed as completed after community confirmed correct response schema]
- [60-permission-prompt-tool-verification — breadmin-conductor (Issue #60: empirical research confirming Mechanism A behaviors)](docs/research/31-permission-prompt-tool.md#empirical-verification-issue-60) [internal cross-reference]
- [61-allowedtools-non-bypass-mode — breadmin-conductor (Issue #61: tracks --allowedTools reliability in non-bypass mode)](https://github.com/bread-wood/breadmin-composer/issues/61) [OPEN]
- [62-permission-prompt-tool-subagent-inheritance — breadmin-conductor (Issue #62: tracks subagent inheritance of --permission-prompt-tool)](https://github.com/bread-wood/breadmin-composer/issues/62) [OPEN]

