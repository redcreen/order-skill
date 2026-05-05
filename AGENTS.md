# Project Assistant Loop Front Door

When a user message arrives in this repository, treat it as entering the project-assistant loop before substantial work.

If local project-assistant scripts are unavailable, use the global project-assistant skill scripts from `$CODEX_HOME/skills/project-assistant/scripts`.

Keep the project control surface fresh:

- `.codex/brief.md`
- `.codex/status.md`
- `.codex/plan.md`
- `.codex/module-dashboard.md`
- `.codex/modules/order.md`

Before declaring delivery complete, run the smallest relevant verification gate and record the result in `.codex/status.md`.
