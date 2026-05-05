# 2026-05-04 Order Runtime API Adapter Boundary

## Problem

OpenClaw was becoming more than an interaction surface for `order`. The wrapper and host plugin knew too much about runtime command routing, which would make future MCP, Hermes, CLI, or warehouse-system entry points repeat the same business integration work.

## Decision

Introduce a stable JSON runtime command boundary under adapters:

- `order/scripts/order_runtime_api.py` is the command entry point.
- `order/runtime/command_manifest.json` owns command metadata and adapter contract details.
- `plugins/openclaw-order/scripts/order_hard_execute.py` remains the OpenClaw hard-execution entry, but now calls only the runtime API.
- `order-host-plugin/src/plugin/index.js` keeps only agent-scope prompt guidance and bypass guards.

## Implementation

The new runtime API accepts request envelopes with `command`, `request_id`, `actor`, `source`, `data_root`, and `payload`, then returns `request_id`, `command`, `status`, `result`, `error`, and `warnings`.

Core runtime actions are direct Python function calls. Legacy history/backfill helpers and smoke scripts are temporarily exposed through an internal compatibility subprocess mode so adapter callers still use the same JSON envelope.

## Validation

- `python3 order/scripts/smoke_order_runtime_api.py`
- `bash scripts/test_order_plugin_runtime.sh`

The smoke coverage proves direct CLI JSON calls and the OpenClaw wrapper adapter both execute through the same runtime API.

## Follow-Up

MCP and Hermes should now be adapter work, not a rewrite of order business logic. ERP / warehouse bridge work should start from dry-run contracts after this boundary is reviewed.
