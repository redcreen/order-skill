import os from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const PLUGIN_ROOT = dirname(dirname(dirname(fileURLToPath(import.meta.url))));
const DEFAULT_AGENT_IDS = ["order"];
const DEFAULT_DATA_ROOT = "~/Documents/openclaw-order";
const DIRECT_ORDER_SCRIPT_TO_SUBCOMMAND = new Map([
  ["order_runtime_api.py", "<runtime-api-command>"],
  ["init_order_runtime.py", "init-runtime"],
  ["persist_order_input.py", "persist-input"],
  ["open_guided_intake_draft.py", "open-draft"],
  ["prepare_draft_confirmation.py", "prepare-confirmation"],
  ["commit_workflow_draft.py", "commit-draft"],
  ["resolve_pending_association.py", "resolve-association"],
  ["record_settlement_allocations.py", "allocate"],
  ["refresh_order_control_tower.py", "refresh-control-tower"],
  ["generate_daily_report.py", "daily-report"],
  ["search_order_history.py", "history-search"],
  ["show_order_history_item.py", "history-show"],
  ["replay_order_history.py", "history-replay"],
  ["open_history_backfill_draft.py", "history-backfill"],
  ["list_pending_association_candidates.py", "association-candidates"],
  ["resolve_pending_association_direct.py", "resolve-pending"],
  ["list_history_backfill_queue.py", "backfill-queue"],
  ["list_history_backfill_ready.py", "backfill-ready"],
  ["finalize_history_backfill.py", "backfill-finalize"],
  ["smoke_order_runtime.py", "smoke-runtime"],
  ["smoke_order_stage89.py", "smoke-stage89"],
]);

function normalizeText(value) {
  return typeof value === "string" ? value.trim() : "";
}

function uniqueNonEmptyStrings(values, fallback) {
  const input = Array.isArray(values) ? values : fallback;
  return [...new Set(input.map((item) => normalizeText(item)).filter(Boolean))];
}

function agentIdFromSessionKey(sessionKey) {
  const normalized = normalizeText(sessionKey);
  if (!normalized.startsWith("agent:")) {
    return "";
  }
  const parts = normalized.split(":");
  return parts.length >= 3 ? normalizeText(parts[1]) : "";
}

function resolveHomePath(value) {
  const normalized = normalizeText(value);
  if (!normalized) {
    return "";
  }
  if (normalized === "~") {
    return os.homedir();
  }
  if (normalized.startsWith("~/") || normalized.startsWith("~\\")) {
    return join(os.homedir(), normalized.slice(2));
  }
  return normalized;
}

function normalizeConfig(raw) {
  const value = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
  return {
    enabled: value.enabled !== false,
    pythonBin: normalizeText(value.pythonBin) || "python3",
    dataRoot: resolveHomePath(normalizeText(value.dataRoot) || DEFAULT_DATA_ROOT),
    agentIds: uniqueNonEmptyStrings(value.agentIds, DEFAULT_AGENT_IDS),
    injectRoleContract: value.injectRoleContract !== false,
  };
}

function isOrderSession(config, agentId, sessionKey) {
  const normalizedAgentId = normalizeText(agentId) || agentIdFromSessionKey(sessionKey);
  return Boolean(normalizedAgentId) && config.agentIds.includes(normalizedAgentId);
}

function buildOrderPromptContract(config, agentId) {
  const currentAgentId = normalizeText(agentId) || "<current-agent-id>";
  const wrapperPath = orderWrapperPath();
  return [
    "You are running inside the OpenClaw order runtime adapter plugin.",
    `This session belongs to the order agent id: ${currentAgentId}.`,
    `Local source of truth: ${config.dataRoot}`,
    "Runtime boundary:",
    "- OpenClaw is only an interaction surface. The durable order system is the local order runtime API.",
    "- All OpenClaw execution must go through the bound hard-execution wrapper, not direct runtime scripts and not order_runtime_api.py.",
    "- The wrapper enforces agent binding and returns a JSON response envelope: status, result, error, warnings.",
    "Rules:",
    "- Treat order as a local-first order operations system.",
    "- Persist order-related input before interpretation when the input may matter later.",
    "- Never claim a formal order write happened unless the wrapper returns status=ok and the runtime result confirms it.",
    "- Formal writes must stay draft -> confirmation -> commit, with an explicit confirmation token before commit.",
    "- Legacy/backfill work, delayed links, and settlement allocations must stay explicit and reviewable.",
    "- Non-order content does not enter the formal order business thread.",
    "Use this hard-execution wrapper for bundled runtime actions:",
    `${config.pythonBin} ${wrapperPath} <subcommand> --agent ${currentAgentId} ...`,
    "Typical subcommands: persist-input, open-draft, prepare-confirmation, commit-draft, resolve-association, allocate, refresh-control-tower, daily-report, history-search, history-show, history-replay, history-backfill, association-candidates, resolve-pending, backfill-queue, backfill-ready, backfill-finalize.",
    `For plain inbound text, first run: ${config.pythonBin} ${wrapperPath} persist-input --agent ${currentAgentId} --text '<raw user text>'`,
    `Then open the guided draft directly with: ${config.pythonBin} ${wrapperPath} open-draft --agent ${currentAgentId} --inbox-item-id <inbox_item_id> --intent-type <intent> --summary-text '<summary>' --field customer_name=<customer> --field product_name=<product> --field qty=<qty>`,
    "If you need richer metadata for persist-input, use --payload-file instead of calling the underlying script directly.",
    "If you need richer metadata for open-draft, use --payload-file instead of calling the underlying script directly.",
    "If the wrapper or runtime API fails, return the actual blocker instead of pretending success.",
  ].join("\n");
}

function orderWrapperPath() {
  return join(PLUGIN_ROOT, "plugins", "openclaw-order", "scripts", "order_hard_execute.py");
}

function directOrderRuntimeSubcommand(command) {
  const normalized = normalizeText(command).toLowerCase();
  if (!normalized || normalized.includes("order_hard_execute.py")) {
    return "";
  }
  for (const [scriptName, subcommand] of DIRECT_ORDER_SCRIPT_TO_SUBCOMMAND.entries()) {
    if (normalized.includes(scriptName.toLowerCase())) {
      return subcommand;
    }
  }
  return "";
}

function buildOrderToolGuardReason(config, agentId, subcommand) {
  const currentAgentId = normalizeText(agentId) || "<current-agent-id>";
  return (
    "Use the order hard-execution wrapper instead of calling runtime API/scripts directly. " +
    `Run: ${config.pythonBin} ${orderWrapperPath()} ${subcommand} --agent ${currentAgentId} ...`
  );
}

const plugin = {
  register(api) {
    const config = normalizeConfig(api.config);
    if (!config.enabled) {
      api.logger.info("[order-runtime-guard] plugin disabled");
      return;
    }

    api.on("before_prompt_build", async (_event, ctx) => {
      if (!config.injectRoleContract) {
        return;
      }
      const sessionKey = normalizeText(ctx?.sessionKey || "");
      const agentId = normalizeText(ctx?.agentId || "");
      if (!isOrderSession(config, agentId, sessionKey)) {
        return;
      }
      return {
        appendSystemContext: buildOrderPromptContract(config, agentId),
      };
    });

    api.on("before_tool_call", (event, ctx) => {
      const sessionKey = normalizeText(ctx?.sessionKey || "");
      const agentId = normalizeText(ctx?.agentId || "");
      if (!isOrderSession(config, agentId, sessionKey)) {
        return;
      }
      if (normalizeText(event?.toolName) !== "exec") {
        return;
      }
      const subcommand = directOrderRuntimeSubcommand(event?.params?.command);
      if (!subcommand) {
        return;
      }
      return {
        block: true,
        blockReason: buildOrderToolGuardReason(config, agentId, subcommand),
      };
    });

    api.logger.info("[order-runtime-guard] plugin loaded");
  },
};

export default plugin;
