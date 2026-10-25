# Security checks

A list of vulnerability checks tagged by technology, environment and deploy target,
plus a script that picks the ones that apply to a project and to each work item and runs
the automated ones.

| File | What it is |
|---|---|
| `security/checks.yaml` | The registry: checks, version floors, detection rules, advisory sources. Shared by all projects. |
| `security/checks.py` | Detects the stack, selects checks, runs grep rules, commands and version floors. |
| `security/stack.yaml` | Per project. Written by `detect --write`; you edit `extra`, `exclude`, `envs` and `vars`. |
| `docs/items/<ID>/security.md` | Per item. Written by `item <ID> --write`; agents tick the boxes. |
| `/flow:security-refresh` (plugin skill) | Weekly refresh of floors and checks from vendor advisories. |

`/flow:init` copies `checks.yaml`, `checks.py` and this README into the project's
`security/` folder, so CI can run the checks without the plugin.

The script needs [uv](https://docs.astral.sh/uv/) (already installed for Graphify); `uv run`
installs PyYAML on first use. Python 3.11+.

## How it fits the flow

A check is picked for a project when all its `tech` and `deploy` tags are in the stack
and its `env` overlaps the project's environments. At S4, S6 and S7 it is also picked
only if the item touches a file that matches its `paths` or `content` trigger, so a CSS
fix does not get the Stripe checklist.

| Stage | Who | Command | Result |
|---|---|---|---|
| S1 Quiz | main session | `item <ID>` | S1 questions that apply; answers become D-IDs in spec.md |
| S2 Design | architect | — | S2 boxes: shown in the UML and data-structure notes |
| S3 Impact | `/flow:plan` | `item <ID> --write` | security.md for the item, now with path triggers from impact.json |
| S4 Specs | e2e-author | — | S4 boxes: Playwright specs tagged `@security` (`@rls`, `@security-headers`) |
| S6 Test gate | Stop hook, CI | `run --stage S6 --item <ID>` | exit 1 if a blocker or high check fails |
| S7 Review | reviewer | — | S7 boxes, plus allow-list changes |
| S8 Merge | `/flow:ship` | `run --stage S8` on a production build, then `gate <ID>` | blocks merge on failures or unticked blocker/high boxes |
| Weekly | `/flow:security-refresh` | `run --stage weekly` | platform checks through MCP, new floors and checks |

Severity: **blocker** must pass to merge. **high** must pass, or you tick it and write the
exception after it in security.md. **medium** prints a warning.

Statuses: PASS, FAIL, WARN (a failing medium check), ERROR (tool missing or command
broken; counts as a failure, except with `--missing-ok`, which the Stop hook uses), SKIP (could not check, for example no build output; fails
only with `--strict`), N/A (nothing the rule applies to).

## Setup in a repo

`/flow:init` does steps 1 and 3 and adds the CLAUDE.md rules; the plugin's Stop hook runs
`run --stage S6 --item <ID>` for the active item.

1. `uv run security/checks.py detect --write`, then edit `security/stack.yaml`: add tags
   detection cannot see to `extra` (Note Noodle: `gamification`; `remote-dev` if you reach
   the dev machine through a tunnel), set `vars.mail_domain`, and set `vars.auth_guard` to
   your own auth helper names.
2. Install the tools the `run` checks call: gitleaks, osv-scanner, zizmor, and for Rust
   cargo-deny (grype if you ship Docker images). A missing tool shows as ERROR.
3. In CI, run `run --stage S6` on every pull request and `run --stage weekly` on a weekly
   schedule. Pin the actions you use to commit SHAs (GHA-01).
4. Run `uv run security/checks.py run --stage S6` once without an item to see the
   current state of the whole repo, and turn the failures into work items.

To share one registry across repos instead of copying it, point
`SECURITY_CHECKS=/path/to/checks.yaml` at it.

## Commands

```
uv run security/checks.py detect [--write]          stack tags from manifests and code
uv run security/checks.py list [--stage S] [--env E] [--md] [--all]
uv run security/checks.py item ITEM-042 [--write]   the item's checks, by stage
uv run security/checks.py run --stage S6 [--item ITEM-042] [--only ID ...] [--strict] [--missing-ok]
uv run security/checks.py floors                    version floors only
uv run security/checks.py gate ITEM-042             fail while blocker/high boxes are unticked
uv run security/checks.py validate                  check the registry after editing it
```

`list --env prod --stage weekly` gives the production checklist; `list --md --all` prints
the whole registry as a table.

## Adding or changing a check

Copy a similar entry in `checks.yaml`, give it a new ID (never reuse or renumber IDs;
specs and reviews cite them) and run `validate`. The field list is at the top of the file.

Verification is one of:

- `grep`: a static rule over `files` (globs). Exactly one of:
  - `forbid: regex` fails on any match (with `when`, only in files that match `when`);
  - `require: regex` fails for each file without a match (with `when`, only files that match `when`);
  - `present: regex` fails unless at least one file matches.
  Optional: `when`, `unless` (skip files that match), `allow` (globs to skip, each with a
  reason in a comment), `missing: fail` (fail when no file matches the globs).
  Rules are heuristics that catch the common mistake fast; they do not prove absence.
- `run`: a shell command run from the repo root; exit 0 passes. `{{name}}` is replaced
  from `vars`; an empty var turns the check into SKIP. `$BASE_REF` and `$ITEM` are set.
- `builtin: floors`: compares lockfile and runtime versions with the `floors` list.
- none: a manual check. It becomes a checkbox in security.md, or a line in the weekly
  list with the MCP tool to use (`mcp`).

Multi-stage text uses stage markers, for example `S1 ask ... S4 a spec ... S7 ...`; each
stage shows only its own part.

Floors use release-line keys: `'16': '16.4.1'` covers 16.x, `'15.5': '15.5.28'` covers
15.5.x, `'*'` covers everything. Quote the keys. A version on an older unlisted line
fails; a newer major passes with a note to check the vendor page.

## Limits

- Grep rules look at files, not runtime behaviour. Playwright specs (S4) and the manual
  checks cover the rest.
- Floors read lockfiles (pnpm, npm, yarn, bun, Cargo) and runtime versions from
  `node -v`, Dockerfiles, `.nvmrc` and workflow `node-version` pins.
- Platform settings (Vercel, Supabase, Stripe, Play Console) cannot be read from the repo;
  they are manual weekly checks done through the MCP tools named in each check.
