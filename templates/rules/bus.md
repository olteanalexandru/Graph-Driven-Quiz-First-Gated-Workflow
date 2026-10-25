---
paths:
  - "src/bus/**"
  - "**/bus/**"
---
# Bus rules
- One folder per domain: src/bus/domains/<domain>/ with messages.ts and handlers.ts.
- Message types are '<domain>/<action>'. Each command has exactly one handler, named handle<Action> and exported from handlers.ts.
- After adding or renaming a message run `node scripts/bus-gen.mjs`. Never edit src/bus/messages.ts or src/bus/dispatch.ts by hand; they are generated.
- Handlers call pure helpers for rules, ctx.services for side effects and ctx.emit for results. No React and no fetch inside handlers.
- Components send commands with the typed builders and listen with useBusEvent. No window events, string topics or imports of another domain's internals.
- Per-frame data (audio buffers, animation positions) stays in refs and loops; only meaningful events go on the bus.
