"""The YAML a run is made of, read and written from the client.

Why this is a module and not two more routes in `serve/api.py`: it is the only place in
harness that **writes a file a person wrote**. A read that is wrong wastes a minute; a
write that is wrong destroys work, and the client offering an editor makes that write
reachable from a text box. So the rules are stated once, here, and the route is a thin
translation of them.

Three of them are worth naming, because each is a decision rather than a detail:

1. **A path cannot leave the content root.** Not by `..`, not by an absolute path, not
   by a symlink: the resolved path is checked, so a link pointing outside is refused
   for the same reason `..` is. The desktop client is a loopback app, but "only I can
   reach the port" has never been a reason to let a path escape a root.

2. **The kind is decided by the directory, never by the caller.** `contracts/` gets the
   contract loader, `rounds/` gets the round validator. If the caller could name the
   kind, it could ask for a round to be checked by the contract rules and be told a
   broken round is fine.

3. **Saving and validating are one operation.** The validator reads the file on disk,
   which means what the screen reports is a fact about what exists — not about a copy
   of it. A separate "validate these bytes" path would be a second answer, and the two
   would disagree the first time the loader resolved an `include:` relative to itself.

The file is written through a temporary in the same directory and an atomic rename, so
an interrupted save leaves the previous version rather than half of the new one. That
is the same reason the engine writes evidence per step instead of at the end.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError
from yaml import YAMLError

from heimdall_qa.campaign import validate_campaign
from heimdall_qa.errors import HarnessError
from heimdall_qa.findings import ValidationFinding
from heimdall_qa.findings import finding
from heimdall_qa.project import ProjectView
from heimdall_qa.schema.load import content_root
from heimdall_qa.schema.load import load_case
from heimdall_qa.schema.load import load_contract
from heimdall_qa.schema.load import load_suite
from heimdall_qa.schema.load import resolve_path
from heimdall_qa.schema.load import split_selector
from heimdall_qa.schema.models import SuiteFile
from heimdall_qa.validate import validate_round

#: Which directory holds which kind. The first component of a content path is the
#: whole of the rule, and a directory that is not here is read-only by construction.
_KINDS_BY_DIRECTORY = {
    "contracts": "contract",
    "rounds": "round",
    "campaigns": "campaign",
    "suites": "suite",
}

#: What a document the client cannot write is called. `unknown` rather than `None` so
#: that a model field of `str` does not need a null case for "the tree showed us a
#: file we cannot edit".
READ_ONLY = "unknown"

_SUFFIXES = frozenset({".yaml", ".yml"})


@dataclass(frozen=True)
class SourceDocument:
    """One file, its kind, and what `validate` says about it as it stands on disk."""

    path: str
    kind: str
    text: str
    findings: tuple[ValidationFinding, ...]

    @property
    def editable(self) -> bool:
        """Whether this path is one the client may save over."""
        return self.kind != READ_ONLY

    @property
    def valid(self) -> bool:
        return not self.findings


def read_source(
    root: Path,
    project: ProjectView | None,
    raw_path: str,
) -> SourceDocument:
    """The file at `raw_path`, with the findings the validator gives it right now.

    Validating on read and not only on save is deliberate: opening a round that is
    already broken should show why, without the reviewer having to press anything. It
    costs a parse of the file plus its includes, and it happens when a person clicks.
    """
    target, relative = _resolve(root, project, raw_path)
    if not target.is_file():
        raise HarnessError(
            code="NOT_FOUND",
            message=f"no such file in the project's content: {relative}",
            hint="the path is relative to the content root, e.g. rounds/smoke.yaml",
        )
    return SourceDocument(
        path=relative,
        kind=kind_of(relative),
        text=target.read_text(encoding="utf-8"),
        findings=validate_source(kind_of(relative), target, root, project),
    )


def write_source(
    root: Path,
    project: ProjectView | None,
    raw_path: str,
    text: str,
) -> SourceDocument:
    """Save the text, then report what the validator makes of what was saved.

    A document that turns out invalid is **kept**: refusing the write would throw away
    the edit the reviewer just made, and an invalid round on disk is not dangerous —
    `validate` is the gate and a run refuses it. What the screen then has to do is show
    the findings, which is what the returned document carries.
    """
    target, relative = _resolve(root, project, raw_path)
    kind = kind_of(relative)
    if kind == READ_ONLY:
        raise HarnessError(
            code="SOURCE_NOT_EDITABLE",
            message=f"{relative} is not one of the files this client edits",
            hint="edit contracts, rounds, campaigns and suites; the rest is hand-written",
        )
    _write_atomically(target, text)
    return SourceDocument(
        path=relative,
        kind=kind,
        text=text,
        findings=validate_source(kind, target, root, project),
    )


def kind_of(relative: str) -> str:
    """The kind a content path declares, from its first directory."""
    first = Path(relative).parts[0] if Path(relative).parts else ""
    return _KINDS_BY_DIRECTORY.get(first, READ_ONLY)


def create_folder(root: Path, project: ProjectView | None, raw_path: str) -> str:
    """Make a real folder under `campaigns/`, and answer with its relative path.

    Folders are how a reviewer sorts the campaigns agents wrote, so this is the one
    directory the client may create. It writes nothing else: a folder is an empty
    directory, and the tree keeps empty directories — see `collection._campaign_subtree`
    — precisely so that creating one is immediately visible and a campaign has
    somewhere to be moved to.

    Parents are made as needed, so "Nova pasta" with `campaigns/ingest/sandbox` is one
    action and not two. An existing path is refused rather than tolerated: silently
    succeeding on a folder that was already there would tell the reviewer their new
    folder is somewhere it is not.
    """
    content = content_root(root, project).resolve()
    target, relative = _contained(content, raw_path)
    if not _is_campaign_folder(relative):
        raise HarnessError(
            code="SOURCE_NOT_EDITABLE",
            message=f"folders live under campaigns/: {relative}",
            hint="create a folder like campaigns/ingest",
        )
    if target.exists():
        raise HarnessError(
            code="FOLDER_EXISTS",
            message=f"a folder or file is already at {relative}",
            hint="pick another name, or move the campaign into the one that is there",
        )
    target.mkdir(parents=True)
    return relative


def move_campaign(
    root: Path,
    project: ProjectView | None,
    raw_path: str,
    raw_directory: str,
) -> str:
    """Move a campaign file into another folder under `campaigns/`.

    Only campaigns move. The request was to sort what the agents wrote, and a round or
    a suite moved out of its directory would change the kind `kind_of` derives from the
    first component — a round is a round because it lives in `rounds/`, so moving one
    would silently turn the client's editor against a different loader.

    The destination folder must already exist, which is what makes "Nova pasta" and
    "Mover para…" two honest steps: a move that invented its destination would leave
    the reviewer without the thing they thought they were choosing.
    """
    content = content_root(root, project).resolve()
    source_file, relative = _resolve(root, project, raw_path)
    if kind_of(relative) != "campaign":
        raise HarnessError(
            code="SOURCE_NOT_EDITABLE",
            message=f"only campaigns move between folders: {relative}",
            hint="move a campaign file, under campaigns/",
        )
    destination_dir, directory_rel = _contained(content, raw_directory)
    if not _is_campaign_folder(directory_rel):
        raise HarnessError(
            code="SOURCE_NOT_EDITABLE",
            message=f"campaigns move between folders under campaigns/: {directory_rel}",
            hint="name a folder like campaigns/ingest",
        )
    if not destination_dir.is_dir():
        raise HarnessError(
            code="FOLDER_MISSING",
            message=f"there is no folder at {directory_rel}",
            hint="create it first with Nova pasta, then move the campaign into it",
        )
    destination = destination_dir / source_file.name
    if destination == source_file:
        return relative
    if destination.exists():
        raise HarnessError(
            code="FOLDER_EXISTS",
            message=f"{directory_rel} already holds {source_file.name}",
            hint="rename one of them, or move the campaign somewhere else",
        )
    # `os.replace` rather than a copy: same filesystem, so the move is atomic and a
    # campaign that is being read by nothing in this process cannot be left half in
    # two places by an interruption.
    os.replace(source_file, destination)
    return destination.relative_to(content).as_posix()


def validate_source(
    kind: str,
    path: Path,
    root: Path,
    project: ProjectView | None,
) -> tuple[ValidationFinding, ...]:
    """The findings for a file, by the validator that owns its kind.

    Each branch calls the same function the CLI's `validate` calls, so the editor and
    the terminal cannot disagree about whether a file is good. A loader raising is
    turned into the finding the CLI would print for it, and not into a 500: a YAML file
    with a typo is the normal case this endpoint exists to report.
    """
    try:
        if kind == "round":
            return tuple(validate_round(path, root, project))
        if kind == "campaign":
            return tuple(validate_campaign(path, root, project))
        if kind == "contract":
            load_contract(path)
            return ()
        if kind == "suite":
            return _suite_findings(load_suite(path), root, project)
    except (OSError, ValueError, ValidationError, YAMLError) as exc:
        return (_unreadable(kind, path, exc),)
    return ()


def _suite_findings(
    suite: SuiteFile,
    root: Path,
    project: ProjectView | None,
) -> tuple[ValidationFinding, ...]:
    """A suite's own parse, plus whether the cases its loops name are there.

    `heimdall-qa validate` has no suite entry point — a suite is checked when a round
    serves it — so this is the one place a suite is read on its own. Checking the loop
    targets is the part that matters: a suite whose step points at a case file that was
    renamed is syntactically perfect and fails the first time anyone runs it.

    The case is resolved the way `SuiteRun` resolves it — `resolve_path` against the
    content root — and *not* against the suite's own directory. The two agree only
    until someone writes a suite in a folder of its own, and then the check would
    report a rename that never happened.
    """
    findings: list[ValidationFinding] = []
    for step in suite.steps:
        if step.loop is None:
            continue
        relative, case_id = split_selector(step.loop.case)
        try:
            load_case(resolve_path(root, relative, project), case_id)
        except (OSError, ValueError, ValidationError, YAMLError) as exc:
            findings.append(
                finding(
                    "CASE_UNREADABLE",
                    f"loop {step.loop.case}",
                    str(exc),
                    fix=(
                        f"point the loop at a case file that exists, relative to the"
                        f" content root, such as {Path(relative).name}."
                    ),
                )
            )
    return tuple(findings)


def _unreadable(kind: str, path: Path, exc: Exception) -> ValidationFinding:
    """The finding a file that does not load gets, worded per kind.

    The codes are the ones the CLI already uses, not new ones: an editor that invented
    its own vocabulary would leave a reader comparing two names for one problem.
    """
    codes = {
        "round": "ROUND_UNREADABLE",
        "campaign": "ROUND_UNREADABLE",
        "contract": "CONTRACT_UNREADABLE",
        "suite": "SUITE_UNREADABLE",
    }
    fixes = {
        "round": "correct the YAML; heimdall-qa validate rounds/<id>.yaml says the same.",
        "campaign": "correct the campaign YAML and its rounds.",
        "contract": "correct the contract YAML; every case built from it reads this.",
        "suite": "correct the suite YAML and the cases its loops name.",
    }
    code = codes.get(kind, "ROUND_UNREADABLE")
    return finding(
        code,
        str(path),
        str(exc),
        fix=fixes.get(kind, "correct the YAML the loader reports."),
    )


def _resolve(
    root: Path,
    project: ProjectView | None,
    raw_path: str,
) -> tuple[Path, str]:
    """The absolute file `raw_path` names, and the clean relative path for the wire.

    The containment check is done on the *resolved* path and not on the string, which
    is what makes a symlink out of the content root fail here instead of quietly
    working. `Path.resolve` on a path that does not exist still resolves its parents,
    so a save to a new file inside a real directory is allowed and a save through a
    link that points outside is not.
    """
    content = content_root(root, project).resolve()
    target, relative = _contained(content, raw_path)
    if Path(relative).suffix.lower() not in _SUFFIXES:
        raise HarnessError(
            code="SOURCE_NOT_EDITABLE",
            message=f"only YAML is read and written here: {raw_path}",
            hint="contracts, rounds, campaigns and suites are `.yaml`",
        )
    return target, relative


def _contained(content: Path, raw_path: str) -> tuple[Path, str]:
    """`raw_path` resolved inside `content`, with the relative spelling to report.

    The half both `_resolve` and the folder operations need, kept apart from the
    suffix rule because a folder has no extension: creating `campaigns/ingest` must be
    contained exactly as strictly as saving a campaign, and one implementation is the
    only way two callers cannot disagree about what "inside the content root" means.
    """
    candidate = Path(raw_path)
    if candidate.is_absolute() or _climbs(candidate):
        raise HarnessError(
            code="SOURCE_OUTSIDE_CONTENT",
            message=f"the path must stay inside the project's content: {raw_path}",
            hint="paths are relative to the content root, e.g. campaigns/ingest",
        )
    target = (content / candidate).resolve()
    if not target.is_relative_to(content):
        raise HarnessError(
            code="SOURCE_OUTSIDE_CONTENT",
            message=f"the path resolves outside the project's content: {raw_path}",
            hint="a symlink may be pointing out of the content root",
        )
    # The relative path is rebuilt from the resolved target so that what the client is
    # told it opened is what it will save, and not the spelling it happened to send.
    return target, target.relative_to(content).as_posix()


def _is_campaign_folder(relative: str) -> bool:
    """Whether a content path names a directory under `campaigns/`, and not that folder."""
    parts = Path(relative).parts
    return len(parts) >= 2 and parts[0] == "campaigns"


def _climbs(candidate: Path) -> bool:
    """Whether a relative path walks upward at any point."""
    return any(part == ".." for part in candidate.parts)


def _write_atomically(target: Path, text: str) -> None:
    """Replace the file, or leave the previous version exactly as it was.

    `os.replace` is atomic within a filesystem, so a process that dies mid-save leaves
    either the old file or the new one. Writing in place would leave a truncated file,
    and a truncated round is worse than no change: it looks like a mistake the reviewer
    made and it loses the version that worked.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
