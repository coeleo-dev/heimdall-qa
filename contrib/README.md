# Contributing to Heimdall QA

This directory is the harness's documentation. There is no `docs/`: a harness that
carries a product's study is a harness that has not finished separating itself from
that product, so the working papers stay with whoever wrote them and only what
stands on its own lives here.

## Read first

| Document | What it is |
| --- | --- |
| [architecture.md](architecture.md) | **Start here.** The seams: descriptor, `ProjectView`, packs, step kinds, oracle, provider discovery, run storage. |

[`../README.md`](../README.md) is the CLI reference — every command, what it prints,
and what it exits with. [`../AGENTS.md`](../AGENTS.md) is for an agent working in this
repository rather than a person.

## The one rule

**Nothing belonging to the project under review may reach this repository.**

Heimdall QA is the deliverable. The project it was built against is one checkout that
answers its questions, and it stays on that machine. Concretely, these are whole trees
rather than lists of file names, because a list ages — the next case file or campaign
gets added and nobody remembers to add a line, so the leak is silent:

```
/providers/     a provider distribution — its content and its domain logic
/qa/            a target's descriptor
/docs/          a project's study, plan and write-ups
```

The core never names a product either, and that is a rule about `src/heimdall_qa/`
rather than about the repository: the guard tests, the gate and `.gitignore` are
allowed to name what they guard, because a guard that cannot name its subject does not
guard anything.

## The gates

A change is done when these are green, not when the suite is.

```bash
bin/bootstrap                      # once: venv, the core, the reader, the skills
pytest                             # the core's suite, and it needs no product
pytest packages/spring             # the reader's suite, and it needs no JVM
bin/audit-remote                   # nothing the product owns would be published
bash examples/toy-provider/gate.sh    # a clean wheel still measures an API
bash examples/spring-fixture/gate.sh  # and still reads Java source
```

`bin/audit-remote` is the boundary above, enforced: it checks that the set which would
be published carries no product path and no product token, that the core is
product-neutral, and that the suite passes in the tree exactly as it would ship. It is
claim 6 of the toy gate, so it cannot rot unnoticed.

It guards the *content*. The *history* is guarded too, because a `.gitignore` cannot
unpublish a commit that already carries a product's cases:

```bash
bin/public-history --check         # the published branch is one orphan commit of the audited tree
```

`bin/public-history` is what writes that branch, and it refuses to write one from a
tree `bin/audit-remote` rejects. It is not part of the gate runs above because only a
maintainer publishing a release needs it; `CONTRIBUTING.md` has the procedure.

## Scope: REST

The harness replays HTTP. Browser automation is not in this plan and there is no
dependency on one in this distribution. It was core code once, it left, and if it
returns it returns as a provider — the same shape as any other product's answer,
installed by whoever wants it.

The review UI (`src/heimdall_qa/serve/`) is not an exception: it is the screen a human
reads a run on, not a way of testing a front end.

## Ways to contribute

**A reader.** The contract source is a seam, not a format: `inline`, `openapi`, and a
registered plugin like `spring`, which arrives with `heimdall-qa-spring`. A reader for
another stack is a distribution of its own, and it is the highest-leverage thing to
add, because discovery is what makes a new project cheap to onboard.

**A provider.** A provider answers what the core refuses to guess — a price model
today. It is a separate distribution found by entry point;
[architecture.md](architecture.md) §1 has the contract, and
[`../README.md`](../README.md) has the short version.

**An example.** [`../examples/toy-provider/`](../examples/toy-provider/) is the
cheapest thing that runs end to end, and
[`../examples/spring-fixture/`](../examples/spring-fixture/) is the Java counterpart.
An example earns its place by being gate-able: it should come with a `gate.sh` that
proves its claim on a machine that has nothing else installed.

**A pack.** Packs are the checks applied to a step's evidence. A pack that needs a
product fact belongs to a provider; a pack that measures a property of HTTP — a leak,
a status, a timing — belongs in the core, and the test for it is a test with no
provider installed.

## House rules

- **English.** Code, CLI output, logs, commits and these documents. The review UI and
  a provider's own docs speak their readers' language, and that is the whole of the
  exception.
- **A DTO is a contract's source, never an example.** Do not commit a memorised
  identity; `heimdall-qa fixture KIND` and `generate:` produce one.
- **No credential in a round.** `secrets.local.yaml` is the only place one lives, and
  it is never committed.
- **A rule is stated once.** If two documents state the same rule they will diverge;
  state it where it is enforced and link to it.
