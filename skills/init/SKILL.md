---
name: init
description: Set up the current repository for the workflow, new or existing - config, CLAUDE.md rules, project settings, security checks, Graphify, Playwright and the bus template. Existing projects start in warn mode with baselines.
argument-hint: "[--enforce] [--no-bus]"
disable-model-invocation: true
---

# Set up the workflow in this repository

Work on a branch named `workflow-setup`. Never overwrite a file the user wrote: merge,
or show the difference and ask. Templates are in `${CLAUDE_PLUGIN_ROOT}/templates/`.

1. Detect:
   - existing project: more than 20 commits, or application code beyond scaffolding;
     otherwise new;
   - stack: `next` in package.json -> nextjs; Cargo.toml -> rust (both can apply);
   - source root: `src/` if it exists, otherwise the repo root.
2. Tools: check and report what is missing, without installing anything unasked:
   git, python3 (runs the hooks), uv, node (web stacks), graphify, gitleaks, osv-scanner, zizmor,
   cargo-deny (rust), and `claude --version` 2.1.222 or later.
3. Config: write `.flow/config.json` from `templates/config.json`. Mode `enforce` for a
   new project or with `--enforce`; `warn` for an existing one. Adjust `hot_files`,
   `shared` and `aliases` (from tsconfig `paths`) to the real layout, and make each
   budget's `stop_checks`, `baseline_check` and `integration_checks` use the project's
   real test commands (Rust: `cargo test --quiet`). Set `ux_audit.base_url` to the dev
   server. Ask once, in one AskUserQuestion, whether clarification should be deep by
   default (`deep_clarification`) and whether to set a global cost budget per day
   (`orchestration.cost_budget_usd`, null for none). Add `.flow/state/` to .gitignore.
   If a formatter rewrites markdown on save or commit (prettier, lint-staged), add
   `docs/items/` to its ignore file: the design guard compares file hashes.
4. CLAUDE.md: insert `templates/CLAUDE.md` between `<!-- flow:start -->` and
   `<!-- flow:end -->` (replace the block if it exists; keep everything else). Keep the
   whole file under 200 lines; move long project notes into `.claude/rules/`.
5. Rules that load only when matching files are read: copy `templates/rules/tests.md`
   always, `bus.md` for web stacks, `supabase.md` when there is a supabase/ folder,
   `rust.md` for Rust, each to `.claude/rules/flow-<name>.md`.
6. Settings: merge `templates/settings.json` into `.claude/settings.json` (deny rules,
   advisor model, auto-compact window, status line). Keep an existing status line unless
   the user wants this one; copy `templates/statusline.py` to `.flow/statusline.py`.
   Ask once whether to turn on the Bash sandbox (it blocks network access outside an
   allow list; Linux needs bubblewrap and socat); on yes merge
   `templates/settings.sandbox.json` and add the project's own domains.
7. Security: copy `${CLAUDE_PLUGIN_ROOT}/security/` (checks.py, checks.yaml, README.md)
   to `security/`, run `uv run security/checks.py detect --write`, and ask in one
   AskUserQuestion which extra tags apply (gamification, remote-dev).
8. Graphify: `graphify claude install --project`, `graphify hook install`,
   `graphify update .`.
9. Playwright (web stacks): if missing, add @playwright/test, install Chromium, and copy
   `templates/nextjs/playwright.config.ts` when there is no config yet. Then
   `npx playwright init-agents --loop=claude`. The UX audit uses `@axe-core/playwright`
   when it is installed; it is a new dependency, so ask before adding it.
10. Bus (nextjs, unless `--no-bus`): if there is no bus folder, copy
   `templates/nextjs/bus/` to `<src>/bus/`, `templates/nextjs/helpers/xp.ts` to
   `<src>/helpers/` (example; delete if unused), and `templates/nextjs/scripts/bus-gen.mjs`
   to `scripts/`. Add package.json scripts `bus:gen` and `bus:check`, run `bus:gen`, and
   add `env: { NEXT_PUBLIC_E2E: process.env.NEXT_PUBLIC_E2E ?? '0' }` to next.config.
   In an existing project do not wire the provider into the layout; the first refactor
   item does that.
11. Folders: `docs/items/` and `docs/architecture/` (with .gitkeep).
12. Commit on `workflow-setup` with the user's own git identity and no co-author or
    tool attribution lines: `Set up the coding workflow`.
13. Next steps:
    - new project: `/flow:quiz "Playwright foundation and bus skeleton"` as ITEM-001;
    - existing project: `/flow:refactor-scan`, and switch `mode` to `enforce` yourself
      once a week of warnings looks right.
