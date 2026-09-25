"""The projects the client knows about, and where it remembers them.

The client used to open exactly one root, the one it was started in. That is the right
default and a poor ceiling: a reviewer who is working across an API and the library that
consumes it has two collections, and re-launching the app to switch between them is the
kind of friction that ends with nobody using the tree at all.

So the roots a client has opened are remembered in a file of its own, and the tree draws
all of them. The file lives in the **user's** configuration directory and not in any
repository, because what it records is the reviewer's organisation rather than the
project's: two people may legitimately open different sets of projects, and neither set
belongs in a commit.

Three decisions in here are worth naming:

**The id is the folder name plus a hash of the resolved path.** A bare name collides the
moment somebody has two checkouts of the same repository — `~/work/orders-api` and
`~/sandbox/orders-api` are the same name and different projects — and a bare path is
unreadable in a log line or a tree key. The pair gives a key that is stable for a
checkout, legible, and unique across them.

**Registering demands that the directory look like a project.** This is the one place
the harness grows what it reads: a registered root is a root the tree walks, the editor
opens and the engine can run. Accepting `/` or `$HOME` by accident would turn a slip in
a file dialog into a very slow index over a home directory, so a root without any of the
markers below is refused by name.

**The registry path is overridable from the environment.** `HEIMDALL_QA_REGISTRY` exists
so the test suite can point at a throwaway file: without it, every test that touches a
registry writes into the home of whoever is running the suite, and a test that mutates
the developer's real projects is worse than no test.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

import yaml

from heimdall_qa.config import HarnessConfig
from heimdall_qa.errors import HarnessError

#: What a directory has to contain to be registered. Any one of them is enough, and the
#: `qa/` pair is here because a project whose descriptor declares `content_root: qa`
#: keeps its campaigns and rounds one level down — checking only the root would refuse
#: the layout the documentation recommends.
_PROJECT_MARKERS = (
    Path("qa/project.yaml"),
    Path("campaigns"),
    Path("rounds"),
    Path("qa/campaigns"),
    Path("qa/rounds"),
)

_SLUG = re.compile(r"[^a-z0-9]+")

_HASH_LENGTH = 8


def registry_path() -> Path:
    """Where the registry file is, honouring the environment override.

    `$HEIMDALL_QA_REGISTRY` wins outright — it names a file, not a directory, so a test
    can point at `tmp_path / "projects.yaml"` and be certain nothing else is touched.
    Failing that, the XDG config directory, and failing that `~/.config`, which is what
    XDG says to do when the variable is unset.
    """
    override = os.environ.get("HEIMDALL_QA_REGISTRY")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(base) if base else Path.home() / ".config"
    return config_home / "heimdall-qa" / "projects.yaml"


def data_home() -> Path:
    """Where the harness keeps data of its own — not configuration, not evidence.

    One thing lives here today: the materialized demo project. It is data rather than
    configuration because it is not the reviewer's decision — nobody chose a
    `campaigns/demo.yaml` — and it is not evidence because a run's evidence lives
    beside the project that ran.

    `$HEIMDALL_QA_DATA` names a directory and wins outright, so a test materializes
    into a throwaway path instead of the home of whoever runs the suite. A file
    override would have been the wrong shape here: a caller may want to add a second
    thing under this directory later, and a variable that points at a file cannot.
    """
    override = os.environ.get("HEIMDALL_QA_DATA")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_DATA_HOME")
    data_root = Path(base) if base else Path.home() / ".local" / "share"
    return data_root / "heimdall-qa"



def id_for(root: Path) -> str:
    """The stable id of a project root: its folder name, then a short path hash.

    Pure and deterministic, so the id a registry writes and the id a workspace derives
    without one are the same string — which matters because the key format carries it
    and a mismatch would be two trees over one project.
    """
    resolved = Path(root).expanduser().resolve()
    slug = _SLUG.sub("-", resolved.name.lower()).strip("-") or "project"
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:_HASH_LENGTH]
    return f"{slug}-{digest}"


def looks_like_project(root: Path) -> bool:
    """Whether a directory carries any of the marks of a project this harness reviews."""
    if not root.is_dir():
        return False
    return any((root / marker).exists() for marker in _PROJECT_MARKERS)


@dataclass(frozen=True)
class ProjectEntry:
    """One line of the registry: what is durable about a project.

    Deliberately *not* the resolved configuration. `runs_dir`, the descriptor and the
    secrets are facts about a moment — a secrets file moves, a descriptor gains a
    source — and a registry that stored them would be a second copy of the truth that
    goes stale between two launches. They are resolved when a session opens the project.
    """

    id: str
    name: str
    root: Path


@dataclass(frozen=True)
class ProjectRef:
    """A project with the wiring a session or an engine needs to use it.

    `config` carries the `ProjectView`, which is what `plan_for` reads to find the
    manifest behind a campaign. The two travel together because they are resolved
    together and a caller holding one without the other has half a project.
    """

    id: str
    name: str
    root: Path
    runs_dir: Path
    config: HarnessConfig
    secrets: Mapping[str, str] = field(default_factory=dict)


class ProjectsRegistry:
    """The registry file: read it, change it, write it back.

    A value object per load. Nothing here caches across a write, because the file is
    small, it is read once at startup, and a cache would be one more thing to
    invalidate when a second window registers a project.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or registry_path()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> tuple[ProjectEntry, ...]:
        """Every registered project, in registration order.

        A file that does not exist is an empty registry and not an error: the first
        launch of a fresh install has none, and refusing to start over that would make
        the client unusable until somebody hand-wrote a YAML file.
        """
        if not self._path.is_file():
            return ()
        try:
            data = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise HarnessError(
                code="REGISTRY_INVALID",
                message=f"the projects registry could not be read: {self._path}",
                hint="fix or delete it; the client starts with no projects without it",
                details=(str(exc),),
            ) from exc
        if not isinstance(data, dict):
            raise HarnessError(
                code="REGISTRY_INVALID",
                message=f"the projects registry must be a mapping: {self._path}",
                hint="it holds one key, `projects`, with a list under it",
            )
        entries: list[ProjectEntry] = []
        for raw in data.get("projects") or []:
            if not isinstance(raw, dict) or not raw.get("root"):
                continue
            root = Path(str(raw["root"])).expanduser()
            entries.append(
                ProjectEntry(
                    id=str(raw.get("id") or id_for(root)),
                    name=str(raw.get("name") or root.name),
                    root=root,
                )
            )
        return tuple(entries)

    def list(self) -> tuple[ProjectEntry, ...]:
        """`load`, named for the caller that only wants to draw them."""
        return self.load()

    def resolve(self, project_id: str) -> ProjectEntry | None:
        for entry in self.load():
            if entry.id == project_id:
                return entry
        return None

    def add(self, root: Path, *, name: str | None = None, save: bool = True) -> ProjectEntry:
        """Register a root, or refresh the name of one that is already registered.

        Idempotent on the id, so pointing the launcher at the same directory twice is
        one entry and not two. The gate runs first: a directory that does not look like
        a project is refused before anything is written.
        """
        resolved = Path(root).expanduser().resolve()
        if not looks_like_project(resolved):
            raise HarnessError(
                code="REGISTRY_NOT_A_PROJECT",
                message=f"{resolved} does not look like a project to review",
                hint=(
                    "a project has qa/project.yaml, or campaigns/ and rounds/;"
                    " point --root at one of those, or run `heimdall-qa init` there"
                ),
            )
        entry = ProjectEntry(
            id=id_for(resolved),
            name=name or resolved.name,
            root=resolved,
        )
        entries = [item for item in self.load() if item.id != entry.id]
        if not save:
            return entry
        self._write((*entries, entry))
        return entry

    def remove(self, project_id: str) -> bool:
        """Forget a project. `False` when it was never registered."""
        entries = self.load()
        kept = tuple(item for item in entries if item.id != project_id)
        if len(kept) == len(entries):
            return False
        self._write(kept)
        return True

    def _write(self, entries: tuple[ProjectEntry, ...]) -> None:
        """Replace the file atomically, so an interrupted write keeps the old registry.

        A filesystem refusal becomes the harness's own error rather than a bare
        `OSError`: this runs on a request that flipped a switch in the dialog, and the
        reader needs the path and the reason, not a 500 with a traceback in a log they
        are not looking at.
        """
        payload = {
            "projects": [
                {"id": entry.id, "name": entry.name, "root": str(entry.root)}
                for entry in entries
            ]
        }
        text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                newline="\n",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            )
            temporary = Path(handle.name)
            try:
                with handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self._path)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise HarnessError(
                code="REGISTRY_UNWRITABLE",
                message=f"the projects registry could not be written: {self._path}",
                hint=(
                    "check that the directory exists and is writable, or point"
                    " HEIMDALL_QA_REGISTRY at a path that is"
                ),
                details=(str(exc),),
            ) from exc
