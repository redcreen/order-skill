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

Pending:

- GitHub public repository creation and push
