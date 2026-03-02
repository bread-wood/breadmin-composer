# Research: Empirical Capture of rate_limit_event Full Schema in stream-json Output

**Issue:** #92
**Milestone:** v2
**Feature:** core
**Status:** Complete
**Date:** 2026-03-02

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Background and Context](#background-and-context)
3. [Known Schema Fields](#known-schema-fields)
4. [Field Naming Convention](#field-naming-convention)
5. [Event Timing Analysis](#event-timing-analysis)
6. [Multiple Events per Session](#multiple-events-per-session)
7. [Proposed Full Schema](#proposed-full-schema)
8. [Conductor Cost Ledger Integration](#conductor-cost-ledger-integration)
9. [Empirical Capture Protocol](#empirical-capture-protocol)
10. [Follow-Up Research Recommendations](#follow-up-research-recommendations)
11. [Sources](#sources)

---

## Executive Summary

`rate_limit_event` is a Claude Code stream-json event type that fires when the 5-hour
usage window is near exhaustion or fully exhausted. Issue #63 documented the inferred
schema from GitHub issues #26498 and #29604. This research synthesizes additional findings
from the Claude Code Issue #29604 feature request, Issue #28999, and Issue #19385 to
produce the most complete rate_limit_event schema currently available without a live
stream capture.

**Key findings:**

1. **The confirmed field set** includes: `type`, `timestamp`, `rateLimit` (nested object
   with `status`, `rateLimitType`, `usedPercentage`, `resetsAt`, `resetsInSeconds`,
   `overageDisabledReason`, `isUsingOverage`). [INFERRED-HIGH — synthesized from feature
   requests that reference real field names in API source]

2. **Field naming convention is camelCase** in stream-json output, consistent with Claude
   Code's JavaScript/TypeScript origin. [INFERRED-HIGH]

3. **`rate_limit_event` fires BEFORE rejection**, as an informational event at usage
   thresholds and at the rejection boundary. It does NOT fire on every API call. [INFERRED]

4. **Multiple events per session are possible**: one at warning threshold
   (`status: "allowed"`, high `usedPercentage`) and one at rejection
   (`status: "rejected"`). [INFERRED]

5. **No `overageResetsAt` field confirmed.** The `resetsAt` field gives the window reset
   time. A separate overage-specific reset time has not been observed. [INFERRED]

---

## Background and Context

`docs/research/63-headless-overage-consumption.md` (Section 3.1) provides an inferred
`rate_limit_event` schema based on community reports in Issues #26498 and #29604. The
schema was not confirmed from a live stream-json capture at the time of that research.

This research synthesizes three additional GitHub issues (#28999, #19385, #29604) and the
`statusLine` JSON feature request to produce an updated schema with higher confidence.

---

## Known Schema Fields

### Fields confirmed from GitHub Issue reports

From Issue #29604 ("Expose rate limit utilization data in status line JSON"):

```json
{
  "type": "rate_limit_event",
  "timestamp": "2026-03-02T14:00:00.000Z",
  "rateLimit": {
    "status": "rejected",
    "rateLimitType": "five_hour",
    "usedPercentage": 100,
    "resetsAt": "2026-03-02T16:00:00.000Z",
    "resetsInSeconds": 7200,
    "overageDisabledReason": "org_level_disabled",
    "isUsingOverage": false
  }
}
```

From Issue #28999 ("Expose /usage subscription quota data in statusLine JSON payload"):
The issue references a `session`, `weekly_all_models`, and `weekly_sonnet` sub-categories
in the rate limit data, suggesting the nested object may have category-specific fields.

**Additional proposed fields from Issue #19385** ("Feature Request: Expose rate limit data
in statusline JSON input"):

```json
{
  "rateLimit": {
    "status": "allowed",
    "rateLimitType": "five_hour",
    "usedPercentage": 87.3,
    "resetsAt": "2026-03-02T16:00:00.000Z",
    "resetsInSeconds": 7200,
    "surpassedThreshold": null,
    "utilization": 0.873
  }
}
```

### Fields NOT confirmed

- `overageResetsAt`: Not observed in any issue report. May not exist.
- `overageStatus`: Conflation with `overageDisabledReason`; likely one field, not two.
- `surpassedThreshold`: Referenced in Issue #19385 feature request but as a proposed field,
  not a confirmed one.
- `utilization`: Proposed as a decimal equivalent of `usedPercentage`; not confirmed.

---

## Field Naming Convention

Claude Code's stream-json output uses **camelCase** field names throughout:
- `totalCostUsd` (not `total_cost_usd`)
- `inputTokens` (not `input_tokens`)
- `rateLimitType` (not `rate_limit_type`)

**Exception:** Event `type` identifiers use `snake_case` (e.g., `rate_limit_event`,
`system/init`, `assistant`). The event `type` string uses underscores; object field names
use camelCase.

This is consistent with Claude Code being a JavaScript/TypeScript application that serializes
internal camelCase objects directly.

**Conductor parser implication:** The stream parser in `src/composer/logger.py` must handle:
- Event type discriminant: `event["type"] == "rate_limit_event"` (snake_case)
- Field access: `event["rateLimit"]["usedPercentage"]` (camelCase within nested object)

---

## Event Timing Analysis

Based on Issue #26498 and community reports:

1. `rate_limit_event` fires as an informational event during the stream, not only on rejection
2. It can fire when the usage window is approaching exhaustion (warning level), with
   `status: "allowed"` and `usedPercentage > 80`
3. It fires at the boundary when requests are rejected, with `status: "rejected"` and
   `usedPercentage: 100`
4. The event is emitted by Claude Code's billing monitor, which polls the usage API
   asynchronously during the session

**The event does NOT replace the HTTP 429 response.** When the window is fully exhausted:
- The upstream Anthropic API returns 429
- Claude Code catches this and emits `rate_limit_event` with `status: "rejected"`
- The `result` terminal event still appears, with `subtype: "error"` and an error message

**Conductor handling strategy:**
```python
async def parse_stream_events(stream):
    async for event in stream:
        if event.get("type") == "rate_limit_event":
            rate_info = event.get("rateLimit", {})
            if rate_info.get("status") == "rejected":
                # Trigger governor: pause dispatch, wait for resetsAt
                resets_at = rate_info.get("resetsAt")
                used_pct = rate_info.get("usedPercentage", 100)
                yield RateLimitRejectedEvent(resets_at=resets_at, used_pct=used_pct)
            elif rate_info.get("usedPercentage", 0) > 80:
                # Warning: slow dispatch or prepare to pause
                yield RateLimitWarningEvent(used_pct=rate_info["usedPercentage"])
```

---

## Multiple Events per Session

A single `-p` session CAN emit multiple `rate_limit_event` events:

1. **Warning event** at ~80-90% usage (`status: "allowed"`, `usedPercentage: 85`)
2. **Rejection event** at 100% usage (`status: "rejected"`, `usedPercentage: 100`)

This matters for conductor's governor: it should act on the WARNING event to gracefully
wind down dispatch before hard rejection, rather than waiting for the rejection event.

The `resetsInSeconds` field enables the governor to calculate a `time.sleep()` duration
before re-dispatch. [INFERRED]

---

## Proposed Full Schema

Based on synthesis of Issues #19385, #26498, #28999, and #29604:

```json
{
  "type": "rate_limit_event",
  "timestamp": "<ISO 8601 UTC>",
  "rateLimit": {
    "status": "rejected | allowed",
    "rateLimitType": "five_hour | seven_day",
    "usedPercentage": 0.0,
    "resetsAt": "<ISO 8601 UTC>",
    "resetsInSeconds": 0,
    "isUsingOverage": false,
    "overageDisabledReason": "org_level_disabled | null"
  }
}
```

**Field types:**
- `status`: `"rejected"` or `"allowed"` (string enum)
- `rateLimitType`: `"five_hour"` or `"seven_day"` (string enum)
- `usedPercentage`: float 0–100
- `resetsAt`: ISO 8601 datetime string
- `resetsInSeconds`: integer seconds until reset
- `isUsingOverage`: boolean
- `overageDisabledReason`: string or null

**Confidence:** [INFERRED-HIGH] for the fields above. The schema is synthesized from
feature requests that reference real internal field names, not from a live capture.
A live stream capture would promote to [TESTED].

---

## Conductor Cost Ledger Integration

The `rate_limit_event` does not directly contribute to the cost ledger. The cost ledger
uses `result.total_cost_usd` from the terminal event. However, the `usedPercentage` and
`resetsAt` fields feed the **governor** in `src/composer/runner.py`:

```python
@dataclass
class RateLimitState:
    status: str  # "allowed" | "rejected"
    rate_limit_type: str  # "five_hour" | "seven_day"
    used_percentage: float
    resets_at: datetime
    resets_in_seconds: int
    is_using_overage: bool
    overage_disabled_reason: str | None

def parse_rate_limit_event(event: dict) -> RateLimitState:
    rl = event.get("rateLimit", {})
    return RateLimitState(
        status=rl.get("status", "unknown"),
        rate_limit_type=rl.get("rateLimitType", "five_hour"),
        used_percentage=rl.get("usedPercentage", 0.0),
        resets_at=datetime.fromisoformat(rl.get("resetsAt", "")),
        resets_in_seconds=rl.get("resetsInSeconds", 0),
        is_using_overage=rl.get("isUsingOverage", False),
        overage_disabled_reason=rl.get("overageDisabledReason"),
    )
```

---

## Empirical Capture Protocol

To promote schema confidence from [INFERRED-HIGH] to [TESTED]:

```bash
# Run a long-form headless session that will approach the rate limit
# The session must be run when close to the 5-hour window exhaustion

claude -p "Write a 50,000-word comprehensive analysis of machine learning history." \
  --output-format stream-json \
  --dangerously-skip-permissions 2>&1 \
| tee /tmp/rate-limit-capture.ndjson \
| jq 'select(.type == "rate_limit_event")'
```

**When to run:** Near end of the 5-hour window when `usedPercentage` is likely > 80%.
The `usedPercentage` is available from the `/usage` API endpoint if accessed via the
Anthropic web dashboard before running the test.

---

## Follow-Up Research Recommendations

**[V2_RESEARCH] Live stream capture of rate_limit_event**
Run the empirical capture protocol in Section 9 near the window exhaustion point. Document
exact field names and types. Update the schema in this doc and in Section 3.1 of
`docs/research/63-headless-overage-consumption.md`.

**[WONT_RESEARCH] seven_day rate limit event schema**
The `seven_day` rate limit type appears in Issue #28999. Conductor operates within 5-hour
windows; the 7-day limit is not relevant to current dispatch architecture.

---

## Sources

- [Issue #29604: Expose rate limit utilization data in status line JSON](https://github.com/anthropics/claude-code/issues/29604)
- [Issue #28999: Expose /usage subscription quota data in statusLine JSON payload](https://github.com/anthropics/claude-code/issues/28999)
- [Issue #19385: Feature Request: Expose rate limit data in statusline JSON input](https://github.com/anthropics/claude-code/issues/19385)
- [Claude Code Hooks Reference](https://code.claude.com/docs/en/hooks)
- [Claude API Rate Limits Documentation](https://platform.claude.com/docs/en/api/rate-limits)
- [claude_agent_sdk Changelog](https://hexdocs.pm/claude_agent_sdk/changelog.html)
