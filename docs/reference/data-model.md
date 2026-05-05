[English](data-model.md) | [中文](data-model.zh-CN.md)

# Order Data Model

## What This Document Answers

This document defines the canonical data model for the future `order` system:

- core object groups
- core tables
- core states
- core views

## Main Groups

Recommended groups:

1. intake and continuity
2. master data
3. development and order
4. work and fulfillment
5. finance and settlement
6. control tower and external bridge

## Key Tables

Recommended core tables include:

- `inbox_items`
- `evidence_assets`
- `intake_sessions`
- `workflow_drafts`
- `draft_field_values`
- `draft_checkpoints`
- `object_threads`
- `pending_associations`
- `link_candidates`
- `parties`
- `process_providers`
- `products`
- `product_variants`
- `materials`
- `process_templates`
- `process_template_steps`
- `samples`
- `quotes`
- `bom_headers`
- `bom_items`
- `sales_orders`
- `sales_order_items`
- `order_change_requests`
- `production_lots`
- `lot_process_plans`
- `work_orders`
- `work_order_links`
- `business_events`
- `shipments`
- `shipment_order_links`
- `warehouse_receipts`
- `return_cases`
- `stock_items`
- `stock_movements`
- `receivables`
- `payables`
- `cash_transactions`
- `settlement_allocations`
- `invoices`
- `refunds`
- `supplier_deductions`
- `commitment_items`
- `followup_items`
- `exception_cases`
- `alerts`
- `daily_reports`
- `outbound_tasks`
- `fulfillment_plans`
- `fulfillment_plan_lines`
- `external_system_connections`
- `external_sync_jobs`
- `external_inventory_snapshots`

## Core Views

Recommended core views:

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

## Core Rules

The data model should enforce:

1. persist all input first
2. write formal business truth only after confirmation
3. model many-to-many money mapping explicitly
4. use movement-led inventory
5. preserve both final state and event history
