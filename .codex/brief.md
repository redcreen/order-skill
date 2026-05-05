# Order Skill Brief

## Objective

`order-skill` is an independent local-first order operations runtime and OpenClaw plugin adapter.

The project must let an order-focused agent accept messy natural-language business evidence, persist it locally, guide missing information, require confirmation before formal writes, and expose a stable runtime API that future adapters can reuse.

## Non-Negotiables

- OpenClaw is an interaction adapter, not the business truth source.
- Formal writes must be draft -> confirmation -> commit.
- Settlement allocation must be dry-run -> confirmation token -> formal write.
- The plugin must be explicitly bound to one target agent and must not auto-attach to every agent.
- Local SQLite and archived local evidence are the source of truth.
- LLM extraction may propose fields, links, and follow-up questions; it must not bypass runtime confirmation.

## Current Distribution Shape

- Repository root is the installable OpenClaw plugin package.
- `order/` owns the runtime core.
- `plugins/openclaw-order/` owns the hard-execution wrapper and skill.
- `src/plugin/` owns OpenClaw host guard hooks.
- `docs/` owns durable project documentation.
