"""Reading a YAML content file, and finding it in the first place.

Content paths inside rounds, suites and contracts are written relative to the
project's content root — `cases/...`, `contracts/...` — so that a round reads the
same whether the content sits at the harness root or under `providers/<id>/`. The
prefix that joining requires is declared by the descriptor, and these two helpers
are the only place it is applied.

A case file holds **a map of id to case**, not one case, because a round that wants
every case of an area should name one file: `- cases/ingest.yaml`. Naming one case
inside it is the same reference with a fragment — `cases/ingest.yaml#ingest-H01` —
and that is the only selector shape there is.
"""

import copy
from collections.abc import Callable
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import NamedTuple
from typing import cast

import yaml

from heimdall_qa.schema.models import CampaignFile
from heimdall_qa.schema.models import CaseFile
from heimdall_qa.schema.models import Contract
from heimdall_qa.schema.models import RoundFile
from heimdall_qa.schema.models import SuiteFile

if TYPE_CHECKING:
    from heimdall_qa.project import ProjectView

#: What separates a case file from the one case inside it. There is no second
#: spelling: a selector a human has to guess at is a selector nobody uses.
SELECTOR = "#"

#: Parsed YAML, keyed by the file's own stamp. See `load_yaml` for why it exists and
#: why the limit is enforced by clearing rather than by evicting one entry.
_YAML_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}
_YAML_CACHE_LIMIT = 1024

#: Validated models, keyed by `(kind, path, mtime_ns, size)`. See `_validated`.
_MODEL_CACHE: dict[tuple[str, str, int, int], object] = {}
_MODEL_CACHE_LIMIT = 1024


class LoadedCase(NamedTuple):
    """A case and the reference that found it.

    A named tuple, not a dataclass, because every caller that only wants the case
    already unpacks the pair it used to be — and a rule that says *which* case is
    wrong has to name the file too, since forty of them share a consolidated one.
    """

    reference: str
    case: CaseFile


class _StrictLoader(yaml.SafeLoader):
    """A loader that refuses a duplicated key instead of keeping the last one.

    A consolidated case file is a map of 40-odd entries, and PyYAML would drop a
    duplicated case without a word — a case that never runs and a count that still
    says it is there. Silence is the only failure mode worth refusing outright.
    """


def _construct_unique_mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False):
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(
                f"duplicate key {key!r} at line {key_node.start_mark.line + 1}"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def content_root(root: Path, project: "ProjectView | None" = None) -> Path:
    """The directory content paths are relative to.

    With no project there is no prefix to apply and the root passes through, so a
    caller that holds only a path keeps working.
    """
    if project is None:
        return root
    return project.content_root(root)


def resolve_path(
    root: Path,
    raw: str,
    project: "ProjectView | None" = None,
) -> Path:
    """`raw` inside the project's content, or itself when it is already absolute."""
    path = Path(raw)
    if path.is_absolute():
        return path
    return content_root(root, project) / path


def split_selector(raw: str) -> tuple[str, str]:
    """A case reference as `(file, case id)`; the id is `""` for a whole file.

    The file half is what a human would type at a shell and what `resolve_path`
    takes. Everything after the first `#` is the id, so a path that somehow holds
    one still reads as a path.
    """
    path, _, case_id = raw.partition(SELECTOR)
    return path, case_id.strip()


def load_yaml(path: Path) -> dict[str, Any]:
    """The mapping a YAML file holds, or a `ValueError` naming what is wrong with it.

    `yaml.YAMLError` is not a `ValueError`, and that one fact leaked. Every caller in
    this codebase catches `(ValidationError, ValueError, OSError)` for "this file is
    bad" — that is the declared vocabulary, and `runner._LOAD_ERRORS` spells it out —
    so a YAML *syntax* typo, the most ordinary mistake in a file a person hand-writes,
    escaped as an internal error wherever the tuple did not also happen to name
    `YAMLError`. Saving a contract with a stray `[` returned a 500 instead of
    `CONTRACT_UNREADABLE`, and `heimdall-qa validate` on a round whose coverage
    contract had one reported `STEP_INTERNAL` — "the harness broke" — for a typo.

    The message is passed through unchanged, so the line and column the parser reports
    still reach the reader. Only the type changes, to the one every caller already
    catches. `OSError` is left alone: a missing file is not a malformed one.

    **Parsed once per file per change.** This is not an optimisation for its own sake:
    it is what keeps the review screen responsive. Indexing a project re-reads the same
    case file once per round that includes it and again for every validation of that
    round, which on a real collection was 1059 parses of 179 files — seven seconds of
    PyYAML, per unit the plan moves to, with the UI blocked behind it. The key is the
    file's own stamp (`mtime_ns` and size), so an edit invalidates it and nothing has
    to remember to. The copy on the way out is deliberate: 1% of the parse cost, and it
    keeps a caller that mutates what it loaded from poisoning every later reader.
    """
    stamp = _stamp(path)
    if stamp is not None:
        cached = _YAML_CACHE.get(stamp)
        if cached is not None:
            return copy.deepcopy(cached)
    try:
        with path.open(encoding="utf-8") as handle:
            data = yaml.load(handle, Loader=_StrictLoader)
    except yaml.YAMLError as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(data, dict):
        raise ValueError(f"YAML must be a mapping: {path}")
    if stamp is not None:
        if len(_YAML_CACHE) >= _YAML_CACHE_LIMIT:
            # A session that edits the same file forty times would otherwise grow this
            # without bound. Clearing wholesale costs one re-parse of what is in use
            # and cannot leak a stale entry, which is worth more than hit rate here.
            _YAML_CACHE.clear()
        _YAML_CACHE[stamp] = data
        return copy.deepcopy(data)
    return data


def _stamp(path: Path) -> tuple[str, int, int] | None:
    """`(path, mtime_ns, size)` — what a parsed file is cached under.

    `None` when the file cannot be stat'd, which is the case `load_yaml` is about to
    report as an `OSError` anyway: a missing file is not cached and not a cache miss.
    """
    try:
        info = path.stat()
    except OSError:
        return None
    return (str(path), info.st_mtime_ns, info.st_size)


def load_contract(path: Path) -> Contract:
    return _validated("contract", path, lambda: Contract.model_validate(load_yaml(path)))


def load_cases(path: Path) -> list[CaseFile]:
    """Every case a case file holds, in the order the file lists them.

    The order is the file's, not the id's: a corpus that reorders its cases by
    sorting them is a corpus whose rounds take a different route through the
    shared captures, and `H01` creating what the `I-*` cases replay is not an
    accident a sort can be trusted to preserve.
    """
    # A fresh list around the cached models: the caller owns the list it is handed,
    # and every caller in this codebase only reads the cases. See `_validated`.
    return list(_validated("cases", path, lambda: tuple(_build_cases(path))))


def _build_cases(path: Path) -> list[CaseFile]:
    cases: list[CaseFile] = []
    for case_id, body in _case_bodies(path).items():
        if not isinstance(body, dict):
            raise ValueError(f"{path}#{case_id}: a case must be a mapping")
        cases.append(CaseFile.model_validate({**body, "id": case_id}))
    return cases


def load_case(path: Path, case_id: str = "") -> CaseFile:
    """One case from a case file, or the only one when there is no id to pick.

    `case_id` empty and the file holding more than one case is an error rather
    than a first-entry guess: a caller that meant the whole file iterates, and a
    caller that meant one case says which.
    """
    found = load_cases(path)
    if not case_id:
        if len(found) != 1:
            raise ValueError(
                f"{path} holds {len(found)} cases; name one as {path}#<case-id>"
            )
        return found[0]
    for case in found:
        if case.id == case_id:
            return case
    raise ValueError(f"{path} has no case {case_id!r}")


def iter_cases(
    root: Path,
    includes: list[str] | tuple[str, ...],
    project: "ProjectView | None" = None,
) -> Iterator[LoadedCase]:
    """`LoadedCase` for every case an include list names, in its order.

    The reference is the include as written — `cases/ingest.yaml#ingest-H01` — so
    a message about one case can name a file *and* a case, which is what a person
    needs to open the right line out of forty.
    """
    for raw in includes:
        relative, case_id = split_selector(raw)
        path = resolve_path(root, relative, project)
        for case in _selected(path, relative, case_id):
            yield LoadedCase(f"{relative}{SELECTOR}{case.id}", case)


def load_included_cases(
    root: Path,
    includes: list[str] | tuple[str, ...],
    project: "ProjectView | None" = None,
) -> list[CaseFile]:
    """The cases only, for a caller that has nothing to say about which file."""
    return [case for _, case in iter_cases(root, includes, project)]


def existing_case_ids(path: Path) -> list[str]:
    """The ids a case file already holds, in order; empty when there is no file."""
    if not path.is_file():
        return []
    return list(_case_bodies(path))


def _selected(path: Path, relative: str, case_id: str) -> list[CaseFile]:
    found = load_cases(path)
    if not case_id:
        return found
    for case in found:
        if case.id == case_id:
            return [case]
    raise ValueError(
        f"{relative}{SELECTOR}{case_id}: {path.name} has no such case"
    )


def _case_bodies(path: Path) -> dict[str, Any]:
    """The raw id → body map, with the one mistake worth naming by hand.

    A single-case file — `id:` at the top, which is every case file written before
    the corpus was consolidated — would otherwise be read as a map whose keys are
    the case's own fields, and fail as a dozen unrelated validation errors.
    """
    data = load_yaml(path)
    if isinstance(data.get("id"), str) and "contract" in data:
        raise ValueError(
            f"{path} holds one case at the top level; a case file is a map of"
            " case id to case, so wrap it: `<case-id>: {{...}}`"
        )
    for case_id in data:
        if not isinstance(case_id, str):
            raise ValueError(f"{path}: case ids must be strings, got {case_id!r}")
    return data


def load_round(path: Path) -> RoundFile:
    return _validated("round", path, lambda: RoundFile.model_validate(load_yaml(path)))


def load_suite(path: Path) -> SuiteFile:
    return _validated("suite", path, lambda: SuiteFile.model_validate(load_yaml(path)))


def load_campaign(path: Path) -> CampaignFile:
    return _validated("campaign", path, lambda: CampaignFile.model_validate(load_yaml(path)))


def _validated[T](kind: str, path: Path, build: Callable[[], T]) -> T:
    """One file's validated model, built once per change.

    The other half of `load_yaml`'s cache, and the half that matters more: indexing a
    project validates the same case file once per round that includes it — 13775
    `model_validate` calls to draw one tree — and the validation is not free even when
    the parse is. Read-only by contract: nothing in this codebase assigns to a field of
    a loaded model (the one place that needs a variation calls `model_copy`), which is
    what makes handing the same object to every caller safe rather than a trap.

    Raises are never cached. A file reported as broken stays broken until it is fixed,
    and the next read is what finds that out.
    """
    stamp = _stamp(path)
    if stamp is None:
        return build()
    key = (kind, *stamp)
    cached = _MODEL_CACHE.get(key)
    if cached is not None:
        return cast(T, cached)
    value = build()
    if len(_MODEL_CACHE) >= _MODEL_CACHE_LIMIT:
        _MODEL_CACHE.clear()
    _MODEL_CACHE[key] = value
    return value
