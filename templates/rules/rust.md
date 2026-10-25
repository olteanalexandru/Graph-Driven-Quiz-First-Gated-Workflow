---
paths:
  - "**/*.rs"
  - "**/Cargo.toml"
---
# Rust rules
- Cross-module communication goes through the command enum and one `match` that calls named handler functions; pure rules live in helper modules.
- SQL uses sqlx macros or bound parameters; never build a query with format!.
- Each unsafe block has a `// SAFETY:` comment; crates without unsafe code declare `#![forbid(unsafe_code)]`.
- Handlers return errors instead of calling unwrap or expect on request data; arithmetic on scores and prices uses checked or saturating operations.
- Leptos and Dioxus `#[server]` functions are public endpoints: check the session and ownership inside each one.
- Tests: `cargo test --quiet`; integration tests named after the item and acceptance criterion (fn ac1_...).
