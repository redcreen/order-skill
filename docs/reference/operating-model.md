[English](operating-model.md) | [中文](operating-model.zh-CN.md)

# Order Operating Model

## What This Document Answers

This document defines how the agent should behave after the `order` plugin is installed and bound to one target agent.

It focuses on runtime behavior, not schema shape.

## Immediate Installed Behavior

After plugin installation and explicit target-agent binding, the agent should already know:

- it owns the order fulfillment domain
- all input is persisted first
- unrelated content does not enter the formal business thread
- natural-language input does not write directly into formal business tables
- all formal recording must follow draft, confirm, then commit

## Install Scope And Hard Execution

`order` should not be installed globally by default.

The contract is:

1. the plugin must be bound to one explicit target agent
2. runtime execution stays unavailable until that binding exists
3. order actions must call real scripts
4. execution can be reported as done only after the script succeeds

## Input Handling

### Step 1: Persist everything first

All user input should be persisted first.

This protects continuity across:

- crashes
- restarts
- `/new`
- context loss

### Step 2: Decide whether it enters the order business thread

If the input is order-related, it enters the order thread.

If not, it stays only in the raw persisted input layer.

### Step 3: Guided natural-language intake

The agent should:

1. accept natural language
2. extract known fields
3. detect the next critical missing gap
4. ask only for that gap
5. build a draft

### Step 4: Draft, confirm, commit

Formal writes must always move through:

1. draft
2. confirmation
3. commit

## Out-of-Order and Backfill Handling

This is normal behavior, not an edge case.

The system should:

1. receive the fact
2. stage it safely
3. keep unresolved links when necessary
4. resolve links later
5. commit only after confirmation

## Process-Template Guidance

Different products and lots may follow different routes.

So the agent should:

1. check for a default process template
2. confirm whether the lot follows it
3. if needed, guide the user through the actual lot route

All later prompts, reminders, and reports should use the confirmed lot plan.

## Query Behavior

Users should not need to ask in structured query language.

The system should:

1. infer the likely query target
2. apply a sensible default scope
3. ask one clarifying question only if needed
4. return the answer

## Proactive Follow-Up

The system should actively track:

- promised delivery times
- promised shipment times
- promised return times
- promised payment times
- promised confirmations

When commitments are overdue, the system should:

1. mark risk
2. create follow-up work
3. suggest the next action
4. generate communication drafts if needed

## Daily Report

The daily report is a required capability.

It should stay:

- concise
- clear
- actionable

It should include:

1. daily overview
2. key changes
3. risks and blockers
4. cash view
5. next actions

## Outbound Sending Policy

Outbound communication should support three levels:

1. draft only
2. confirm then send
3. explicitly authorized low-risk auto-send

## Recovery After `/new` or Restart

The system should recover:

- relevant object threads
- unfinished drafts
- pending confirmations
- follow-up items
- exception items

It should not depend on replaying the whole chat transcript.
