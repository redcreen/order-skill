[English](2026-04-19-order-plugin-first-agent-binding.md) | [中文](2026-04-19-order-plugin-first-agent-binding.md)

# 2026-04-19 Order Plugin-First Agent Binding

## Context

The runtime and control-tower behaviors were already in place, but the install and execution surface still had two problems:

- `order` was still documented mainly as a skill-style install target
- nothing enforced explicit agent targeting or script-backed execution at the distribution layer

That left a real mismatch between the intended behavior and the install story.

## Problem

The user requirement was explicit:

1. stop treating `order` as “install a skill”
2. make it “install a plugin”
3. require explicit installation onto one target agent
4. do not treat it as globally installed by default
5. execution must be hard execution, not chat-only claims

## Decision

Add a plugin-first wrapper around the existing `order/` runtime instead of moving the runtime itself.

That preserves the runtime core while changing the install contract and execution contract:

- install via plugin
- bind to one explicit agent
- block runtime execution until bound
- funnel runtime actions through one hard-execution wrapper command

## Implementation

1. Added repo-local plugin marketplace entry:
   - `.agents/plugins/marketplace.json`
2. Added plugin manifest and binding state:
   - `plugins/openclaw-order/.codex-plugin/plugin.json`
   - `plugins/openclaw-order/.codex-plugin/agent-binding.json`
3. Added plugin skill and README pair under `plugins/openclaw-order/`.
4. Added `plugins/openclaw-order/scripts/order_hard_execute.py` as the unified wrapper for runtime actions.
5. Added `bind-agent` and `show-binding` commands.
6. Made all real runtime subcommands refuse to run while the plugin remains `unbound`.
7. Updated public docs so `order` is now described as plugin-first and explicit-agent-only.

## Outcome

- `order` no longer relies on a skill-first install story
- the plugin is not globally enabled by default
- agent targeting is now explicit
- hard execution has a concrete command surface instead of only prompt wording
