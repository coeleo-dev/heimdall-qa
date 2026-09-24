# AGENTS.md — Heimdall QA

A REST API review harness: the core in `src/heimdall_qa/`. A product's provider is a
distribution of its own. `contrib/architecture.md` is the structural reference,
`README.md` is the CLI, and the skill in `.agents/skills/` is the work itself.

## The boundary

- The core never names a product: nothing under `src/heimdall_qa/` may carry one. That
  is a review step and not a gate — a grep only catches the names someone thought of —
  so `examples/toy-provider/gate.sh` is the gate, and it proves both rules below.
- The core never imports a provider — `heimdall_qa.provider` and an entry point do it.
  Its suite passes with every provider uninstalled.
- `.cursor/skills/` and `.kiro/skills/` are copies `bin/sync-skills` generates; the
  work is `.agents/skills/`.

## The rules

- A DTO is a contract's source, never an example.
- `heimdall-qa fixture KIND` or `generate:` — never a memorised value.
- No credential in a round; `secrets.local.yaml` is never committed.
- Never waive a failing round by hand: a waiver needs a registered gap.
- Never call port 7878 — the run directory is the API, the UI is the operator's.

## Language

Code, CLI output, logs, commits and this file are English; the review UI and a
provider's docs speak their readers' language.
