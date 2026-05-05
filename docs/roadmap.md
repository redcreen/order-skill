[English](roadmap.md) | [中文](roadmap.zh-CN.md)

# Order Roadmap

| Phase | Status | Goal | Exit Criteria |
| --- | --- | --- | --- |
| Phase 1 | done | Establish the local-first runtime foundation | runtime root, `SQLite schema v1`, raw-input persistence, and runtime smoke all exist |
| Phase 2 | done | Close guided intake and formal recording guardrails | draft, confirmation, commit, delayed-link resolution, and settlement allocation all work |
| Phase 3 | done | Close control tower, reporting, and plugin-first distribution | control-tower refresh, daily reporting, explicit agent binding, and hard-execution wrapper all work |
| Phase 4 | done | Consolidate order-only docs inside root `docs/` | order architecture, plugin install docs, design reference, test plan, and order devlog are all owned by root `docs/` |
| Phase 5.5 | active | Product process templates and per-lot process confirmation | when users provide lazy short input, the system can suggest a product process, wait for confirmation, then derive formal work orders |
| Phase 5 | done | Establish an independent `order` runtime API and multi-entry adapter boundary | the OpenClaw plugin is reduced to an adapter; CLI and OpenClaw call the same JSON command API; MCP / Hermes can attach through the same protocol |
| Phase 6 | planned | Prepare ERP and warehouse bridge work | dry-run bridge contracts, inventory snapshots, sync-job semantics, and split/merge/supplement boundaries are defined |

## Current Focus

The active structural outcome is now:

- `order/` remains the runtime core
- the OpenClaw plugin remains available, but its role has been reduced to an entry adapter
- core business behavior is now behind a stable runtime API entry point
- order-only durable docs are now owned locally under root `docs/`
- the new project root is the OpenClaw-installable plugin package; there is no nested host-package copy layer

## Future Items

- MCP / Hermes / CLI adapter boundaries
- bridge contracts for JuShuiTan and warehouse execution
- inventory snapshot and sync-job semantics
- split / merge / supplement boundaries before adapter code
