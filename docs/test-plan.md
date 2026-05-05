[English](test-plan.md) | [中文](test-plan.zh-CN.md)

# Order Test Plan

## Scope

Validate the independent `order-skill` project: docs, runtime, OpenClaw plugin adapter, confirmation-gated formal writes, LLM lazy-input flow, and messy real-business event flow.

## Documentation Cases

- Case: root README states the project boundary
  - Setup: open [../README.md](../README.md)
  - Action: inspect install, binding, verification, and docs links
  - Expected Result: the repository root is described as the installable plugin package and does not depend on the old nested host-package copy

- Case: docs route from root `docs/`
  - Setup: open [README.md](README.md)
  - Action: inspect architecture, roadmap, test plan, install docs, reference docs, and devlog links
  - Expected Result: all durable order docs are reachable from root `docs/`

- Case: install docs do not contain old workspace paths
  - Setup: open [install/plugin-install.md](install/plugin-install.md)
  - Action: inspect install guidance
  - Expected Result: install guidance points at `/Users/redcreen/Project/order-skill`, not the old nested host-package path in the workspace

## Runtime And Plugin Cases

- Case: plugin starts unbound
  - Setup: run `python3 plugins/openclaw-order/scripts/order_hard_execute.py show-binding`
  - Action: inspect output
  - Expected Result: `status=unbound` and `targetAgent` is empty

- Case: unbound runtime execution is rejected
  - Setup: keep default unbound state
  - Action: run `python3 plugins/openclaw-order/scripts/order_hard_execute.py smoke-runtime --agent order`
  - Expected Result: command fails and asks for `bind-agent`

- Case: bound agent can run runtime smoke
  - Setup: run `python3 plugins/openclaw-order/scripts/order_hard_execute.py bind-agent --agent order`
  - Action: run `python3 plugins/openclaw-order/scripts/order_hard_execute.py smoke-runtime --agent order`
  - Expected Result: runtime initialization and basic order path pass

- Case: non-bound agent is hard-blocked
  - Setup: plugin is bound to `order`
  - Action: run `python3 plugins/openclaw-order/scripts/order_hard_execute.py smoke-runtime --agent finance`
  - Expected Result: command fails and says the plugin is bound to `order`

- Case: Stage 8-9 smoke still passes
  - Setup: plugin is bound to `order`
  - Action: run `python3 plugins/openclaw-order/scripts/order_hard_execute.py smoke-stage89 --agent order`
  - Expected Result: confirmation, commit guard, delayed association resolve, many-to-many allocation, control tower, and daily report all pass

- Case: OpenClaw host plugin guard blocks wrapper bypass
  - Setup: run `bash scripts/test_order_plugin_runtime.sh`
  - Action: inspect Node hook and wrapper checks
  - Expected Result: direct calls to `order/scripts/*.py` or `order_runtime_api.py` are blocked, wrapper calls are allowed

- Case: CLI business E2E checks database state
  - Setup: run `python3 scripts/test_order_business_cli_e2e.py`
  - Action: inspect SQLite assertions in the script
  - Expected Result: order, work order, receivable, payable, cash transaction, allocation, shipment, and daily-report state matches expectations

## LLM And Messy Real-World Cases

- Case: lazy short input does not write directly
  - Setup: user only says “小兔子王总 500 个，按上次做”
  - Action: run `python3 scripts/test_order_lazy_guided_intake_50.py --case-count 10`
  - Expected Result: only drafts and missing-field checkpoints are created; formal `sales_orders` do not increase

- Case: lazy input requires product-process confirmation
  - Setup: user adds customer, spec, price, due date, and factory but does not confirm the suggested process
  - Action: continue the same intake session
  - Expected Result: the draft keeps the process confirmation gap and cannot auto-create work orders

- Case: GPT-5.5 short-input extraction is wired into lazy intake
  - Setup: each case has only 3 short user turns; the script calls the OpenClaw order agent with `openai-codex/gpt-5.5`
  - Action: run `python3 scripts/test_order_llm_lazy_guided_intake_50.py --case-count 50 --batch-size 5 --model openai-codex/gpt-5.5`
  - Expected Result: `fallback_used=false`, fields exact-match, no formal writes before process confirmation, and final order/work-order state is correct

- Case: messy post-order events require confirmation before formal writes
  - Setup: create base orders, then input 50 short events covering payments, receivables, supplier bills, supplier payouts, cut-piece logistics, customer shipment, returns, refunds, deductions, replenishment, rework, and unrelated chatter
  - Action: run `python3 scripts/test_order_messy_event_confirmation_50.py --case-count 50 --llm-extract --batch-size 5 --model openai-codex/gpt-5.5`
  - Expected Result: every formal business event enters a draft first; unprepared commits, fake tokens, and direct allocation writes fail; final `open_drafts=0`, `pending_associations_open=0`, and SQLite integrity is `ok`
  - Timeout handling: if OpenClaw times out on a GPT-5.5 batch, rerun the same 50 cases with a smaller `--batch-size`; the test still requires `openai-codex/gpt-5.5` and `fallback_used=false`

## Gates

Before publishing, run at minimum:

```bash
python3 scripts/validate_order_skill_repo.py
python3 -m compileall order/scripts plugins/openclaw-order/scripts scripts
node --check src/plugin/index.js
bash scripts/test_order_plugin_runtime.sh
python3 scripts/test_order_business_cli_e2e.py
```
