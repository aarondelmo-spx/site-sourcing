# gstack

Use the `/browse` skill from gstack for all web browsing. Never use `mcp__claude-in-chrome__*` tools directly.

Available gstack skills:
- `/office-hours` — YC-style forcing questions to stress-test ideas
- `/plan-ceo-review` — CEO/founder-mode plan review
- `/plan-eng-review` — Eng manager-mode plan review
- `/plan-design-review` — Designer's eye plan review
- `/design-consultation` — Full design system proposal
- `/design-shotgun` — Generate multiple design variants for comparison
- `/design-html` — Production-quality HTML/CSS finalization
- `/review` — Pre-landing PR review
- `/ship` — Ship workflow: tests, VERSION bump, PR creation
- `/land-and-deploy` — Merge PR, wait for CI/deploy, verify production
- `/canary` — Post-deploy canary monitoring
- `/benchmark` — Performance regression detection
- `/browse` — Fast headless browser for QA and dogfooding
- `/connect-chrome` — Connect to your real Chrome browser
- `/qa` — Systematic QA testing with bug fixes
- `/qa-only` — Report-only QA testing
- `/design-review` — Designer's eye visual QA
- `/setup-browser-cookies` — Import cookies from real Chromium into headless session
- `/setup-deploy` — Configure deployment settings
- `/retro` — Weekly engineering retrospective
- `/investigate` — Systematic root cause debugging
- `/document-release` — Post-ship documentation update
- `/codex` — (see gstack docs)
- `/cso` — Chief Security Officer infrastructure audit
- `/autoplan` — Auto-review pipeline (CEO + design + eng + DX)
- `/plan-devex-review` — Developer experience plan review
- `/devex-review` — Live developer experience audit
- `/careful` — Safety guardrails for destructive commands
- `/freeze` — Restrict edits to a specific directory
- `/guard` — Full safety mode (careful + freeze combined)
- `/unfreeze` — Clear the freeze boundary
- `/gstack-upgrade` — Upgrade gstack to the latest version
- `/learn` — Manage and review project learnings across sessions

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill
tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.
The skill has specialized workflows that produce better results than ad-hoc answers.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Design system, brand → invoke design-consultation
- Visual audit, design polish → invoke design-review
- Architecture review → invoke plan-eng-review
- Save progress, checkpoint, resume → invoke checkpoint
- Code quality, health check → invoke health
