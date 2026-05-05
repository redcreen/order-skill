[English](README.md) | [中文](README.zh-CN.md)

# 2026-04-21 Order Wrapper Friendly Persist Input

## Context

The `order` host plugin had already been corrected to use wrapper injection plus a host-side `before_tool_call` guard. That fixed the architectural problem. A real `openclaw agent --agent order` CLI replay still exposed one execution-surface problem:

- the model naturally tried `order_hard_execute.py persist-input --agent order --text ...`
- the old wrapper only forwarded `--payload-file`
- the first hop failed even though the model had chosen the right wrapper path

## Change

The hard-execution wrapper now accepts a friendly first-hop form for plain inbound text:

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py persist-input --agent <agent> --text "raw inbound text"
```

The wrapper converts that call into a temporary payload file for `persist_order_input.py`. The original `--payload-file` form remains available for richer structured inputs.

The host-plugin prompt contract and the `openclaw-order` skill were also updated to show this exact first-hop example.

## Validation

The change was validated three ways:

1. `bash scripts/test_order_plugin_runtime.sh`
2. direct wrapper CLI with `persist-input --text ...`
3. real `openclaw agent --agent order` replay using a fresh message:

   - `李总，小熊，88个，周五开工，还没确认工厂。`

The latest transcript now shows:

- `persist-input --agent order --payload-file ...` succeeds after a wrapper-generated plain-text first hop
- `open-draft --agent order --payload-file ...` succeeds
- the final user-visible reply is `草稿已记下，还差 1 个销售订单关联待确认。`

## Outcome

The remaining issue is no longer the OpenClaw injection layer. The wrapper path now survives a real CLI turn without forcing the model to first rediscover the payload-file shape of `persist_order_input.py`.
