[English](architecture.md) | [中文](architecture.zh-CN.md)

# Order Architecture

## What This Document Answers

This document explains the stable system shape of the future `order` system:

- what it is
- where its boundary is
- why it cannot continue as a table-first or chat-only design

## System Goal

`order` should not be a better chat-based entry tool.

It should be a local-first order operations system for messy real-world manufacturing and fulfillment work.

## Core Boundary

Inside the system:

- sample
- quote
- sales order
- production lot
- process route and work
- shipment / return shipment
- receivable / payable
- cash movement
- invoice
- receipt / inventory-sync preparation
- follow-up, reminders, reports, and exceptions

Outside the system:

- broad finance
- general ledger
- budgeting and tax
- full ERP ownership
- full warehouse execution ownership

## Three Recording Layers

1. raw persisted input
2. candidate interpretation
3. confirmed business truth

All input is persisted first.

Only confirmed facts enter the formal business layer.

## Source of Truth

Formal truth should live in:

- local `SQLite`
- local file-system archives

Chat context and external systems are not the final source of truth.

## OpenClaw Injection Model

`order` should stay on the wrapper path instead of using an ingress short-circuit.

Current host-side contract:

- `before_prompt_build` injects the order wrapper contract into the real session
- normal agent flow continues on the same transcript
- `before_tool_call` blocks direct execution of `order/scripts/order_runtime_api.py` and lower-level bundled `order/scripts/*.py`
- runtime actions must go through `plugins/openclaw-order/scripts/order_hard_execute.py`
- the wrapper calls only `order/scripts/order_runtime_api.py`, so OpenClaw does not own business command routing

This keeps continuity inside the real session while still preventing bypass of the hard-execution wrapper.

## AI Role

AI should act as the understanding and linking layer:

- classify input
- extract candidate facts
- propose links
- ask for missing fields
- generate confirmation previews
- surface risks

AI should not become the source of truth itself.

## Object-Thread Continuity

This is not a pure chat system, so continuity must not depend only on the current conversation window.

Continuity should be maintained through object threads such as:

- order thread
- sample thread
- lot thread
- shipment thread
- return / repair thread

## Work Graph

The business is not linear.

It includes:

- add-on
- reduction
- cancellation
- replenishment purchase
- rework
- repair
- return
- partial return
- partial delivery

So the system must model work and dependency, not only order state.

## Process Templates

Different products and lots may follow different routes.

So the system must support:

- default process templates per product
- actual per-lot process plans

## Built-In Follow-Up

The system should not only store status.

It should also track:

- commitments
- follow-up items
- exception cases
- alerts

## Controlled Outbound Behavior

Future outbound communication may support:

- draft only
- confirm then send
- authorized low-risk auto-send

But it must always remain policy-controlled.

## Documentation Ownership

- `docs/*` owns order-skill durable docs
- `plugins/openclaw-order/*` owns plugin distribution docs
- `order/*` owns the runtime core and runtime usage docs
