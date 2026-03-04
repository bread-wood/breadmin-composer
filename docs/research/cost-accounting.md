# Research: Cost Accounting

**Consolidated from:** #5, #8, #12, #63, #92, #94
**Status:** Current
**Date:** 2026-03-04

---

## 1. Token Counting

The `result` event in `--output-format stream-json` is the sole authoritative source for
token counts and cost. It carries:

```json
{
  "type": "result",
  "is_error": false,
  "subtype": "success",
  "total_cost_usd": 0.8341,
  "usage": {
    "input_tokens": 142500,
    "output_tokens": 18200,
    "cache_read_input_tokens": 118000,
    "cache_creation_input_tokens": 4800
  },
  "num_turns": 28,
  "duration_ms": 187432
}
```

| Field | Description |
|-------|-------------|
| `input_tokens` | Regular input tokens billed at full rate |
| `output_tokens` | Output tokens generated |
| `cache_read_input_tokens` | Tokens read from prompt cache (10% of input price) |
| `cache_creation_input_tokens` | Tokens written to prompt cache (125% of input price) |
| `total_cost_usd` | Authoritative cost (API key mode); null or 0 in subscription mode |

---

## 2. Prompt Cache Pricing Tiers

Prompt caching significantly reduces cost for long-running agents that re-read the same
context on every turn (CLAUDE.md files, skill files, prior conversation history).

| Token type | Sonnet 4.6 per MTok | Notes |
|------------|---------------------|-------|
| Input (regular) | $3.00 | Full billing rate |
| Output | $15.00 | Full billing rate |
| Cache write (`cache_creation_input_tokens`) | $3.75 | 125% of input |
| Cache read (`cache_read_input_tokens`) | $0.30 | 10% of input |

**Opus 4.6 multiplier:** approximately 5x across all token types.

Cache reads are the most cost-effective token type. A session that reads 100K tokens from
cache costs $0.03 instead of $0.30 for uncached input.

> Needs verification as of v0.1.0 — pricing subject to change

---

## 3. Model Families

| Model | Notes |
|-------|-------|
| `claude-sonnet-4-6` | Default; balanced cost/quality |
| `claude-opus-4-6` | ~5x cost; higher quality for complex tasks |
| Haiku (any version) | ~0.2x cost; fast; lower quality |

The `system/init` event contains the actual model used:

```json
{"type": "system", "subtype": "init", "model": "claude-sonnet-4-6", ...}
```

---

## 4. `cost_usd` Calculation in Subscription Mode

When `auth_mode == "subscription"`, `result.total_cost_usd` is null or 0. Estimate:

```python
SONNET_PRICES = {
    "input_per_mtok": 3.00,
    "output_per_mtok": 15.00,
    "cache_write_per_mtok": 3.75,
    "cache_read_per_mtok": 0.30,
}
OPUS_MULTIPLIER = 5.0

def estimate_cost_usd(usage: dict, model: str) -> float:
    p = SONNET_PRICES
    mult = OPUS_MULTIPLIER if "opus" in model.lower() else 1.0
    return round((
        usage.get("input_tokens", 0) / 1_000_000 * p["input_per_mtok"]
        + usage.get("output_tokens", 0) / 1_000_000 * p["output_per_mtok"]
        + usage.get("cache_creation_input_tokens", 0) / 1_000_000 * p["cache_write_per_mtok"]
        + usage.get("cache_read_input_tokens", 0) / 1_000_000 * p["cache_read_per_mtok"]
    ) * mult, 6)
```

---

## 5. Subprocess Token Overhead

Each `claude -p` subprocess loads context before doing any work. Overhead sources:

| Layer | Token estimate |
|-------|---------------|
| Core Claude Code system prompt | ~5,000-8,000 |
| CLAUDE.md files from ancestor dirs | ~4,000-15,000 |
| Plugin/skill descriptions | ~5,000-20,000 |
| MCP tool catalogs | ~5,000-20,000 |
| Built-in tool definitions | ~8,000-12,000 |
| **Total (unoptimized)** | **~50,000** |

**With `CLAUDE_CONFIG_DIR` isolation + minimal `--allowedTools`:** ~5,000 tokens.

This 10x reduction matters significantly for subscription sessions where token usage
counts against the 5-hour window limit.

---

## 6. `--max-budget-usd` Scope

`--max-budget-usd` enforces a hard budget cap for API key-authenticated sessions only.
It has NO effect on subscription-authenticated sessions (Pro, Max, Max 20x). Do not
use it as a safety control for subscription runs.

---

## 7. Usage Governor Budget Gate

The orchestrator's `UsageGovernor` tracks cumulative cost across all dispatched agents
for the current run:

```python
total_cost = sum(entry.total_cost_usd for entry in cost_ledger if entry.run_id == run_id)
```

This is used for reporting and soft warnings, not hard enforcement (since subscription
sessions cannot have cost enforced programmatically).

---

## 8. Sources

- Research files: #5, #8, #12, #63, #92, #94
- Anthropic pricing page (anthropic.com/pricing)
- DEV.to: Building a 24/7 Claude Code Wrapper (token overhead analysis)
