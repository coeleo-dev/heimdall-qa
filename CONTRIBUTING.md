# Contributing

Heimdall QA replays HTTP against an API it did not write and leaves evidence a human
can judge. It carries no product: a project's descriptor, cases and price model are
its own checkout, and this repository is the harness that measures them.

That separation is the design, so the first thing to read is not this file — it is
[contrib/README.md](contrib/README.md), which is the documentation and the short list
of ways to contribute. [contrib/architecture.md](contrib/architecture.md) is the
structural reference.

## Get set up

```bash
git clone <this repository>
cd heimdall-qa
bin/bootstrap
```

`bin/bootstrap` is idempotent: it checks the toolchain, creates `.venv`, installs the
core and the Spring reader, and syncs the agent skills. Python 3.12 or newer. A JDK is
needed only for the Java fixture — the harness itself does not use one.

## Before you open a pull request

```bash
pytest                                # the core's suite, needing no product
pytest packages/spring                # the reader's suite, needing no JVM
bin/audit-remote                      # nothing the product owns would be published
bash examples/toy-provider/gate.sh    # a clean wheel still measures an API
bash examples/spring-fixture/gate.sh  # and still reads Java source (needs a JDK)
```

A change is done when those are green. `bin/audit-remote` is not a formality: it is
what keeps the boundary real, and it fails if a product path or a product name reaches
the set that would be published. CI runs all five, so a red job names which claim broke.

## The bar

- **English** in code, CLI output, logs, commits and documentation.
- **State a rule once.** If two documents say the same thing they will diverge; say it
  where it is enforced and link to it.
- **A test proves the claim, not the intent.** The gates above are how this project
  argues, and a new claim is expected to arrive with the check that fails without it.
- **No credential, ever.** `secrets.local.yaml` is gitignored and is the only place one
  lives.

By contributing you agree that your work is licensed under the terms in
[LICENSE](LICENSE), and that you will keep to the [Code of Conduct](CODE_OF_CONDUCT.md).
A vulnerability goes through [SECURITY.md](SECURITY.md) and never into an issue —
what counts as one here is wider than you might expect.

## Publishing

There are two branches locally, and only one of them is ever pushed.

`master` is the archive: every commit that produced this harness, including the ones
written against the product it was built to measure. Those commits name that product's
cases and campaigns, and an object in a pack is on the remote whether or not the tip
still points at it — so `master` stays here.

`main` is what the world sees: **one root commit**, no parents, holding the tree
`bin/audit-remote` approves. It is generated, not authored:

```bash
bin/public-history            # audit the content, then write the root commit
bin/public-history --check    # assert the branch is one orphan commit of that tree
```

Re-run it after the tree changes — `--check` fails the moment the branch is stale, so
a forgotten refresh is caught before a push rather than after one. Then:

```bash
git remote add origin <url>
git push -u origin main
```

Never `git push --all` in this checkout. It publishes `master`, and no gate can refuse
a push that has already happened. This is the one instruction in this file that a
mistake cannot be undone by another commit.
