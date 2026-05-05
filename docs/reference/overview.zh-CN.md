[English](overview.md) | [中文](overview.zh-CN.md)

# Order 文档总览

## 这套文档的作用

这组文档定义 `order` 系统的统一设计口径。

目标是只保留一套可维护、可继续实现的 order 文档，不再保留多份并行设计稿。

## 从这里开始

按这个顺序阅读：

1. [Order 架构](../architecture.zh-CN.md)
   回答 order 系统到底是什么、边界在哪里、为什么要这样设计。
2. [Order 数据模型](data-model.zh-CN.md)
   回答 order 系统应该有哪些核心对象、表、状态和视图。
3. [Order 运行模型](operating-model.zh-CN.md)
   回答安装后 agent 怎么工作、怎么接住自然语言输入、怎么跟单、怎么日报、怎么保证确认后落库。

## 当前设计结论

`order` 现在应被理解成：

- 一个 `local-first` 的订单运营系统
- AI 负责理解、串联、补全、提醒和推动
- 本地 `SQLite` 和文件系统负责形成正式真相源
- 所有输入先持久化，再逐步收敛成业务事实
- 首选分发形态是绑定到指定 agent 的 plugin，而不是默认全局 skill 安装

## 主题覆盖图

下面这些是本轮讨论中已经明确纳入新文档集的主题：

| 主题 | 文档位置 |
| --- | --- |
| 所有输入先持久化，防止 `/new`、重启、崩溃后丢失 | [Order 架构](../architecture.zh-CN.md)、[Order 运行模型](operating-model.zh-CN.md) |
| 自然语言输入，不强迫用户按标准模板说话 | [Order 运行模型](operating-model.zh-CN.md) |
| 草稿 -> 确认 -> 落库 | [Order 运行模型](operating-model.zh-CN.md) |
| 乱序录入、补录、弱上下文、关系后补 | [Order 架构](../architecture.zh-CN.md)、[Order 运行模型](operating-model.zh-CN.md) |
| 一个付款对应多个对象的多对多分配 | [Order 数据模型](data-model.zh-CN.md) |
| 不同产品 / 不同批次流程不同，需流程模板 | [Order 架构](../architecture.zh-CN.md)、[Order 数据模型](data-model.zh-CN.md)、[Order 运行模型](operating-model.zh-CN.md) |
| 作业、返工、返修、退货、补采是一级对象 | [Order 架构](../architecture.zh-CN.md)、[Order 数据模型](data-model.zh-CN.md) |
| 主动跟单、承诺、异常、提醒 | [Order 架构](../architecture.zh-CN.md)、[Order 运行模型](operating-model.zh-CN.md) |
| 每日简洁报告和行动建议 | [Order 运行模型](operating-model.zh-CN.md) |
| 对外通知、自动发送策略 | [Order 架构](../architecture.zh-CN.md)、[Order 运行模型](operating-model.zh-CN.md) |
| 聚水潭 / 仓库系统 / 库存同步预留 | [Order 架构](../architecture.zh-CN.md)、[Order 数据模型](data-model.zh-CN.md) |

## 当前范围

`order` 当前设计范围包括：

- 样品
- 报价
- 销售订单
- 生产
- 发货
- 应收应付
- 收付款
- 开票
- 入库 / 库存同步准备
- 跟单、提醒、日报

## 当前不做的事

这版不把以下内容做成 V1 正式范围：

- 泛财务系统
- 总账
- 预算管理
- 投资和税务分析
- ERP 全量对接实现
- 仓库系统全量执行编排

这些只保留接口和边界，不现在展开。
