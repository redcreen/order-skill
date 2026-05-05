[English](README.md) | [中文](README.zh-CN.md)

# OpenClaw Order Plugin

This is now the preferred install surface for `order`.

Instead of installing `order` as a raw skill target, install it as the independent `order-skill` plugin:

- plugin root: `plugins/openclaw-order`
- OpenClaw host entry: repository-root `openclaw.plugin.json`

## Installation Scope

This plugin is not meant to auto-attach everywhere.

The intended contract is:

1. install the plugin
2. bind it to one explicit agent
3. block runtime execution until that binding exists

Bind the plugin:

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py bind-agent --agent <agent-name>
```

Inspect the current binding:

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py show-binding
```

## What This Plugin Changes

- it turns the install target from `skill` into `plugin`
- it turns execution from prompt-only behavior into runtime-API-backed hard execution
- it keeps the existing `order/` runtime as the implementation core and reduces OpenClaw to an entry adapter

## Hard-Execution Contract

After this plugin is installed, order work must follow these rules:

1. never claim “recorded” or “done” unless the bound wrapper called `order/scripts/order_runtime_api.py`
2. only claim completion when the runtime API envelope returns `status=ok` and the `result` confirms the action
3. never call `order/scripts/*.py` directly or bypass agent binding
4. if the runtime API did not run or failed, return the actual blocker instead of pretending the action happened

Unified hard-execution entry:

- `python3 plugins/openclaw-order/scripts/order_hard_execute.py <subcommand> ...`

Stable runtime protocol:

- `order/scripts/order_runtime_api.py`
- `order/runtime/command_manifest.json`
- wrapper output is a JSON envelope with `request_id`, `command`, `status`, `result`, `error`, and `warnings`

New history-retrieval commands:

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py history-search --agent <current-agent-id> --query Wang boss rabbit
python3 plugins/openclaw-order/scripts/order_hard_execute.py history-show --agent <current-agent-id> --source-message-id <source_message_id>
python3 plugins/openclaw-order/scripts/order_hard_execute.py history-replay --agent <current-agent-id> --channel-session-key legacy-session:<session-id>
python3 plugins/openclaw-order/scripts/order_hard_execute.py history-backfill --agent <current-agent-id> --source-message-id <source_message_id> --intent-type supplier_payable
python3 plugins/openclaw-order/scripts/order_hard_execute.py association-candidates --agent <current-agent-id> --pending-association-id <pending_association_id>
python3 plugins/openclaw-order/scripts/order_hard_execute.py resolve-pending --agent <current-agent-id> --pending-association-id <pending_association_id> --target-key <target_key>
python3 plugins/openclaw-order/scripts/order_hard_execute.py backfill-queue --agent <current-agent-id> --limit 20
python3 plugins/openclaw-order/scripts/order_hard_execute.py backfill-ready --agent <current-agent-id> --only-ready
python3 plugins/openclaw-order/scripts/order_hard_execute.py backfill-finalize --agent <current-agent-id> --workflow-draft-id <workflow_draft_id>
```

Runtime subcommands must explicitly pass the current agent:

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py smoke-runtime --agent <current-agent-id>
```

Until the plugin is bound to one agent, every runtime subcommand except `bind-agent` and `show-binding` will refuse to run.  
Even after binding, the call is rejected when `--agent` does not match the bound `targetAgent`.

## Runtime Core

This plugin wraps the existing `order/` runtime:

- [Order Runtime](../../order/README.md)
- [Order Documentation](../../docs/README.md)

## Current Coverage

- raw-input persistence
- guided-intake draft opening
- confirmation summaries
- confirm-before-commit
- delayed-link resolution
- many-to-many settlement allocation
- control-tower refresh
- daily reporting
- history search across imported legacy inputs
- single-item history inspection
- session/thread history replay
- direct guided backfill draft opening from one history item
- unresolved-association candidate listing
- direct pending-association resolution
- batch backfill queue grouped by source
- ready backfill draft listing
- backfill preview / explicit confirm-and-commit

## Not Included Yet

- JuShuiTan / warehouse adapters are not implemented yet
- external execution bridges are still the next phase
