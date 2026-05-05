[English](plugin-install.md) | [中文](plugin-install.zh-CN.md)

# Order Plugin Install And Validation

## Current State

`order-skill` is now an independent OpenClaw plugin project. The repository root is the host-installable plugin package; there is no separate nested host-package copy layer.

Core layers:

- `src/plugin/index.js`: OpenClaw host plugin guard for agent matching, role contract injection, and bypass blocking
- `plugins/openclaw-order/`: agent-bound hard-execution wrapper and skill
- `order/`: local-first runtime core
- `docs/`: project docs, roadmap, test plan, and development plan
- `scripts/`: install validation, E2E, stress, and migration scripts

## Install

Local development install:

```bash
openclaw plugins install -l /Users/redcreen/Project/order-skill
openclaw plugins enable openclaw-order-runtime-guard
```

For GitHub installation, install from the repository URL or clone first and use `-l` to link the local checkout.

## Bind An Agent

The default binding state must be `unbound`:

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py show-binding
```

Bind to one explicit agent:

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py bind-agent --agent order
```

Runtime commands must explicitly pass the current agent:

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py smoke-runtime --agent order
```

If the caller agent is not the bound agent, the wrapper rejects execution immediately.

## Hard Execution

`order` does not accept chat-only claims. Every formal business action must go through:

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py <subcommand> --agent <agent-id> ...
```

Current subcommands:

- `persist-input`
- `open-draft`
- `prepare-confirmation`
- `commit-draft`
- `resolve-association`
- `allocate`
- `refresh-control-tower`
- `daily-report`
- `history-search`
- `history-show`
- `history-replay`
- `history-backfill`
- `association-candidates`
- `resolve-pending`
- `backfill-queue`
- `backfill-ready`
- `backfill-finalize`
- `smoke-runtime`
- `smoke-stage89`

Hard-execution rules:

1. do not claim “recorded” unless the wrapper actually ran
2. if the wrapper fails, return the real blocker
3. only `status=ok` counts as completed execution
4. formal writes must stay draft -> confirmation -> commit
5. settlement allocation must dry-run first, produce a confirmation token, then write formally

## Minimal Validation

```bash
python3 -m compileall order/scripts plugins/openclaw-order/scripts
node --check src/plugin/index.js
bash scripts/test_order_plugin_runtime.sh
python3 scripts/test_order_business_cli_e2e.py
```

## OpenClaw Behavior Validation

After installation and binding, run:

```bash
openclaw agent --local --agent order --model openai-codex/gpt-5.5 --message "启动自检：你是不是 order agent？正式写入必须走哪个 wrapper？" --json
```

Expected:

- provider is `openai-codex`
- model is `gpt-5.5`
- plugin logs include `[order-runtime-guard] plugin loaded`
- the agent answer mentions `plugins/openclaw-order/scripts/order_hard_execute.py`
