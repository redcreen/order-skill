[English](README.md) | [中文](README.zh-CN.md)

# 2026-04-21 Order Wrapper Friendly Open Draft

## Context

After `persist-input --text ...` was added to the hard-execution wrapper, the next real CLI bottleneck moved one step later:

- the model could now persist raw input through the wrapper
- but it still tended to hand-write `.tmp_open_draft_payload*.json`
- then call `open-draft --payload-file ...`

That still worked, but it meant the second hop was not yet as ergonomic as the first.

## Change

The hard-execution wrapper now also accepts a standard-flag form for `open-draft`:

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py open-draft --agent <agent> --inbox-item-id <inbox_item_id> --intent-type production_arrangement --summary-text "..." --field customer_name=... --field product_name=... --field qty=...
```

The wrapper converts those flags into a temporary payload file for `open_guided_intake_draft.py`. The original `--payload-file` form remains available for richer draft shapes.

## Validation

Validated with:

1. `bash scripts/test_order_plugin_runtime.sh`
2. direct wrapper CLI smoke for:
   - `persist-input --text ...`
   - `open-draft --inbox-item-id ... --field key=value`
3. real `openclaw agent --agent order` replay after gateway restart

## Result

The shell-level path now supports both first and second hops without requiring hand-built JSON payload files.

One live observation remains:

- the real model can still choose the older temp-payload style even though the wrapper no longer requires it
- that does not break success
- it means the remaining gap is prompt/tool-use preference, not missing wrapper capability
