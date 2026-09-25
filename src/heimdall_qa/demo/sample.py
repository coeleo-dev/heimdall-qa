"""The sample tree, copied out of the wheel and turned into an openable project.

The sample is package data, so it is read-only: a descriptor whose `base_url` names a
port fixed in a repository would be a descriptor pointing at nothing half the times it
was opened. Materialization is what makes the two halves agree — it copies the tree,
rewrites the one line that names an origin, copies the recorded runs into an empty
`runs/`, and registers the result through the same gate a project added by hand goes
through.

**The recorded runs are copied only into an empty `runs/`.** A demo the reviewer has
already run holds their own evidence, and overwriting it with the shipped run would
delete the one thing the demo is for. An empty `runs/` is the first materialization,
and that is the only moment the shipped evidence is wanted.

`DemoService` is the process-level half: it owns the `DemoRuntime` and the decision of
when the mock has to come up before a project can be materialized around it.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

from heimdall_qa import operations
from heimdall_qa.demo.runtime import HOST
from heimdall_qa.demo.runtime import DemoApiState
from heimdall_qa.demo.runtime import DemoRuntime
from heimdall_qa.errors import HarnessError
from heimdall_qa.projects import ProjectRef
from heimdall_qa.projects import ProjectsRegistry
from heimdall_qa.projects import data_home

#: The names materialization moves between: the shipped folder, the evidence folder it
#: becomes, and the folder under the data home the whole thing lands in.
SAMPLE_DIR_NAME = "sample"
RECORDED_DIR_NAME = "recorded"
RUNS_DIR_NAME = "runs"
DEMO_DIR_NAME = "demo"
LOG_FILE_NAME = "demo.log"

#: The one line of the descriptor materialization rewrites, anchored to the key so a
#: comment that happens to mention a URL is not the thing that gets replaced.
_BASE_URL = re.compile(r"^(?P<prefix>\s*base_url:\s*)\S+\s*$", re.MULTILINE)


def sample_dir() -> Path:
    """The shipped tree, read off `__file__`.

    The harness copies and rewrites beside it, which a zip-based `importlib.resources`
    handle cannot do, so the plain path is the honest one.
    """
    return Path(__file__).resolve().parent / SAMPLE_DIR_NAME


def demo_root(data_dir: Path | None = None) -> Path:
    """Where the materialized demo lives. `data_home()` unless a caller said otherwise."""
    return (data_dir if data_dir is not None else data_home()) / DEMO_DIR_NAME


def materialize_demo(
    *,
    base_url: str,
    data_dir: Path | None = None,
    registry: ProjectsRegistry | None = None,
) -> ProjectRef:
    """Copy the sample out, point it at `base_url`, and register it.

    Idempotent in the sense that matters: re-running it refreshes the content and
    leaves an existing `runs/` alone. `base_url` is required and has no default,
    because a default would be a second origin the caller did not choose and cannot
    see.
    """
    root = demo_root(data_dir)
    _copy_tree(root)
    _rewrite_base_url(root / "qa" / "project.yaml", base_url)
    _copy_recorded(root)
    return operations.register_project(root, registry=registry)


def _copy_tree(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        sample_dir(),
        root,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(RECORDED_DIR_NAME),
    )


def _rewrite_base_url(descriptor: Path, base_url: str) -> None:
    """The one generated line. A descriptor without a `base_url` is a bug in the
    sample, so it is refused by name instead of silently left pointing at 8130."""
    text = descriptor.read_text(encoding="utf-8")
    rewritten, count = _BASE_URL.subn(lambda match: f"{match.group('prefix')}{base_url}", text)
    if count == 0:
        raise HarnessError(
            code="DEMO_NO_BASE_URL",
            message=f"the demo descriptor has no base_url to point at the mock: {descriptor}",
            hint="reinstall heimdall-qa; the bundled sample is missing a line it ships",
        )
    descriptor.write_text(rewritten, encoding="utf-8")


def _copy_recorded(root: Path) -> None:
    """The shipped evidence, into `runs/` only when there is no evidence already."""
    recorded = sample_dir() / RECORDED_DIR_NAME
    if not recorded.is_dir():
        return
    runs = root / RUNS_DIR_NAME
    if runs.is_dir() and any(runs.iterdir()):
        return
    shutil.copytree(recorded, runs, dirs_exist_ok=True)


@dataclass
class DemoService:
    """The demo's process: the mock it starts, and the project built around it.

    The runtime is created on the first `start` and kept, so the second press of the
    button — and every `GET /api/demo` — reports the same socket. `stop` leaves the
    files where they are: the project the reviewer has been reading is not theirs to
    lose because they closed a dialog.
    """

    data_dir: Path | None = None
    host: str = HOST
    _runtime: DemoRuntime | None = field(default=None, init=False, repr=False)

    @property
    def root(self) -> Path:
        return demo_root(self.data_dir)

    def state(self) -> DemoApiState:
        if self._runtime is None:
            return DemoApiState(
                host=self.host,
                port=0,
                state="stopped",
                log_path=str(self.root / LOG_FILE_NAME),
            )
        return self._runtime.state()

    def start(self, registry: ProjectsRegistry | None = None) -> tuple[DemoApiState, ProjectRef | None]:
        """Bring the mock up, and materialize a project pointed at it.

        A refused bind is the answer and not an exception: the state carries the
        reason and the caller draws it where the button is. Nothing is materialized
        then — a descriptor pointed at a port nothing listens on would be a project
        that only fails.
        """
        if self._runtime is None:
            self._runtime = DemoRuntime(
                log_path=self.root / LOG_FILE_NAME,
                host=self.host,
            )
        state = self._runtime.start()
        if not state.enabled:
            return state, None
        ref = materialize_demo(
            base_url=state.base_url,
            data_dir=self.data_dir,
            registry=registry,
        )
        return state, ref

    def stop(self) -> DemoApiState:
        if self._runtime is None:
            return self.state()
        return self._runtime.stop()
