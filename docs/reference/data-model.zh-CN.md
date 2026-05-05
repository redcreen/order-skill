[English](data-model.md) | [中文](data-model.zh-CN.md)

# Order 数据模型

## 这份文档回答什么

这份文档定义 `order` 系统应有哪些核心对象、表、状态和视图。

它不再沿用旧的多份 order 表设计稿，而是给出统一的数据模型口径。

## 数据分层

推荐按 6 组来建模：

1. 输入与连续性
2. 主数据
3. 开发与订单
4. 作业与履约
5. 财务与结算
6. 控制塔与外部桥接

## 一、输入与连续性

### `inbox_items`

所有输入先入这张表。

作用：

- 不丢
- 可回放
- 支持重启和 `/new` 后恢复

### `evidence_assets`

图片、截图、账单、物流单、凭证等文件资产。

### `intake_sessions`

记录一次会话式业务处理过程。

### `workflow_drafts`

正式落库前的草稿对象。

### `draft_field_values`

草稿中的字段、值、来源、置信度。

### `draft_checkpoints`

系统下一步需要追问或确认的事项。

### `object_threads`

对象级连续性。

建议至少支持：

- order
- sample
- lot
- shipment
- return_case
- party

### `pending_associations`

用于保存还没确认挂到哪个正式对象上的事实。

### `link_candidates`

用于保存候选关系和匹配置信度。

## 二、主数据

### `parties`

统一表示：

- 客户
- 供应商
- 工厂
- 物流方
- 仓库方
- 内部主体

### `process_providers`

记录不同 `party` 能承接什么工序。

### `products`

产品主数据。

### `product_variants`

规格、颜色、变体、ERP SKU。

### `materials`

材料和配件主数据。

### `process_templates`

产品可复用的默认流程模板。

关键原则：

- 模板是“系统建议”，不是本批次事实
- 模板可以来自历史订单、人工维护、导入数据或系统归纳
- 模板需要有状态，例如 `draft`、`active`、`retired`
- 同一产品可以有多个模板，例如普通激光、复合后激光、刺绣定位切
- 模板被修改时应保留版本或修订记录，避免历史订单解释被新模板污染

### `process_template_steps`

模板中的工序顺序和默认承接方。

每一步至少要表达：

- 顺序
- 工序类型
- 是否必需
- 默认承接方
- 默认提前期
- 成本/计价备注
- 需要用户确认的问题

例如：

| step_no | step_type | 默认承接方 | 需要确认 |
| --- | --- | --- | --- |
| 10 | material | 布料供应商 | 布料是否需要多买富余 |
| 20 | composite | 弘辉复合 | 这批是否需要复合 |
| 30 | laser_cut | 刘旭激光 | 普通切还是定位切 |
| 40 | sewing_full | 定远乡冯杰 | 是否车缝、充棉、手工都在一个厂 |
| 50 | qc | 自检 | 是否需要客户验货 |

## 三、开发与订单

### `samples`

样品单。

### `quotes`

报价单。

### `bom_headers`

BOM 头。

### `bom_items`

BOM 明细。

### `sales_orders`

订单单头。

### `sales_order_items`

订单行。

### `order_change_requests`

加单、减单、取消、改交期、改规格。

## 四、作业与履约

### `production_lots`

生产批次。

### `lot_process_plans`

这一批货实际确认采用的流程计划。

这是流程管理的关键事实表。

它和 `process_templates` 的区别：

- `process_templates` 说明“这个产品通常怎么做”
- `lot_process_plans` 说明“这一批确认怎么做”

生命周期建议：

- `suggested`: 系统根据产品模板、历史订单或当前输入给出的建议
- `draft`: 用户正在修正，还没有确认
- `confirmed`: 用户确认后，可以派生 work orders
- `revised`: 已确认后发生修改，旧版本保留，新版本继续执行
- `cancelled`: 订单取消或流程废弃

必须记录：

- 来源模板
- 当前版本
- 人类确认时间
- 确认人或来源 channel
- 本次修订原因
- 影响范围，例如交期、应付、库存、发货

用户输入不完整时，系统应先创建 `suggested/draft` 计划和 checkpoint，而不是直接创建正式作业。

### `work_orders`

具体工作对象，例如：

- 采购
- 复合
- 激光
- 绣花
- 平车
- 充棉
- 手工
- 质检
- 返工
- 返修

### `work_order_links`

作业和订单行之间的关系。

### `business_events`

先发生、后确认的业务事件。

### `shipments`

发货 / 回货 / 调拨。

### `shipment_order_links`

发货和订单行之间的关系。

### `warehouse_receipts`

义乌 / 云仓 / ERP 入库单。

### `return_cases`

退货、返修、售后回流案件。

### `stock_items`

库存对象。

建议支持：

- 原材料
- 配件
- 半成品
- 成品
- 返修品

### `stock_movements`

库存变动流水。

## 五、财务与结算

### `receivables`

应收。

可表示：

- 预付款
- 尾款
- 代收款
- 平账尾款

### `payables`

应付。

可表示：

- 材料采购
- 复合
- 激光
- 绣花
- 平车
- 充棉
- 手工
- 物流
- 返工补充费用

### `cash_transactions`

真实收付款流水。

### `settlement_allocations`

一笔钱分配到多个应收 / 应付 / 退款 / 扣款的分配层。

这张表是刚需，不是可选增强。

### `invoices`

开票记录。

### `refunds`

退货引发的退款。

### `supplier_deductions`

返修、质量问题、短缺等导致对供应商的扣款。

## 六、控制塔与外部桥接

### `commitment_items`

承诺对象。

例如：

- 承诺交期
- 承诺发货时间
- 承诺回货时间
- 承诺付款时间

### `followup_items`

所有待跟进事项。

### `exception_cases`

异常池。

### `alerts`

系统级提醒。

### `daily_reports`

正式日报对象。

### `outbound_tasks`

待发送通知和沟通草稿。

### `fulfillment_plans`

订单与外部仓配执行之间的桥接计划。

### `fulfillment_plan_lines`

拆单、合单、补录后的履约行。

### `external_system_connections`

聚水潭 / 仓库系统等连接配置。

### `external_sync_jobs`

同步任务。

### `external_inventory_snapshots`

外部库存快照。

## 核心状态

推荐至少统一这些状态模型：

### 样品

- `draft`
- `quoting`
- `sampling`
- `sample_ready`
- `approved`
- `rejected`
- `converted`

### 订单

- `draft`
- `waiting_deposit`
- `confirmed`
- `in_production`
- `partially_delivered`
- `delivered`
- `closed`
- `cancelled`
- `on_hold`

### 批次

- `planned`
- `in_progress`
- `partial`
- `completed`
- `closed`
- `blocked`
- `cancelled`

### 应收 / 应付

- `pending`
- `partial`
- `paid`
- `overdue`
- `void`
- `disputed`

### 跟进项

- `open`
- `waiting_external`
- `waiting_internal`
- `done`
- `dropped`

## 关键视图

系统至少要稳定提供这些视图：

- `v_order_production_status`
- `v_order_finance_status`
- `v_order_profit_snapshot`
- `v_cash_forecast`
- `v_factory_load`
- `v_step_delay_alerts`
- `v_deposit_gate_orders`
- `v_uninvoiced_delivered_orders`
- `v_unreconciled_cash`
- `v_pending_associations`
- `v_open_return_repair_cases`
- `v_work_due_today`

## 数据约束

系统必须坚持：

1. 所有输入先持久化
2. 正式业务事实只在确认后写入
3. 多对多款项分配必须通过分配层表达
4. 库存靠流水，不靠单个覆盖值
5. 对象状态和事件记录并存，不能只留最终状态
