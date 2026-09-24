# Heimdall QA

A review harness for REST APIs. It replays HTTP, checks what came back against what
the API said it would do, and leaves a directory of evidence a human can read and
judge. It is not a JUnit suite and it does not replace [Bruno](https://www.usebruno.com/)
— it is the thing you run when "the tests pass" is not the same question as "this
behaviour is correct".

Two halves, deliberately separable:

| | |
| --- | --- |
| **The core** (`heimdall_qa`) | Replaying, packing, storing runs, and serving the review screen. Knows nothing about any product. |
| **A provider** (`heimdall_qa_<id>`) | What a product's answers *mean*: its price model, its error vocabulary, its fixtures. Installed separately, discovered by entry point. |

The loop:

1. An agent (or you) writes YAML: a **contract** per endpoint, **cases** per
   condition, a **round** that runs them.
2. `heimdall-qa run` replays them for real, applies the **packs** (checks), and
   writes `runs/<stamp>-<id>/`.
3. You open `heimdall-qa serve`, walk the collection, and approve or reject each
   step.
4. `heimdall-qa last-run` gives an agent the path to the evidence; it writes up
   what it read.

Nothing in the harness decides a product question. When a run needs to know what a
value *was worth*, it asks the project's oracle — and a project with no oracle gets
the neutral one, which counts what it can price and calls nothing a `402`.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
heimdall-qa init --root .       # the starter tree a project begins from
heimdall-qa --help
```

Python ≥ 3.12. `--verbose` on any command prints a traceback. There is no browser
dependency and there never will be one in this distribution.

The optional pieces are distributions of their own and install the same way:

```bash
pip install -e packages/spring   # the Spring reader: contracts from Java source
pytest                           # the core's suite, no product required
pytest packages/spring           # the reader's suite, no JVM required
```

A product's provider is a distribution like any other, and it is not in this
repository. [`examples/toy-provider/`](examples/toy-provider/) is a whole provider
small enough to read, and [Writing a provider](#writing-a-provider) is the shape of
one.

## The project descriptor

Everything about a *target* lives in one file, `qa/project.yaml` in the target
repository. The descriptor is looked for in two places, in this order:

1. `<root>/qa/project.yaml` — the target's own, which wins;
2. `<harness>/providers/<id>/project.yaml` — the fallback a provider ships.

`--descriptor PATH` overrides both. Whichever won is recorded in the run's
`summary.json` under `descriptor`, so a run is never ambiguous about which project
it was measuring.

A complete descriptor is six lines:

```yaml
version: 1

project:
  id: toy

environments:
  local:
    base_url: http://127.0.0.1:8110
```

Everything past `environments` is an addition rather than a form to fill in. A route
that needs a decision declares one — the key is `prefix`, and `auth` is the scheme the
descriptor's `auth:` block defines:

```yaml
routes:
  - prefix: /toy
```

Everything else is optional, and what you leave out is the honest answer. A project
that declares no `log_sources` gets no log correlation and the packs that need a
trace line report **skipped** — unmeasured is not incomplete. A source that declares
`propagate: false` is the other half of that rule: it is read and reported, and its
silence is never a product failure, because the project said the trace does not reach
it. A project that declares
no `provider` gets the neutral oracle. A project that declares no `budgets` gets the
harness defaults.

The full shape, with every field and its default, is in
[contrib/architecture.md](contrib/architecture.md).

## Configuration

`config.yaml` holds *harness* infrastructure only — the review UI's bind address,
poll timeouts, the floor between paced requests. The project itself (origins, auth,
routing, budgets, log sources) is the descriptor. Keeping the two apart is what lets
the same harness serve two projects on one machine.

`--config PATH` overrides; without it, the defaults in the package are used.

## Secrets

`secrets.local.yaml` is gitignored and is the only place a credential may live.
Copy [secrets.example.yaml](secrets.example.yaml) and fill it in. Never paste a real
token, API key or UUID into a round's YAML — cases refer to secrets by name.

`--secrets PATH` overrides. Without the file the harness starts with an empty map,
which is what the test fixtures use.

## CLI

Every round path is relative to `--root` (default: the working directory). Failures
print `error[CODE]:` plus a `hint:` on stderr.

### `heimdall-qa init [--root DIR]`

Writes the starter tree a project begins from — the descriptor, one contract, its
case, the suite and round that include it, `baselines/empty.json`, a
`secrets.example.yaml` and a `README.md` explaining the lot. It is the one command
that answers offline and the only one worth running before anything else.

```bash
heimdall-qa init --root .
heimdall-qa validate rounds/smoke.yaml     # answers: the tree agrees with itself
```

Idempotent: a file that already exists is kept, so the second run is safe on a tree
somebody has started editing. `--force` overwrites — and still only touches the files
it ships, never a file of yours. The starter measures `GET /health`; point
`qa/project.yaml` at your API and edit the path, and it runs.

### `heimdall-qa validate ROUND`

Checks the round's YAML, its cases, that `expect.status` is not still `TODO`, and
that the contract is **covered** by the cases. Suites that declare `steps` are exempt
from the coverage rule — a loop is not one case per row.

```bash
heimdall-qa validate rounds/smoke.yaml --root examples/toy-provider
heimdall-qa validate rounds/walk-hn.yaml --root tests/fixtures
```

Exit `0` means zero errors. Exit `1` lists them, e.g. `coverage: missing
ingest-N-omit-timestamp`.

### `heimdall-qa scaffold-endpoint CONTRACT`

Generates the stub cases the contract implies: `H`/`O`/`B`/`N`/`I`/`E`/`P`. What the
contract decides is filled in (`N-omit` → 400, `N-auth` → 401, `N-rule-*` → `rules[]`);
the rest stays `TODO`, and a round with a `TODO` does not validate. `--h01-status 202`
seeds the happy path and clones its 2xx into `B-*` / `I-replay` / `I-new-key`.

```bash
heimdall-qa scaffold-endpoint contracts/toy-items-post.yaml --out /tmp/stubs --root examples/toy-provider
heimdall-qa scaffold-endpoint contracts/api-ingest-post.yaml --out /tmp/stubs --root tests/fixtures
```

It writes one file per area — `cases/ingest.yaml`, a map of case id to case — and
appends to it when the file is already there, so a scaffold that runs again next to a
corpus a human has filled in does not undo that work. `--force` overwrites. Fill the
diffs and statuses **one axis at a time**, then validate.

### `heimdall-qa scaffold-round CONTRACT --out rounds/`

`scaffold-endpoint` plus a round that includes the area's file, one line. The declared
`area` names both the case file and the round.

```bash
heimdall-qa scaffold-round contracts/toy-items-post.yaml --out /tmp/stubs-rounds \
    --cases-out /tmp/stubs-cases --root examples/toy-provider
```

### `heimdall-qa discover`

The other direction: instead of starting from a contract a human wrote, read the
API's own description and generate the contracts, the cases and a gaps report. The
reader is chosen by `contract.source` in the project descriptor — `inline` (the
contract file *is* the source), `openapi` (both ship with the core) or a registered
plugin such as `spring`, which arrives with `heimdall-qa-spring`. The first
*installed* reader in the declared order wins, so a list is a fallback policy:

```yaml
contract:
  source: [spring, openapi]     # try the code first, fall back to the document
  location: ../openapi.json     # relative to this descriptor, or a URL
```

`openapi` reads a document. `spring` (`pip install -e packages/spring`, or the
`heimdall-qa-spring` distribution) reads the **Java source of a Spring Boot API**
instead — text only, no JVM: the controller for the route, the `record` for the
fields, the `@RestControllerAdvice` for what each failure answers. Its `location` is
a *directory* (`src/main/java`), which is why one `location` serves one kind of
reader: a list of sources is a fallback policy only among readers that take the same
kind of place to look.

```bash
heimdall-qa discover                                   # to the declaration
heimdall-qa discover --source openapi --location http://127.0.0.1:8110/openapi.json
heimdall-qa discover --source spring --location ../api/src/main/java --dry-run
```

`--dry-run` reads and reports without writing a file, and `--route "POST /api/ingest"`
narrows the run to one endpoint — which is how a reader is checked against a contract
that already exists.

It writes one contract per endpoint under `contracts/`, one case file per area under
`cases/`, the `baselines/empty.json` they point at, and `DISCOVERY.md`. Nothing is
overwritten without `--force`, so a second run after a human has filled a gap changes
nothing.

What the source cannot say is written down and never invented: an operation with no
declared 2xx becomes `status: TODO`, a path parameter with no known value becomes
`path_values: TODO`, a body with a required field the source left without an example
makes the baseline a reported hole, and all three are refused by `validate` and by
`run`. Everything else the format cannot spell — a PII denylist, a timestamp window,
a second accepted spelling of a field, a product rule's status — becomes a line in
`DISCOVERY.md` naming the axis it leaves uncovered and how to close it. The report
also separates what is *declared and unread* from what is *read and different*: a
status the descriptor answers and the source does not is printed as answered, and a
status the two disagree about is printed as a disagreement, because showing one as a
gap would send someone to fix a fact already written down.

### `heimdall-qa campaign validate CAMPAIGN`

Runs `validate` on every round in a manifest. Stderr names the path that failed —
missing file, or coverage error.

```bash
heimdall-qa campaign validate campaigns/smoke.yaml --root examples/toy-provider
```

### `heimdall-qa campaign status CAMPAIGN`

For each round, whether `runs/<stamp>-<round-id>/summary.json` exists (pass / fail /
5xx) or whether it has not been reviewed yet. JSON on stdout; the hook a campaign
report is built from.

```bash
heimdall-qa campaign status campaigns/smoke.yaml --root examples/toy-provider
```

### `heimdall-qa run ROUND --mode headless`

Replays the round with no browser: real HTTP (or a mock, in tests), packs, a run
directory, and the `runs/latest` symlink. Prints the absolute path of the run.

Only `--mode headless` is valid here. `walk` and `review` are modes of `serve`.

```bash
heimdall-qa run rounds/smoke.yaml --root examples/toy-provider --mode headless
heimdall-qa run rounds/example.yaml --root tests/fixtures --mode headless
```

Exit `1` if `summary.json` counts a `fail` or an `http_5xx`.

### `heimdall-qa last-run`

Prints the absolute path of `runs/latest`. `--runs-dir` defaults to `runs`.

```bash
heimdall-qa last-run
```

### `heimdall-qa fixture KIND`

Identity data as JSON on stdout — the harness's answer to "do not invent a CPF".
One kind per invocation: `email`, `password`, `person_name`, `company_name`,
`address`, `cpf`, `cnpj`, or `register` for a typical onboarding block.

```bash
heimdall-qa fixture register
heimdall-qa fixture cnpj
```

The runner accepts those kinds in a case's `generate:` and in `secret.<name>`, and
carries captured tokens (a `jwt`, a `refresh_token`, an `api_key`) between steps in
`runs/shared-captures.json`.

### `heimdall-qa serve [TARGET]`

Serves the review UI on `http://127.0.0.1:7878`. A bind that is not localhost is
refused. With no argument it indexes every `campaigns/*.yaml` and every orphan round;
with an argument it focuses that campaign or round.

```bash
heimdall-qa serve
heimdall-qa serve campaigns/smoke.yaml --root examples/toy-provider
heimdall-qa serve rounds/walk-hn.yaml --root tests/fixtures
```

An unparseable YAML does **not** start (`ROUND_INVALID`). A parseable but incomplete
round — a `TODO`, a coverage gap — appears in the tree as not ready and cannot be
started. One process reviews one HTTP round at a time; starting another with a verdict
pending is `ROUND_BUSY`.

**Agents do not call port 7878.** The run directory is the API.

The review UI's copy is in Portuguese, because the team that reads it is. The code,
the docs and the CLI output are in English.

## The review screen

Two columns. Left is the **collection**: campaign → flow → endpoint → case, with
status. Right is the **detail**: the campaign rollup, the start form, or the step
under review — packs first (fail and warn on top, pass collapsed), then the
expected-vs-read table on a probe, then the HTTP exchange and timings, then bodies,
headers and logs inside `<details>`. Secrets are redacted on the way out.

A verdict form appears only on the step waiting for one. **Approve** moves on;
**reject and continue** and **reject and stop** both require a comment (an empty one
is a 400 with the message shown in the form itself).

Two modes:

| Mode | Behaviour |
| --- | --- |
| **walk** | Stops at **every** step, and at the probe. |
| **review** | Advances on its own while packs pass; stops on a failing pack, on a case marked `gate: human`, or at a probe whose `values.*` failed. |

At the end: the KPIs (pass/fail/skip/5xx, packs, coverage, p50/p95, incomplete logs,
human rejection rate) and the run path in a read-only field.

## Artifacts of a run

```
runs/<stamp>-<round-id>/
  round.yaml
  summary.json              # counts, packs, coverage, latency, descriptor origin, oracle
  evidence.md               # the same story, for a human
  book.json                 # only for suites: what the oracle priced, and why not
  steps/001-<case-id>/      # request, response, packs, logs, verdict
  probes/<id>/              # before, after, delta, oracle
runs/latest -> <the last run>
```

An agent's job after a review: `heimdall-qa last-run`, read those files, write up
what it found. Never invent the harness's output.

## Writing a provider

A provider is a separate distribution that answers the questions the core refuses to
guess. The full contract is in [contrib/architecture.md](contrib/architecture.md); the
short version:

```toml
[project]
name = "heimdall-qa-acme"
dependencies = ["heimdall-qa"]

[project.entry-points."heimdall_qa.providers"]
acme = "heimdall_qa_acme"
```

```python
# heimdall_qa_acme/__init__.py
from heimdall_qa_acme.oracle import AcmeOracle


def oracle():
    return AcmeOracle()
```

Then a descriptor says `provider: acme`, and `heimdall_qa.provider` resolves it
through the entry point. The core never imports a provider directly, and the core's
own suite passes with every provider uninstalled.

## The examples

[`examples/toy-provider/`](examples/toy-provider/) is three routes, a six-line
descriptor, three cases, and no provider at all. It is the cheapest thing that runs
end to end, and `examples/toy-provider/gate.sh` is the procedure that proves a clean
wheel still can.

[`examples/spring-fixture/`](examples/spring-fixture/) is the Java counterpart: a
minimal Spring Boot API whose *source* is the contract. `heimdall-qa discover` reads
the controllers, the records and the advice, generates the contracts and the cases,
and the fixture's own gate then runs them against the built application —
`bash examples/spring-fixture/gate.sh`. It is what "this harness measured a Java
project" means in practice, and the two edits it needs are written down in its
`README.md`.

## Where a product lives

There is no product in this repository, and that is the design: a provider is a
distribution you install, and a project's content — its descriptor, its cases, its
price model — is its own checkout. Keeping them apart is what makes the core testable
without any of them: the suite passes with every provider uninstalled, which is claim
5 of `examples/toy-provider/gate.sh`.

The two examples are what a project looks like, and between them they exercise
everything the core can do:

- [`examples/toy-provider/`](examples/toy-provider/README.md) — three routes, six
  lines of descriptor, three cases, a suite, a round and a campaign, and no provider
  at all. The ramp.
- [`examples/spring-fixture/`](examples/spring-fixture/README.md) — a minimal Spring
  Boot API whose *source* is the contract, plus its own gate.

[Writing a provider](#writing-a-provider) is the shape of one for your own project.

## Driving it from a model (MCP)

The CLI is the primary interface. It is what CI calls and what a person reads, and
nothing below changes it. When a model is the one doing the review, though, a shell in
the middle is a place for a command to be mistyped or a value to be invented, so the
harness also speaks MCP:

```bash
pip install 'heimdall-qa[mcp]'
heimdall-qa-mcp                    # stdio, which is what a client launches
heimdall-qa-mcp --transport streamable-http --port 8765   # loopback only, asserted
```

Twelve tools, one resource and two prompts. The tools are `init`, `validate_round`,
`explain_rules`, `discover`, `scaffold_endpoint`, `scaffold_round`, `run_round`,
`last_run`, `campaign_validate`, `campaign_status`, `fixture` and `read_run_file`; the
resource is `heimdall://run/latest/{name}`, which is `read_run_file` against the latest
run; the prompts are `onboarding` and `review_run`, which are the two procedures this
project would otherwise have to state again in a document.

Two differences from the CLI, and both are because a server has nobody's working
directory. `root` defaults to the process's directory, and run evidence defaults to
`root/runs` rather than `cwd/runs` — when a client launches this from the project
directory the two agree exactly. And a **domain** refusal comes back as an object with
the code, the message and the fix instead of as an exception, because a refusal a model
can act on is worth more than a traceback it can only repeat:

```json
{"error": {"code": "LAST_RUN_MISSING",
           "message": "no /tmp/project/runs/latest symlink",
           "hint": "run a round first: heimdall-qa run ROUND --mode headless"}}
```

A bug still raises. Both front ends call the same `heimdall_qa.operations`, so a tool
and a command cannot drift into disagreeing about which project they measured.

## Contributing

- [contrib/README.md](contrib/README.md) — the documentation, and how to add a reader, a provider, an example or a pack.
- [contrib/architecture.md](contrib/architecture.md) — the seams: descriptor, packs, oracle, provider discovery, run storage.
- [CONTRIBUTING.md](CONTRIBUTING.md) — the short version, and the gates a change has to pass.
- [AGENTS.md](AGENTS.md) — what an agent working in this repository must and must not do.

The paper a public repository is expected to carry: [LICENSE](LICENSE) (Apache-2.0),
[SECURITY.md](SECURITY.md) (which is wider here than you might expect — a check that
reports a result it did not measure is one), and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) (Contributor Covenant 2.1).

## Tests

```bash
pytest                        # the core's suite
pytest packages/spring        # the reader's suite, no JVM required
HEIMDALL_QA_SLOW=1 pytest     # additionally tries a live API on localhost, and
                              # builds the Spring fixture when a JDK is present
```

Pytest does not need any API to be running, and does not need a provider installed.
The Java parity target has the same rule: its `-m slow` half skips without a JDK, and
its source-reading half never needed one.

CI runs these plus the five gates from [contrib/README.md](contrib/README.md) on Python
3.12, 3.13 and 3.14, each from `bin/bootstrap` rather than from a hand-written install —
so a broken clone fails there and not at a colleague's first command. The workflow is
[.github/workflows/ci.yml](.github/workflows/ci.yml).
