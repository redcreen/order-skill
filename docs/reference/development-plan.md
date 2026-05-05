[English](development-plan.md) | [中文](development-plan.zh-CN.md)

# Order Development Plan

This plan only owns the `order` subproject. It does not own `health` or workspace-level work.

## Current Goal

`order` already has the local-first runtime core, draft / confirmation guards, history backfill, control tower, daily report flow, and runtime decoupling. The next step is ERP / warehouse bridge preparation, starting with dry-run contracts and inventory/order sync boundaries rather than real external writes.

Core direction:

- `order-core` owns business truth, state transitions, and local data
- `order-runtime-api` owns the stable JSON command protocol
- OpenClaw plugin, CLI, MCP, Hermes, and future UIs are adapters

## Phase 1: Local-First Runtime Foundation

### Status

Complete.

### Completed Work

1. Established the `order/` runtime root.
2. Landed `SQLite schema v1`.
3. Implemented raw-input persistence and local raw archive storage.
4. Connected live-export import.
5. Added the runtime smoke test.

## Phase 2: Guided Intake and Formal Recording Guards

### Status

Complete.

### Completed Work

1. Added workflow drafts, field values, and checkpoints.
2. Added confirmation previews and confirm-before-commit.
3. Supported delayed association resolution.
4. Supported many-to-many settlement allocation.
5. Added Stage 8-9 smoke coverage.

## Phase 3: Control Tower, Daily Reports, and Plugin-First Distribution

### Status

Complete.

### Completed Work

1. Added commitments, followups, exceptions, and alerts.
2. Added control-tower views and daily-report generation.
3. Added the plugin-first wrapper and explicit agent binding.
4. Added the OpenClaw host plugin guard to prevent bypassing the hard-execution wrapper.
5. Imported legacy order data and history backfill queues.

## Phase 4: Order Doc Tree Consolidation

### Status

Complete.

### Completed Work

1. Root `docs/` became the only durable order-skill documentation tree.
2. Architecture, roadmap, test plan, install docs, references, and devlogs moved into the independent project docs tree.
3. `order/` now keeps only the runtime core and runtime entry docs.

## Phase 5: Runtime Decoupling and Multi-Entry Adapters

### Status

Complete.

### Completed Work

1. Added `order/scripts/order_runtime_api.py` as the stable JSON request / response envelope.
2. Added `order/runtime/command_manifest.json` so command metadata and adapter rules are owned by `order` instead of OpenClaw prompt text.
3. Added runtime API fixtures and `smoke_order_runtime_api.py`, covering both CLI JSON calls and OpenClaw wrapper adapter calls.
4. Updated `plugins/openclaw-order/scripts/order_hard_execute.py` so the OpenClaw wrapper only calls the runtime API instead of direct runtime scripts.
5. Thinned `src/plugin/index.js` and blocked direct calls to `order_runtime_api.py` or lower-level runtime scripts from bypassing the wrapper.
6. Made the independent project root the installable OpenClaw plugin package; there is no nested host-package copy layer.
7. Deferred MCP / Hermes implementation; they should attach later as adapters over the same JSON command protocol.

### Goal

Reduce OpenClaw from a business-behavior carrier to an entry adapter, while exposing the `order` core through a stable runtime API.

This stage does not replace the existing `SQLite`, draft, confirm, commit, history backfill, or daily-report flows. It only clarifies ownership.

### Must Deliver

1. Runtime command inventory covering existing `persist-input`, `open-draft`, `prepare-confirmation`, `commit-draft`, `history-search`, `history-backfill`, `backfill-queue`, `backfill-finalize`, `daily-report`, and related actions.
2. Stable JSON request / response envelope with `command`, `request_id`, `actor`, `source`, `data_root`, `payload`, `status`, `result`, `error`, and `warnings`.
3. Adapter conformance fixtures that can be exercised by both the CLI adapter and the OpenClaw adapter.
4. A thinner OpenClaw plugin that only handles target-agent recognition, short runtime guidance, and direct-script bypass prevention.
5. A runtime contract manifest owned by `order`, so stable agent rules no longer live only inside OpenClaw plugin code.
6. At least one non-OpenClaw call proof through a CLI JSON entry point; an MCP adapter can follow as a thin proof layer if it stays low-risk.

### Ordered Queue

1. Inventory the current `order/scripts/*.py` files and classify them as core commands, adapter wrappers, or smoke / migration utilities.
2. Define the runtime command envelope and error model, then write fixtures.
3. Add one unified runtime API entry point that can invoke existing command behavior from JSON.
4. Update `order_hard_execute.py` to call the runtime API instead of owning command-routing knowledge.
5. Thin the prompt contract inside `src/plugin/index.js` to adapter-level rules only.
6. Add adapter conformance tests proving CLI and OpenClaw wrapper calls use the same command fixtures.
7. Decide whether the first MCP adapter should land at the end of this stage or stay as the next adapter follow-up.

### Risks

- Service-ifying too early could add deployment complexity.
- If the runtime API is not stabilized first, MCP, Hermes, and OpenClaw may each grow incompatible integration paths.
- If OpenClaw plugin keeps owning business rules, future UI replacement still requires a full rewrite of order behavior.

### Exit Criteria

- Core query and guarded write flows can run without OpenClaw through the same runtime command API.
- The OpenClaw plugin no longer maintains large business-process instructions.
- A new adapter only maps identity, transport, and payload shape; it does not reimplement order business logic.
- Stage 8-9 smoke coverage still passes, including history backfill.

## Phase 6: ERP and Warehouse Bridge Preparation

### Status

Not started.

### Goal

Prepare the bridge layer for JuShuiTan and warehouse-system integration.

### Must Deliver

1. `fulfillment_plans`
2. `fulfillment_plan_lines`
3. `external_system_connections`
4. `external_sync_jobs`
5. `external_inventory_snapshots`
6. dry-run bridge contracts

## Current Execution Queue

1. Review the Phase 5 runtime API envelope and adapter result.
2. Confirm the Phase 6 ERP / warehouse bridge dry-run scope.
3. Design inventory snapshots, external order sync, order merge/split/supplement, and inbound-stock action boundaries.
4. Decide whether the first MCP / Hermes adapter should be a Phase 6 pre-check or wait until bridge contracts stabilize.
