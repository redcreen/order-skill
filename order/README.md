[English](README.md) | [中文](README.zh-CN.md)

# Order Skill Set

This folder is the runtime root for `order`.

Its goal is not to build a chat-based order entry toy. Its goal is to establish a local-first order operations runtime where the agent can:

- receive fragmented natural-language and out-of-order input
- persist all input first so restarts, `/new`, and context loss do not drop operational signals
- turn input into formal business truth through draft, confirm, then commit
- operate around sample, quote, sales order, production, shipment, receivable / payable, cash movement, invoice, follow-up, and daily reporting

## Current Status

The current implemented layer is the local runtime plus adapter boundary:

- suite entry `SKILL.md`
- install metadata `agents/openai.yaml`
- `SQLite schema v1`
- raw-input persistence script
- raw-input local archive under `raw/YYYYMMDD/`
- guided-intake draft entry `order/scripts/open_guided_intake_draft.py`
- confirmation-summary entry `order/scripts/prepare_draft_confirmation.py`
- confirm-before-commit entry `order/scripts/commit_workflow_draft.py`
- pending-association resolution entry `order/scripts/resolve_pending_association.py`
- settlement-allocation entry `order/scripts/record_settlement_allocations.py`
- control-tower refresh entry `order/scripts/refresh_order_control_tower.py`
- daily-report entry `order/scripts/generate_daily_report.py`
- runtime API entry `order/scripts/order_runtime_api.py`
- runtime API command manifest `order/runtime/command_manifest.json`
- local runtime initialization script
- Stage 7 smoke test
- Stage 8-9 end-to-end smoke test
- runtime API / OpenClaw wrapper adapter smoke test

ERP / warehouse bridge work remains the next future stage.

## Default Data Root

The default external data root is:

- `~/Documents/openclaw-order`

Recommended structure:

```text
~/Documents/openclaw-order/
  db/
    order.db
  attachments/
  exports/
  logs/
  reports/
  raw/
```

## Install

`order/` is no longer the preferred install surface.

Install the plugin instead:

- [OpenClaw Order Plugin](../plugins/openclaw-order/README.md)

`order/` now acts as the runtime core and development surface rather than the final distribution entry.

## Runtime API

External entry points should use the JSON command protocol exposed by `order/scripts/order_runtime_api.py`. The OpenClaw plugin is one adapter; MCP, Hermes, CLI, or future UIs should not reimplement order business logic.

## Installed Behavior

After installation, the agent should already know:

- persist all input first
- natural-language input must not write directly into formal business tables
- formal recording must follow draft, confirm, then commit
- non-order content does not enter the formal order thread

## Documentation

See the repository-root `docs/` directory:

- [Order docs home](../docs/README.md)
- [Order architecture](../docs/architecture.md)
- [Order roadmap](../docs/roadmap.md)
- [Order test plan](../docs/test-plan.md)
- [Order overview](../docs/reference/overview.md)
- [Order data model](../docs/reference/data-model.md)
- [Order operating model](../docs/reference/operating-model.md)
- [Order plugin install](../docs/install/plugin-install.md)
