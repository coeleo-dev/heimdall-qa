# The bundled demo project

A complete, self-contained project the review screen can open with nothing of yours
on disk. It exists so the harness's vocabulary is visible without an API to point it
at: every status the tree can draw, every pack outcome worth showing, a probe, a
capture chain and a recorded run for each round that has one.

`materialize_demo` copies this tree into the harness's data directory, rewrites the
one `base_url:` line to the port the mock actually bound, copies `recorded/` into
`runs/`, and registers the result — the same `operations.register_project` gate a
project you add by hand goes through.

## What produces what

| Where | What it shows |
| --- | --- |
| `rounds/smoke.yaml` | Four greens: health, a lookup, the counter, a clean 404. |
| `rounds/chain.yaml` | A `probe_begin` + `probe` step, and `captured.id` carried from one create into the next. |
| `rounds/boom.yaml` | `http_5xx`: one route that always answers 500. |
| `rounds/warn.yaml` | Two warns: a `WARN` log on a 200, and 120 ms against a 40 ms budget. |
| `rounds/red.yaml` | The plain `fail`: a case expecting 404 that the API answers 200. |
| `rounds/not-ready.yaml` | `not_ready`: the round loads, the case it includes is not there. |
| `rounds/unrun.yaml` | `not_reviewed`: a valid round nobody has run. |
| `campaigns/demo.yaml` | The roll-up: one row per round, including `rounds/absent.yaml`, whose file does not exist, so the tree draws `missing`. |

Two rows in `campaigns/demo.yaml` are broken on purpose — `not-ready` and `absent` —
because a campaign is a manifest of intentions and the table has to say which rows
are broken rather than refuse to draw. `heimdall-qa campaign validate` reports both;
that is the command working, not the sample failing.

## The mock

`../app.py` is the API these rounds talk to: five routes, no auth, no database, and
one deterministic behaviour per route. It writes one log line per request carrying
the trace id, which is why the correlation packs are measured here instead of
skipped — `qa/project.yaml` declares that file as its one `log_source`.

## The recorded runs

`recorded/` holds five finished runs in the `runs/<stamp>-<round_id>/` shape the
harness writes, so the KPIs, the tree badges and the step review render before the
API is started. They ship under a second name because `runs/` is gitignored; the
materializer copies them into `runs/` only when `runs/` is empty, so a demo you have
already run is never overwritten by the shipped evidence.

## Running it

From a checkout:

```
bin/demo                 # materialize, start the mock, run one green and one red round
```

Or from the client: `Servidor` → `Projeto de demonstração`. Both materialize the same
tree into `~/.local/share/heimdall-qa/demo/` (override with `$HEIMDALL_QA_DATA`).
