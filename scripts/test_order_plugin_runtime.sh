#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "[1/9] compile order runtime and plugin wrapper"
python3 -m compileall order/scripts plugins/openclaw-order/scripts

echo "[2/9] verify host plugin wrapper and tool guard"
node --input-type=module <<'NODE'
import assert from "node:assert/strict";
import plugin from "./src/plugin/index.js";

const hooks = new Map();
const api = {
  config: {
    enabled: true,
    pythonBin: "python3",
    dataRoot: "~/Documents/openclaw-order",
    agentIds: ["order"],
    injectRoleContract: true,
  },
  on(name, fn) {
    hooks.set(name, fn);
  },
  logger: {
    info() {},
    warn() {},
    error() {},
  },
};

plugin.register(api);

assert.ok(hooks.has("before_prompt_build"));
assert.ok(hooks.has("before_tool_call"));

const promptResult = await hooks.get("before_prompt_build")({}, { sessionKey: "agent:order:main", agentId: "order" });
assert.match(promptResult.appendSystemContext, /order_hard_execute\.py/);

const blockedDirectCall = hooks.get("before_tool_call")(
  {
    toolName: "exec",
    params: {
      command: "python3 order/scripts/persist_order_input.py --payload-file /tmp/payload.json",
    },
  },
  { sessionKey: "agent:order:main", agentId: "order" },
);
assert.equal(blockedDirectCall.block, true);
assert.match(blockedDirectCall.blockReason, /order_hard_execute\.py/);
assert.match(blockedDirectCall.blockReason, /persist-input/);

const blockedRuntimeApiCall = hooks.get("before_tool_call")(
  {
    toolName: "exec",
    params: {
      command: "python3 order/scripts/order_runtime_api.py --request-file /tmp/request.json",
    },
  },
  { sessionKey: "agent:order:main", agentId: "order" },
);
assert.equal(blockedRuntimeApiCall.block, true);
assert.match(blockedRuntimeApiCall.blockReason, /order_hard_execute\.py/);

const allowedWrapperCall = hooks.get("before_tool_call")(
  {
    toolName: "exec",
    params: {
      command:
        "python3 plugins/openclaw-order/scripts/order_hard_execute.py persist-input --agent order --payload-file /tmp/payload.json",
    },
  },
  { sessionKey: "agent:order:main", agentId: "order" },
);
assert.equal(allowedWrapperCall, undefined);

const ignoredOtherAgent = hooks.get("before_tool_call")(
  {
    toolName: "exec",
    params: {
      command: "python3 order/scripts/persist_order_input.py --payload-file /tmp/payload.json",
    },
  },
  { sessionKey: "agent:health:main", agentId: "health" },
);
assert.equal(ignoredOtherAgent, undefined);

console.log(
  JSON.stringify(
    {
      status: "ok",
      checks: [
        "prompt-wrapper-injected",
        "direct-runtime-script-blocked",
        "direct-runtime-api-blocked",
        "wrapper-call-allowed",
        "other-agent-ignored",
      ],
    },
    null,
    2,
  ),
);
NODE

echo "[3/9] reset and show default binding state"
python3 - <<'PY'
import json
from pathlib import Path

path = Path("plugins/openclaw-order/.codex-plugin/agent-binding.json")
payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
    "installationScope": "explicit_agent_only",
    "autoInstall": False,
    "notes": "Bind this plugin to one specific agent before running order execution commands.",
}
payload["status"] = "unbound"
payload["targetAgent"] = ""
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
PY
python3 plugins/openclaw-order/scripts/order_hard_execute.py show-binding

echo "[4/9] verify unbound plugin refuses runtime execution"
if python3 plugins/openclaw-order/scripts/order_hard_execute.py smoke-runtime --agent docker-order-agent; then
  echo "expected smoke-runtime to fail while plugin is unbound" >&2
  exit 1
fi

echo "[5/9] bind plugin to docker-order-agent"
python3 plugins/openclaw-order/scripts/order_hard_execute.py bind-agent --agent docker-order-agent

echo "[6/9] verify other agents are rejected"
if python3 plugins/openclaw-order/scripts/order_hard_execute.py smoke-runtime --agent other-agent; then
  echo "expected smoke-runtime to fail for a non-bound agent" >&2
  exit 1
fi

echo "[7/9] verify wrapper-friendly persist-input and open-draft paths"
TEMP_ORDER_ROOT="$(mktemp -d /tmp/order-wrapper-test.XXXXXX)"
export TEMP_ORDER_ROOT
python3 plugins/openclaw-order/scripts/order_hard_execute.py persist-input --agent docker-order-agent \
  --data-root "$TEMP_ORDER_ROOT" \
  --text "王总，小兔子，100个，今天开始做，还没确认工厂。" \
  --source-actor "wrapper-smoke" \
  --channel-session-key "wrapper-session" >"$TEMP_ORDER_ROOT/persist.json"
python3 - <<'PY'
import json
import os
from pathlib import Path

temp_root = Path(os.environ["TEMP_ORDER_ROOT"])
payload = json.loads((temp_root / "persist.json").read_text(encoding="utf-8"))
assert payload["status"] == "ok"
assert payload["command"] == "persist-input"
payload = payload["result"]
assert payload["status"] == "persisted"
assert payload["attachment_count"] == 0
assert Path(payload["raw_archive_path"]).exists()
Path(temp_root / "inbox_item_id.txt").write_text(payload["inbox_item_id"], encoding="utf-8")
print(json.dumps({"status": "ok", "checks": ["friendly-persist-input"]}, ensure_ascii=False, indent=2))
PY
INBOX_ITEM_ID="$(cat "$TEMP_ORDER_ROOT/inbox_item_id.txt")"
python3 plugins/openclaw-order/scripts/order_hard_execute.py open-draft --agent docker-order-agent \
  --data-root "$TEMP_ORDER_ROOT" \
  --inbox-item-id "$INBOX_ITEM_ID" \
  --intent-type production_arrangement \
  --target-object-type production_arrangement \
  --target-action create \
  --summary-text "王总，小兔子，100个，今天开始做，还没确认工厂。" \
  --field customer_name=王总 \
  --field product_name=小兔子 \
  --field qty=100 \
  --field factory_name=未确认 \
  --required-field customer_name \
  --required-field product_name \
  --required-field qty \
  --required-field factory_name >"$TEMP_ORDER_ROOT/open_draft.json"
python3 - <<'PY'
import json
import os
from pathlib import Path

temp_root = Path(os.environ["TEMP_ORDER_ROOT"])
payload = json.loads((temp_root / "open_draft.json").read_text(encoding="utf-8"))
assert payload["status"] == "ok"
assert payload["command"] == "open-draft"
payload = payload["result"]
assert payload["status"] == "draft_opened"
assert payload["preview"]["captured_fields"]["customer_name"] == "王总"
assert payload["preview"]["captured_fields"]["product_name"] == "小兔子"
assert payload["preview"]["captured_fields"]["qty"] == "100"
assert payload["preview"]["captured_fields"]["factory_name"] == "未确认"
print(json.dumps({"status": "ok", "checks": ["friendly-open-draft"]}, ensure_ascii=False, indent=2))
PY
python3 plugins/openclaw-order/scripts/order_hard_execute.py persist-input --agent docker-order-agent --help
python3 plugins/openclaw-order/scripts/order_hard_execute.py open-draft --agent docker-order-agent --help
rm -rf "$TEMP_ORDER_ROOT"

echo "[8/9] run runtime smoke checks through the bound agent"
python3 plugins/openclaw-order/scripts/order_hard_execute.py smoke-runtime --agent docker-order-agent
python3 plugins/openclaw-order/scripts/order_hard_execute.py smoke-stage89 --agent docker-order-agent

echo "[9/9] restore default unbound state"
python3 - <<'PY'
import json
from pathlib import Path

path = Path("plugins/openclaw-order/.codex-plugin/agent-binding.json")
payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
    "installationScope": "explicit_agent_only",
    "autoInstall": False,
    "notes": "Bind this plugin to one specific agent before running order execution commands.",
}
payload["status"] = "unbound"
payload["targetAgent"] = ""
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
PY

python3 plugins/openclaw-order/scripts/order_hard_execute.py show-binding

echo "order plugin runtime checks passed"
