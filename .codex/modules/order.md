# Order Module

## Mission

Convert messy order operations evidence into confirmed local business state while preserving raw evidence and preventing unconfirmed formal writes.

## Owned Paths

- `order/`
- `plugins/openclaw-order/`
- `src/plugin/`
- `docs/`
- `scripts/test_order_*`
- `scripts/import_order_*`
- `scripts/order_cutover.py`

## Active Risks

- Product-process templates are not yet fully implemented in runtime commands.
- ERP / warehouse bridge remains dry-run planning work.
- LLM extraction tests depend on OpenClaw and GPT-5.5 availability.

## Required Gates

- `python3 scripts/validate_order_skill_repo.py`
- `python3 -m compileall order/scripts plugins/openclaw-order/scripts scripts`
- `node --check src/plugin/index.js`
- `bash scripts/test_order_plugin_runtime.sh`
- `python3 scripts/test_order_business_cli_e2e.py`
