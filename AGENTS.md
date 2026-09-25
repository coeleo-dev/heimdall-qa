# AGENTS.md — Heimdall QA

A REST API review harness: the core in `src/heimdall_qa/`, a provider a distribution of
its own. `contrib/architecture.md` is the structure, `README.md` the CLI, and
`.agents/skills/` the work.

## The boundary

- The core never names a product: nothing under `src/heimdall_qa/` may carry one. A
  grep is a review, not a gate; `examples/toy-provider/gate.sh` is the gate.
- The core never imports a provider — `heimdall_qa.provider` and an entry point do it.
  Its suite passes with every provider uninstalled.
- `.cursor/skills/` and `.kiro/skills/` are generated copies; the work is
  `.agents/skills/`.

## The rules

- A DTO is a contract's source, never an example.
- `heimdall-qa fixture KIND` or `generate:` — never a memorised value.
- No credential in a round; `secrets.local.yaml` is never committed.
- Never waive a failing round by hand: a waiver needs a registered gap.
- Never call port 7878 — the run directory is the API, the UI is the operator's.
- `~/.config/heimdall-qa/projects.yaml` is the reviewer's memory, not the repo's: a
  test points `$HEIMDALL_QA_REGISTRY` at a throwaway file, its project gate never
  bypassed by hand.
- Only campaigns move between folders under `campaigns/`.

## Language

Code, CLI, logs, commits and this file are English; the review UI and provider docs
speak their readers' language.
