[English](README.md) | [中文](README.zh-CN.md)

# Order Skill

`order-skill` is an independent local-first order operations project and an OpenClaw-installable plugin package.

It is not a chat-only order entry helper. Its purpose is to turn messy, out-of-order, natural-language business evidence into confirmed, traceable, auditable local order data.

## Current Capabilities

- Local SQLite runtime with default data root `~/Documents/openclaw-order`
- Persist raw input before interpretation so `/new`, restart, or context compaction does not lose business evidence
- Guided intake drafts, confirmation summaries, and confirm-before-commit formal writes
- Foundational models for samples, orders, production, shipment, receivables, payables, cash transactions, invoices, follow-ups, and daily reports
- Delayed association resolution for out-of-order input and many-to-many cash settlement allocation
- Control tower, exceptions, reminders, concise daily report, and concrete action suggestions
- OpenClaw plugin adapter that only activates for an explicitly bound agent
- Stable runtime JSON command API so MCP, Hermes, CLI, and future UIs can connect as adapters

## Repository Layout

```text
.
  openclaw.plugin.json
  index.js
  src/plugin/                 # OpenClaw host plugin guard
  plugins/openclaw-order/     # agent-bound hard-execution wrapper and skill
  order/                      # order runtime core
  docs/                       # project docs, roadmap, test plan, and development plan
  scripts/                    # smoke, E2E, stress, and migration scripts
```

## Install Into OpenClaw

```bash
openclaw plugins install -l /Users/redcreen/Project/order-skill
openclaw plugins enable openclaw-order-runtime-guard
python3 /Users/redcreen/Project/order-skill/plugins/openclaw-order/scripts/order_hard_execute.py bind-agent --agent order
```

The plugin is not installed for every agent by default. Only the bound agent can execute the order runtime; other agents are hard-blocked.

## Verify

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py show-binding
python3 plugins/openclaw-order/scripts/order_hard_execute.py smoke-runtime --agent order
python3 plugins/openclaw-order/scripts/order_hard_execute.py smoke-stage89 --agent order
bash scripts/test_order_plugin_runtime.sh
```

## Documentation

- [Docs home](docs/README.md)
- [Architecture](docs/architecture.md)
- [Roadmap](docs/roadmap.md)
- [Test plan](docs/test-plan.md)
- [Development plan](docs/reference/development-plan.md)
- [Install guide](docs/install/plugin-install.md)
