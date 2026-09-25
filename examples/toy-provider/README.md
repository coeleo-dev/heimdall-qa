# Toy provider — the entry ramp

The cheapest complete Heimdall QA setup there is, and the thing that proves the
harness is generic rather than merely renamed: **three routes, one descriptor of
six lines, three cases, no provider package, no log files**. If any of that
required a special case in `src/`, the harness would not be generic.

It is the `tests/fixtures/descriptors/toy.yaml` fixture, grown up: same six-line
descriptor, now with a mock to run it against.

| Path | What it is |
| --- | --- |
| `app.py` | The API, three routes, FastAPI only. `GET /toy/health`, `GET /toy/items`, `POST /toy/items`. |
| `qa/project.yaml` | The project descriptor: one environment, nothing else. No auth, no routes, no budgets, no log sources, no provider. |
| `contracts/` | One contract per endpoint. Empty `fields`, so one case each covers the whole endpoint. |
| `cases/` | Three cases, one per route, in one file per area. |
| `suites/smoke.yaml` | A loop step per case. |
| `rounds/smoke.yaml` | The round, and what you actually run. |
| `campaigns/smoke.yaml` | The manifest: one row, naming the round. |

## Run it

```bash
pip install fastapi uvicorn          # if the harness did not already bring them

cd examples/toy-provider
python -m uvicorn app:app --port 8110    # leave it running
heimdall-qa validate rounds/smoke.yaml
heimdall-qa run rounds/smoke.yaml
heimdall-qa last-run
```

`heimdall-qa run` writes into `./runs` relative to where you stand, which is why
the commands `cd` first.

Expected: three passes, zero failures, and `runs/latest/summary.json` recording
`"origin": "target"` for the descriptor above.

## What it deliberately does not do

- **No log sources.** The API writes no files, so the packs that need a trace line
  (`http.success`, `observability`) report **skipped — no log source declared,
  nothing to read**. Unmeasured is not incomplete, and it is never a failure.
- **No provider.** With no `provider:` in the descriptor the run uses the neutral
  oracle, which counts what it can price and calls nothing a `402`.
- **No auth.** `auth.surface` passes on a project that declares no credential, and
  moves aside on one that does.
- **No certificate of realism.** This is not a second product; it is the ramp.

## Gate T3

From the repository root, with a wheel built by `python -m build` or
`pip wheel . --no-deps -w dist`:

```bash
python -m venv /tmp/gate
/tmp/gate/bin/pip install dist/heimdall_qa-*.whl
/tmp/gate/bin/python -c "import heimdall_qa"   # the core, on its own
/tmp/gate/bin/python -c "
import importlib.util
assert not any(importlib.util.find_spec(n) for n in ('playwright', 'axe_playwright_python', 'selenium'))
"                                                                          # no browser automation
# A webview is not automation: the desktop client draws this harness's own screen in a
# native window and drives nothing. What is banned is Playwright and axe, which steer a
# real browser to test a frontend — and no part of reviewing an API needs one.
/tmp/gate/bin/python -c "
from importlib.metadata import entry_points
assert not list(entry_points(group='heimdall_qa.providers')), 'a provider came with the wheel'
"                                                                          # no provider, either

cd examples/toy-provider
python -m uvicorn app:app --port 8110 &
/tmp/gate/bin/heimdall-qa validate rounds/smoke.yaml
/tmp/gate/bin/heimdall-qa run rounds/smoke.yaml
/tmp/gate/bin/heimdall-qa last-run
```

The navigation artifact of a run today is `runs/latest/` (`summary.json`,
`book.json`, `steps/`, `evidence.md`). `run.json` — the single versioned document
the review UI reads — is fase 2.5, so the gate asserts `summary.json` until then.
