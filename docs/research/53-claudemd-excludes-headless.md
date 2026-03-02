# Research: claudeMdExcludes Behavior in Headless -p Mode

**Issue:** #53
**Milestone:** v2
**Feature:** core
**Status:** Complete
**Date:** 2026-03-02

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Background: claudeMdExcludes Mechanism](#background-claudemdexcludes-mechanism)
3. [Headless Mode CLAUDE.md Loading Behavior](#headless-mode-claudemd-loading-behavior)
4. [Research Findings by Area](#research-findings-by-area)
5. [Empirical Test Protocol](#empirical-test-protocol)
6. [Impact on Layer 1 Defense Assessment](#impact-on-layer-1-defense-assessment)
7. [Recommended Conductor Implementation](#recommended-conductor-implementation)
8. [Follow-Up Research Recommendations](#follow-up-research-recommendations)
9. [Sources](#sources)

---

## Executive Summary

`claudeMdExcludes` is a `settings.json` mechanism that prevents specific CLAUDE.md files from
being loaded into the agent's context. Issue #18 (`18-claudemd-opt-out.md`) recommends it as
a "Layer 1 defense" against context pollution when conductor dispatches agents into arbitrary
worktrees. This research assesses whether `claudeMdExcludes` applies in headless `-p` mode.

**Key findings:**

1. **`claudeMdExcludes` applies in headless `-p` mode.** CLAUDE.md loading is part of the
   context initialization that occurs before any prompt is processed, regardless of
   interactive vs. headless mode. The `-p` flag changes the output mode, not the settings
   loading pipeline. [INFERRED-HIGH — confirmed by CLAUDE.md loading architecture; not
   yet empirically tested with a live session]

2. **Glob pattern support is ambiguous.** The documented syntax for `claudeMdExcludes` uses
   absolute paths. Whether glob wildcards (`**/repo/.claude/CLAUDE.md`) match absolute paths
   has not been confirmed. The safest approach is to use absolute paths, not globs.
   [INFERRED]

3. **The `--settings` flag can inject `claudeMdExcludes` at dispatch time**, avoiding the
   need for pre-written `settings.local.json` files in each worktree. This is the recommended
   pattern for conductor. [INFERRED — requires empirical verification]

4. **The Layer 1 defense from `18-claudemd-opt-out.md` is likely valid in headless mode.**
   The recommendation to exclude target repo CLAUDE.md files via `claudeMdExcludes` should
   be implemented. Confidence remains [INFERRED-HIGH] until empirical testing confirms it.

---

## Background: claudeMdExcludes Mechanism

`claudeMdExcludes` is a `settings.json` array that specifies CLAUDE.md file paths to exclude
from the context loading pipeline. When a matching path is found during CLAUDE.md discovery
(the walk-up-from-CWD process), it is skipped rather than loaded.

**Documentation reference:** Claude Code settings documentation references `claudeMdExcludes`
as a mechanism for monorepo setups where certain subdirectories should not contribute
CLAUDE.md content to agent sessions.

**Context loading sequence in `claude -p`:**
1. Parse CLI flags (`--settings`, `--dangerously-skip-permissions`, etc.)
2. Load settings chain: system settings → user settings → workspace settings → local settings
3. Apply `claudeMdExcludes` filter to the CLAUDE.md discovery walk
4. Load all non-excluded CLAUDE.md files into system prompt
5. Process the `-p` prompt and begin the agentic loop

Step 3 applies before any prompt processing. This means `claudeMdExcludes` operates during
the settings/context phase, not the conversation phase. The `-p` flag does not skip steps
1-4.

---

## Headless Mode CLAUDE.md Loading Behavior

In `claude -p` (headless) mode, the CLAUDE.md discovery walk proceeds identically to
interactive mode:

1. Start from the CWD (the worktree)
2. Walk up directory tree until reaching filesystem root or a git root
3. Load `CLAUDE.md`, `.claude/CLAUDE.md` at each level
4. Apply `claudeMdExcludes` exclusions

The `-p` flag affects:
- Output format (JSON/stream-json vs. TUI)
- Permission prompting behavior (`--dangerously-skip-permissions`)
- Conversation management (single turn vs. continuous)

The `-p` flag does NOT affect settings loading or CLAUDE.md discovery. [INFERRED-HIGH]

**Evidence from headless mode documentation:** The Claude Code headless docs state that all
settings files are respected in headless mode. No exception for `claudeMdExcludes` is
documented. The absence of a documented exception supports the inference that it applies.

---

## Research Findings by Area

### 1. Does claudeMdExcludes work in -p mode?

**Assessment:** [INFERRED-HIGH] Yes. The settings loading pipeline precedes prompt
processing in both modes. CLAUDE.md exclusions are applied at settings load time.

**Caveat:** The only way to confirm this is empirical testing using the protocol in Section 5.
Until tested, this remains INFERRED.

### 2. Glob pattern matching

**Assessment:** [INFERRED] Glob support in `claudeMdExcludes` is described in some community
documentation but is not confirmed in official Anthropic docs. The official example uses
absolute paths. Conductors should use absolute paths, not glob patterns, to ensure
exclusions work reliably.

**Recommended pattern:**
```json
{
  "claudeMdExcludes": [
    "/absolute/path/to/target-repo/.claude/CLAUDE.md",
    "/absolute/path/to/target-repo/CLAUDE.md"
  ]
}
```

**Not recommended (glob, may not work):**
```json
{
  "claudeMdExcludes": [
    "**/target-repo/.claude/CLAUDE.md"
  ]
}
```

### 3. --settings flag vs. settings.local.json

**Assessment:** [INFERRED] The `--settings` CLI flag accepts a path to a settings JSON file.
If the file includes `claudeMdExcludes`, it should be applied during settings loading.

**Conductor pattern:**
```python
import json, tempfile, os

def build_agent_settings(worktree_path: str, excluded_claude_md_paths: list[str]) -> str:
    """Write a minimal settings file for conductor agent dispatch."""
    settings = {
        "hasCompletedOnboarding": True,
        "claudeMdExcludes": excluded_claude_md_paths,
    }
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="conductor-settings-", delete=False
    )
    json.dump(settings, tmp)
    tmp.flush()
    return tmp.name

# Usage at dispatch time:
target_repo_path = "/path/to/target/repo"
excluded_paths = [
    os.path.join(target_repo_path, ".claude", "CLAUDE.md"),
    os.path.join(target_repo_path, "CLAUDE.md"),
]
settings_file = build_agent_settings(worktree_path, excluded_paths)

cmd = [
    "claude", "-p", prompt,
    "--settings", settings_file,
    "--dangerously-skip-permissions",
    "--output-format", "stream-json",
]
```

**Limitation:** The `--settings` flag priority in the settings chain is not fully documented.
If a workspace `settings.json` or `settings.local.json` also defines `claudeMdExcludes`,
the two lists may or may not merge. The safest approach is to ensure no conflicting
`claudeMdExcludes` entries exist in the worktree.

### 4. Precedence when claudeMdExcludes appears in multiple settings files

**Assessment:** [INFERRED] Settings files are merged with later files taking precedence.
For arrays like `claudeMdExcludes`, the behavior (merge vs. replace) is undocumented.

**Safe assumption:** Treat `claudeMdExcludes` as replace-on-conflict. If the `--settings`
file is loaded last in the chain, it may override worktree settings. If loaded first, it
may be overridden. Use `CLAUDE_CONFIG_DIR` isolation (per Issue #73) as the primary
mechanism, which eliminates user settings chain conflicts.

---

## Empirical Test Protocol

The following protocol tests `claudeMdExcludes` in headless `-p` mode. Run manually with a
live claude session.

```bash
#!/usr/bin/env bash
# Test: claudeMdExcludes in headless -p mode
# Issue #53

set -euo pipefail

SENTINEL="SENTINEL_CONTENT_ABC123_UNIQUETAG"
TEST_REPO=$(mktemp -d)
CONFIG_DIR=$(mktemp -d)
SETTINGS_FILE=$(mktemp)

# Setup: create a CLAUDE.md with sentinel content
mkdir -p "$TEST_REPO/.claude"
echo "# $SENTINEL" > "$TEST_REPO/.claude/CLAUDE.md"

# Setup: write settings with claudeMdExcludes
cat > "$SETTINGS_FILE" << EOF
{
  "hasCompletedOnboarding": true,
  "claudeMdExcludes": ["$TEST_REPO/.claude/CLAUDE.md"]
}
EOF

echo "=== Test T-01: claudeMdExcludes prevents CLAUDE.md from loading ==="
OUTPUT=$(CLAUDE_CONFIG_DIR="$CONFIG_DIR" claude -p \
  "Does your context contain the exact string '$SENTINEL'? Answer 'YES' or 'NO' only." \
  --settings "$SETTINGS_FILE" \
  --dangerously-skip-permissions \
  --output-format json \
  --cwd "$TEST_REPO" 2>&1 || true)

echo "Output: $OUTPUT"

if echo "$OUTPUT" | grep -qi '"NO"'; then
  echo "PASS: claudeMdExcludes prevented CLAUDE.md from loading in -p mode"
elif echo "$OUTPUT" | grep -qi '"YES"'; then
  echo "FAIL: CLAUDE.md was loaded despite claudeMdExcludes"
else
  echo "INCONCLUSIVE: Unexpected output format. Review manually."
fi

echo ""
echo "=== Test T-02: Without claudeMdExcludes, CLAUDE.md IS loaded ==="
OUTPUT2=$(CLAUDE_CONFIG_DIR="$CONFIG_DIR" claude -p \
  "Does your context contain the exact string '$SENTINEL'? Answer 'YES' or 'NO' only." \
  --dangerously-skip-permissions \
  --output-format json \
  --cwd "$TEST_REPO" 2>&1 || true)

echo "Output: $OUTPUT2"

if echo "$OUTPUT2" | grep -qi '"YES"'; then
  echo "PASS (control): CLAUDE.md was loaded without claudeMdExcludes"
elif echo "$OUTPUT2" | grep -qi '"NO"'; then
  echo "FAIL (control): CLAUDE.md was NOT loaded even without claudeMdExcludes"
else
  echo "INCONCLUSIVE: Unexpected output format. Review manually."
fi

# Cleanup
rm -rf "$TEST_REPO" "$CONFIG_DIR"
rm -f "$SETTINGS_FILE"
```

---

## Impact on Layer 1 Defense Assessment

The Layer 1 defense in `docs/research/18-claudemd-opt-out.md` is:

> Use `claudeMdExcludes` in the conductor agent's settings to explicitly exclude the target
> repo's CLAUDE.md files, preventing unintended context injection from hostile or
> misconfigured repositories.

**Updated assessment:** The defense is likely effective in headless mode. Conductor should
implement it. The defense remains [INFERRED-HIGH] rather than [TESTED] until the empirical
protocol above is run. This does NOT block v2 implementation.

**Defense-in-depth stack** (in priority order):
1. `CLAUDE_CONFIG_DIR` isolation (Issue #73) — primary; prevents user settings pollution
2. `claudeMdExcludes` via `--settings` — secondary; blocks specific CLAUDE.md files
3. Worktree CWD control — limits the walk-up scope
4. `--permission-prompt-tool` policy server — catches any tool calls that shouldn't happen

---

## Recommended Conductor Implementation

In `src/composer/runner.py`, when spawning a claude agent for a target repository issue:

```python
def get_excluded_claude_md_paths(repo_path: str) -> list[str]:
    """Return CLAUDE.md paths to exclude for a given repo."""
    candidates = [
        os.path.join(repo_path, "CLAUDE.md"),
        os.path.join(repo_path, ".claude", "CLAUDE.md"),
        os.path.join(repo_path, ".github", "CLAUDE.md"),
    ]
    # Include all candidates regardless of existence (exclusions are harmless if file absent)
    return candidates
```

This list is injected into the `--settings` file at dispatch time, alongside
`hasCompletedOnboarding: true`.

---

## Follow-Up Research Recommendations

**[V2_RESEARCH] Empirically confirm claudeMdExcludes in -p mode**
Run the test protocol in Section 5 with a live claude session. Update confidence ratings in
`docs/research/18-claudemd-opt-out.md` from [INFERRED-HIGH] to [TESTED]. This is a V-09
candidate for the verification suite.

**[WONT_RESEARCH] Glob pattern support in claudeMdExcludes**
The glob syntax is undocumented and unreliable. Conductor uses absolute paths. No need to
research glob behavior further — the absolute path approach is the correct implementation.

---

## Sources

- [Claude Code Headless Mode Documentation](https://code.claude.com/docs/en/headless)
- [Claude Code Settings Documentation](https://code.claude.com/docs/en/settings)
- [SFEIR: Headless Mode and CI/CD Common Mistakes](https://institute.sfeir.com/en/claude-code/claude-code-headless-mode-and-ci-cd/errors/)
- [Claude Code Configuration Reference](https://adrianomelo.com/posts/claude-code-headless.html)
