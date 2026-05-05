[English](2026-04-19-order-runtime-foundation.md) | [中文](2026-04-19-order-runtime-foundation.md)

# 2026-04-19 Order Runtime Foundation

## Context

The `order` module had already converged at the documentation level, but it still had no runnable local-first runtime. That left the project in an unstable middle state:

- the design required persist-first input handling
- the design required draft -> confirm -> commit behavior
- the live Feishu export had already been reviewed locally
- none of that was yet expressed as a shared runtime contract inside `order/`

## Problem

Without a real runtime foundation, every next feature would have been built on assumptions instead of a durable storage boundary:

- raw user input could still be lost across restart or `/new`
- importer logic and runtime logic could drift into separate schemas
- object-thread continuity would stay implicit instead of queryable
- guided intake would become a chat behavior before it became a storage-backed behavior

## Decision

Land the order runtime foundation first and prove it locally before moving deeper into guided intake.

That meant:

1. make `order/` a real installable suite entry
2. promote one shared `SQLite schema v1` as the runtime contract
3. persist every inbound input before interpretation
4. archive the raw input outside chat state
5. keep live-export import on the same schema
6. expose the first storage-backed guided-intake development path

## Implementation

1. Added the `order/` root with landing docs, `SKILL.md`, and install metadata.
2. Added `order/runtime/schema_v1.sql` as the shared schema used by both runtime and importer.
3. Implemented `order/scripts/persist_order_input.py` and the shared helpers in `runtime_common.py`.
4. Added local raw-archive files under `raw/YYYYMMDD/` so every input has a replayable persisted envelope.
5. Added continuity hooks through `intake_session_items`, `draft_source_links`, and `object_thread_items`.
6. Added `order/scripts/open_guided_intake_draft.py` so persisted input can open a draft with checkpoints instead of writing formal business truth.
7. Added `order/scripts/smoke_order_runtime.py` to verify schema init, persist-first behavior, raw archive, guided-intake draft opening, and baseline views.
8. Updated `scripts/import_order_live_export_to_sqlite.py` to initialize from the shared schema.

## Validation

Validated on the current worktree with:

```bash
python3 -m compileall order/scripts scripts/import_order_live_export_to_sqlite.py
python3 order/scripts/smoke_order_runtime.py
python3 scripts/import_order_live_export_to_sqlite.py
```

The smoke test proved:

- runtime initialization works
- raw input is persisted before interpretation
- a raw archive file is written locally
- a first guided-intake draft opens with an explicit missing-field checkpoint
- baseline views exist on the initialized database

The live-export import still produced the review database and core order-side view counts successfully.

## Outcome

- `order` is no longer documentation-only
- runtime and importer now share one schema contract
- all later guided-intake work can build on persisted input and explicit draft objects
- the active risk has shifted from “no runtime exists” to “formal write guards are not complete yet”
