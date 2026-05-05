---
name: order
description: Installable umbrella skill for the local-first order operations runtime. Use when the user wants one entry point that can receive fragmented order-related input, persist it first, guide natural-language intake, and gradually form formal operational records around sample, quote, order, production, shipment, settlement, follow-up, and reporting.
---

# Order Suite

## Overview

This skill is the suite-level entry for the `order/` module.

It gives the agent one install target for the local-first order operations runtime while keeping the truth model grounded in local storage instead of chat context or remote tables.

## Use This Skill When

- the user installs the full `order` capability from one GitHub URL
- the user sends likely order-related, production-related, shipment-related, or settlement-related input in fragmented natural language
- the user expects the system to persist everything first and normalize it safely later
- the user expects draft, confirm, then commit behavior for formal recording
- the user wants the system to support follow-up, reminders, and daily reporting later on the same local runtime

## Working Contract

- default external data root: `~/Documents/openclaw-order`
- local `SQLite` plus local file archives remain the source of truth
- external adapters should call `order/scripts/order_runtime_api.py` instead of reimplementing order behavior
- all inbound input must be persisted first
- natural-language input must not write directly into formal business tables
- formal business writes must follow draft -> confirm -> commit
- non-order content must not enter the formal order business thread

## Default Operating Mode

This suite should behave like an order operations agent, not like a passive recorder.

Default behavior:

1. If the user sends likely order-related input, persist it first.
2. Decide whether the input belongs in the formal order business thread.
3. If yes, extract the known facts and identify only the next critical missing gap.
4. Keep the input in draft state until confirmation.
5. Commit into formal business records only after explicit confirmation.
6. Keep pending associations explicit until resolved.
7. Generate follow-up, alert, and daily-report outputs from formal records instead of chat summaries.

## Non-Goals

- acting as a generic chat assistant
- writing formal order data directly from raw natural-language turns
- treating remote table systems as the source of truth
- absorbing broad standalone finance into the order runtime
