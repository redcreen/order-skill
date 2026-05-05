[English](README.md) | [中文](README.zh-CN.md)

# OpenClaw Order Plugin

这是 `order` 的首选安装入口。

这里不再推荐直接“装 skill”，而是推荐安装独立 `order-skill` plugin：

- plugin 根目录：`plugins/openclaw-order`
- OpenClaw 宿主入口：仓库根目录 `openclaw.plugin.json`

## 安装范围

这个 plugin 默认不是“装了就全局生效”。

要求是：

1. 先安装 plugin
2. 再明确绑定到某一个 agent
3. 没有绑定前，执行入口会拒绝实际运行

绑定命令：

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py bind-agent --agent <agent-name>
```

查看当前绑定：

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py show-binding
```

## 这个 plugin 解决什么

- 把安装目标从 `skill` 切到 `plugin`
- 把执行口径从“聊天式约定”切到“runtime API 硬执行”
- 保留现有 `order/` 作为运行时主体，并把 OpenClaw 降级为入口 adapter

## 硬执行约束

安装这个 plugin 后，order 相关工作必须遵守：

1. 不能只在聊天里说“我已经记录了”
2. 必须通过绑定后的 wrapper 调用 `order/scripts/order_runtime_api.py`
3. 只有 runtime API envelope 返回 `status=ok`，并且 `result` 表明动作完成，才能对用户说执行完成
4. 不能直接调用 `order/scripts/*.py` 或绕过 agent 绑定
5. 如果 runtime API 没跑、失败、或前置条件不足，只能返回真实 blocker

统一硬执行入口：

- `python3 plugins/openclaw-order/scripts/order_hard_execute.py <subcommand> ...`

底层稳定协议：

- `order/scripts/order_runtime_api.py`
- `order/runtime/command_manifest.json`
- wrapper 输出统一 JSON envelope：`request_id`、`command`、`status`、`result`、`error`、`warnings`

新增的历史检索能力：

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py history-search --agent <current-agent-id> --query 王总 白兔子
python3 plugins/openclaw-order/scripts/order_hard_execute.py history-show --agent <current-agent-id> --source-message-id <source_message_id>
python3 plugins/openclaw-order/scripts/order_hard_execute.py history-replay --agent <current-agent-id> --channel-session-key legacy-session:<session-id>
python3 plugins/openclaw-order/scripts/order_hard_execute.py history-backfill --agent <current-agent-id> --source-message-id <source_message_id> --intent-type supplier_payable
python3 plugins/openclaw-order/scripts/order_hard_execute.py association-candidates --agent <current-agent-id> --pending-association-id <pending_association_id>
python3 plugins/openclaw-order/scripts/order_hard_execute.py resolve-pending --agent <current-agent-id> --pending-association-id <pending_association_id> --target-key <target_key>
python3 plugins/openclaw-order/scripts/order_hard_execute.py backfill-queue --agent <current-agent-id> --limit 20
python3 plugins/openclaw-order/scripts/order_hard_execute.py backfill-ready --agent <current-agent-id> --only-ready
python3 plugins/openclaw-order/scripts/order_hard_execute.py backfill-finalize --agent <current-agent-id> --workflow-draft-id <workflow_draft_id>
```

运行时子命令必须显式带当前 agent：

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py smoke-runtime --agent <current-agent-id>
```

没有完成 agent 绑定前，除 `bind-agent` 和 `show-binding` 外，其它子命令都不会执行。  
即使已经绑定，只要 `--agent` 和绑定的 `targetAgent` 不一致，也会被拒绝。

## 运行时主体

这个 plugin 包装的是现有 `order/` 运行时：

- [Order Runtime](../../order/README.zh-CN.md)
- [Order 文档总览](../../docs/README.zh-CN.md)

## 当前能力

- 原始输入持久化
- guided-intake 草稿
- 确认摘要
- confirm-before-commit
- 待关联 resolve
- 收付款多对多分配
- 控制塔刷新
- 日报生成
- 历史输入检索
- 历史单条详情查看
- 历史会话 / 线程回放
- 从历史输入直接打开补录 draft
- 待关联候选列表
- 待关联一步式 resolve
- 历史来源批量补录队列
- 历史补录 ready draft 列表
- 历史补录预览 / 显式确认提交

## 当前不做

- 还没有正式接入聚水潭 / 仓配执行适配器
- 还没有把外部执行桥接做成 plugin 内部 adapter
