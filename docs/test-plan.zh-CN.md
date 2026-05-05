[English](test-plan.md) | [中文](test-plan.zh-CN.md)

# Order 测试计划

## 范围

验证独立 `order-skill` 项目的文档、运行时、OpenClaw plugin adapter、确认式正式写入、LLM 懒人输入链路和混乱业务事件链路。

## 文档用例

- Case: 根 README 可正确说明项目边界
  - Setup: 打开 [../README.zh-CN.md](../README.zh-CN.md)
  - Action: 检查安装、绑定、验证和文档入口
  - Expected Result: 仓库根目录被说明为可安装 plugin 包，不再依赖旧的嵌套宿主包副本

- Case: 文档树从根目录 `docs/` 进入
  - Setup: 打开 [README.zh-CN.md](README.zh-CN.md)
  - Action: 检查架构、路线图、测试计划、安装文档、参考资料、开发日志入口
  - Expected Result: 所有 order durable 文档都从根目录 `docs/` 进入

- Case: 安装文档不包含旧工作区路径
  - Setup: 打开 [install/plugin-install.zh-CN.md](install/plugin-install.zh-CN.md)
  - Action: 检查安装说明
  - Expected Result: 安装路径指向 `/Users/redcreen/Project/order-skill`，不再要求旧工作区里的嵌套宿主包路径

## Runtime 和 Plugin 用例

- Case: plugin 默认未绑定
  - Setup: 运行 `python3 plugins/openclaw-order/scripts/order_hard_execute.py show-binding`
  - Action: 检查输出
  - Expected Result: `status=unbound` 且 `targetAgent` 为空

- Case: 未绑定时拒绝 runtime 执行
  - Setup: 保持默认未绑定
  - Action: 运行 `python3 plugins/openclaw-order/scripts/order_hard_execute.py smoke-runtime --agent order`
  - Expected Result: 命令失败，并提示先 bind-agent

- Case: 绑定 agent 后 runtime smoke 通过
  - Setup: 运行 `python3 plugins/openclaw-order/scripts/order_hard_execute.py bind-agent --agent order`
  - Action: 运行 `python3 plugins/openclaw-order/scripts/order_hard_execute.py smoke-runtime --agent order`
  - Expected Result: 运行时初始化和基础订单链路通过

- Case: 非绑定 agent 被硬拦截
  - Setup: plugin 已绑定 `order`
  - Action: 运行 `python3 plugins/openclaw-order/scripts/order_hard_execute.py smoke-runtime --agent finance`
  - Expected Result: 命令失败，并提示当前插件绑定到 `order`

- Case: Stage 8-9 smoke 继续通过
  - Setup: plugin 已绑定 `order`
  - Action: 运行 `python3 plugins/openclaw-order/scripts/order_hard_execute.py smoke-stage89 --agent order`
  - Expected Result: 确认、落库守卫、待关联 resolve、多对多分配、控制塔和日报全部通过

- Case: OpenClaw host plugin guard 可阻断绕过 wrapper
  - Setup: 运行 `bash scripts/test_order_plugin_runtime.sh`
  - Action: 检查 Node hook 和 wrapper 测试
  - Expected Result: 直接调用 `order/scripts/*.py` 或 `order_runtime_api.py` 被拦截，wrapper 调用被允许

- Case: CLI 业务 E2E 检查数据库状态
  - Setup: 运行 `python3 scripts/test_order_business_cli_e2e.py`
  - Action: 检查脚本里的 SQLite 查询断言
  - Expected Result: 订单、工单、应收应付、收付款、分配、发货和日报状态都符合预期

## LLM 和真实混乱场景用例

- Case: 懒人短输入不会直接落库
  - Setup: 用户只输入“小兔子王总 500 个，按上次做”
  - Action: 运行 `python3 scripts/test_order_lazy_guided_intake_50.py --case-count 10`
  - Expected Result: 系统只创建 draft 和缺失字段 checkpoint，正式 `sales_orders` 不增加

- Case: 懒人输入需要确认产品流程
  - Setup: 用户补了客户、规格、价格、交期和工厂，但没有明确确认系统建议流程
  - Action: 继续同一个 intake session 更新 draft
  - Expected Result: draft 仍保留流程确认缺口，不能自动派生 work orders

- Case: GPT-5.5 短句抽取接入懒人引导链路
  - Setup: 每个案例只有 3 轮短输入，脚本通过 OpenClaw order agent 调用 `openai-codex/gpt-5.5`
  - Action: 运行 `python3 scripts/test_order_llm_lazy_guided_intake_50.py --case-count 50 --batch-size 5 --model openai-codex/gpt-5.5`
  - Expected Result: `fallback_used=false`，字段 exact match，流程确认前不落库，确认后订单和工单状态正确

- Case: 订单后续混乱事件必须确认后才正式写入
  - Setup: 先创建基础订单，再输入 50 条短事件，覆盖付款、应收、供应商账单、付款、裁片物流、客户发货、退货、退款、扣款、补裁片、返工和无关闲聊
  - Action: 运行 `python3 scripts/test_order_messy_event_confirmation_50.py --case-count 50 --llm-extract --batch-size 5 --model openai-codex/gpt-5.5`
  - Expected Result: 正式业务事件全部先进入 draft；未 prepare、假 token、直接 allocate 都不能写入；最终 `open_drafts=0`、`pending_associations_open=0`、SQLite integrity 为 `ok`

## 门禁

发布前至少运行：

```bash
python3 scripts/validate_order_skill_repo.py
python3 -m compileall order/scripts plugins/openclaw-order/scripts scripts
node --check src/plugin/index.js
bash scripts/test_order_plugin_runtime.sh
python3 scripts/test_order_business_cli_e2e.py
```
