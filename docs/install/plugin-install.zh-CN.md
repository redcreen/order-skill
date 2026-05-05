[English](plugin-install.md) | [中文](plugin-install.zh-CN.md)

# Order Plugin 安装与验证

## 当前状态

`order-skill` 现在是独立 OpenClaw plugin 项目。仓库根目录就是可安装的 host plugin 包，不再需要旧的嵌套宿主包副本。

核心层次：

- `src/plugin/index.js`：OpenClaw host plugin guard，负责 agent 识别、角色契约注入和绕过执行拦截
- `plugins/openclaw-order/`：agent-bound hard-execution wrapper 和 skill
- `order/`：本地优先 runtime 主体
- `docs/`：项目文档、路线图、测试计划和开发计划
- `scripts/`：安装验证、E2E、压力测试和迁移脚本

## 安装

本地开发安装：

```bash
openclaw plugins install -l /Users/redcreen/Project/order-skill
openclaw plugins enable openclaw-order-runtime-guard
```

从 GitHub 安装时，使用仓库地址或先 clone 到本地后用 `-l` link 安装。

## 绑定 agent

默认绑定状态必须是 `unbound`：

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py show-binding
```

绑定到指定 agent：

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py bind-agent --agent order
```

运行时命令必须显式带当前 agent：

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py smoke-runtime --agent order
```

如果调用 agent 不是绑定的那个 agent，wrapper 会直接拒绝执行。

## 硬执行

`order` 不接受“只在聊天里说做了”。所有正式业务动作必须走：

```bash
python3 plugins/openclaw-order/scripts/order_hard_execute.py <subcommand> --agent <agent-id> ...
```

当前支持：

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

硬执行规则：

1. 没跑 wrapper，不能说“已记录”
2. wrapper 失败，必须返回真实 blocker
3. 只有 wrapper 返回 `status=ok`，才能说执行完成
4. 正式写入必须保持 draft -> confirmation -> commit
5. 收付款分配也必须 dry-run 生成确认 token 后再正式写入

## 最小验证

```bash
python3 -m compileall order/scripts plugins/openclaw-order/scripts
node --check src/plugin/index.js
bash scripts/test_order_plugin_runtime.sh
python3 scripts/test_order_business_cli_e2e.py
```

## OpenClaw 行为验证

安装并绑定后，使用：

```bash
openclaw agent --local --agent order --model openai-codex/gpt-5.5 --message "启动自检：你是不是 order agent？正式写入必须走哪个 wrapper？" --json
```

期望：

- provider 是 `openai-codex`
- model 是 `gpt-5.5`
- 插件日志显示 `[order-runtime-guard] plugin loaded`
- agent 回答必须提到 `plugins/openclaw-order/scripts/order_hard_execute.py`
