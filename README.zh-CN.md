[English](README.md) | [中文](README.zh-CN.md)

# Order Skill

`order-skill` 是独立的本地优先订单运营项目，也是可直接安装到 OpenClaw 的插件包。

它的目标不是做一个“聊天录单工具”，而是把杂乱、乱序、自然语言的订单现场信息转成可确认、可追踪、可审计的本地业务数据。

## 当前能力

- 本地 SQLite 运行时，默认数据目录为 `~/Documents/openclaw-order`
- 原始输入先持久化，防止 `/new`、重启或上下文压缩后丢失现场信息
- guided intake 草稿、确认摘要、confirm-before-commit 正式落库
- 样品、订单、生产、发货、应收应付、收付款、开票、跟单、日报的基础模型
- 待关联对象后补，支持乱序输入和一个收付款分配到多个对象
- 控制塔、异常、提醒、每日简报和行动建议
- OpenClaw plugin adapter，只对显式绑定的 agent 生效
- 稳定 runtime JSON command API，未来 MCP、Hermes、独立 UI 都应作为 adapter 接入

## 仓库结构

```text
.
  openclaw.plugin.json
  index.js
  src/plugin/                 # OpenClaw host plugin guard
  plugins/openclaw-order/     # agent-bound hard-execution wrapper 和 skill
  order/                      # order runtime 主体
  docs/                       # 项目文档、路线图、测试计划和开发计划
  scripts/                    # smoke、E2E、压力测试和迁移脚本
```

## 安装到 OpenClaw

```bash
openclaw plugins install -l /Users/redcreen/Project/order-skill
openclaw plugins enable openclaw-order-runtime-guard
python3 /Users/redcreen/Project/order-skill/plugins/openclaw-order/scripts/order_hard_execute.py bind-agent --agent order
```

默认不会安装到所有 agent。只有绑定的 agent 能调用 order runtime；其他 agent 会被硬拦截。

## 验证

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py show-binding
python3 plugins/openclaw-order/scripts/order_hard_execute.py smoke-runtime --agent order
python3 plugins/openclaw-order/scripts/order_hard_execute.py smoke-stage89 --agent order
bash scripts/test_order_plugin_runtime.sh
```

## 文档

- [文档首页](docs/README.zh-CN.md)
- [架构](docs/architecture.zh-CN.md)
- [路线图](docs/roadmap.zh-CN.md)
- [测试计划](docs/test-plan.zh-CN.md)
- [开发计划](docs/reference/development-plan.zh-CN.md)
- [安装说明](docs/install/plugin-install.zh-CN.md)
