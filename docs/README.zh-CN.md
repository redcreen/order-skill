[English](README.md) | [中文](README.zh-CN.md)

# Order 文档

这个目录是 `order-skill` 独立项目的唯一 durable 文档树。凡是 order 专属的架构、安装、设计参考、测试计划和开发日志，统一放到这里。

## 从这里开始

- Order 架构: [architecture.zh-CN.md](architecture.zh-CN.md)
- Order 路线图: [roadmap.zh-CN.md](roadmap.zh-CN.md)
- Order 开发计划: [reference/development-plan.zh-CN.md](reference/development-plan.zh-CN.md)
- Order 测试计划: [test-plan.zh-CN.md](test-plan.zh-CN.md)
- 安装文档: [install/README.zh-CN.md](install/README.zh-CN.md)
- 参考资料: [reference/README.zh-CN.md](reference/README.zh-CN.md)
- Order 开发日志: [devlog/README.zh-CN.md](devlog/README.zh-CN.md)
- Order 运行时入口页: [../README.zh-CN.md](../README.zh-CN.md)

## 职责边界

- `docs/*`: order-skill 项目 durable 文档
- `order/*/README*`: order 模块内各入口的安装和使用说明
- `plugins/openclaw-order/*`: OpenClaw wrapper 和 agent skill 使用说明

## 覆盖范围

这个文档树负责：

- order 运行时和分发架构
- order 专属 roadmap 和验证口径
- order plugin 安装与运行说明
- order 专属设计参考和开发日志

它不替代：

- `order/` 下的运行时 README
- `plugins/openclaw-order/` 下的 plugin README
- `scripts/` 下的测试脚本和迁移脚本
