PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS import_metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inbox_items (
  inbox_item_id TEXT PRIMARY KEY,
  channel_type TEXT NOT NULL,
  channel_session_key TEXT,
  source_actor TEXT,
  source_message_id TEXT,
  content_type TEXT NOT NULL DEFAULT 'text',
  raw_text TEXT,
  raw_payload_json TEXT,
  raw_archive_path TEXT,
  received_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_assets (
  evidence_asset_id TEXT PRIMARY KEY,
  inbox_item_id TEXT REFERENCES inbox_items(inbox_item_id) ON DELETE CASCADE,
  file_name TEXT NOT NULL,
  mime_type TEXT,
  local_path TEXT NOT NULL,
  file_hash TEXT,
  source_path TEXT,
  extracted_text TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intake_sessions (
  intake_session_id TEXT PRIMARY KEY,
  channel_type TEXT NOT NULL,
  channel_session_key TEXT,
  intent_type TEXT,
  session_status TEXT NOT NULL DEFAULT 'collecting',
  summary_text TEXT,
  started_at TEXT NOT NULL,
  last_active_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intake_session_items (
  link_id INTEGER PRIMARY KEY AUTOINCREMENT,
  intake_session_id TEXT NOT NULL REFERENCES intake_sessions(intake_session_id) ON DELETE CASCADE,
  inbox_item_id TEXT NOT NULL REFERENCES inbox_items(inbox_item_id) ON DELETE CASCADE,
  link_role TEXT NOT NULL DEFAULT 'source',
  linked_at TEXT NOT NULL,
  UNIQUE(intake_session_id, inbox_item_id)
);

CREATE TABLE IF NOT EXISTS workflow_drafts (
  workflow_draft_id TEXT PRIMARY KEY,
  intake_session_id TEXT REFERENCES intake_sessions(intake_session_id) ON DELETE CASCADE,
  target_object_type TEXT,
  target_action TEXT,
  draft_status TEXT NOT NULL DEFAULT 'collecting',
  confidence_score REAL,
  preview_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS draft_source_links (
  link_id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_draft_id TEXT NOT NULL REFERENCES workflow_drafts(workflow_draft_id) ON DELETE CASCADE,
  inbox_item_id TEXT NOT NULL REFERENCES inbox_items(inbox_item_id) ON DELETE CASCADE,
  link_role TEXT NOT NULL DEFAULT 'source',
  linked_at TEXT NOT NULL,
  UNIQUE(workflow_draft_id, inbox_item_id)
);

CREATE TABLE IF NOT EXISTS draft_field_values (
  draft_field_value_id TEXT PRIMARY KEY,
  workflow_draft_id TEXT REFERENCES workflow_drafts(workflow_draft_id) ON DELETE CASCADE,
  field_name TEXT NOT NULL,
  field_value TEXT,
  value_source_type TEXT NOT NULL,
  source_turn_ref TEXT,
  confidence_score REAL,
  is_required INTEGER NOT NULL DEFAULT 0,
  is_confirmed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS draft_checkpoints (
  draft_checkpoint_id TEXT PRIMARY KEY,
  workflow_draft_id TEXT REFERENCES workflow_drafts(workflow_draft_id) ON DELETE CASCADE,
  checkpoint_type TEXT NOT NULL,
  prompt_text TEXT NOT NULL,
  checkpoint_status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL,
  resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS object_threads (
  object_thread_id TEXT PRIMARY KEY,
  object_type TEXT NOT NULL,
  object_key TEXT NOT NULL,
  title TEXT,
  last_summary TEXT,
  last_active_at TEXT NOT NULL,
  UNIQUE(object_type, object_key)
);

CREATE TABLE IF NOT EXISTS object_thread_items (
  link_id INTEGER PRIMARY KEY AUTOINCREMENT,
  object_thread_id TEXT NOT NULL REFERENCES object_threads(object_thread_id) ON DELETE CASCADE,
  inbox_item_id TEXT NOT NULL REFERENCES inbox_items(inbox_item_id) ON DELETE CASCADE,
  link_role TEXT NOT NULL DEFAULT 'source',
  linked_at TEXT NOT NULL,
  UNIQUE(object_thread_id, inbox_item_id)
);

CREATE TABLE IF NOT EXISTS pending_associations (
  pending_association_id TEXT PRIMARY KEY,
  inbox_item_id TEXT REFERENCES inbox_items(inbox_item_id) ON DELETE CASCADE,
  target_type TEXT NOT NULL,
  target_key TEXT,
  association_status TEXT NOT NULL DEFAULT 'unresolved',
  reason_text TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS link_candidates (
  link_candidate_id TEXT PRIMARY KEY,
  inbox_item_id TEXT REFERENCES inbox_items(inbox_item_id) ON DELETE CASCADE,
  target_type TEXT NOT NULL,
  target_key TEXT NOT NULL,
  confidence_score REAL,
  candidate_reason TEXT,
  candidate_status TEXT NOT NULL DEFAULT 'provisional',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parties (
  party_id INTEGER PRIMARY KEY AUTOINCREMENT,
  party_name TEXT NOT NULL,
  party_role TEXT NOT NULL,
  contact_person TEXT,
  phone TEXT,
  address TEXT,
  invoice_title TEXT,
  tax_number TEXT,
  bank_info TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  notes TEXT,
  source TEXT NOT NULL DEFAULT 'local_first',
  UNIQUE(party_name, party_role)
);

CREATE TABLE IF NOT EXISTS process_providers (
  provider_id INTEGER PRIMARY KEY AUTOINCREMENT,
  party_id INTEGER REFERENCES parties(party_id),
  process_type TEXT NOT NULL,
  location_label TEXT,
  lead_time_days INTEGER,
  capacity_note TEXT,
  pricing_note TEXT,
  status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS products (
  product_id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_name TEXT NOT NULL,
  spec_text TEXT,
  category TEXT,
  default_unit TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  notes TEXT,
  source TEXT NOT NULL DEFAULT 'local_first',
  UNIQUE(product_name, spec_text)
);

CREATE TABLE IF NOT EXISTS product_variants (
  variant_id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER REFERENCES products(product_id),
  variant_code TEXT,
  variant_name TEXT,
  size_spec TEXT,
  color_spec TEXT,
  erp_sku TEXT,
  status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS materials (
  material_id INTEGER PRIMARY KEY AUTOINCREMENT,
  material_name TEXT NOT NULL,
  material_type TEXT NOT NULL,
  unit TEXT,
  default_supplier_party_id INTEGER REFERENCES parties(party_id),
  reference_price REAL,
  notes TEXT,
  UNIQUE(material_name, material_type)
);

CREATE TABLE IF NOT EXISTS process_templates (
  process_template_id INTEGER PRIMARY KEY AUTOINCREMENT,
  template_name TEXT NOT NULL,
  product_id INTEGER REFERENCES products(product_id),
  variant_id INTEGER REFERENCES product_variants(variant_id),
  template_scope TEXT NOT NULL DEFAULT 'product_default',
  status TEXT NOT NULL DEFAULT 'draft',
  notes TEXT
);

CREATE TABLE IF NOT EXISTS process_template_steps (
  template_step_id INTEGER PRIMARY KEY AUTOINCREMENT,
  process_template_id INTEGER REFERENCES process_templates(process_template_id) ON DELETE CASCADE,
  step_no INTEGER NOT NULL,
  step_type TEXT NOT NULL,
  is_required INTEGER NOT NULL DEFAULT 1,
  default_provider_party_id INTEGER REFERENCES parties(party_id),
  notes TEXT
);

CREATE TABLE IF NOT EXISTS samples (
  sample_id INTEGER PRIMARY KEY AUTOINCREMENT,
  legacy_record_id TEXT UNIQUE,
  sample_no TEXT,
  sample_date TEXT,
  customer_name TEXT,
  product_name TEXT,
  spec_text TEXT,
  sample_status TEXT,
  estimated_unit_price REAL,
  confirmed_unit_price REAL,
  raw_fields_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quotes (
  quote_id INTEGER PRIMARY KEY AUTOINCREMENT,
  quote_no TEXT,
  sample_id INTEGER REFERENCES samples(sample_id),
  quote_version TEXT,
  price_type TEXT,
  unit_price REAL,
  currency TEXT,
  min_qty REAL,
  valid_until TEXT,
  confirmed_at TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS bom_headers (
  bom_id INTEGER PRIMARY KEY AUTOINCREMENT,
  variant_id INTEGER REFERENCES product_variants(variant_id),
  bom_version TEXT,
  source_sample_id INTEGER REFERENCES samples(sample_id),
  status TEXT NOT NULL DEFAULT 'draft',
  confirmed_at TEXT
);

CREATE TABLE IF NOT EXISTS bom_items (
  bom_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
  legacy_record_id TEXT UNIQUE,
  bom_id INTEGER REFERENCES bom_headers(bom_id),
  product_name TEXT,
  planned_qty REAL,
  part_name TEXT,
  material_name TEXT,
  supplier_name TEXT,
  color_code TEXT,
  effective_width REAL,
  unit_consumption REAL,
  approved_material_qty REAL,
  unit_name TEXT,
  unit_price REAL,
  line_amount REAL,
  notes TEXT,
  raw_fields_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sales_orders (
  sales_order_id INTEGER PRIMARY KEY AUTOINCREMENT,
  legacy_record_id TEXT UNIQUE,
  order_no TEXT,
  order_date TEXT,
  order_type TEXT,
  customer_name TEXT,
  product_name TEXT,
  spec_text TEXT,
  qty REAL,
  unit TEXT,
  confirmed_unit_price REAL,
  confirmed_total_amount REAL,
  tax_unit_price REAL,
  tax_total_amount REAL,
  promised_delivery_date TEXT,
  order_status TEXT,
  deposit_ratio REAL,
  deposit_expected_amount REAL,
  deposit_received_amount REAL,
  received_amount REAL,
  outstanding_amount REAL,
  receipt_status TEXT,
  invoice_type TEXT,
  invoice_status TEXT,
  invoice_amount REAL,
  estimated_profit REAL,
  notes TEXT,
  current_step TEXT,
  current_factory TEXT,
  progress_text TEXT,
  processing_cost REAL,
  material_cost REAL,
  delivered_qty REAL,
  total_cost REAL,
  cut_pieces_sent_qty REAL,
  finished_goods_returned_qty REAL,
  raw_fields_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sales_order_items (
  sales_order_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
  sales_order_id INTEGER REFERENCES sales_orders(sales_order_id) ON DELETE CASCADE,
  variant_id INTEGER REFERENCES product_variants(variant_id),
  product_name_snapshot TEXT,
  spec_snapshot TEXT,
  qty_ordered REAL,
  unit TEXT,
  confirmed_unit_price REAL,
  confirmed_line_amount REAL,
  estimated_unit_cost REAL,
  standard_unit_cost REAL,
  actual_unit_cost REAL,
  line_status TEXT NOT NULL DEFAULT 'draft'
);

CREATE TABLE IF NOT EXISTS order_change_requests (
  change_request_id INTEGER PRIMARY KEY AUTOINCREMENT,
  sales_order_id INTEGER REFERENCES sales_orders(sales_order_id) ON DELETE CASCADE,
  change_type TEXT NOT NULL,
  requested_at TEXT NOT NULL,
  requested_by_party_id INTEGER REFERENCES parties(party_id),
  change_status TEXT NOT NULL DEFAULT 'pending_review',
  impact_summary TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS production_lots (
  production_lot_id INTEGER PRIMARY KEY AUTOINCREMENT,
  legacy_record_id TEXT UNIQUE,
  lot_no TEXT,
  production_date TEXT,
  factory_name TEXT,
  product_name TEXT,
  qty_total REAL,
  processing_cost REAL,
  cost_detail TEXT,
  status TEXT,
  notes TEXT,
  process_template_id INTEGER REFERENCES process_templates(process_template_id),
  raw_fields_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lot_process_plans (
  lot_process_plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
  production_lot_id INTEGER REFERENCES production_lots(production_lot_id) ON DELETE CASCADE,
  process_template_id INTEGER REFERENCES process_templates(process_template_id),
  plan_status TEXT NOT NULL DEFAULT 'draft',
  notes TEXT
);

CREATE TABLE IF NOT EXISTS production_lot_order_links (
  link_id INTEGER PRIMARY KEY AUTOINCREMENT,
  production_lot_id INTEGER NOT NULL REFERENCES production_lots(production_lot_id) ON DELETE CASCADE,
  sales_order_id INTEGER NOT NULL REFERENCES sales_orders(sales_order_id) ON DELETE CASCADE,
  relation_text TEXT,
  UNIQUE(production_lot_id, sales_order_id)
);

CREATE TABLE IF NOT EXISTS work_orders (
  work_order_id INTEGER PRIMARY KEY AUTOINCREMENT,
  work_order_no TEXT,
  work_type TEXT NOT NULL,
  source_object_type TEXT,
  source_object_id TEXT,
  provider_party_id INTEGER REFERENCES parties(party_id),
  work_status TEXT NOT NULL DEFAULT 'draft',
  planned_qty REAL,
  completed_qty REAL,
  planned_due_at TEXT,
  actual_finished_at TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS work_order_links (
  link_id INTEGER PRIMARY KEY AUTOINCREMENT,
  work_order_id INTEGER REFERENCES work_orders(work_order_id) ON DELETE CASCADE,
  sales_order_item_id INTEGER REFERENCES sales_order_items(sales_order_item_id) ON DELETE CASCADE,
  qty_allocated REAL
);

CREATE TABLE IF NOT EXISTS business_events (
  business_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  event_time TEXT NOT NULL,
  source_channel TEXT,
  source_actor TEXT,
  event_status TEXT NOT NULL DEFAULT 'unresolved',
  raw_content TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS shipments (
  shipment_id INTEGER PRIMARY KEY AUTOINCREMENT,
  legacy_record_id TEXT UNIQUE,
  shipment_date TEXT,
  shipment_type TEXT,
  factory_name TEXT,
  cut_detail TEXT,
  cut_qty REAL,
  finished_qty REAL,
  shipment_status TEXT,
  notes TEXT,
  raw_fields_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shipment_order_links (
  link_id INTEGER PRIMARY KEY AUTOINCREMENT,
  shipment_id INTEGER NOT NULL REFERENCES shipments(shipment_id) ON DELETE CASCADE,
  sales_order_id INTEGER NOT NULL REFERENCES sales_orders(sales_order_id) ON DELETE CASCADE,
  relation_text TEXT,
  UNIQUE(shipment_id, sales_order_id)
);

CREATE TABLE IF NOT EXISTS warehouse_receipts (
  warehouse_receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
  receipt_no TEXT,
  sales_order_id INTEGER REFERENCES sales_orders(sales_order_id),
  production_lot_id INTEGER REFERENCES production_lots(production_lot_id),
  warehouse_party_id INTEGER REFERENCES parties(party_id),
  erp_receipt_no TEXT,
  receipt_status TEXT NOT NULL DEFAULT 'draft',
  qty_planned REAL,
  qty_confirmed REAL,
  arrived_at TEXT,
  confirmed_at TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS return_cases (
  return_case_id INTEGER PRIMARY KEY AUTOINCREMENT,
  sales_order_id INTEGER REFERENCES sales_orders(sales_order_id),
  case_type TEXT NOT NULL,
  opened_at TEXT NOT NULL,
  opened_by_party_id INTEGER REFERENCES parties(party_id),
  reason_text TEXT,
  case_status TEXT NOT NULL DEFAULT 'open',
  refund_expected_amount REAL,
  supplier_deduction_expected_amount REAL,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS stock_items (
  stock_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
  stock_type TEXT NOT NULL,
  material_id INTEGER REFERENCES materials(material_id),
  variant_id INTEGER REFERENCES product_variants(variant_id),
  stock_name_snapshot TEXT
);

CREATE TABLE IF NOT EXISTS stock_movements (
  stock_movement_id INTEGER PRIMARY KEY AUTOINCREMENT,
  stock_item_id INTEGER REFERENCES stock_items(stock_item_id),
  warehouse_party_id INTEGER REFERENCES parties(party_id),
  movement_type TEXT NOT NULL,
  qty REAL NOT NULL,
  source_type TEXT,
  source_id TEXT,
  movement_date TEXT NOT NULL,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS receivables (
  receivable_id INTEGER PRIMARY KEY AUTOINCREMENT,
  receivable_no TEXT,
  sales_order_id INTEGER REFERENCES sales_orders(sales_order_id),
  receivable_type TEXT NOT NULL,
  due_date TEXT,
  amount_due REAL,
  amount_received REAL DEFAULT 0,
  receivable_status TEXT NOT NULL DEFAULT 'pending',
  trigger_object_type TEXT,
  trigger_object_id TEXT,
  collection_mode TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS supplier_settlements (
  supplier_settlement_id INTEGER PRIMARY KEY AUTOINCREMENT,
  legacy_record_id TEXT UNIQUE,
  settlement_date TEXT,
  supplier_name TEXT,
  lot_relation_text TEXT,
  order_relation_text TEXT,
  product_name TEXT,
  qty REAL,
  unit_price REAL,
  amount REAL,
  payable_type TEXT,
  settlement_status TEXT,
  payment_status TEXT,
  process_name TEXT,
  notes TEXT,
  voucher_text TEXT,
  raw_fields_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payables (
  payable_id INTEGER PRIMARY KEY AUTOINCREMENT,
  payable_no TEXT,
  party_id INTEGER REFERENCES parties(party_id),
  sales_order_id INTEGER REFERENCES sales_orders(sales_order_id),
  production_lot_id INTEGER REFERENCES production_lots(production_lot_id),
  work_order_id INTEGER REFERENCES work_orders(work_order_id),
  payable_type TEXT NOT NULL,
  due_date TEXT,
  amount_due REAL,
  amount_paid REAL DEFAULT 0,
  payable_status TEXT NOT NULL DEFAULT 'pending',
  trigger_object_type TEXT,
  trigger_object_id TEXT,
  billing_mode TEXT,
  statement_cycle TEXT,
  source_attachment_id TEXT REFERENCES evidence_assets(evidence_asset_id),
  notes TEXT
);

CREATE TABLE IF NOT EXISTS supplier_settlement_order_links (
  link_id INTEGER PRIMARY KEY AUTOINCREMENT,
  supplier_settlement_id INTEGER NOT NULL REFERENCES supplier_settlements(supplier_settlement_id) ON DELETE CASCADE,
  sales_order_id INTEGER NOT NULL REFERENCES sales_orders(sales_order_id) ON DELETE CASCADE,
  relation_text TEXT,
  UNIQUE(supplier_settlement_id, sales_order_id)
);

CREATE TABLE IF NOT EXISTS cash_transactions (
  cash_transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
  legacy_record_id TEXT UNIQUE,
  transaction_date TEXT,
  direction TEXT,
  counterparty_name TEXT,
  amount REAL,
  purpose TEXT,
  payment_method TEXT,
  is_marked_paid TEXT,
  expected_payment_date TEXT,
  notes TEXT,
  raw_fields_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cash_transaction_order_links (
  link_id INTEGER PRIMARY KEY AUTOINCREMENT,
  cash_transaction_id INTEGER NOT NULL REFERENCES cash_transactions(cash_transaction_id) ON DELETE CASCADE,
  sales_order_id INTEGER NOT NULL REFERENCES sales_orders(sales_order_id) ON DELETE CASCADE,
  relation_text TEXT,
  UNIQUE(cash_transaction_id, sales_order_id)
);

CREATE TABLE IF NOT EXISTS settlement_allocations (
  allocation_id INTEGER PRIMARY KEY AUTOINCREMENT,
  cash_transaction_id INTEGER REFERENCES cash_transactions(cash_transaction_id),
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  allocated_amount REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS invoices (
  invoice_id INTEGER PRIMARY KEY AUTOINCREMENT,
  invoice_no TEXT,
  invoice_kind TEXT NOT NULL,
  sales_order_id INTEGER REFERENCES sales_orders(sales_order_id),
  party_id INTEGER REFERENCES parties(party_id),
  invoice_type TEXT,
  invoice_amount REAL,
  issued_date TEXT,
  invoice_status TEXT NOT NULL DEFAULT 'pending',
  attachment_id TEXT REFERENCES evidence_assets(evidence_asset_id),
  notes TEXT
);

CREATE TABLE IF NOT EXISTS refunds (
  refund_id INTEGER PRIMARY KEY AUTOINCREMENT,
  return_case_id INTEGER REFERENCES return_cases(return_case_id),
  sales_order_id INTEGER REFERENCES sales_orders(sales_order_id),
  refund_amount REAL,
  refund_status TEXT NOT NULL DEFAULT 'pending',
  notes TEXT
);

CREATE TABLE IF NOT EXISTS supplier_deductions (
  supplier_deduction_id INTEGER PRIMARY KEY AUTOINCREMENT,
  party_id INTEGER REFERENCES parties(party_id),
  return_case_id INTEGER REFERENCES return_cases(return_case_id),
  work_order_id INTEGER REFERENCES work_orders(work_order_id),
  deduction_amount REAL,
  deduction_reason TEXT,
  deduction_status TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS commitment_items (
  commitment_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
  object_type TEXT NOT NULL,
  object_id TEXT NOT NULL,
  commitment_type TEXT NOT NULL,
  due_at TEXT,
  commitment_status TEXT NOT NULL DEFAULT 'open',
  owner_party_id INTEGER REFERENCES parties(party_id),
  notes TEXT
);

CREATE TABLE IF NOT EXISTS followup_items (
  followup_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
  object_type TEXT NOT NULL,
  object_id TEXT NOT NULL,
  followup_type TEXT NOT NULL,
  followup_status TEXT NOT NULL DEFAULT 'open',
  due_at TEXT,
  priority TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS exception_cases (
  exception_case_id INTEGER PRIMARY KEY AUTOINCREMENT,
  object_type TEXT NOT NULL,
  object_id TEXT NOT NULL,
  exception_type TEXT NOT NULL,
  severity TEXT,
  exception_status TEXT NOT NULL DEFAULT 'open',
  notes TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
  alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_type TEXT NOT NULL,
  object_type TEXT,
  object_id TEXT,
  alert_status TEXT NOT NULL DEFAULT 'open',
  alert_text TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_reports (
  daily_report_id INTEGER PRIMARY KEY AUTOINCREMENT,
  report_date TEXT NOT NULL UNIQUE,
  report_body TEXT NOT NULL,
  report_json TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbound_tasks (
  outbound_task_id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_type TEXT NOT NULL,
  target_channel TEXT,
  target_identity TEXT,
  send_policy TEXT NOT NULL DEFAULT 'draft_only',
  task_status TEXT NOT NULL DEFAULT 'draft',
  message_subject TEXT,
  message_body TEXT,
  created_at TEXT NOT NULL,
  sent_at TEXT
);

CREATE TABLE IF NOT EXISTS fulfillment_plans (
  fulfillment_plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
  sales_order_id INTEGER REFERENCES sales_orders(sales_order_id),
  plan_type TEXT NOT NULL,
  plan_status TEXT NOT NULL DEFAULT 'draft',
  target_system TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS fulfillment_plan_lines (
  plan_line_id INTEGER PRIMARY KEY AUTOINCREMENT,
  fulfillment_plan_id INTEGER REFERENCES fulfillment_plans(fulfillment_plan_id) ON DELETE CASCADE,
  sales_order_item_id INTEGER REFERENCES sales_order_items(sales_order_item_id),
  qty_planned REAL,
  line_action TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS external_system_connections (
  connection_id INTEGER PRIMARY KEY AUTOINCREMENT,
  system_name TEXT NOT NULL,
  connection_status TEXT NOT NULL DEFAULT 'inactive',
  config_json TEXT
);

CREATE TABLE IF NOT EXISTS external_sync_jobs (
  sync_job_id INTEGER PRIMARY KEY AUTOINCREMENT,
  connection_id INTEGER REFERENCES external_system_connections(connection_id),
  sync_type TEXT NOT NULL,
  job_status TEXT NOT NULL DEFAULT 'pending',
  started_at TEXT,
  finished_at TEXT,
  result_json TEXT
);

CREATE TABLE IF NOT EXISTS external_inventory_snapshots (
  snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
  connection_id INTEGER REFERENCES external_system_connections(connection_id),
  variant_id INTEGER REFERENCES product_variants(variant_id),
  warehouse_code TEXT,
  available_qty REAL,
  reserved_qty REAL,
  inbound_qty REAL,
  snapshot_time TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
  audit_log_id INTEGER PRIMARY KEY AUTOINCREMENT,
  object_type TEXT NOT NULL,
  object_id TEXT NOT NULL,
  action_type TEXT NOT NULL,
  actor_label TEXT,
  old_value_json TEXT,
  new_value_json TEXT,
  reason_text TEXT,
  created_at TEXT NOT NULL
);

CREATE VIEW IF NOT EXISTS v_order_production_status AS
SELECT
  so.sales_order_id,
  so.order_no,
  so.customer_name,
  so.product_name,
  so.spec_text,
  so.qty,
  so.promised_delivery_date,
  so.order_status,
  so.current_factory,
  so.current_step,
  so.progress_text,
  COUNT(DISTINCT pl.production_lot_id) AS linked_lot_count,
  GROUP_CONCAT(DISTINCT pl.lot_no) AS linked_lots
FROM sales_orders so
LEFT JOIN production_lot_order_links link ON link.sales_order_id = so.sales_order_id
LEFT JOIN production_lots pl ON pl.production_lot_id = link.production_lot_id
GROUP BY so.sales_order_id;

DROP VIEW IF EXISTS v_cash_forecast;
DROP VIEW IF EXISTS v_order_profit_snapshot;
DROP VIEW IF EXISTS v_order_finance_status;

CREATE VIEW IF NOT EXISTS v_order_finance_status AS
WITH payable_rollup AS (
  SELECT
    source.sales_order_id,
    SUM(COALESCE(source.amount, 0)) AS total_payable_amount
  FROM (
    SELECT
      sales_order_id,
      amount_due AS amount
    FROM payables
    WHERE sales_order_id IS NOT NULL
    UNION ALL
    SELECT
      link.sales_order_id,
      ss.amount AS amount
    FROM supplier_settlement_order_links link
    JOIN supplier_settlements ss ON ss.supplier_settlement_id = link.supplier_settlement_id
    UNION ALL
    SELECT
      sales_order_id,
      refund_amount AS amount
    FROM refunds
    WHERE sales_order_id IS NOT NULL
  ) source
  GROUP BY source.sales_order_id
),
cash_in AS (
  SELECT
    source.sales_order_id,
    SUM(COALESCE(source.amount, 0)) AS total_cash_in
  FROM (
    SELECT
      r.sales_order_id,
      sa.allocated_amount AS amount
    FROM settlement_allocations sa
    JOIN receivables r ON r.receivable_id = CAST(sa.target_id AS INTEGER)
    WHERE sa.target_type = 'receivable'
      AND r.sales_order_id IS NOT NULL
    UNION ALL
    SELECT
      link.sales_order_id,
      ct.amount AS amount
    FROM cash_transaction_order_links link
    JOIN cash_transactions ct ON ct.cash_transaction_id = link.cash_transaction_id
    WHERE ct.direction = '收款'
      AND NOT EXISTS (
        SELECT 1
        FROM settlement_allocations sa
        WHERE sa.cash_transaction_id = ct.cash_transaction_id
      )
  ) source
  GROUP BY source.sales_order_id
),
cash_out AS (
  SELECT
    source.sales_order_id,
    SUM(COALESCE(source.amount, 0)) AS total_cash_out
  FROM (
    SELECT
      p.sales_order_id,
      sa.allocated_amount AS amount
    FROM settlement_allocations sa
    JOIN payables p ON p.payable_id = CAST(sa.target_id AS INTEGER)
    WHERE sa.target_type = 'payable'
      AND p.sales_order_id IS NOT NULL
    UNION ALL
    SELECT
      r.sales_order_id,
      sa.allocated_amount AS amount
    FROM settlement_allocations sa
    JOIN refunds r ON r.refund_id = CAST(sa.target_id AS INTEGER)
    WHERE sa.target_type = 'refund'
      AND r.sales_order_id IS NOT NULL
    UNION ALL
    SELECT
      link.sales_order_id,
      ct.amount AS amount
    FROM cash_transaction_order_links link
    JOIN cash_transactions ct ON ct.cash_transaction_id = link.cash_transaction_id
    WHERE ct.direction = '付款'
      AND NOT EXISTS (
        SELECT 1
        FROM settlement_allocations sa
        WHERE sa.cash_transaction_id = ct.cash_transaction_id
      )
  ) source
  GROUP BY source.sales_order_id
)
SELECT
  so.sales_order_id,
  so.order_no,
  so.customer_name,
  so.product_name,
  so.confirmed_total_amount,
  so.deposit_expected_amount,
  so.deposit_received_amount,
  so.received_amount,
  so.outstanding_amount,
  so.invoice_status,
  so.invoice_amount,
  COALESCE(payable_rollup.total_payable_amount, 0) AS payable_amount,
  COALESCE(cash_in.total_cash_in, 0) AS cash_in_amount,
  COALESCE(cash_out.total_cash_out, 0) AS cash_out_amount
FROM sales_orders so
LEFT JOIN payable_rollup ON payable_rollup.sales_order_id = so.sales_order_id
LEFT JOIN cash_in ON cash_in.sales_order_id = so.sales_order_id
LEFT JOIN cash_out ON cash_out.sales_order_id = so.sales_order_id;

CREATE VIEW IF NOT EXISTS v_order_profit_snapshot AS
WITH cost_rollup AS (
  SELECT
    source.sales_order_id,
    SUM(COALESCE(source.amount, 0)) AS total_cost_payable_amount
  FROM (
    SELECT
      sales_order_id,
      amount_due AS amount
    FROM payables
    WHERE sales_order_id IS NOT NULL
    UNION ALL
    SELECT
      link.sales_order_id,
      ss.amount AS amount
    FROM supplier_settlement_order_links link
    JOIN supplier_settlements ss ON ss.supplier_settlement_id = link.supplier_settlement_id
  ) source
  GROUP BY source.sales_order_id
),
refund_rollup AS (
  SELECT
    sales_order_id,
    SUM(COALESCE(refund_amount, 0)) AS total_refund_amount
  FROM refunds
  WHERE sales_order_id IS NOT NULL
  GROUP BY sales_order_id
)
SELECT
  so.sales_order_id,
  so.order_no,
  so.customer_name,
  so.product_name,
  so.confirmed_total_amount,
  so.material_cost,
  so.processing_cost,
  so.total_cost,
  COALESCE(cost_rollup.total_cost_payable_amount, 0) + COALESCE(refund_rollup.total_refund_amount, 0) AS payable_amount,
  CASE
    WHEN so.confirmed_total_amount IS NULL THEN NULL
    ELSE so.confirmed_total_amount
      - COALESCE(refund_rollup.total_refund_amount, 0)
      - COALESCE(
      NULLIF(so.total_cost, 0),
      CASE
        WHEN NULLIF(so.material_cost, 0) IS NOT NULL OR NULLIF(so.processing_cost, 0) IS NOT NULL
          THEN COALESCE(NULLIF(so.material_cost, 0), 0) + COALESCE(NULLIF(so.processing_cost, 0), 0)
      END,
      cost_rollup.total_cost_payable_amount,
      0
    )
  END AS estimated_gross_profit
FROM sales_orders so
LEFT JOIN cost_rollup ON cost_rollup.sales_order_id = so.sales_order_id
LEFT JOIN refund_rollup ON refund_rollup.sales_order_id = so.sales_order_id;

CREATE VIEW IF NOT EXISTS v_cash_forecast AS
WITH receivable_open AS (
  SELECT
    sales_order_id,
    SUM(COALESCE(amount_due, 0) - COALESCE(amount_received, 0)) AS open_receivable_amount
  FROM receivables
  WHERE sales_order_id IS NOT NULL
    AND COALESCE(receivable_status, 'pending') NOT IN ('received', 'closed')
  GROUP BY sales_order_id
)
SELECT
  so.sales_order_id,
  so.order_no,
  so.customer_name,
  so.product_name,
  CASE
    WHEN so.confirmed_total_amount IS NULL THEN COALESCE(receivable_open.open_receivable_amount, 0)
    WHEN (COALESCE(so.confirmed_total_amount, 0) - COALESCE(fin.cash_in_amount, 0)) > COALESCE(receivable_open.open_receivable_amount, 0)
      THEN COALESCE(so.confirmed_total_amount, 0) - COALESCE(fin.cash_in_amount, 0)
    ELSE COALESCE(receivable_open.open_receivable_amount, 0)
  END AS expected_cash_in,
  COALESCE(fin.payable_amount, 0) - COALESCE(fin.cash_out_amount, 0) AS expected_cash_out,
  so.promised_delivery_date AS expected_collection_or_delivery_date
FROM sales_orders so
LEFT JOIN v_order_finance_status fin ON fin.sales_order_id = so.sales_order_id
LEFT JOIN receivable_open ON receivable_open.sales_order_id = so.sales_order_id;

CREATE VIEW IF NOT EXISTS v_factory_load AS
SELECT
  factory_name,
  COUNT(*) AS lot_count,
  SUM(COALESCE(qty_total, 0)) AS qty_total,
  GROUP_CONCAT(lot_no) AS lot_list
FROM production_lots
WHERE factory_name IS NOT NULL AND factory_name != ''
GROUP BY factory_name;

CREATE VIEW IF NOT EXISTS v_pending_associations AS
SELECT
  pending_association_id,
  inbox_item_id,
  target_type,
  target_key,
  association_status,
  reason_text,
  created_at
FROM pending_associations
WHERE association_status != 'confirmed';

CREATE VIEW IF NOT EXISTS v_work_due_today AS
SELECT
  work_order_id,
  work_order_no,
  work_type,
  work_status,
  planned_due_at,
  planned_qty,
  completed_qty,
  notes
FROM work_orders
WHERE planned_due_at IS NOT NULL
  AND work_status NOT IN ('done', 'cancelled');

CREATE VIEW IF NOT EXISTS v_open_followups AS
SELECT
  followup_item_id,
  object_type,
  object_id,
  followup_type,
  due_at,
  priority,
  notes
FROM followup_items
WHERE followup_status = 'open';

CREATE VIEW IF NOT EXISTS v_open_exceptions AS
SELECT
  exception_case_id,
  object_type,
  object_id,
  exception_type,
  severity,
  notes,
  created_at
FROM exception_cases
WHERE exception_status = 'open';

CREATE VIEW IF NOT EXISTS v_open_alerts AS
SELECT
  alert_id,
  alert_type,
  object_type,
  object_id,
  alert_text,
  created_at
FROM alerts
WHERE alert_status = 'open';
