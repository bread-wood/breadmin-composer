# Research: Stream-JSON Events and Streaming

**Consolidated from:** #3, #5, #14, #75, #87, #92
**Status:** Current
**Date:** 2026-03-04

---

## 1. Stream-JSON Event Types

`--output-format stream-json` emits one JSON object per line. Key event types:

| `type` | `subtype` | Description |
|--------|-----------|-------------|
| `system` | `init` | Session initialization; contains `session_id`, `model`, `tools` |
| `assistant` | — | Assistant turn; contains text or tool call |
| `user` | — | Tool result returned to assistant |
| `result` | `success` / `error_*` | Session completion; authoritative cost and token data |
| `rate_limit_event` | — | Usage window status (see rate-limits.md) |
| `compact_boundary` | — | Context compaction occurred |

### 1.1 `system/init` Event

```json
{
  "type": "system",
  "subtype": "init",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "model": "claude-sonnet-4-6",
  "tools": ["Bash", "Read", "Edit", "Write", "Glob", "Grep"]
}
```

`session_id` from this event is used as the per-session log filename.

### 1.2 `result` Event

```json
{
  "type": "result",
  "subtype": "success",
  "is_error": false,
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
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

The `result` event is always the final event. Parse it to classify success vs. failure
and extract cost data.

### 1.3 `result.subtype` Values

| `subtype` | `is_error` | Description |
|-----------|-----------|-------------|
| `success` | false | Normal completion |
| `error_max_turns` | true | `--max-turns` limit hit |
| `error_max_budget_usd` | true | `--max-budget-usd` limit hit (API key only) |
| `error_during_execution` | true | Tool/execution error or billing cap |
| `rate_limited` | true | 429 rate limit |
| `billing_error` | true | 402 billing error |

---

## 2. Partial Output

`stream-json` events arrive incrementally as the model generates tokens. The conductor
reads events line by line from the subprocess stdout:

```python
async for line in process.stdout:
    try:
        event = json.loads(line.decode())
    except json.JSONDecodeError:
        continue  # partial line or non-JSON output
    handle_event(event)
```

**Important:** the final `result` event may not arrive if the subprocess is killed or crashes.
Always handle the case where no `result` event is received (treat as failure with
`subtype="process_killed"`).

---

## 3. Crash Recovery

If the subprocess exits without emitting a `result` event:

1. Check the exit code (see rate-limits.md for the taxonomy).
2. Treat as `is_error=True` with `subtype="process_exit"`.
3. Increment retry count.
4. If retry count < max: re-dispatch from the claimed state.
5. If retry count >= max: exhaust the issue.

Partial bead state written before the crash is preserved. The orchestrator reconciles
on restart by scanning `store.list_work_beads(state="claimed")` and checking for
associated open PRs.

---

## 4. SSE Streaming Through Credential Proxy

When using `ANTHROPIC_BASE_URL` to point to a loopback credential proxy, the proxy must
correctly forward Server-Sent Events (SSE) without buffering.

### 4.1 Correct aiohttp Pattern

```python
import aiohttp
from aiohttp import web

async def proxy_handler(request: web.Request) -> web.StreamResponse:
    async with aiohttp.ClientSession() as session:
        async with session.request(
            request.method,
            f"https://api.anthropic.com{request.path_qs}",
            headers={**request.headers, "x-api-key": REAL_API_KEY},
        ) as upstream:
            response = web.StreamResponse(
                status=upstream.status,
                headers={k: v for k, v in upstream.headers.items()
                         if k.lower() not in ("transfer-encoding", "connection")},
            )
            await response.prepare(request)
            async for chunk in upstream.content.iter_any():
                await response.write(chunk)
            return response
```

**Key:** use `iter_any()` or `iter_chunked()` — not `read()`. Buffering the full response
causes sub-agent sessions to appear frozen until the LLM finishes generating.

### 4.2 Required Response Headers

The proxy must forward:
- `Content-Type: text/event-stream`
- `Cache-Control: no-cache`
- `X-Accel-Buffering: no` (prevents nginx buffering if behind a reverse proxy)

Do NOT forward `Transfer-Encoding` or `Connection` headers directly — aiohttp manages these.

### 4.3 `ANTHROPIC_BASE_URL` Precedence

Process-level env var takes precedence over `settings.json` `env` field in normal operation.

**Known regression (v2.0.1 - v2.0.7x):** `settings.json` `env.ANTHROPIC_BASE_URL` incorrectly
overrode process-level env in some versions. Ensure the subprocess's `CLAUDE_CONFIG_DIR`
points to a clean temp directory with no `settings.json` that sets `ANTHROPIC_BASE_URL`.

> Needs verification as of v0.1.0 — precedence regression may be fixed in current versions

---

## 5. `select()` Multiplexing for Multiple Agents

When monitoring multiple concurrent `claude -p` subprocesses, use asyncio rather than
blocking reads:

```python
import asyncio

async def monitor_agents(agents: list[AgentProcess]) -> list[RunResult]:
    tasks = [asyncio.create_task(read_stream(a)) for a in agents]
    return await asyncio.gather(*tasks, return_exceptions=True)
```

This ensures that slow agents do not block progress reporting for fast agents.

---

## 6. Sources

- Research files: #3, #5, #14, #75, #87, #92
- Claude Code CLI reference: stream-json format
- aiohttp documentation: streaming responses
- GitHub Issue #28482: Agent hangs indefinitely
