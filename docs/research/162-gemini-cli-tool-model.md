# Research: Gemini CLI Tool Allowlist and Permission Model

**Issue:** #162
**Milestone:** v2
**Feature:** feat:llm-alloc
**Status:** Complete
**Date:** 2026-03-02

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Built-in Tool Inventory](#built-in-tool-inventory)
3. [Tool Allowlist and Deny List Mechanisms](#tool-allowlist-and-deny-list-mechanisms)
4. [Permission Model in Headless Mode](#permission-model-in-headless-mode)
5. [MCP Tool Allowlist](#mcp-tool-allowlist)
6. [Mapping to claude -p Tool Control Flags](#mapping-to-claude--p-tool-control-flags)
7. [Module Isolation Enforcement for GeminiBackend](#module-isolation-enforcement-for-geminibackend)
8. [Follow-Up Research Recommendations](#follow-up-research-recommendations)
9. [Sources](#sources)

---

## Executive Summary

This research documents the Gemini CLI's tool system for the purpose of implementing
`GeminiBackend` in conductor's multi-model backend architecture (see
`docs/research/36-multi-model-backends.md`). The focus is on which tool controls are
available in headless mode and how they map to `claude -p`'s `--allowedTools` and
`--disallowedTools` flags.

**Key findings:**

1. **Gemini CLI's built-in tool set** includes: `ShellTool` (arbitrary shell execution),
   `WebFetchTool`, `WebSearchTool`, `MemoryTool`, plus file system tools (read, write,
   list directory, `ReadManyFiles`). [DOCUMENTED]

2. **No `--allowedTools` equivalent CLI flag exists for built-in tools in headless mode.**
   GitHub Issue #9011 ("--allowed-tools does not work in non-interactive mode") confirms
   that the `--allowed-tools` flag is IGNORED when a prompt is supplied on the command
   line (headless mode). [DOCUMENTED]

3. **MCP tool filtering IS supported** via `includeTools` and `excludeTools` per-server
   config in `settings.json`. This is the only reliable tool restriction mechanism in
   headless mode. [DOCUMENTED]

4. **No `--disallowedTools` equivalent exists** for built-in tools. The built-in tool set
   is fixed in headless mode and cannot be restricted via CLI flags. [DOCUMENTED]

5. **Permission model in headless mode:** Gemini CLI suppresses confirmation prompts
   automatically when not in a TTY. No `--dangerously-skip-permissions` equivalent is
   required. [DOCUMENTED]

6. **Module isolation enforcement must occur at the orchestrator level**, not via
   GeminiBackend tool restrictions. GeminiBackend cannot enforce the conductor
   "one agent per module" rule via tool flags. Worktree CWD control is the primary
   isolation mechanism.

---

## Built-in Tool Inventory

Gemini CLI's built-in tools (from `packages/core/src/tools/`):

| Tool Name | Description | Headless Available |
|-----------|-------------|-------------------|
| `run_shell_command` (ShellTool) | Execute arbitrary shell commands | Yes |
| `web_fetch` (WebFetchTool) | Fetch content from a URL | Yes |
| `google_web_search` (WebSearchTool) | Search the web via Google | Yes |
| `save_memory` (MemoryTool) | Save/recall memory across sessions | Yes |
| `read_file` | Read a file's contents | Yes |
| `write_file` | Write content to a file | Yes |
| `list_directory` | List directory contents | Yes |
| `read_many_files` | Read multiple files or directories | Yes |
| `move_file` | Move/rename a file | Yes |
| `create_directory` | Create a directory | Yes |
| `delete_file` | Delete a file | Yes |

**Comparison with claude -p built-in tools:**

| Claude Code | Gemini CLI | Notes |
|-------------|-----------|-------|
| `Bash` | `run_shell_command` | Both execute shell commands |
| `Read` | `read_file` | Equivalent |
| `Write` | `write_file` | Equivalent |
| `Edit` | *(uses write_file)* | No patch-edit equivalent |
| `Glob` | `list_directory` | Approximate; Glob supports patterns |
| `Grep` | *(uses run_shell_command)* | No dedicated tool |
| `WebFetch` | `web_fetch` | Equivalent |
| `WebSearch` | `google_web_search` | Equivalent |
| `Task/Agent` | *(no equivalent)* | Gemini CLI has no built-in subagent tool |
| `mcp__*` | `mcp__*` | Both support MCP; different config syntax |

---

## Tool Allowlist and Deny List Mechanisms

### --allowed-tools CLI flag: NOT functional in headless mode

GitHub Issue #9011 ("--allowed-tools does not work in non-interactive mode") documents that
when a prompt is supplied on the command line (`gemini -p "..."` or `gemini "..."`), the
`--allowed-tools` flag is SILENTLY IGNORED. [DOCUMENTED]

GitHub Issue #1917 ("Have an allowedTools for non-interactive mode") confirms this is a
known limitation and was open as of March 2026. The feature has been requested but not
implemented.

**GitHub Issue #15629** ("Add tool allowlist (whitelist) for seamless command execution")
is a related request. As of March 2026, neither issue has been resolved.

**Implication for GeminiBackend:** Conductor CANNOT restrict built-in tools in Gemini CLI
headless mode via CLI flags. GeminiBackend cannot enforce an allowlist equivalent to
`claude -p --allowedTools "Read,Write,Edit"`.

### No --disallowedTools equivalent

No CLI flag exists to remove specific built-in tools from Gemini CLI in any mode. The
built-in tool set is all-or-nothing for built-in tools in headless mode.

---

## Permission Model in Headless Mode

Claude Code requires `--dangerously-skip-permissions` in headless mode to suppress
confirmation prompts. Gemini CLI behaves differently:

**Gemini CLI headless mode trigger:** When stdin is not a TTY OR when a prompt is supplied
as a positional argument, Gemini CLI automatically suppresses confirmation prompts and
executes tools without user confirmation. [DOCUMENTED]

**No explicit permission-skip flag required.** The confirmation gate is tied to TTY
detection, not a flag. This means:
- `gemini "run ls -la"` → executes ShellTool immediately, no confirmation
- `gemini` (interactive) → asks for confirmation before tool use

**Security implication:** Gemini CLI in headless mode is equivalent to `claude -p
--dangerously-skip-permissions` — all tools execute without confirmation. This is
appropriate for conductor's dispatch model but means there is no permission gate to
configure.

---

## MCP Tool Allowlist

While built-in tool restriction is not available via CLI flags, **MCP tool filtering IS
fully supported** via `settings.json`:

```json
{
  "mcpServers": {
    "conductor-tools": {
      "command": "python3",
      "args": ["/path/to/conductor-mcp-server.py"],
      "includeTools": ["read_file", "write_file"],
      "excludeTools": ["dangerous_tool"]
    }
  }
}
```

**`includeTools`:** Allowlist — only listed MCP tools from this server are available.
**`excludeTools`:** Denylist — listed tools from this server are blocked. Takes precedence
over `includeTools`.

**Enterprise allowlist:** The `mcp.allowed` settings field (enterprise config) restricts
which MCP servers can be used at all, regardless of per-server `includeTools`.

For conductor's `GeminiBackend`: If conductor needs to restrict tool access, it should
implement conductor-controlled tools as MCP tools (not built-in tools) and use
`includeTools`/`excludeTools` to enforce restrictions.

---

## Mapping to claude -p Tool Control Flags

| claude -p flag | Gemini CLI equivalent | Available in headless? |
|----------------|----------------------|----------------------|
| `--allowedTools "Read,Write"` | No built-in equivalent | NO — Issue #9011 |
| `--disallowedTools "Bash(env)"` | No equivalent | NO |
| `--dangerously-skip-permissions` | Not needed (auto in headless) | N/A |
| MCP `includeTools` per-server | `mcpServers[*].includeTools` | YES (settings.json only) |
| MCP `excludeTools` per-server | `mcpServers[*].excludeTools` | YES (settings.json only) |
| `--permission-prompt-tool` | Not supported | NO |

**Critical gap:** Gemini CLI headless mode has NO mechanism equivalent to
`--allowedTools` for built-in tools. This fundamentally limits `GeminiBackend`'s ability
to enforce conductor's tool isolation model.

---

## Module Isolation Enforcement for GeminiBackend

Conductor's module isolation model requires that each sub-agent can only modify files
within its assigned module scope. With `claude -p`, this is partially enforced via
`--allowedTools` (e.g., only `Bash(git diff src/composer/runner.py)` allowed) and the
`--permission-prompt-tool` policy server.

**For GeminiBackend, built-in tool restriction is not available.** Module isolation must
be enforced at the orchestrator level via:

1. **Worktree CWD control:** Gemini CLI inherits process CWD like `claude -p`. Setting
   CWD to the worktree limits file operations to paths relative to the worktree.

2. **Prompt-level enforcement:** The dispatch prompt explicitly states the allowed scope
   and instructs the agent to only modify files within that scope.

3. **Post-run diff validation:** After the Gemini agent completes, conductor diffs the
   worktree against the base branch. Files changed outside the allowed module scope are
   rejected, the agent's work is discarded, and an issue comment is filed.

4. **MCP policy server (future):** If conductor implements a MCP-based policy server
   (analogous to the `--permission-prompt-tool` server), it could intercept write operations
   via MCP tools. However, built-in `write_file` calls would bypass any MCP-based gate.

**Recommended GeminiBackend isolation strategy:**

```python
class GeminiBackend(ModelBackend):
    async def spawn(self, prompt: str, *, cwd: str, env: dict, ...) -> AsyncIterator[ConductorEvent]:
        # CWD control is the primary isolation mechanism
        # Post-run diff validation is the secondary mechanism
        # No CLI-level tool restriction available for built-in tools

        settings = self._write_agent_settings(allowed_mcp_tools=self._allowed_mcp_tools)
        cmd = [
            "gemini",
            "--yolo",  # headless mode flag (suppresses confirmations, if needed)
            f"--settings={settings}",
            prompt,
        ]
        # Launch with CWD = worktree
        process = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd, env={**os.environ, **env}, ...
        )
        ...
```

**Note on `--yolo` flag:** Gemini CLI has a `--yolo` flag that combines headless mode
with full tool auto-approval. In headless mode (TTY detection), this is typically not
required, but it may be needed on some platforms. Check Issue #1917 for updates.

---

## Follow-Up Research Recommendations

**[V2_RESEARCH] Confirm --allowed-tools flag behavior on current Gemini CLI version**
Issues #9011 and #1917 were open as of March 2026. If Anthropic ships a fix that enables
`--allowed-tools` in headless mode, the GeminiBackend isolation model changes significantly.
Monitor these issues and update this doc when resolved.

**[V2_RESEARCH] Evaluate MCP policy server approach for GeminiBackend**
If conductor implements conductor-side tools as MCP tools (rather than relying on built-in
tools), `includeTools`/`excludeTools` in settings.json could enforce module isolation.
This requires conductor to provide an MCP server with Read/Write/Edit equivalents, which
is significant engineering work. Assess in v2.1.

**[WONT_RESEARCH] --permission-prompt-tool equivalent for Gemini CLI**
Gemini CLI does not support a permission hook equivalent. No action.

---

## Sources

- [Gemini CLI Tools Documentation](https://google-gemini.github.io/gemini-cli/docs/tools/)
- [Gemini CLI Core Tools API](https://google-gemini.github.io/gemini-cli/docs/core/tools-api.html)
- [Gemini CLI Headless Mode Documentation](https://google-gemini.github.io/gemini-cli/docs/cli/headless.html)
- [Gemini CLI MCP Servers Configuration](https://geminicli.com/docs/tools/mcp-server/)
- [Gemini CLI Configuration Reference](https://google-gemini.github.io/gemini-cli/docs/get-started/configuration.html)
- [Issue #9011: --allowed-tools does not work in non-interactive mode](https://github.com/google-gemini/gemini-cli/issues/9011)
- [Issue #1917: Have an allowedTools for non-interactive mode](https://github.com/google-gemini/gemini-cli/issues/1917)
- [Issue #15629: Add tool allowlist for seamless command execution](https://github.com/google-gemini/gemini-cli/issues/15629)
- [Discussion #8980: How to use --allowed-tools option with other options?](https://github.com/google-gemini/gemini-cli/discussions/8980)
- [Gemini CLI for the Enterprise](https://google-gemini.github.io/gemini-cli/docs/cli/enterprise.html)
