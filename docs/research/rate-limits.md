# Research: Rate Limits and Backoff

**Consolidated from:** #3, #8, #23, #63, #92, #94
**Status:** Current
**Date:** 2026-03-04

---

## 1. Rate Limit Event Schema

`rate_limit_event` is a Claude Code stream-json event type that fires when the 5-hour usage
window is near exhaustion or fully exhausted.

### 1.1 Known Fields

```json
{
  "type": "rate_limit_event",
  "status": "approaching_limit",
  "isUsingOverage": false,
  "requestsRemaining": 12,
  "tokensRemaining": 45000,
  "resetsAt": "2026-03-04T18:00:00Z"
}
```

| Field | Type | Notes |
|-------|------|-------|
| `type` | string | Always `"rate_limit_event"` |
| `status` | string | `"approaching_limit"` or `"limit_reached"` |
| `isUsingOverage` | bool | `true` if consuming extra usage credits beyond subscription |
| `requestsRemaining` | int | Requests remaining in the current 5-hour window |
| `tokensRemaining` | int | Tokens remaining in the current 5-hour window |
| `resetsAt` | string (ISO 8601 UTC) | When the current window resets |

> Needs verification as of v0.1.0 — full schema not empirically confirmed; field names
> use camelCase but some may differ

### 1.2 When to Act

On `rate_limit_event` with `status == "limit_reached"`: stop dispatching new agents, start
backoff, requeue any in-flight issues. Do not kill active agents — let them complete or fail
naturally.

---

## 2. Backoff Patterns

### 2.1 Exponential Backoff Formula

```python
delay = min(max_delay, base * (2 ** attempt)) + jitter
# base = 30s, max_delay = 3600s, jitter = uniform(0, base * 0.25)
```

| Attempt | Base=30s | Effective delay (no jitter) |
|---------|----------|----------------------------|
| 0 | 30s | 30s |
| 1 | 30s | 60s |
| 2 | 30s | 120s |
| 3 | 30s | 240s |
| 4 | 30s | 480s |
| 5 | 30s | 960s |
| 6+ | 30s | 3600s (capped) |

### 2.2 Backoff State Persistence

The backoff deadline is persisted in the checkpoint (`backoff_until` field). A restarted
orchestrator reads this value and waits correctly rather than immediately retrying.

### 2.3 Probe After Backoff

After the backoff deadline passes, run a cheap probe before resuming full dispatch:

```python
result = runner.run("Respond with 'OK'.", max_turns=1, allowed_tools=[])
if result.is_error:
    extend_backoff(attempt + 1)
else:
    clear_backoff()
```

---

## 3. 429 vs. Billing Errors

### 3.1 HTTP 402 vs. HTTP 429

| HTTP code | Condition | Action |
|-----------|-----------|--------|
| `429` | 5-hour window exhausted (standard rate limit) | Exponential backoff; requeue |
| `402` | Extra usage billing authorization failure (overage disabled) | Human escalation; not retryable |

**Key finding:** HTTP 402 does NOT indicate weekly cap exhaustion. It indicates that the
account's extra usage (overage) settings prevented consumption beyond the window limit.
Both 5-hour and weekly cap exhaustion return 429 when extra usage is disabled.

### 3.2 `result.subtype` Values for Rate Limit Errors

| `result.subtype` | Description | Action |
|------------------|-------------|--------|
| `rate_limited` | 429 — 5-hour window exhausted | Backoff and requeue |
| `error_during_execution` | May indicate billing cap (402) | Check `result.error_code`; may need human escalation |
| `billing_error` | Billing-related failure | Human escalation |

### 3.3 Discriminating Weekly Cap vs. 5-Hour Window

The most reliable discrimination signal is the `anthropic-ratelimit-unified-representative-claim`
response header, but this is not accessible from outside the Claude Code subprocess.

Practical approach: use the `resetsAt` field in `rate_limit_event`. If `resetsAt` is more
than 6 hours in the future, the exhaustion is likely a weekly cap. If within 5 hours, it is
the standard 5-hour window.

> Needs verification as of v0.1.0 — weekly cap vs 5-hour discrimination

---

## 4. Overage / Extra Usage

### 4.1 Headless Overage Consumption

When `claude -p` encounters a 5-hour window exhaustion with extra usage (overage) enabled
at the account level, it **automatically consumes extra usage credits** without presenting
an interactive prompt. In headless mode there is no mechanism to pause for user input.

`isUsingOverage: true` in `rate_limit_event` is the programmatic signal that overage
consumption is active.

### 4.2 `--max-budget-usd` Does Not Protect Against Overage

`--max-budget-usd` applies only to API key-authenticated sessions. It has no effect on
subscription-authenticated headless runs and does not prevent overage charges.

To disable overage: set via claude.ai Settings > Usage (account-level) or organization
settings (Team/Enterprise). There is no per-session CLI flag.

### 4.3 `CLAUDE_CODE_DISABLE_EXTRA_USAGE`

No undocumented env var to disable extra usage was found in Claude Code binaries as of
March 2026.

> Needs verification as of v0.1.0

---

## 5. Subscription Tier Concurrency Limits

| Tier | Max concurrent agents (recommended) | 5-hour window approx. |
|------|-------------------------------------|----------------------|
| Pro | 2 | ~45 Opus / ~100 Sonnet requests |
| Max 5x | 3 | ~5x Pro |
| Max 20x | 5 | ~20x Pro |
| API key | Per tier limits | Governed by RPM limits |

Pre-dispatch budget check is mandatory. On 429: backoff, record `backoff_until`, requeue.

---

## 6. Sources

- Research files: #3, #8, #23, #63, #92, #94
- GitHub Issue #30484 (openclaw/openclaw): 402 vs 429 discrimination
- Anthropic rate limit documentation
