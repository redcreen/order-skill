# 2026-04-21 Order Doc Tree Restructure

## Context

The repository had already converged the `order` runtime and plugin packaging, but order-specific durable docs were still scattered across root `docs/`, `docs/reference/openclaw-skills/`, and `docs/devlog/`.

That created the same problem health had before:

- the order module did not own its own durable documentation tree
- root docs still mixed order-only material with workspace-level material

## Decision

Move order-only durable docs into:

```text
order/docs/
  README*
  architecture*
  roadmap*
  test-plan*
  install/
  reference/
  devlog/
```

Keep root `docs/` for workspace-wide material only.

## Changes

1. Moved order design reference, plugin install docs, and order devlogs under `order/docs/`.
2. Added `order/docs/README*`, `roadmap*`, and `test-plan*`.
3. Repointed root README, root docs, order README, and plugin README to the new order-local doc tree.
4. Updated project-assistant control files so the order module now treats `order/docs/` as its durable doc home.

## Validation

- order docs are now discoverable from one index under `order/docs/README*`
- root docs now route to the local order doc home instead of the old scattered reference files
- old order-only root doc paths were removed from repo-facing links

## Result

`order/` now owns both its runtime tree and its durable doc tree. The root workspace docs remain available, but order-specific architecture, install, reference, and devlog material no longer lives in the shared doc surface.

