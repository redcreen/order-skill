# 2026-04-21 Order Wrapper Guard

## Context

`order` was already using a wrapper-style OpenClaw integration through `before_prompt_build`, which avoided the earlier `health` mistake of short-circuiting the real session.

But it still had one weakness:

- the wrapper contract existed only as prompt guidance
- the host plugin did not prevent direct `exec` calls to bundled `order/scripts/*.py`

That meant the model could still bypass `order_hard_execute.py` and drift away from the intended hard-execution contract.

## Decision

Keep the wrapper architecture, but add a host-side tool guard.

The intended host contract is now:

1. inject the wrapper contract into the real session
2. keep the normal agent flow and transcript intact
3. block direct execution of bundled runtime scripts
4. force runtime actions through `plugins/openclaw-order/scripts/order_hard_execute.py`

## Implementation

1. Added `before_tool_call` to `order-host-plugin/src/plugin/index.js`.
2. Added a direct runtime-script map that recognizes bundled `order/scripts/*.py`.
3. Blocked direct execution of those scripts in matching `order` sessions.
4. Returned a concrete wrapper command in the block reason so the model can retry on the correct path.
5. Extended `scripts/test_order_plugin_runtime.sh` with host-plugin checks for:
   - prompt wrapper injection
   - direct runtime-script blocking
   - wrapper-call allow path
   - non-order-session ignore path

## Validation

Validated with:

```bash
node --check order-host-plugin/src/plugin/index.js
bash scripts/test_order_plugin_runtime.sh
```

## Outcome

`order` stays on the correct wrapper architecture and now also has a host-side guard against direct script bypass. That removes the main remaining path where it could have drifted back toward the same class of “prompt-only contract” failure that earlier affected `health`.

