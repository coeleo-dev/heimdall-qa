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
3. You open `heimdall-qa` — the desktop client, with the API already up — and walk the
   collection, across every project you have registered, approving or rejecting each
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

Python ≥ 3.12. `--verbose` on any command prints a traceback. Reviewing an API never
requires driving a browser: there is no Playwright, no axe, and no browser-automation
dependency in this distribution. The client draws its own screen in a native window,
which is a different thing — see [`contrib/architecture.md`](contrib/architecture.md)
§9.2.

### A clone, one command

To work *on* the harness rather than just install it, a clone is one idempotent step:

```bash
git clone <this repository>
cd heimdall-qa
bin/bootstrap
```

`bin/bootstrap` checks the toolchain, creates `.venv`, installs the core, the Spring
reader and the `mcp` extra, syncs the agent skills, and on a fresh venv it uses
`--system-site-packages` so the Linux window can see the system GTK. It needs
**Python 3.12+**, **Node** (only to rebuild the client's bundle — `pip install` never
needs it) and, for the desktop window on Linux, `python3-gi` and `gir1.2-webkit2-4.1`.
A JDK is needed only for the Java fixture. On a box with no GTK — a server, a container
— `bin/bootstrap --no-desktop` skips Node and the GTK checks and installs everything
the core, the reader and the suite need.

[`contrib/development.md`](contrib/development.md) is the developer's path: the scripts,
the test tiers, the client build and its committed-bundle rule, and the failure modes
with their fix.

### Development scripts

| Script | What it does |
| --- | --- |
| `bin/bootstrap [--check] [--no-desktop]` | The fresh-clone checklist above. `--check` asserts what is installed and installs nothing. |
| `bin/web [--check]` | The client's five checks: `npm ci`, typecheck, lint, test, build, and the committed-bundle drift check. `--check` skips the install. |
| `bin/verify [--fast]` | The five gates in order; `--fast` skips the two provider gates. |
| `bin/demo` | Materializes the bundled demo project, runs one green and one red round headless, and prints the run paths. |
| `bin/sync-skills [--check]` | Regenerates `.cursor/skills/` and `.kiro/skills/` from `.agents/skills/`. |

Run it with **no subcommand** to open that window:

```bash
heimdall-qa                        # the client, over every project you have registered
heimdall-qa --root ../orders-api   # ...and remember this one before drawing
heimdall-qa desktop                # the same thing, spelled out
```

The window is not a wrapper around the server: the process starts the API on an
ephemeral loopback port, hands the client its token in the URL fragment, and serves
the built bundle from the same process. There is no second command to start and no
port to keep in mind.

On Linux the window draws through the system GTK + WebKit2GTK, whose Python binding
(`python3-gi`) belongs to the *system* Python — so a venv on a different one cannot see
it. Use a Python the distro's packages match, or create the venv with
`--system-site-packages`:

```bash
/usr/bin/python3.12 -m venv --system-site-packages .venv    # the system's own Python
sudo apt install python3-gi gir1.2-webkit2-4.1
```

If it is missing, the client refuses by name and tells you which interpreter is short;
`heimdall-qa serve` is the same client in a browser and needs none of it.

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

## Projects

The client draws **one tree over every project it has been told about** — the same
organisation Postman gives a collection, one level up. A project is a directory that
looks like one: `qa/project.yaml`, or `campaigns/` and `rounds/`. Registering anything
else is refused by name, which is what keeps a slip in a file dialog from enrolling
`$HOME` and indexing a home directory.

```bash
heimdall-qa --root ../orders-api     # remember it and open on it
heimdall-qa desktop --root ../storefront
```

An explicit `--root` is the only thing that registers. The working directory is a
default for every other command and is deliberately *not* remembered — a client that
enrolled whatever directory it happened to be started from would fill the registry
with the shell's own habits.

The registry is a file in **your** configuration directory, not in any repository:

| | |
| --- | --- |
| `$HEIMDALL_QA_REGISTRY` | wins outright, and names a file |
| `$XDG_CONFIG_HOME/heimdall-qa/projects.yaml` | otherwise |
| `~/.config/heimdall-qa/projects.yaml` | failing that |

```yaml
projects:
  - id: orders-api-1f2a9c3d
    name: orders-api
    root: /home/you/work/orders-api
```

The `id` is the folder name plus a short hash of the resolved path, so two checkouts
of one repository are two projects and not one, and adding the same directory twice is
one line and not two. A line whose directory is gone is skipped rather than fatal: a
checkout you moved is ordinary, and refusing to launch over one stale line would leave
you no way to reach the dialog that removes it. Adding and removing projects happens in
the **Servidor** dialog in the client, or by editing the file.

A project's runs stay its own: `runs/` beside its campaigns. Two projects can each hold
a round called `smoke`, and a run is found by round *id*, so a shared run directory
would let one project's run answer for the other's round.

## Folders

Under `campaigns/` the client creates **real directories** and moves campaign files into
them:

```text
campaigns/
  agent-wrote-this.yaml       # top level, before anyone sorted it
  checkout/
    payment-retry.yaml
    refunds.yaml
```

A folder is an empty directory with nothing in it, and that is the point: the tree keeps
empty directories so that creating one is visible the moment you do it and there is
somewhere to move a campaign to. `Nova pasta` and `Mover para…` are two honest steps —
the destination has to exist, so you always move into something you can see.

Only campaigns move. A round or a suite moved out of its directory would change the kind
the client derives from the first path component — a round is a round *because* it lives
in `rounds/` — so a move of anything else is refused by name. A folder is also the unit
of a run: selecting one offers **Rodar tudo nesta pasta** (`directory`), which replays
every campaign under it in a deterministic order — by campaign path, then by the order
each manifest lists its rounds.

A campaign's `id:` comes from the file itself, so moving a campaign does **not** change
its tree key or its selection; the paths its manifest lists are relative to the content
root, so the move does not break them either. Git sees one rename.

## CLI

Every round path is relative to `--root` (default: the working directory). Failures
print `error[CODE]:` plus a `hint:` on stderr.

With no command at all, `heimdall-qa` opens the desktop client — see
[Install](#install). `heimdall-qa --help` lists the thirteen verbs below.

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

The same client as the bare command, in whatever browser you already have — for a
remote box, or when you want devtools. It serves on `http://127.0.0.1:7878` and prints
the URL with the token in it; a bind that is not localhost is refused. With no argument
it indexes every campaign and every orphan round of **every registered project**; with
an argument it focuses that campaign or round, resolved against the project `--root`
named.

```bash
heimdall-qa serve
heimdall-qa serve campaigns/smoke.yaml --root examples/toy-provider
heimdall-qa serve rounds/walk-hn.yaml --root tests/fixtures
```

An unparseable YAML does **not** start (`ROUND_INVALID`). A parseable but incomplete
round — a `TODO`, a coverage gap — appears in the tree as not ready and cannot be
started. One process runs one **plan** at a time; starting another while one is going
is `ROUND_BUSY`. That is global rather than per project: the engine is a single worker
and there is no second one to run your other project while this one walks.

A plan is what the selection adds up to, and the selection can be any node in the
tree:

| Selected | Runs |
| --- | --- |
| folder (real, under `campaigns/`) | every campaign under that subtree |
| campaign | every round the manifest lists, in the order it lists them |
| flow | the rounds of that matrix |
| round | that round |
| case | that case; or that case and everything after it |
| step of a suite round | that round — a loop and a probe only mean something inside their sequence |

A **project** has no scope on purpose: the engine runs one plan at a time, and "every
project I have registered" is a bigger question than a button on a card should answer.
Run a folder — a thing you organised deliberately.

A round written as a **suite** shows its steps in the tree instead of cases (`loop
chain ×2`, `probe two-loops`). A loop is a count and a probe compares against a baseline
the loops left behind, so neither runs alone: the step offers the round, and the card
says why. A round written with `include:` has real cases and keeps both case scopes.

A plan runs on the server, not in the request that starts it: closing the tab does not
stop it, and the client is told as it goes over Server-Sent Events rather than by
polling. A run of a single case writes its own run directory (`~case-<id>`), so it never
becomes the round's latest run.

**Agents do not call port 7878.** The run directory is the API.

The review UI's copy is in Portuguese, because the team that reads it is. The code,
the docs and the CLI output are in English.

## The review screen

The client is a React + Tailwind + shadcn/ui desktop app — a native pywebview window
opened by `heimdall-qa`, in the same process as the API, which it talks to over `/api`
(JSON) and `/api/events` (Server-Sent Events). Monaco edits the evidence and the YAML,
and `⌘K`/`Ctrl+K` opens a command palette over the loaded tree. There is one renderer:
the desktop client replaced the Jinja page rather than sitting beside it.
`contrib/architecture.md` §9.2 records that decision (ADR-05) and §9.5 the bounded
transition that removed the old one.

A live strip along the top, a **Servidor** button, then two columns. The **strip** shows
the phase, which unit of the plan is running, which step of that unit, the case under
review, the elapsed time and the running counts, plus a **Cancel** that stops the plan
between steps and keeps what already ran. Under it, the last few things that happened by
name — counts going up is not feedback when a campaign is 41 rounds.

The **Servidor** dialog is where the process's own state is: the API (always up, on a
loopback port), the embedded MCP server and its switch, the **Demonstração** switch
that materializes the bundled demo project, and the projects this client knows about —
with add and remove.

Left is the **collection**: project → folder → campaign → flow → endpoint → case, with
status, a search box, status chips (failed / 5xx / not reviewed / not ready) and a
per-node count of how many descendants are failing. A row's status is an **icon**, not a
chip: at seventy rounds a two-word badge per row is the widest thing in the sidebar, and
the word is worth more on demand than it is repeated nine hundred times. Hovering the
icon — or tabbing to it — shows the status by name, and the reason a round cannot run is
in the same tooltip. Rows carry the actions the tree needs — **Nova pasta** under
`campaigns/`, **Mover para…** on a campaign, and remove on a project. Right is the
**detail**: the campaign rollup, the unit card (what is selected,
the scopes you can run it in, the cases and their status, the summary of the last run),
or the step under review — packs first (fail and warn on top, pass collapsed), then the
expected-vs-read table on a probe, then the HTTP exchange and timings, then bodies,
headers and logs, collapsed unless the step failed. The request and the response sit side
by side once the pane is wide enough for both — measured against the pane and not the
window, because the collection is draggable — so comparing them stops being a scroll.
Secrets are redacted on the way out.

A verdict form appears only on the step waiting for one, pinned to the bottom.
**Approve** moves on; **reject and continue** and **reject and stop** both require a
comment (an empty one is a 400 with the message shown in the form itself). The
keyboard is wired to the same three buttons: `A` approve, `R` reject and continue,
`Shift+R` reject and stop, `J`/`K` move between steps, `/` focus the comment.

Starting a plan lands on what it needs: a walk that parks on its first case puts that
case's verdict form on screen. From there the parked step holds the pane only as long as
the reviewer is looking at the plan that owns it — the round it is walking, its cases, or
the node the plan was started from. Selecting anything else — another case, another
round, another campaign — opens what was selected, because "review this other thing while
the run waits" is a normal thing to want and a run waiting for a verdict is not a modal.
The strip keeps a **Ir ao passo em espera** button whenever the parked step is not on
screen, so leaving it is always one click from coming back. A case that has already been
reviewed is reopened as its step — never as a unit card again — so that case's own scopes
(*run this case*, *from here on*) are drawn on the step's bar; that is how a red case
inside a green round gets run on its own. The tree is re-indexed when the plan moves on to
another unit and when it ends, so a campaign's rollup fills in as it walks instead of
staying "not reviewed" until the server restarts; that re-index reads each content file
once per change rather than once per round that includes it, which is what keeps a
seventy-round campaign from pausing the window on every case.

Two modes:

| Mode | Behaviour |
| --- | --- |
| **walk** | Stops at **every** step, and at the probe. |
| **review** | Advances on its own while packs pass; stops on a failing pack, on a case marked `gate: human`, or at a probe whose `values.*` failed. |

At the end: the KPIs as a compact strip (pass/fail/skip/5xx, packs, coverage, p50/p95,
incomplete logs, human rejection rate), **the cases that failed with a link back to
each step**, and the run path.

The KPIs are read **from the run directory on disk**, not from the engine's memory of
what it last ran. That is what makes them survive a restart, a reload, and simply
selecting away and back: an endpoint opens on the KPIs of its latest run whenever that
run exists, and only a round that has never been run shows nothing.

A **campaign** (or a folder, or a project) gets a roll-up instead: a totals strip —
pass/fail/skip/5xx, pack alerts, logs incomplete, and how many of its rounds have never
run — over **one row per endpoint**, each row the round's own latest run with its
status, counts, coverage and p50/p95, and a link to each case that failed. Coverage and
latency stay per row and are never summed: a p95 over forty rounds is not the average
of forty p95s, so the table shows each and claims no campaign number. Selecting a row
opens that round on its history.

## Artifacts of a run

```
runs/<stamp>-<round-id>/
  round.yaml
  summary.json              # counts, packs, coverage, latency, descriptor origin, oracle, selection
  evidence.md               # the same story, for a human
  book.json                 # only for suites: what the oracle priced, and why not
  steps/001-<case-id>/      # request, response, packs, logs, verdict
  probes/<id>/              # before, after, delta, oracle
runs/latest -> <the last run>
```

A run that replayed a slice of its round appends `~case-<case id>` or `~from-<case id>`
to the directory name and puts the same string in `summary.json` as `selection`. That is
what keeps a single-case run from answering as the round's latest run.

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

### The bundled demo

Both examples above are green on purpose, and neither is package data — so neither can
show a failure from a wheel. The demo can:

```bash
bin/demo                          # the terminal door
```

or the **Demonstração** switch in the client's **Servidor** dialog, or **Abrir o
projeto de demonstração** in `⌘K`. All three do the same thing: start a deterministic
mock API on an ephemeral loopback port, copy the sample project out of the wheel into
`$XDG_DATA_HOME/heimdall-qa/demo/`, point its `base_url` at the port the socket
actually bound, and open it. The sample ships recorded runs, so the tree, the statuses
and the step review render with nothing running at all.

It carries a *green* path and every deliberate red one — a case expecting a status the
API will not give (`fail`), `GET /demo/boom` (`http_5xx`), a route past the budget and a
`WARN` log line (`warn`), a round that fails `validate` (`not_ready`), a campaign row
pointing at a file that is not there (`missing`) and a round with no run
(`not_reviewed`) — plus a suite with a `probe_begin`/`probe` pair and a
`capture_response` → `generate: captured.*` chain. Its own
[`README.md`](src/heimdall_qa/demo/sample/README.md) says what each case is for and
which status it produces.

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
harness also speaks MCP.

Inside the client, it is a **switch**: `heimdall-qa` has the server in its own process,
off until you turn it on in the **Servidor** dialog, which also prints the block to
paste into your MCP client and takes a `--mcp-port` if 8765 is taken. The window and the
model then look at one registry, one tree and one set of runs — they cannot disagree,
because they are the same process.

```bash
pip install 'heimdall-qa[mcp]'   # the extra the switch needs
heimdall-qa --mcp-port 9100      # the window, with MCP on 127.0.0.1:9100
```

Without the extra the switch reports `MCP_EXTRA_MISSING` and the `pip install` line
rather than taking the window down. The bind is loopback and asserted as such: an MCP
server exposes *execution* — `run_round` goes to the network and writes evidence — so
it is not something to hand to a LAN.

A process with no window is the other way round, and the same tools:

```bash
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
directory. `root` defaults to the **first project in the registry** — the one the person
has open, which is the one they are asking about — and only then to the process's
directory; and run evidence defaults to `root/runs` rather than `cwd/runs`, so a model
in the project directory gets the same answer either way. And a **domain** refusal comes
back as an object with the code, the message and the fix instead of as an exception,
because a refusal a model can act on is worth more than a traceback it can only repeat:

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
bin/verify                    # all five gates in order; --fast skips the provider ones
```

Pytest does not need any API to be running, and does not need a provider installed.
The Java parity target has the same rule: its `-m slow` half skips without a JDK, and
its source-reading half never needed one.

CI runs these plus the five gates from [contrib/README.md](contrib/README.md) — the
list `bin/verify` writes down once — on Python 3.12, 3.13 and 3.14, each from
`bin/bootstrap` rather than from a hand-written install, so a broken clone fails there
and not at a colleague's first command. The workflow is
[.github/workflows/ci.yml](.github/workflows/ci.yml).
