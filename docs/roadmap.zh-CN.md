[English](roadmap.md) | [中文](roadmap.zh-CN.md)

# Order 路线图

| 阶段 | 状态 | 目标 | 退出条件 |
| --- | --- | --- | --- |
| Phase 1 | done | 建立 local-first 运行时基础层 | runtime 根目录、`SQLite schema v1`、原始输入持久化和 runtime smoke 全部存在 |
| Phase 2 | done | 收口 guided intake 和正式录入守卫 | 草稿、确认、落库、待关联 resolve、多对多分配全部可用 |
| Phase 3 | done | 收口控制塔、日报和 plugin-first 分发 | 控制塔刷新、日报、显式 agent 绑定和硬执行 wrapper 全部可用 |
| Phase 4 | done | 把 order 专属文档全部收进根目录 `docs/` | order 架构、plugin 安装文档、设计参考、测试计划和 order 开发日志全部由根目录 `docs/` 持有 |
| Phase 5.5 | active | 产品流程模板和批次流程确认 | 用户只给懒人短句时，系统能建议产品流程、等待确认、再派生正式 work orders |
| Phase 5 | done | 建立 `order` 独立 runtime API 和多入口 adapter 边界 | OpenClaw plugin 已压薄为 adapter；CLI / OpenClaw 通过同一套 JSON command API 调用 runtime；MCP / Hermes 可按同一协议接入 |
| Phase 6 | planned | 准备 ERP / 仓配桥接阶段 | 定义 dry-run bridge 契约、库存快照、sync-job 语义，以及 split/merge/supplement 边界 |

## 当前焦点

现在的结构性结果是：

- `order/` 继续作为运行时主体
- OpenClaw plugin 继续保留，但职责已经降级为入口 adapter
- 核心业务逻辑已经收敛到稳定 runtime API 入口
- order 专属 durable 文档已经统一归属到根目录 `docs/`
- 新项目根目录就是 OpenClaw 可安装插件包，不再维护旧的嵌套宿主包副本

## 后续事项

- MCP / Hermes / CLI 接入边界
- 聚水潭和仓配执行桥接契约
- 库存快照和 sync-job 语义
- 在适配器编码前先收口 split / merge / supplement 边界
