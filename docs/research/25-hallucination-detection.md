# Research: Hallucination Detection and Ground-Truth Verification for Autonomous Agents

**Issue**: #25
**Milestone**: M1: Foundation
**Status**: Complete
**Date**: 2026-03-02

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Citation Validation Tooling and CI Integration](#citation-validation-tooling-and-ci-integration)
3. [Confidence Tagging Taxonomy](#confidence-tagging-taxonomy)
4. [M1 Findings Requiring Empirical Verification Before M2 Dispatch](#m1-findings-requiring-empirical-verification-before-m2-dispatch)
5. [Cross-Document Contradiction Analysis](#cross-document-contradiction-analysis)
6. [Test Fixture Strategy for Implementation Agents](#test-fixture-strategy-for-implementation-agents)
7. [Recommended Additions to Research Agent Prompts](#recommended-additions-to-research-agent-prompts)
8. [Post-Merge Review Checklist for the Orchestrator](#post-merge-review-checklist-for-the-orchestrator)
9. [Follow-Up Research Recommendations](#follow-up-research-recommendations)
10. [Sources](#sources)

---

## Executive Summary

Research and implementation agents can hallucinate at three distinct layers: fabricated
or misrepresented citations in research docs, architectural claims asserted as fact
without empirical verification, and invented API signatures and method behaviors in
implementation code. This document provides a concrete framework for detecting,
classifying, and mitigating each type of hallucination across the breadmin-conductor
pipeline.

The principal recommendations are:

1. **Use lychee** for automated URL validation in CI. It is the fastest and most
   actively maintained option, runs as a GitHub Action, and produces machine-readable
   output. Run it as a post-merge check rather than a blocking PR gate to avoid failing
   on transient network errors.

2. **Adopt a three-tier confidence tagging taxonomy**: `[TESTED]`, `[DOCUMENTED]`, and
   `[INFERRED]`. Each tag has explicit promotion criteria. High-stakes `[INFERRED]`
   findings from M1 must be verified before M2 agents build on them.

3. **Seven M1 findings are high-stakes `[INFERRED]`**: they appear across multiple
   research docs but have not been independently tested and would have architectural
   consequences if wrong.

4. **Four cross-document contradictions exist**: two involve conflicting numbers, two
   involve conflicting guidance about isolation patterns.

5. **Test fixtures for stream-json parsing must be captured from real `claude -p` runs**,
   not synthesized. Use pytest golden files. Establish a `tests/fixtures/capture.py`
   script that runs against a live API key and writes NDJSON fixture files.

6. **Three prompt additions** reduce research-agent hallucination: verbatim-quote
   anchoring, explicit uncertainty budgeting, and a post-write link audit step.

7. **Eight-step post-merge review checklist** for the orchestrator before unblocking
   downstream issues.

---

## Citation Validation Tooling and CI Integration

### Tool Recommendation: lychee

The recommended tool for automated URL validation in `docs/research/` is
**lychee** — a fast, async, stream-based link checker written in Rust.

**Why lychee over alternatives:**

| Tool | Language | Speed | GitHub Action | JSON Output | Maintenance |
|------|----------|-------|---------------|-------------|-------------|
| lychee | Rust | Fast (async) | Yes (native) | Yes | Active (2026) |
| markdown-link-check | Node.js | Moderate | Via wrapper | JUnit XML | Active |
| mlc | Rust | Fast | Via manual install | Yes | Moderate |
| linkchecker | Python | Slow | No native | Yes | Minimal |

lychee scans 576 links in approximately 1 minute in CI. It supports per-URL exclusion
patterns via `.lycheeignore`, configurable timeouts, response caching (`.lycheecache`
for re-runs), and outputs structured JSON suitable for downstream processing.

**Installation:**

```bash
# Via Homebrew
brew install lychee

# Or as a Cargo binary
cargo install lychee
```

**Local invocation:**

```bash
lychee \
  --timeout 20 \
  --max-retries 3 \
  --no-progress \
  --format json \
  docs/research/*.md
```

**GitHub Actions integration** (`.github/workflows/link-check.yml`):

```yaml
name: Link Check

on:
  push:
    branches: [main]
    paths:
      - "docs/research/**"
  schedule:
    - cron: "0 6 * * 1"  # Weekly Monday 6am UTC

jobs:
  link-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: lycheeverse/lychee-action@v2
        with:
          args: --timeout 20 --no-progress --format markdown docs/research/*.md
          fail: true
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

**Recommended false-positive mitigation:**

Certain link categories produce false positives that should be excluded. Create
`.lycheeignore` at the repository root:

```
# GitHub issue URLs sometimes return 200 for deleted issues
# Use the API endpoint instead for validation
^https://github.com/anthropics/claude-code/issues/[0-9]+$

# LinkedIn blocks crawlers with 999 codes
^https://www.linkedin.com/.*$

# arxiv sometimes rate-limits CI runners
^https://arxiv.org/abs/.*$
^https://arxiv.org/html/.*$
```

**False positive rate:** lychee's async HTTP GET approach will produce false positives
for: (a) URLs that require authentication or cookies, (b) URLs behind CAPTCHAs or
crawler-blocking middleware, and (c) transient server errors. An acceptable false
positive budget for research docs is approximately 5–10% of links requiring manual
review. Using `--max-retries 3` and `--timeout 20` reduces transient failures
significantly.

**CI integration strategy — post-merge, not blocking PR gate:**

Run lychee on the `push` to `main` trigger rather than as a required PR check. This
avoids blocking PRs due to external URL instability. If lychee reports broken links on
main, open a `research` issue automatically (via `gh issue create`) and label it
`docs:broken-links`. The orchestrator should check for open `docs:broken-links` issues
at the start of each session and resolve them before dispatching new research agents.

---

## Confidence Tagging Taxonomy

### Overview

Research agents write findings from a combination of sources: official documentation,
community inference, and untested architectural assertions. A three-tier confidence
taxonomy makes the reliability level of each key finding explicit and provides
actionable criteria for promotion.

### The Three Tiers

**`[TESTED]`** — Empirically verified by actually running the code or behavior in
question and observing the result. The test was repeatable, the conditions were
documented, and the finding can be reproduced.

Examples of what earns `[TESTED]`:
- "The `isolation: worktree` flag creates a new git worktree when a subagent is
  dispatched in `-p` mode. Verified by running a minimal reproduction case on
  Claude Code v2.1.55."
- "Passing `--strict-mcp-config --mcp-config '{}'` results in zero MCP tool
  definitions injected into the context window. Verified by measuring input token
  count with and without the flag."

**`[DOCUMENTED]`** — The claim is stated in official documentation (Anthropic docs,
official changelogs, or confirmed GitHub issue resolutions) and can be read at a
specific URL. The agent has either directly quoted the relevant passage or linked to
the exact section.

Examples of what earns `[DOCUMENTED]`:
- "Subagents cannot spawn other subagents — this is stated explicitly in the Claude
  Code Subagents Documentation." *(with verbatim quote provided)*
- "The `--cwd` flag is not available as of March 2026. This is confirmed by GitHub
  Issue #26287 which is marked 'Low — Nice to have' and unresolved."

**`[INFERRED]`** — The claim is a reasonable conclusion drawn from combining multiple
documented sources, community observations, or architectural reasoning — but has not
been directly verified by running it, and no single authoritative source states it
directly.

Examples of what warrants `[INFERRED]`:
- "The 50K → 5K token overhead reduction from 4-layer isolation likely applies to
  Claude Code v2.1.55+ since the underlying mechanism (CLAUDE.md walk, plugin loading)
  has not changed."
- "`isolation: worktree` in agent definitions is expected to work in headless `-p`
  mode because no headless-mode restriction is noted in the documentation."

### Promotion Criteria

| From | To | Criteria |
|------|----|----------|
| `[INFERRED]` | `[DOCUMENTED]` | A verbatim quote or direct link to an official source is added that directly states the claim. |
| `[INFERRED]` | `[TESTED]` | A reproduction case is run. The test conditions, Claude Code version, command, and observed output are documented. |
| `[DOCUMENTED]` | `[TESTED]` | The documented behavior is confirmed to behave as described in the specific version of Claude Code currently in use by conductor. |

### When Does Confidence Level Matter?

`[INFERRED]` findings do not block research PRs from being merged. They do, however,
block implementation agents from building directly on them if they are high-stakes.
The classification of a finding as "high-stakes" is determined by: (a) whether a wrong
assumption would require rework of a core module, and (b) whether there is no obvious
runtime fallback if the assumption is wrong.

**Rule:** Any `[INFERRED]` finding that is listed in Section 4 of this document
(M1 findings requiring empirical verification) must have a corresponding "verify claim"
issue filed before M2 implementation agents can depend on it.

### Applying Tags in Research Documents

Tags are applied inline, following the key assertion they apply to. Format:

```
The `isolation: worktree` feature in subagent definitions **is expected to function
correctly** in headless `-p` sessions `[INFERRED]`, provided the Task/Agent tool
itself is available.
```

Tags may also be applied to entire sections with a header note:

```
> **Confidence level for this section:** `[INFERRED]` — no empirical test has been
> run. See issue #XX for the verification task.
```

---

## M1 Findings Requiring Empirical Verification Before M2 Dispatch

The following seven findings appear across M1 research docs and are classified as
`[INFERRED]`. Each is high-stakes because M2 implementation agents will build core
modules on them. Each has a recommended verification approach.

### V-01: `isolation: worktree` Works in Headless `-p` Mode

**Source doc:** `01-agent-tool-in-p-mode.md` Section 2.2
**Claim:** "The `isolation: worktree` feature in subagent definitions is expected to
function correctly in headless `-p` sessions."
**Classification:** `[INFERRED]`
**Why high-stakes:** If this does not work, the conductor's in-process Agent-tool
dispatch pattern fails entirely. The subprocess-based worktree pattern is the
recommended fallback, but the doc relies on `isolation: worktree` as the "native"
path.
**Verification approach:** Spawn a minimal `claude -p` orchestrator with a subagent
definition file that includes `isolation: worktree`. Confirm that a git worktree is
created, the subagent runs in it, and the worktree is cleaned up afterward. Record
the Claude Code version and the exact `--allowedTools` required.

### V-02: 50K → 5K Token Reduction from 4-Layer Isolation

**Source docs:** `01-agent-tool-in-p-mode.md` Section 3.3, `12-subprocess-token-overhead.md` Section 2
**Claim:** "The 4-layer isolation strategy reduces per-turn overhead from ~50,000 tokens
to ~5,000 tokens (10x improvement)."
**Classification:** `[INFERRED]` — the 50K baseline figure was measured by a community
DEV.to article in late 2025 against an unspecified Claude Code version. The 5K target
has not been independently verified.
**Why high-stakes:** The conductor's cost model and `--max-budget-usd` values in
`04-configuration.md` are based on this ratio. If the true overhead is 20K → 10K
instead, cost estimates are wrong by 2–4x.
**Verification approach:** Run a probe `claude -p` in the conductor repo CWD with
zero isolation flags. Parse the `result` event's `usage.input_tokens`. Then repeat
with each isolation layer applied incrementally. Record actual per-layer token savings
against Claude Code v2.1.55+. File a follow-up issue for systematic tracking.

### V-03: `CLAUDE_CODE_ENABLE_TASKS` Default Status in v2.1.50+

**Source doc:** `01-agent-tool-in-p-mode.md` Section 1.2
**Claim:** "As of version 2.1.50+, the Task/Agent tool infrastructure is available in
headless mode" [without setting `CLAUDE_CODE_ENABLE_TASKS=true`].
**Classification:** `[INFERRED]` — the doc states the issue was "closed as COMPLETED
with the note that this would become the default once integrations had time to migrate"
but does not confirm current default behavior.
**Why high-stakes:** If `CLAUDE_CODE_ENABLE_TASKS=true` is still required, every
`claude -p` orchestrator invocation that uses the Agent tool will silently fail to
spawn subagents.
**Verification approach:** On the current Claude Code version, run:
`claude -p "List the tools available to you" --output-format json` without setting
`CLAUDE_CODE_ENABLE_TASKS` and check whether `Agent` appears in the tools list.

### V-04: `--dangerously-skip-permissions` Does Not Skip Deny Rules

**Source doc:** `06-security-threat-model.md` Section "Risk Levels by Configuration"
**Claim:** "Deny rules always take precedence [over `--dangerously-skip-permissions`]."
**Classification:** `[DOCUMENTED]` but also `[INFERRED]` in practice — the note in the
security doc also cites GitHub Issue #12232 documenting that this combination "may not
behave as expected." There is a contradiction within the same document (see Section 5
of this doc for full analysis).
**Why high-stakes:** The entire Layer 3 security defense relies on deny rules firing
even when bypass mode is active. If bypass overrides deny rules, every security control
in the permission policy is void.
**Verification approach:** Run `claude -p` with `--dangerously-skip-permissions` and
an explicit deny rule for a specific Bash command (e.g., `Bash(echo *)`). Confirm
via `PreToolUse` hook log that the rule fires and blocks the command.

### V-05: `--strict-mcp-config --mcp-config '{}'` Produces Zero MCP Overhead

**Source doc:** `12-subprocess-token-overhead.md` Section 3.3
**Claim:** "Passing `--strict-mcp-config --mcp-config '{}'` results in zero MCP tool
definitions injected into the context."
**Classification:** `[INFERRED]` — while the flags are documented, no token count
measurement confirming zero injection has been provided.
**Why high-stakes:** This is the recommended MCP elimination strategy for sub-agents.
If it does not work completely, the token cost estimates for isolated workers are
under-counted.
**Verification approach:** Measure `usage.input_tokens` with vs. without the flags
in an environment with 3+ active MCP servers configured in `~/.claude.json`. Confirm
the token count is the same with the flags as with no MCP servers configured at all.

### V-06: Auto-Compaction Threshold Is ~75% in `-p` Mode

**Source doc:** `02-session-continuity.md` Executive Summary
**Claim:** Auto-compaction fires at "roughly 75% utilization in `-p` mode."
**Cross-claim in another doc:** `12-subprocess-token-overhead.md` Section 6.1 states
compaction fires at "approximately 83.5% of the context window (~167,000 tokens of a
200K window)."
**Classification:** `[INFERRED]` for both — and they conflict. See Section 5 (Contradiction C-01).
**Why high-stakes:** Incorrect compaction threshold assumptions affect how the orchestrator
sizes agent turns and when to instruct agents to compact manually.
**Verification approach:** Run a long-turn `claude -p` session that accumulates context
and observe when the compaction event fires. Record the token count at the compaction
trigger in `-p` mode specifically.

### V-07: The `CLAUDECODE=1` Nesting Rejection in Subprocess

**Source doc:** `04-configuration.md` Section 6.2
**Claim:** "If `CLAUDECODE=1` is inherited by sub-agent subprocesses, Claude Code
CLI refuses to start with 'Error: Claude Code cannot be launched inside another
Claude Code session.'"
**Classification:** `[DOCUMENTED]` (cites PR #594) but the practical fix — filtering
`CLAUDECODE` from the subprocess env — is `[INFERRED]` to be effective.
**Why high-stakes:** During development, conductor tests are typically run inside a
Claude Code session. If filtering `CLAUDECODE` from the subprocess env does not work
for the current version, every development-time test of conductor will fail silently.
**Verification approach:** Run a conductor integration test from inside a Claude Code
session without filtering `CLAUDECODE`. Confirm the error message. Then filter it and
confirm the subprocess launches.

---

## Cross-Document Contradiction Analysis

Four contradictions were identified across the seven M1 research documents. The
orchestrator must resolve these before dispatching M2 implementation agents.

### Contradiction C-01: Auto-Compaction Threshold (75% vs. 83.5%)

**Document A:** `02-session-continuity.md` (Executive Summary):
> "context window limits are managed by auto-compaction, which fires at roughly
> **75% utilization** in `-p` mode"

**Document B:** `12-subprocess-token-overhead.md` (Section 6.1):
> "Claude Code triggers auto-compaction at approximately **83.5%** of the context
> window (~167,000 tokens of a 200K window)"

**Analysis:** Both figures are `[INFERRED]` from community sources. The 83.5% figure
from `12-subprocess-token-overhead.md` cites a dedicated claudefa.st article about
context buffer management and is more precisely sourced. The 75% in
`02-session-continuity.md` may be a conflation with the `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`
variable mentioned in the same document (which accepts a percentage override).

**Resolution:** Trust the 83.5% (~167K token) figure from `12-subprocess-token-overhead.md`
until empirical verification (V-06) is completed. Update `02-session-continuity.md`'s
executive summary to align.

**Action required before M2:** File a verification task (V-06 above) and update the
threshold in both docs to match the empirically observed value.

### Contradiction C-02: `--dangerously-skip-permissions` and Deny Rules

**Document A:** `06-security-threat-model.md` (Recommended Policy section):
> "Note: `--dangerously-skip-permissions` is used here only because the explicit
> `--allowedTools` + `--disallowedTools` policy is in place. The bypass mode does not
> skip the deny rules — **deny rules always take precedence**."

**Document B:** `06-security-threat-model.md` (same document, `--dangerously-skip-permissions`
Risk Analysis section):
> "A reported issue (anthropics/claude-code #12232, 2025) notes that `--allowedTools`
> combined with `--permission-mode bypassPermissions` **may not behave as expected**,
> with bypass mode potentially overriding allow rules in some scenarios."

**Analysis:** This is an internal contradiction within a single document. The first
passage asserts deny rules take precedence; the second passage cites a known issue
suggesting they may not. Both are present in the same security threat model doc.

**Resolution:** The contradiction cannot be resolved without empirical testing (V-04).
Until V-04 is resolved, conductor must not rely on deny rules as a security guarantee
when bypass mode is active. The security architecture should be treated as Layer 4
(OS-level sandbox) being the actual enforcement layer, with Layer 3 (permissions) as
defense-in-depth only.

**Action required before M2:** File V-04 verification task. Add explicit warning to
`06-security-threat-model.md` noting the contradiction is unresolved.

### Contradiction C-03: `CONDUCTOR_MODEL` Default Value

**Document A:** `04-configuration.md` (Section 3.2):
> `CONDUCTOR_MODEL` default: `"claude-opus-4-6"`

**Document B:** `12-subprocess-token-overhead.md` (Section 7.1):
> "Current Anthropic API prices (as of March 2026) for **Claude Sonnet 4.6**"
> (used as the baseline for cost estimates, implying Sonnet is the default model)

**Analysis:** The configuration doc sets Opus as the default model; the cost doc
uses Sonnet as the baseline for all cost estimates, and Section F4 of that doc
recommends Sonnet as the default: "Implementation agents: Sonnet 4.6 (most
implementation work does not require Opus)."

**Resolution:** The cost estimates in `12-subprocess-token-overhead.md` were
calculated using Sonnet pricing. If the default model remains Opus, cost estimates
are approximately 5x too low. The recommendation should be to change the default to
Sonnet 4.6, with Opus available as an explicit opt-in.

**Action required before M2:** Update `04-configuration.md` to change `CONDUCTOR_MODEL`
default to `"claude-sonnet-4-6"`. Update the cost table in `12-subprocess-token-overhead.md`
to note the Opus multiplier explicitly in the default case.

### Contradiction C-04: `isolation: worktree` Worktree Cleanup in Headless Mode

**Document A:** `01-agent-tool-in-p-mode.md` (Section 2.3):
> "in headless mode this prompt cannot be answered, so worktrees with committed
> changes **will persist** and must be cleaned up externally"

**Document B:** `01-agent-tool-in-p-mode.md` (Section 2.1, quoting official docs):
> "Each subagent gets its own worktree that is **automatically cleaned up** when
> the subagent finishes without changes."

**Analysis:** These two passages from the same document describe different conditions:
cleanup is automatic only for worktrees with no changes; worktrees with committed
changes persist. This is not a true contradiction — the conditions are different.
However, the executive summary and recommended pattern sections do not make this
distinction clearly, which could mislead implementation agents into expecting automatic
cleanup in all cases.

**Resolution:** Not a contradiction per se, but a documentation clarity issue.
The implementation agent for the `runner` module must explicitly call
`git worktree remove` in the cleanup path for all completed worker sessions.

**Action required before M2:** Ensure the runner implementation issue includes a
requirement to call `git worktree remove` after each worker completes, regardless of
whether the worktree is expected to be "automatically cleaned up."

---

## Test Fixture Strategy for Implementation Agents

### The Problem

The breadmin-conductor `runner` module must parse `stream-json` output from `claude -p`.
The `stream-json` format is NDJSON (newline-delimited JSON) with multiple event types:
`system/init`, `assistant`, `user`, `result`, and potentially `stream_event` when
`--include-partial-messages` is passed.

Implementing a parser against synthetic or invented event shapes is a hallucination
risk: event field names, optional fields, and nesting depths may differ from reality.
Existing research (from `12-subprocess-token-overhead.md` Section F3) documents the
actual structure of the `result` event:

```json
{
  "type": "result",
  "session_id": "...",
  "is_error": false,
  "result": "...",
  "usage": {
    "input_tokens": 45000,
    "output_tokens": 8000,
    "cache_read_input_tokens": 12000,
    "cache_creation_input_tokens": 3000
  },
  "total_cost_usd": 0.12
}
```

However, the `system/init`, `assistant`, and `user` event shapes are not documented
in any existing M1 research doc. Implementation agents that build the parser without
fixtures will need to invent field names.

### Recommended Strategy: Golden File Fixtures from Real Runs

**Step 1: Create a fixture capture script.**

Create `tests/fixtures/capture.py` that:
1. Runs `claude -p "Echo back: Hello, conductor" --output-format stream-json
   --max-turns 1 --dangerously-skip-permissions`
2. Captures the full NDJSON output
3. Writes each line as a separate fixture file in `tests/fixtures/stream_json/`

```python
# tests/fixtures/capture.py
"""
Run once per Claude Code version to regenerate stream-json fixtures.
Requires ANTHROPIC_API_KEY in environment.

Usage: python tests/fixtures/capture.py
"""
import subprocess
import json
import os
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "stream_json"

CAPTURE_SCENARIOS = [
    {
        "name": "simple_success",
        "prompt": "Return the string 'OK' and nothing else.",
        "flags": ["--max-turns", "1"],
    },
    {
        "name": "tool_use_bash",
        "prompt": "Run: echo hello. Then return 'done'.",
        "flags": ["--max-turns", "2", "--allowedTools", "Bash(echo *)"],
    },
    {
        "name": "error_max_turns",
        "prompt": "Count from 1 to 1000000 one number at a time, pausing after each.",
        "flags": ["--max-turns", "1"],
    },
]

def capture_scenario(scenario: dict) -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        "claude", "-p", scenario["prompt"],
        "--output-format", "stream-json",
        "--dangerously-skip-permissions",
        "--no-session-persistence",
        *scenario["flags"],
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"},
    )
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    events = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    fixture_path = FIXTURE_DIR / f"{scenario['name']}.jsonl"
    with open(fixture_path, "w") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")

    print(f"Wrote {len(events)} events to {fixture_path}")

if __name__ == "__main__":
    for s in CAPTURE_SCENARIOS:
        capture_scenario(s)
```

**Step 2: Commit the captured fixtures and gate tests on them.**

Run `capture.py` once against the installed Claude Code version, commit the output
JSONL files to `tests/fixtures/stream_json/`, and use them as the ground truth in unit
tests:

```python
# tests/unit/test_stream_parser.py
import json
from pathlib import Path
from conductor.session import StreamParser  # the module under test

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "stream_json"

def test_parses_simple_success_result():
    events = []
    with open(FIXTURE_DIR / "simple_success.jsonl") as f:
        for line in f:
            events.append(json.loads(line))
    result_events = [e for e in events if e["type"] == "result"]
    assert len(result_events) == 1
    result = result_events[0]
    assert "session_id" in result
    assert "usage" in result
    assert "input_tokens" in result["usage"]
    # etc.
```

**Step 3: Add a `pytest` marker to skip tests if fixtures don't exist.**

```python
import pytest
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "stream_json"

@pytest.mark.skipif(
    not (FIXTURE_DIR / "simple_success.jsonl").exists(),
    reason="Stream-json fixtures not captured. Run: python tests/fixtures/capture.py"
)
def test_parses_simple_success_result():
    ...
```

**Step 4: Document the capture procedure and version pinning.**

Add a `tests/fixtures/README.md` (or a note in the project README) that records:
- The Claude Code version the fixtures were captured against
- The command used to regenerate them
- A note that fixtures must be regenerated when the stream-json schema changes

**Why capture from real runs (not from the Claude Code source repo):**

The Claude Code source is not public. Community reverse-engineering of event schemas
may be incomplete or stale. The only reliable source is actual output from a live
`claude -p` invocation. The capture script approach:
- Takes < 30 seconds to run with `--max-turns 1`
- Requires only `ANTHROPIC_API_KEY` to be set
- Produces exact, version-specific fixtures
- Can be regenerated automatically in CI via a scheduled job

**`pytest-golden` as an alternative:**

The `pytest-golden` plugin (`pip install pytest-golden`) supports the same golden-file
pattern with automatic regeneration via `pytest --update-goldens`. It is a lighter
alternative to a custom capture script for simple snapshot tests. Use it for unit-level
parser tests; use the custom capture script for integration-level scenario coverage.

---

## Recommended Additions to Research Agent Prompts

The following prompt additions are grounded in the Anthropic "Reduce hallucinations"
documentation, the Chain-of-Verification (CoVe) technique, and the agentic hallucination
propagation literature. They should be added to the research-worker prompt template
and to the `research-worker.md` skill file.

### Addition R-1: Verbatim Quote Anchoring (High Impact)

Add to the research agent prompt after the section on writing key findings:

```markdown
## Citation Discipline

For every key finding in your document:
1. If the finding is from official documentation, copy the exact verbatim quote
   from the source page and include it in a blockquote in your document.
2. If you cannot find a verbatim quote that directly supports the claim, mark
   the claim as `[INFERRED]` and explain your reasoning chain.
3. Never paraphrase official documentation without also providing the verbatim quote.
   Paraphrasing introduces hallucination risk; the quote is the ground truth.

After writing the document, re-read every claim in your Executive Summary.
For each claim, identify the section that supports it. If a section is missing
a source link, add one before committing.
```

**Why this works:** Anthropic's own documentation recommends "extract word-for-word
quotes first before performing the task" as an effective anti-hallucination technique
for tasks involving long documents. The verbatim quote requirement forces the agent
to locate the actual text rather than generating a plausible paraphrase.

### Addition R-2: Explicit Uncertainty Budgeting (Medium Impact)

Add at the end of the research agent system prompt:

```markdown
## Uncertainty and Confidence Levels

You MUST apply confidence tags to key findings using this taxonomy:
- `[TESTED]`: You ran the code or command and observed the result yourself.
  Include the exact command and version number.
- `[DOCUMENTED]`: A verbatim quote from official documentation directly
  states this. Include the blockquote and URL.
- `[INFERRED]`: You are concluding this from combining multiple sources
  or from architectural reasoning. Say so explicitly.

It is better to write fewer [DOCUMENTED] findings than to claim [DOCUMENTED]
for things you have not directly verified in official sources. If unsure
about a source's authority, cite it but tag the finding as [INFERRED].

Do NOT use hedging language like "likely", "probably", or "appears to" as a
substitute for a confidence tag. Use the tag system instead.
```

**Why this works:** The literature on verbal uncertainty calibration (VUC) shows that
explicitly adding hedging instructions to prompts reduces hallucinated confident claims.
The structured tag system provides a concrete mechanism for the agent to express
uncertainty without vague hedging.

### Addition R-3: Post-Write Link Audit Step (Medium Impact)

Add as the final step before the research agent commits its document:

```markdown
## Pre-Commit Link Audit

Before committing your research document, perform the following checks:
1. Extract all URLs from the document (every line starting with `- [` or
   containing `](http`).
2. For each URL, verify it returns a 200 OK response using:
   `curl -s -o /dev/null -w "%{http_code}" --max-time 10 "<URL>"`
3. For any URL that returns non-200 (including 301/302 redirects), note this
   in a comment next to the link: `<!-- link-status: 404 as of YYYY-MM-DD -->`
4. Do not replace broken links with invented alternatives. Remove them or
   replace with a working equivalent you have verified.
5. Log the count of links checked, links passing, and links failing at the
   bottom of your document in a `<!-- link-check: N/N passing -->` comment.
```

**Why this works:** Requiring the agent to actively verify each URL before committing
catches dead links at authoring time. The curl check is cheap (< 5 seconds per URL)
and uses only the Bash tool already available to research workers. This reduces the
work required by the post-merge lychee CI check.

**Note on interaction with `06-security-threat-model.md`:** The security research
establishes that research workers are allowed `WebFetch` only for approved domains.
The `curl` link audit above is a more direct and lower-risk alternative to WebFetch
for link validation — it only checks HTTP status codes, not fetches page content.
It should be included in the research-worker's `--allowedTools` policy as
`"Bash(curl -s -o /dev/null -w * --max-time * *)"`.

### Addition R-4: Cross-Reference Consistency Check (Low Impact, High Value)

Add to the research agent prompt before the "Follow-Up Research Recommendations"
section:

```markdown
## Cross-Document Consistency Check

Before writing your "Cross-References" section, read the executive summaries of
all existing docs in docs/research/. For each claim in your document that also
appears in an existing doc:
1. Check whether the two claims agree on numbers, flags, version numbers,
   and behavioral descriptions.
2. If they contradict, note the contradiction explicitly: "This doc states X.
   doc-N states Y. They are contradictory. Resolution: [your analysis]."
3. Do not silently resolve contradictions by picking one side. Surface them
   explicitly for the orchestrator to investigate.
```

---

## Post-Merge Review Checklist for the Orchestrator

The orchestrator must execute this checklist after merging each research PR before
unblocking downstream issues that depend on the merged findings.

### Step 1: Verify CI Passes
```bash
gh pr checks <PR-number> --json name,state --jq '.[] | select(.state != "SUCCESS")'
```
Expected: empty output (all checks passing). If lychee link-check job exists, confirm
it passed. If it did not exist on this PR (pre-CI-setup), run lychee manually:
```bash
lychee --timeout 20 docs/research/*.md --format json | jq '.error_map | length'
```

### Step 2: Count Newly Introduced `[INFERRED]` Findings
```bash
git diff main..HEAD -- docs/research/ | grep '^\+.*\[INFERRED\]' | wc -l
```
For each `[INFERRED]` finding introduced, assess:
- Is it high-stakes? (Would a wrong assumption require rework of a core module?)
- If yes, file a verification issue immediately.

### Step 3: Check for Unresolved Contradictions
```bash
git diff main..HEAD -- docs/research/ | grep -i 'contradict\|conflict\|but.*doc.*states\|disagrees'
```
If any explicit contradiction notices are found, verify they have a corresponding
issue filed or are flagged in the current research doc's "Cross-Document Contradiction
Analysis" section.

### Step 4: Validate All Sources in the New Doc
For each link in the `## Sources` section:
```bash
grep -h "http" docs/research/<new-doc>.md | grep -o 'https\?://[^)]*' | \
  while read url; do
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$url")
    echo "$code $url"
  done
```
Expected: all 200s (or 301/302 to valid pages). Non-200s must be investigated before
downstream issues are unblocked.

### Step 5: Check Follow-Up Issues Were Created
```bash
gh issue list --state open --label research --json number,title --repo bread-wood/breadmin-conductor | \
  jq -r '.[] | "\(.number) \(.title)"'
```
Compare against the "Follow-Up Research Recommendations" section of the merged doc.
Every recommendation that is not already an open issue should be created now if
it is relevant to M2 dispatch.

### Step 6: Update the Dependency Graph
Check which downstream issues list the merged issue in their "Dependencies" field:
```bash
gh issue list --state open --json number,title,body --repo bread-wood/breadmin-conductor | \
  jq -r '.[] | select(.body | test("Depends on.*#<ISSUE_NUMBER>")) | "\(.number) \(.title)"'
```
For each unblocked downstream issue, verify no `[INFERRED]` finding from the merged
doc is a hard dependency. If it is, file the verification issue before dispatching
the downstream agent.

### Step 7: Confirm Allowed Scope Was Respected
```bash
git diff main..HEAD --name-only | grep -v '^docs/research/'
```
Expected: empty output. Research agents must only write to `docs/research/`. Any
changes outside this directory require explanation.

### Step 8: Archive Session Summary to Notion (If Configured)
```bash
# Only if mcp__notion__API-post-page is available in orchestrator session
# Create a brief session summary in the Research Sessions page
```
Record: PR number, date, number of `[INFERRED]` findings introduced, number of
verification issues filed, broken links found.

---

## Follow-Up Research Recommendations

### FR-01: Empirical Verification of Seven High-Stakes Inferences

The seven V-0N findings in Section 4 each require a "verify claim" agent that runs
a minimal reproduction case and reports pass/fail. These are not covered by existing
open issues.

**Priority:** High — should be filed before M2 dispatch.
**Suggested issue title format:** `Verify: [short claim description] (V-0N from #25)`

### FR-02: Stream-JSON Event Schema Documentation

`12-subprocess-token-overhead.md` documents the `result` event schema from the
`--output-format stream-json` flag. However, the `system/init`, `assistant`, and
`user` event schemas are not documented in any M1 research doc. Implementation agents
for the `runner` module will need this.

**Priority:** High — required before M2 runner implementation.
**Suggested issue title:** `Research: stream-json event schema for system/init, assistant,
and user events` (with empirical fixture capture as the deliverable).

This issue is **not a duplicate** of existing research issues. The closest is #8
(rate limit detection from stream-json output), but that issue addresses only the
`result` event's `is_error` field. The full event taxonomy is undocumented.

### FR-03: Confidence Tag Automation via Pre-Commit Hook

A pre-commit hook that scans research docs for key finding patterns (sentences that
assert behavior with words like "works", "requires", "is available", "fails") and
warns if no confidence tag is present would enforce the taxonomy without relying on
agents remembering to apply it.

**Priority:** Medium — reduces ongoing maintenance overhead.
**Suggested tool:** Python script using `re` to match assertion patterns. Integrate
with `pre-commit` hooks or the conductor's existing `PostToolUse` hook infrastructure.

### FR-04: Contradiction Detection Script

A script that cross-references numbers, flag names, and version assertions across all
research docs would catch C-01 class contradictions (conflicting threshold values)
automatically. The CONTRADOC technique (arXiv:2311.09182) shows that LLMs can be
prompted to identify self-contradictions within documents; the same approach can be
extended to cross-document analysis by including multiple docs in context.

**Priority:** Low — useful for ongoing maintenance but not critical for M2.
**Suggested issue title:** `Tooling: Cross-document contradiction detector for
docs/research/`

### FR-05: `lychee` False Positive Rate Calibration

The recommended `.lycheeignore` patterns in Section 2 are based on general knowledge
of sites that block crawlers (LinkedIn, arxiv). The actual false positive rate for
the specific URLs cited in conductor's research docs has not been measured.

**Priority:** Low — implement lychee CI first, then tune ignore patterns based on
observed false positive output.

---

## Sources

- [AgentHallu: Benchmarking Automated Hallucination Attribution of LLM-based Agents — arXiv:2601.06818](https://arxiv.org/abs/2601.06818) — Hallucination taxonomy for agent workflows (5 categories, 14 sub-categories); step-localization benchmark; tool-use hallucinations most challenging at 11.6% accuracy
- [LLM-based Agents Suffer from Hallucinations: A Survey of Taxonomy, Methods, and Directions — arXiv:2509.18970](https://arxiv.org/abs/2509.18970) — Comprehensive survey of hallucination types in agentic settings; propagation mechanisms; 39% average performance drop in multi-turn vs. single-turn
- [VeriTrail: Closed-Domain Hallucination Detection with Traceability — arXiv:2505.21786](https://arxiv.org/abs/2505.21786) — Multi-step workflow provenance tracking; Claimify-based claim extraction; error localization in intermediate steps
- [VeriTrail — Microsoft Research](https://www.microsoft.com/en-us/research/blog/veritrail-detecting-hallucination-and-tracing-provenance-in-multi-step-ai-workflows/) — Blog explanation of VeriTrail approach and motivation
- [Mitigating Hallucination in Large Language Models: An Application-Oriented Survey on RAG, Reasoning, and Agentic Systems — arXiv:2510.24476](https://arxiv.org/html/2510.24476v1) — RAG hallucination mitigation survey; 42–68% reduction from retrieval integration
- [Chain-of-Verification Reduces Hallucination in Large Language Models — ACL Anthology](https://aclanthology.org/2024.findings-acl.212/) — CoVe four-step verification process; outperforms Zero-Shot, Few-Shot, and CoT for factual tasks
- [Reduce Hallucinations — Anthropic Claude Docs](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-hallucinations) — Authoritative guidance: allow "I don't know", extract word-for-word quotes first, cite quotes for each claim, best-of-N verification
- [Reduce Hallucinations — Anthropic Minimizing Hallucinations](https://docs.anthropic.com/en/docs/minimizing-hallucinations) — Official Anthropic recommendations for reducing hallucination
- [GitHub: tcort/markdown-link-check](https://github.com/tcort/markdown-link-check) — Node.js link checker with JUnit XML output; widely used in CI
- [GitHub: lycheeverse/lychee](https://github.com/lycheeverse/lychee) — Rust-based fast async link checker; recommended tool
- [GitHub: lycheeverse/lychee-action](https://github.com/lycheeverse/lychee-action) — Native GitHub Action for lychee; 576 links in ~1 minute CI benchmark
- [Lychee Documentation — lychee.cli.rs](https://lychee.cli.rs/) — Complete lychee configuration reference: `--cache`, `--timeout`, `--exclude`, `.lycheeignore`
- [GitHub: becheran/mlc](https://github.com/becheran/mlc) — Alternative Rust link checker; simpler than lychee
- [Attribution Techniques for Mitigating Hallucinated Information in RAG Systems — arXiv:2601.19927](https://arxiv.org/html/2601.19927) — System-level vs. prompt-based citation approaches; Trust Integrity Score (TIS)
- [Contradiction Detection in RAG Systems — arXiv:2504.00180](https://arxiv.org/html/2504.00180v1) — LLMs as context validators for information consistency; multi-perspective evidence retrieval
- [CONTRADOC: Understanding Self-Contradictions in Documents — arXiv:2311.09182](https://arxiv.org/pdf/2311.09182) — LLM-based self-contradiction detection within documents; applicable to cross-doc analysis
- [Hallucination Detection in Foundation Models for Decision-Making — ACM Computing Surveys](https://dl.acm.org/doi/10.1145/3716846) — Comprehensive taxonomy and state-of-the-art review
- [Survey and Analysis of Hallucinations: Attribution to Prompting Strategies or Model Behavior — Frontiers in AI](https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2025.1622292/full) — CoT reduces hallucination but not universally effective; prompt strategy comparisons
- [Advanced Prompt Engineering for Reducing Hallucination — Medium](https://medium.com/@bijit211987/advanced-prompt-engineering-for-reducing-hallucination-bb2c8ce62fc6) — Verbatim extraction before task technique; auditable citation approach
- [pytest-subprocess — PyPI](https://pypi.org/project/pytest-subprocess/) — Fake subprocess results for unit testing; `fp.pass_command()` for selective real execution
- [pytest-golden — PyPI](https://pypi.org/project/pytest-golden/) — Golden file test fixtures with `--update-goldens` regeneration
- [ApprovalTests.Python — GitHub](https://github.com/approvals/ApprovalTests.Python) — Approval testing pattern for complex output comparison
- [How to Capture Stdout/Stderr Output — pytest Docs](https://docs.pytest.org/en/stable/how-to/capture-stdout-stderr.html) — `capfd` fixture for subprocess output capture
- [docs/research/01-agent-tool-in-p-mode.md](01-agent-tool-in-p-mode.md) — isolation: worktree headless compatibility (V-01); subprocess token overhead (V-02); worktree cleanup contradiction (C-04)
- [docs/research/02-session-continuity.md](02-session-continuity.md) — Auto-compaction threshold 75% claim (C-01, V-06); stateless chaining model
- [docs/research/04-configuration.md](04-configuration.md) — CONDUCTOR_MODEL default Opus (C-03); CLAUDECODE nesting issue (V-07); CLAUDE_CONFIG_DIR isolation
- [docs/research/06-security-threat-model.md](06-security-threat-model.md) — bypass mode and deny rules contradiction (C-02, V-04); pre-run security scan checklist
- [docs/research/07-skill-adaptation.md](07-skill-adaptation.md) — allow-tools for research agents; headless skill invocation findings
- [docs/research/08-usage-scheduling.md](08-usage-scheduling.md) — Rate limit detection from stream-json; cost estimates that depend on Sonnet default (C-03)
- [docs/research/10-settings-mcp-injection.md](10-settings-mcp-injection.md) — --settings flag precedence; CLAUDE_CONFIG_DIR interaction with MCP injection
- [docs/research/12-subprocess-token-overhead.md](12-subprocess-token-overhead.md) — Auto-compaction threshold 83.5% claim (C-01); --strict-mcp-config verification (V-05); token estimates based on Sonnet (C-03); result event schema
