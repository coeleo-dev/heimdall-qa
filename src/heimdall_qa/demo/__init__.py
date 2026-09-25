"""The demo project that ships inside the package, and the API it reviews.

`examples/` is documentation and is not package data, so neither of the two examples
can be opened from an installed wheel. This one can: `sample/` is declared in
`[tool.setuptools.package-data]`, `app.py` is the mock the sample talks to, and
`runtime.py` starts that mock on an ephemeral loopback port.

The three parts exist because a demo a reviewer cannot run is a screenshot:

- **`app.py`** — a deterministic FastAPI app. No database, no state that grows, and
  no auth. It is deliberately the shape of an ordinary small service: a happy path,
  a 404, a 500, a warning the log carries, and one route that is slower than the
  budget its descriptor declares.
- **`runtime.py`** — that app on a thread, on a port the OS picks, so a demo opened
  twice or beside a real service does not fight for 8000.
- **`sample/`** — the project tree the harness reviews, with a recorded run per
  round so the KPIs, the tree badges and the step review all render before anything
  has been executed.

Nothing here names a product, because everything under `src/heimdall_qa/` is the
core and the core is generic by construction.
"""

from __future__ import annotations

__all__ = ["app", "runtime", "sample"]
