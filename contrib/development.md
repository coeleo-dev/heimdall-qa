# Developing Heimdall QA

[`../CONTRIBUTING.md`](../CONTRIBUTING.md) is the bar and the gate list;
[`architecture.md`](architecture.md) is the structure. This is the path between them:
what to install, which command runs which check, where a given moving part lives, and
what to do when one of the three toolchains fails in the way it likes to fail.

The one rule from `architecture.md` that governs everything here: **nothing belonging to
the project under review may reach this repository.** Its descriptor, cases, providers
and write-ups stay on the machine that has them. `bin/audit-remote` is the gate, not
the reminder.

## Prerequisites

| Tool | Needed for | Notes |
| --- | --- | --- |
| Python 3.12+ | everything | 3.12, 3.13 and 3.14 are all green in CI. |
| Node 20.19+ / 22.12+ | rebuilding the client | Never needed to *run* it: the built bundle is committed. |
| GTK + WebKit2GTK | the desktop window on Linux | `sudo apt install python3-gi gir1.2-webkit2-4.1`, and a venv with `--system-site-packages`. |
| JDK 25 | `examples/spring-fixture/gate.sh` | The harness needs none; the Java *target* it reads and talks to does. |
| Maven | the Spring gate | The fixture carries a wrapper, so `./mvnw` is enough. |

One command gets all of it into place:

```bash
bin/bootstrap
```

It is idempotent, so run it again after a `git pull`. On a machine with no GTK — a
server, a container, CI's own lower half — `bin/bootstrap --no-desktop` skips Node and
the GTK checks; `bin/bootstrap --check` asserts what is already installed and writes
nothing.

## The scripts

| Script | When |
| --- | --- |
| `bin/bootstrap` | Once after cloning, and after a pull that changes `pyproject.toml`. |
| `bin/web` | Before committing anything under `desktop/webapp/`. |
| `bin/verify` | Before opening a pull request; `--fast` while iterating. |
| `bin/demo` | To see the harness work without a target of your own. |
| `bin/sync-skills` | After editing `.agents/skills/`. |
| `bin/audit-remote` | Part of `bin/verify`; run alone when the change touches the boundary. |

`bin/verify` is the gate list written down once — the core's suite, the reader's suite,
`bin/audit-remote`, and the two provider gates. The documents that used to restate it
link here instead, because four copies of a list is three copies that go stale.

## The test tiers

```bash
.venv/bin/python -m pytest -q              # the core, no product required
.venv/bin/python -m pytest -q packages/spring   # the reader, no JVM required
HEIMDALL_QA_SLOW=1 .venv/bin/python -m pytest -q   # the live half
```

The first two are the tier a change is judged by and need nothing running. The third is
marked `slow` and is skipped by default: it tries a real API on `localhost` and builds
the Spring fixture when a JDK is present, which is why CI runs it in the jobs that have
the toolchain rather than in the `clone` job.

A test that needs a project builds one under `tmp_path` and points
`$HEIMDALL_QA_REGISTRY` at a throwaway file — never at the registry in your home, and
never at a project of yours. The same rule reaches the demo project: the suite injects
`DemoService(data_dir=tmp_path/...)` rather than materializing into
`~/.local/share/heimdall-qa/`, so a test run leaves your own demo alone.

## The client

`desktop/webapp/` is a Vite + React + Tailwind app. Its build output is **not** there:
`vite.config.ts` writes it to `src/heimdall_qa/desktop/webapp/`, and that built bundle
is committed, so `pip install` never needs Node — the same contract a lockfile has.

That makes the bundle a checked-in artifact, and `bin/web`'s last step is the check that
keeps it honest:

```bash
bin/web            # npm ci, typecheck, lint, test, build, drift check
bin/web --check    # the same against an existing node_modules
```

If the drift check fails, the committed copy is stale. Commit the rebuild it produced —
the reviewer would otherwise be drawing last week's client. The same check runs as the
`client` job in CI, so a forgotten rebuild is a red job and not a surprise in a window.

Two things a change to the client usually touches:

- **The contract.** `src/types.ts` mirrors `src/heimdall_qa/serve/models.py` field for
  field. The Python models are `extra="forbid"`, so a field that exists in one and not
  the other fails validation before it can render a blank pane — add both, and the
  Python tests in `tests/test_serve*.py` are what prove the pair.
- **The words.** The panel's Portuguese labels ship from Python (`serve/labels.py`),
  once per bootstrap. The client never hardcodes a status word.

## The client and the demo

The bundled demo has two doors into the same act:

```bash
bin/demo                          # terminal: materialize, run green + red, print paths
```

and, in the window, the **Demonstração** switch in the **Servidor** dialog or **Abrir o
projeto de demonstração** in `⌘K`. Both call `POST /api/demo`, which starts the mock on
an ephemeral port, materializes the sample around the port the socket bound, and opens
the project. The sample is package data (`src/heimdall_qa/demo/sample/`,
`[tool.setuptools.package-data]` in `pyproject.toml`), and the recorded runs ship under
`recorded/` because `runs/` is gitignored — materialization copies them in only when
the target `runs/` is empty, so it never overwrites evidence a reviewer made.

To add a case to the demo, add it under `src/heimdall_qa/demo/sample/` and run
`bin/demo`; the package data glob picks it up. Keep the sample provider-neutral: it is
inside `src/heimdall_qa/`, which `bin/audit-remote` claim 3 greps for product names.

## Where things live

| Question | File |
| --- | --- |
| How does a request get replayed and checked? | `src/heimdall_qa/runner.py`, `src/heimdall_qa/packs/` |
| How is the YAML parsed and validated? | `src/heimdall_qa/schema/` |
| What does a project descriptor mean? | `src/heimdall_qa/project.py`, `src/heimdall_qa/schema/descriptor.py` |
| How is a run directory written? | `src/heimdall_qa/run_store.py` |
| Where is the review API? | `src/heimdall_qa/serve/` — `api.py` routes, `panel.py` payloads, `models.py` the contract |
| Where is the client? | `desktop/webapp/src/` (source), `src/heimdall_qa/desktop/webapp/` (the committed build) |
| What runs a plan over a selection? | `src/heimdall_qa/engine.py`, `src/heimdall_qa/plan.py`, `src/heimdall_qa/workspace.py` |
| How does the CLI dispatch? | `src/heimdall_qa/cli.py` |
| How does a provider register? | `src/heimdall_qa/provider.py`, and `architecture.md` §1 |
| What does the MCP surface expose? | `src/heimdall_qa/mcp/` |
| What is the demo? | `src/heimdall_qa/demo/` |

`architecture.md` is the map of the seams; this table is the map of the files.

## Failure modes

**The desktop window does not open.** On Linux pywebview needs the system GTK/WebKit,
and `python3-gi` belongs to the *system* Python — a venv on a different interpreter
cannot import it. The shell refuses by name (`DESKTOP_NO_PYWEBVIEW`) and says which
interpreter is short. Fix: `sudo apt install python3-gi gir1.2-webkit2-4.1`, and a venv
made with `--system-site-packages` (`bin/bootstrap` does that for a fresh one).
`heimdall-qa serve` is the same client in a browser and needs none of it.

**A port is already taken.** `heimdall-qa serve` uses 7878, the embedded MCP server 8765,
and the demo an ephemeral port the kernel chooses. A fixed one is refused by name
(`MCP_BIND_FAILED`) with the port in the message, and the Server dialog draws that where
the switch is. The demo cannot collide, which is the reason its port is ephemeral.

**The registry refuses a directory.** `REGISTRY_NOT_A_PROJECT`: the path is not a
project. A project has `qa/project.yaml`, or `campaigns/` and `rounds/`. The gate runs
before anything is written, so a slip in a file dialog cannot enroll `$HOME`. Point it
at the directory that has one of those, or run `heimdall-qa init --root DIR` there.

**The demo's files are read-only or the data home is not writable.** Materialization is
a `copytree` into `$XDG_DATA_HOME/heimdall-qa/demo/`; `$HEIMDALL_QA_DATA` overrides the
root outright, which is how the suite keeps it hermetic. A refusal to write there comes
back as the harness's own error with the path.

**`bin/web` reports bundle drift.** The build in the working tree differs from the
committed one. Commit the rebuild (or check out the bundle if the change was not meant
to produce one). CI's `client` job runs the same check.

**`bin/audit-remote` fails.** Something in the tree names or carries the product this
harness was built against. Claims 1–2 are the boundary; claim 3 is the core naming a
product; claim 4 is the suite passing in the tree as it would ship. The failure names
the file — start there.

**The wheel is missing the client or the demo.** `package_data` with a single `*` does
not cross a `/`, so a nested tree is silently left out while the source checkout keeps
working. The `wheel` job asserts the bundle's files are actually inside the wheel; add
the new nested path to `[tool.setuptools.package-data]` if you add a tree.
