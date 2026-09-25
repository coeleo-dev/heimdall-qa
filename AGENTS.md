# AGENTS.md — Heimdall QA

A REST API review harness: the core in `src/heimdall_qa/`, a provider a distribution of
its own. Structure: `contrib/architecture.md`; developer: `contrib/development.md`;
CLI: `README.md`; the work: `.agents/skills/`.

Gate: `bin/verify` (five gates; `--fast` skips the two provider ones,
`examples/toy-provider/gate.sh` and `examples/spring-fixture/gate.sh`). `bin/bootstrap`
sets a clone up, `bin/demo` runs the shipped demo, `bin/web` checks the client.

## The boundary

- The core never names a product: nothing under `src/heimdall_qa/` may carry one.
- It never imports a provider — `heimdall_qa.provider` and an entry point do it.
- `.cursor/skills/` and `.kiro/skills/` are generated; the work is `.agents/skills/`.

## The rules

- A DTO is a contract's source, never an example; `heimdall-qa fixture KIND` or
  `generate:` — never a memorised value.
- No credential in a round; `secrets.local.yaml` is never committed.
- Never waive a failing round by hand: a waiver needs a registered gap.
- Never call port 7878 — the run directory is the API.
- A test points `$HEIMDALL_QA_REGISTRY` at a throwaway file, its gate never bypassed.
- Only campaigns move between folders under `campaigns/`.
- `src/heimdall_qa/demo/sample/` is package data; its `fail`, `http_5xx`, `warn`,
  `not_ready`, `missing`, `not_reviewed` cases are deliberate — do not fix one.

Code, CLI, logs, commits and this file are English; the review UI and provider docs
speak their readers' language.
