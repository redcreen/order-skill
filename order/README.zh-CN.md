[English](README.md) | [中文](README.zh-CN.md)

# Order Skill 集

这个目录是 `order` 的运行时根目录。

它的目标不是做一个“聊天录单工具”，而是做一套 `local-first` 的订单运营能力，让 agent 在安装后能够：

- 接住零散、自然语言、乱序输入
- 先持久记录所有输入，保证系统重启、`/new`、上下文收缩后不丢现场信息
- 通过草稿、确认、落库的方式形成正式业务事实
- 围绕样品、报价、订单、生产、发货、应收应付、收付款、开票、跟单和日报运行

## 当前状态

当前已经落地的是本地运行时和 adapter 边界：

- suite 入口 `SKILL.md`
- install 元数据 `agents/openai.yaml`
- `SQLite schema v1`
- 原始输入持久化脚本
- 原始输入本地归档 `raw/YYYYMMDD/`
- guided-intake 草稿入口 `order/scripts/open_guided_intake_draft.py`
- 确认摘要入口 `order/scripts/prepare_draft_confirmation.py`
- confirm-before-commit 入口 `order/scripts/commit_workflow_draft.py`
- 待关联 resolve 入口 `order/scripts/resolve_pending_association.py`
- 收付款多对多分配入口 `order/scripts/record_settlement_allocations.py`
- 控制塔刷新入口 `order/scripts/refresh_order_control_tower.py`
- 日报入口 `order/scripts/generate_daily_report.py`
- runtime API 统一入口 `order/scripts/order_runtime_api.py`
- runtime API 命令清单 `order/runtime/command_manifest.json`
- 本地 runtime 初始化脚本
- Stage 7 smoke test
- Stage 8-9 端到端 smoke test
- runtime API / OpenClaw wrapper adapter smoke test

ERP / 仓配桥接还在后续阶段。

## 默认数据目录

默认外部数据目录是：

- `~/Documents/openclaw-order`

推荐结构：

```text
~/Documents/openclaw-order/
  db/
    order.db
  attachments/
  exports/
  logs/
  reports/
  raw/
```

## 安装方式

当前不再把 `order/` 本身作为首选安装入口。

首选方式是安装 plugin：

- [OpenClaw Order Plugin](../plugins/openclaw-order/README.zh-CN.md)

`order/` 现在更适合作为 runtime 主体和开发目录，而不是最终分发入口。

## Runtime API

所有外部入口都应接 `order/scripts/order_runtime_api.py` 的 JSON command 协议。OpenClaw plugin 只是其中一个 adapter；MCP、Hermes、CLI 或未来 UI 不应重新实现业务逻辑。

## 安装后行为

安装后，agent 应立刻知道：

- 所有输入先持久化
- 自然语言不能直接写正式业务表
- 正式录入必须经过草稿、确认、落库
- 与 order 无关的内容不进入正式业务线程

## 文档入口

完整设计见仓库根目录的 `docs/`：

- [Order 文档首页](../docs/README.zh-CN.md)
- [Order 架构](../docs/architecture.zh-CN.md)
- [Order 路线图](../docs/roadmap.zh-CN.md)
- [Order 测试计划](../docs/test-plan.zh-CN.md)
- [Order 文档总览](../docs/reference/overview.zh-CN.md)
- [Order 数据模型](../docs/reference/data-model.zh-CN.md)
- [Order 运行模型](../docs/reference/operating-model.zh-CN.md)
- [Order Plugin 安装](../docs/install/plugin-install.zh-CN.md)
