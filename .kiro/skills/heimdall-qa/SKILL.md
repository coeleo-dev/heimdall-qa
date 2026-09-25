---
name: heimdall-qa
description: >-
  Writes and runs Heimdall QA reviews against a REST API: descriptors, contracts,
  cases, rounds and suites in YAML, then validate / run / last-run.
---

# Heimdall QA — the core

You write YAML saying what the API should do; the harness replays it and leaves
evidence.

**The one rule: when the descriptor does not say, the harness skips with a reason — it
never guesses.** A project with no `log_sources` reports `observability` as *skipped*,
not failed. Never "fix" a skipped pack by declaring what the project does not have.

## The four files

| File | Answers |
| --- | --- |
| `contracts/<area>.yaml` | the endpoint: method + path, DTO, auth, fields, rules, baseline |
| `baselines/<area>.json` | the canonical body the cases diff against |
| `cases/<area>.yaml` | case id → diff, expectation, what the harness must generate |
| `rounds/<id>.yaml` | the cases to run, in order, for one environment |

One file per **area**; a round names the file or one case in it (`#things-post-H01`).
File order is run order. A **suite** replaces the list when the round repeats a case
(`loop`) or compares a surface (`probe`). Kinds: `H01` happy, `O*` optional, `B*`
boundary, `N*` negative, `I-*` idempotency, `E-*` isolation, `P-*` product rule.

## The workflow

0. **No contract yet?** `heimdall-qa discover` reads `contract.source` and writes the
   tree and `DISCOVERY.md`; a gap it lists is a decision.
1. **Scaffold, do not type.** The contract decides the statuses (`N-omit` → 400,
   `N-auth` → 401, the `rules[]` outcomes); the rest stays `TODO`.
2. **One axis at a time:** statuses and diffs, then rules, then identity. A `TODO` does
   not validate, which is the point.
3. **Identity comes from the harness:** `heimdall-qa fixture KIND` or `generate:`. To
   repeat a value across cases, capture it (`capture:`, then
   `generate: {field: captured.<name>}`).
4. **Validate until it is quiet:** `heimdall-qa validate rounds/things.yaml`. Every
   refusal carries a `code`, the `where`, the `fix` and the `why`; `validate --explain`
   lists every code.
5. **Run it:** `heimdall-qa run rounds/things.yaml --mode headless`, or
   `heimdall-qa serve` and tell the operator. **7878 is their screen, not yours.**

## Reading a run

`heimdall-qa last-run` prints `runs/latest`. In this order: `counts.instrument > 0` is a
**harness** problem, not a product defect — then a `fail` pack (`packs.json`), then a
`fail` in the book (a probe read differently; report expected vs read).
`logs_incomplete`: `logs.json` names the source that owed this trace a line;
`propagate: false` is reported but not counted. Write `analysis.md`: what broke, what
could not be measured, which packs failed, what is still `TODO`.

## Campaigns

`heimdall-qa campaign validate CAMPAIGN` validates every round in it;
`heimdall-qa campaign status CAMPAIGN` reports per round pass / fail / 5xx /
**not reviewed** — no run is not passing.

## The demo

`bin/demo` materializes the sample, starts its mock API and runs one green and
one red round. Its `fail`, `http_5xx`, `warn`, `not_ready`, `missing` and `not_reviewed`
cases are deliberate: do not fix them.
`bin/verify [--fast]` runs the gates; `bin/bootstrap` sets a clone up.

## Never

- Call port **7878** — it is the human's screen; the run directory is your API.
- Commit `secrets.local.yaml`, or paste a credential into a round.
- Invent a waiver (it needs a registered gap id), or ship only the happy path.
- Add a product name to `src/heimdall_qa/`: nothing here may carry one; a project's
  judgement belongs in its descriptor or its own skill.
- Regenerate content a person already reviewed.
