[English](overview.md) | [中文](overview.zh-CN.md)

# Order Documentation

## Purpose

This document set defines the single maintained design direction for the `order` system.

It replaces the earlier scattered order design notes with one consistent documentation set.

## Read In This Order

1. [Order Architecture](../architecture.md)
2. [Order Data Model](data-model.md)
3. [Order Operating Model](operating-model.md)

## Current Direction

`order` should now be understood as:

- a local-first order operations system
- with AI acting as the understanding, linking, follow-up, and guidance layer
- and local structured storage acting as the source of truth
- with plugin-first distribution bound to one explicit agent instead of global default installation

## Topic Coverage Map

The following topics from the current design discussion are intentionally preserved in the new document set:

| Topic | Main Location |
| --- | --- |
| persist all input before anything else | [Order Architecture](../architecture.md), [Order Operating Model](operating-model.md) |
| natural-language input instead of rigid templates | [Order Operating Model](operating-model.md) |
| draft -> confirm -> commit | [Order Operating Model](operating-model.md) |
| out-of-order input, backfill, and delayed linking | [Order Architecture](../architecture.md), [Order Operating Model](operating-model.md) |
| many-to-many payment allocation | [Order Data Model](data-model.md) |
| process templates and per-lot process plans | [Order Architecture](../architecture.md), [Order Data Model](data-model.md), [Order Operating Model](operating-model.md) |
| work, rework, repair, return, and replenishment as first-class entities | [Order Architecture](../architecture.md), [Order Data Model](data-model.md) |
| proactive follow-up, commitments, exceptions, reminders | [Order Architecture](../architecture.md), [Order Operating Model](operating-model.md) |
| daily report and next actions | [Order Operating Model](operating-model.md) |
| outbound communication and controlled automation | [Order Architecture](../architecture.md), [Order Operating Model](operating-model.md) |
| JuShuiTan / warehouse / inventory-sync reserve | [Order Architecture](../architecture.md), [Order Data Model](data-model.md) |

## Current Scope

The current order design covers:

- sample
- quote
- sales order
- production
- shipment
- receivable / payable
- cash movement
- invoice
- receipt / inventory-sync preparation
- follow-up, reminders, and daily reporting
