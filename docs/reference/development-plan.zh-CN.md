[English](development-plan.md) | [中文](development-plan.zh-CN.md)

# Order 开发计划

这份计划只维护 `order` 子项目，不维护 `health` 或工作区级事项。

## 当前目标

`order` 已经完成 local-first 核心运行时、草稿确认守卫、历史补录、控制塔、日报和运行时解耦。下一步进入 ERP / 仓配桥接预备层，但仍先做 dry-run 契约和库存/订单同步边界，不直接做真实外部系统写入。

核心方向：

- `order-core` 持有业务真相、状态机和本地数据
- `order-runtime-api` 持有稳定 JSON command 协议
- OpenClaw plugin、CLI、MCP、Hermes 或未来 UI 都只是 adapter

## Phase 1: Local-first 运行时基础层

### 状态

已完成。

### 已完成工作

1. 建立 `order/` 运行时根目录。
2. 落地 `SQLite schema v1`。
3. 实现原始输入持久化和本地原文归档。
4. 打通 live export 导入。
5. 增加 runtime smoke test。

## Phase 2: Guided intake 和正式录入守卫

### 状态

已完成。

### 已完成工作

1. 建立 workflow draft、field values、checkpoints。
2. 增加确认摘要和 confirm-before-commit。
3. 支持待关联对象后补。
4. 支持多对多收付款分配。
5. 增加 Stage 8-9 smoke test。

## Phase 3: 控制塔、日报和 plugin-first 分发

### 状态

已完成。

### 已完成工作

1. 增加 commitments、followups、exceptions、alerts。
2. 增加控制塔视图和日报生成。
3. 增加 plugin-first wrapper 和显式 agent 绑定。
4. 增加 OpenClaw host plugin guard，阻止绕过硬执行 wrapper。
5. 导入旧 order 数据和历史补录队列。

## Phase 4: Order 文档树收口

### 状态

已完成。

### 已完成工作

1. 根目录 `docs/` 成为 order-skill 唯一 durable 文档树。
2. 架构、路线图、测试计划、安装、参考资料和开发日志都归到独立项目文档树。
3. `order/` 只保留 runtime 主体和运行时入口说明。

## Phase 5: 运行时解耦与多入口适配

### 状态

已完成。

### 已完成工作

1. 新增 `order/scripts/order_runtime_api.py`，提供稳定 JSON request / response envelope。
2. 新增 `order/runtime/command_manifest.json`，把命令、执行模式和 adapter 契约从 OpenClaw prompt 中移出来。
3. 新增 runtime API fixture 和 `smoke_order_runtime_api.py`，覆盖 CLI JSON 调用和 OpenClaw wrapper adapter 调用。
4. 改造 `plugins/openclaw-order/scripts/order_hard_execute.py`，让 OpenClaw wrapper 只调用 runtime API，不再直接调用运行时脚本。
5. 压薄 `src/plugin/index.js` 的 prompt contract，并阻断直接调用 `order_runtime_api.py` 或底层脚本绕过 wrapper。
6. 独立项目根目录成为可安装 OpenClaw plugin 包，不再维护旧的嵌套宿主包副本。
7. MCP / Hermes 暂不在本阶段实现；它们后续只需要按同一 JSON command 协议做 adapter。

### 目标

把 OpenClaw 从 `order` 的业务承载层降级为入口 adapter，让 `order` 的核心能力通过稳定 runtime API 暴露。

这个阶段不推翻现有 `SQLite`、draft、confirm、commit、history backfill 和日报链路，只重新划清边界。

### 必须交付

1. runtime command 清单：覆盖当前 `persist-input`、`open-draft`、`prepare-confirmation`、`commit-draft`、`history-search`、`history-backfill`、`backfill-queue`、`backfill-finalize`、`daily-report` 等核心动作。
2. 稳定 JSON request / response envelope：包含 `command`、`request_id`、`actor`、`source`、`data_root`、`payload`、`status`、`result`、`error`、`warnings`。
3. adapter conformance fixtures：同一组 fixture 必须能被 CLI adapter 和 OpenClaw adapter 共同跑通。
4. OpenClaw plugin 压薄：只负责识别目标 agent、注入最短 runtime 使用说明、阻断绕过 wrapper。
5. runtime contract manifest：把 agent 需要知道的稳定规则放在 `order` 自己的 manifest / docs 中。
6. 非 OpenClaw 调用证明：至少保留一个 CLI JSON 调用入口；MCP adapter 可以作为后续或本阶段末尾的薄层验证。

### 开发顺序

1. 盘点现有 `order/scripts/*.py`，把它们归类为 core command、adapter wrapper、smoke / migration 三类。
2. 定义 runtime command envelope 和错误模型，先写文档和 fixture。
3. 新增统一 runtime API 入口，让现有脚本可以被 JSON command 调用。
4. 让 `order_hard_execute.py` 改为调用 runtime API，而不是继续承担业务路由知识。
5. 压薄 `src/plugin/index.js` 里的 prompt contract，只留下 adapter 级规则。
6. 添加 adapter conformance test，证明 CLI 和 OpenClaw wrapper 走同一套 command fixture。
7. 再决定是否落第一版只读 / 半只读 MCP adapter。

### 风险

- 过早把系统服务化，会增加部署复杂度。
- 如果 runtime API 没有先稳定，MCP、Hermes、OpenClaw 会各自长出一套不兼容接入逻辑。
- 如果 OpenClaw plugin 继续承载业务规则，未来换交互入口时仍然要重做一遍。

### 完成标准

- 不通过 OpenClaw，也能用同一套 runtime command 完成核心查询和安全写入流程。
- OpenClaw plugin 中不再维护大段业务流程规则，只维护 agent gating 和 adapter guard。
- 新 adapter 接入只需要做 identity / transport / payload mapping，不需要重新理解 order 业务逻辑。
- Stage 8-9 的 smoke 覆盖继续通过，历史补录链路不回退。

## Phase 5.5: 产品流程模板和批次流程确认

### 状态

新增，作为 Phase 6 前置补强。

### 背景

现有 LLM 压测里，用户输入会直接包含完整流程，例如“采购布料 -> 复合 -> 激光下料 -> 补裁片 -> 车缝充棉手工全流程 -> 质检”。这不符合真实使用。

真实业务中，用户更可能只说：

- “这个小兔子按上次做法走”
- “这批要先复合再切”
- “刘旭那边要补裁片”
- “冯杰那里继续做”

系统必须根据产品和历史记录主动提示流程，让用户确认或修复。

### 必须交付

1. 产品流程模板查询：按产品、规格、历史订单找到候选流程。
2. 流程建议草稿：用户输入不完整时，生成 suggested / draft 流程计划。
3. 人类确认机制：用户确认前不得创建正式 work orders。
4. 流程修订机制：已确认流程发生变更时，保留旧版本、修订原因和来源证据。
5. 低标准输入测试：测试中不再把完整流程直接写给 LLM，而是提供产品模板上下文和用户的模糊自然语言。
6. 追问模板：自动问是否复合、是否刺绣、是否定位切、是否补裁片/补配件、是否分厂、是否回仓或直发客户。

### 完成标准

- 用户只提供产品、数量、客户和少量现场线索时，系统能提出该产品建议流程。
- 用户可以用自然语言修复流程。
- 修复后系统再次给出确认摘要。
- 未确认流程只停留在 draft/checkpoint。
- 已确认流程能派生 work orders，并被跟单、日报、应收应付预测使用。
- LLM 50 案例测试不再依赖用户明说完整流程。

## Phase 6: ERP / 仓配桥接预备层

### 状态

未开始。

### 目标

建立聚水潭 / 仓配系统桥接所需的模型和 dry-run 测试边界。

### 必须交付

1. `fulfillment_plans`
2. `fulfillment_plan_lines`
3. `external_system_connections`
4. `external_sync_jobs`
5. `external_inventory_snapshots`
6. dry-run 桥接接口契约

## 当前执行队列

1. 先完成 Phase 5.5 产品流程模板和批次流程确认。
2. Review Phase 5 runtime API envelope 是否需要新增流程模板相关 command。
3. 进入 Phase 6 前再确认 ERP / 仓配 bridge 的 dry-run 范围。
4. 设计库存快照、外部订单同步、订单合并/拆分/补录和入库动作边界。
5. 决定第一版 MCP / Hermes adapter 是否作为 Phase 6 前置验证，还是等 bridge 契约稳定后再做。
