---
paths:
  - "tests/**"
  - "**/*.spec.ts"
  - "**/*.test.ts"
  - "playwright.config.*"
---
# Test rules
- Playwright titles: `AC<n> <behavior> @item:<ID>`, plus tags such as @smoke, @security, @rls, @audio-<name>, @visual, @ro.
- New spec files are named with the item ID: tests/e2e/<ID>-<flow>.spec.ts.
- Locators: getByRole and getByLabel first; data-testid for SVG and canvas parts.
- Import message builders from src/bus/domains/<domain>/messages.ts instead of writing message literals; assert bus events through window.__appBus.log.
- Run with `--reporter=line` and `--max-failures`; open traces only for unclear failures.
- A healer change that alters an expected value is a question for review, not a fix.
- Refactor items: characterization specs lock today's behavior and must pass before and after.
