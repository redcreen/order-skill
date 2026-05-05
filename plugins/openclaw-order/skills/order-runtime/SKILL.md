---
name: openclaw-order
description: Plugin-first router for the local-first order operations runtime. Use when the user wants order work to be installed as a plugin and executed through real scripts instead of soft chat-only behavior.
---

# OpenClaw Order

## Overview

Use this skill as the plugin entry for `order`.

This plugin does not replace the `order/` runtime. It wraps the runtime JSON API with a harder execution contract so order work is actually executed instead of only described in chat.

## Agent Binding

This plugin is explicit-agent-only.

- It should not be treated as globally installed by default.
- It must be bound to one target agent before execution.
- If the plugin is unbound, runtime execution should be considered unavailable until the binding step is completed.

## Hard Execution Rules

- For any order-related input, do not stop at chat interpretation when a real state transition is expected.
- Do not say data was recorded unless the bound wrapper returned a runtime API envelope with `status=ok`.
- Do not say a query was checked unless the relevant report or refresh path actually ran.
- Do not call `order/scripts/*.py` or `order/scripts/order_runtime_api.py` directly from OpenClaw; use the bound wrapper so agent scope is enforced.
- If execution is blocked, return the blocker explicitly.

## Unified Execution Entry

Use:

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py <subcommand> ...
```

Binding commands:

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py bind-agent --agent <agent-name>
python3 plugins/openclaw-order/scripts/order_hard_execute.py show-binding
```

Runtime calls must include the current agent id:

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py smoke-runtime --agent <current-agent-id>
```

If `--agent` does not match the bound `targetAgent`, execution must be refused.

The wrapper calls `order/scripts/order_runtime_api.py` and returns a JSON envelope with `request_id`, `command`, `status`, `result`, `error`, and `warnings`. The command manifest is `order/runtime/command_manifest.json`.

For plain inbound text, `persist-input` now supports a wrapper-friendly form:

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py persist-input --agent <current-agent-id> --text "raw inbound text"
```

Use `--payload-file` only when you already have richer structured metadata.

For simple first-pass drafts, `open-draft` now also supports wrapper-friendly flags:

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py open-draft --agent <current-agent-id> --inbox-item-id <inbox_item_id> --intent-type production_arrangement --summary-text "王总，小兔子，100个，今天开始做，还没确认工厂。" --field customer_name=王总 --field product_name=小兔子 --field qty=100 --field factory_name=未确认
```

Use `--payload-file` for `open-draft` only when the draft really needs richer structured metadata than the standard flags can express.

## Expected Command Routing

- Persist raw input: `persist-input`
- Open guided draft: `open-draft`
- Prepare confirmation: `prepare-confirmation`
- Commit after confirmation: `commit-draft`
- Resolve delayed links: `resolve-association`
- Allocate settlement amounts: `allocate`
- Refresh follow-up control tower: `refresh-control-tower`
- Generate daily report: `daily-report`
- Search imported legacy/history inputs: `history-search`
- Show one imported history item in detail: `history-show`
- Replay one imported session/thread timeline: `history-replay`
- Open a guided backfill draft from one imported history item: `history-backfill`
- List practical candidates for one unresolved association: `association-candidates`
- Resolve one pending association directly: `resolve-pending`
- List history-backfill drafts and readiness: `backfill-ready`
- Preview or commit one history-backfill draft: `backfill-finalize`
- List the batch backfill queue grouped by imported history source: `backfill-queue`

## Core Contract

1. Persist order-related input first.
2. Build or update a draft.
3. Generate a reviewable confirmation summary.
4. Commit only after explicit confirmation.
5. Keep unresolved links explicit until resolved.
6. Use formal records to derive follow-up and reporting output.
7. When the user is asking about prior messy records, old conversations, archived CSV/doc evidence, or补录线索, prefer `history-search` / `history-show` / `history-replay` before answering from memory.
8. When a specific legacy clue should become a formal补录流程, use `history-backfill` to open a draft instead of manually restating the old record.
9. For history-driven drafts with unresolved links, use `association-candidates` then `resolve-pending` before asking the user to reconfirm the whole draft.
10. Once a history-backfill draft is ready, use `backfill-ready` to surface it and `backfill-finalize` to preview or commit it with an explicit confirmation token.
11. When scanning many imported legacy sources, start with `backfill-queue` to see which sources are unstarted, in progress, ready, or committed.

## Runtime Reference

The wrapped runtime lives here:

- [../../../../order/README.md](../../../../order/README.md)
- [../../../../docs/reference/operating-model.md](../../../../docs/reference/operating-model.md)
