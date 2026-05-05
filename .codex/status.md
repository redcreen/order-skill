# Order Skill Status

## Current State

Independent split is active. The project now lives in `/Users/redcreen/Project/order-skill`.

## Latest Checkpoint

- Root package is the OpenClaw-installable plugin package.
- The old nested host-package copy layer has been removed from the project shape.
- Root `docs/` is the durable documentation tree.
- Plugin binding defaults to `unbound` for public distribution.
- Runtime wrapper, OpenClaw host guard, and order runtime have been copied into the independent project.

## Verification

Passed during the split task:

- `python3 scripts/validate_order_skill_repo.py`
- `python3 -m compileall order/scripts plugins/openclaw-order/scripts scripts`
- `node --check src/plugin/index.js`
- `bash scripts/test_order_plugin_runtime.sh`
- `python3 scripts/test_order_business_cli_e2e.py`
- `python3 scripts/validate_repo_markdown_integrity.py --root . --json`
- `npm pack --dry-run`
- OpenClaw plugin registry refresh confirms source is `/Users/redcreen/Project/order-skill/index.js`
- OpenClaw order agent self-check used `openai-codex/gpt-5.5` and identified the new hard-execution wrapper path

Published:

- GitHub public repository: https://github.com/redcreen/order-skill
- Initial branch: `main`

## 2026-05-05 Memory Restore

- Restored project context from `README.md`, `.codex/brief.md`, `.codex/status.md`, `.codex/plan.md`, `.codex/module-dashboard.md`, `.codex/modules/order.md`, `docs/reference/development-plan.md`, `docs/test-plan.md`, `package.json`, and local OpenClaw binding.
- Local OpenClaw plugin binding is currently `status=bound`, `targetAgent=order` for live testing; this binding file is ignored for public distribution.

## 2026-05-05 OpenClaw Wrapper Compatibility Check

- Checked latest upstream OpenClaw via npm and GitHub: npm `latest` is `2026.5.4`, and GitHub release `v2026.5.4` was published at `2026-05-05T08:24:01Z`.
- Upstream `v2026.5.4` supports the current order wrapper architecture through native plugin hooks: `before_prompt_build` for injecting the runtime contract and `before_tool_call` for blocking or rewriting tool calls.
- The order implementation remains a supported plugin-hook wrapper pattern, not an OpenClaw core patch. `src/plugin/index.js` injects wrapper instructions and blocks direct `order/scripts/*.py` / `order_runtime_api.py` execution, while `plugins/openclaw-order/scripts/order_hard_execute.py` stays the bound adapter over `order/scripts/order_runtime_api.py`.
- Updated `openclaw.plugin.json` with explicit `activation.onStartup=true` and `activation.onCapabilities=["hook"]` so the guard hooks are startup-loaded under the latest activation planner rules.
- Verification on this checkpoint passed:
  - `node -e 'JSON.parse(require("node:fs").readFileSync("openclaw.plugin.json", "utf8")); console.log("openclaw.plugin.json ok")'`
  - `bash scripts/test_order_plugin_runtime.sh`
  - `python3 -m compileall order/scripts plugins/openclaw-order/scripts scripts`

## 2026-05-05 Wrapper Stability Test

- Refreshed the local OpenClaw plugin registry after the manifest activation update.
- Confirmed local OpenClaw `2026.5.3-1` loads `openclaw-order-runtime-guard` from `/Users/redcreen/Project/order-skill/index.js` with `hookCount=2` and typed hooks:
  - `before_prompt_build`
  - `before_tool_call`
- Confirmed `npx openclaw@2026.5.4` loads the same plugin with `hookCount=2` and the same typed hooks.
- Ran `bash scripts/test_order_plugin_runtime.sh` in a 10-iteration loop; all 10 iterations passed.
- Restarted the local OpenClaw Gateway with `openclaw gateway restart`; post-restart runtime inspect still showed `hookCount=2`.
- Bound the local wrapper to `agent=order` for live testing.
- Ran real `openclaw agent --local --agent order --model openai-codex/gpt-5.5` self-check; the agent identified the wrapper path as `plugins/openclaw-order/scripts/order_hard_execute.py <subcommand> --agent order ...`.
- Ran a live bypass test asking the order agent to directly call `order/scripts/persist_order_input.py --help`; the live agent path loaded `[order-runtime-guard]`, triggered tool use, and returned the guard result requiring the hard-execution wrapper instead of direct script execution.
