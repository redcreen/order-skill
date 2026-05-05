[English](test-realism-review.md) | [中文](test-realism-review.zh-CN.md)

# Order Test Realism Review

## Conclusion

`scripts/test_order_llm_chaos_stress_50.py` is still useful, but it is not a realistic human-input acceptance test. It validates long-form extraction, model routing, and runtime consistency.

Real usage is covered by two layers:

- `scripts/test_order_lazy_guided_intake_50.py` validates deterministic guided-intake runtime behavior.
- `scripts/test_order_llm_lazy_guided_intake_50.py` puts GPT-5.5 short-input extraction in front of the same guided-intake runtime.

The runtime layer verifies:

- The first short message creates only a draft and missing-field checkpoints.
- The system blocks commit before required facts are complete.
- Product/process flow confirmation remains mandatory.
- Formal `sales_orders` and `work_orders` are created only after explicit confirmation.
- All source turns are persisted.

The GPT-5.5 end-to-end layer verifies:

- OpenClaw order agent uses `openai-codex/gpt-5.5`.
- `fallback_used=false` for every batch.
- GPT-5.5 extracts field updates from short Chinese turns.
- Runtime, not the LLM, derives system fields such as order number and work-order steps.
- Final required fields exact-match expected case data before runtime commit.

`scripts/test_order_messy_event_confirmation_50.py` expands coverage beyond order creation. It validates short post-order events such as customer receipts, receivables, supplier bills, supplier payouts, cut-piece logistics, customer shipments, returns, refunds, supplier deductions, replenishment/rework work orders, and unrelated chatter.

## Latest Results

| Batch | Result | Input Shape | Data Result |
| --- | --- | --- | --- |
| runtime 10 cases | Pass | 30 turns, avg 24.37 chars, max 35 chars | 10 sales orders, 59 work orders, 0 open drafts |
| runtime 50 cases | Pass | 150 turns, avg 24.95 chars, max 36 chars | 50 sales orders, 295 work orders, 0 open drafts |
| GPT-5.5 + runtime 10 cases | Pass | 30 turns, avg 24.37 chars, max 35 chars | 10 sales orders, 59 work orders, 0 open drafts |
| GPT-5.5 + runtime 50 cases | Pass | 150 turns, avg 24.95 chars, max 36 chars | 50 sales orders, 295 work orders, 0 open drafts |
| GPT-5.5 + messy event 50 cases | Pass | 50 turns, avg 28.74 chars, max 37 chars | 46 confirmed formal writes, 4 unrelated inputs ignored, 2 confirmed settlement allocations, 0 open drafts |

## Notes

The GPT-5.5 short-input test should use batch size 5. A batch size of 10 previously produced one malformed JSON response, so batch size 5 is the current stable acceptance configuration.

The direct `allocate` settlement command is now confirmation-gated. It must first run with `dry_run=true` to receive a confirmation token. Calls without a token, with a fake token, or with a changed payload are rejected; only a matching token writes `settlement_allocations` and updates settlement rollups.
