"""Loading a project descriptor, and deciding which one wins.

Two locations are legal and the target takes precedence (ADR-01):

1. an explicit `--descriptor PATH`;
2. `<root>/qa/project.yaml`, declared by the team that owns the API;
3. `<harness>/providers/<id>/project.yaml`, the harness's own fallback.

There is no merge. The winner is used whole and its origin is recorded in
`summary.json`, because precedence with no record is indistinguishable from a
mistake — and it is what makes `heimdall-qa doctor` possible later.

Nothing here is wired into the HTTP path yet; that is fase 1.4. This module only
has to answer "which descriptor, and is it coherent".
"""

from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError
import yaml

from heimdall_qa.errors import HarnessError
from heimdall_qa.schema.descriptor import ProjectDescriptor

#: Where a target repo declares its own descriptor.
TARGET_RELATIVE_PATH = Path("qa") / "project.yaml"

ORIGIN_EXPLICIT = "explicit"
ORIGIN_TARGET = "target"
ORIGIN_PROVIDER = "provider"

_CODE_PREFIX = "DESCRIPTOR_"
_PYDANTIC_PREFIX = "Value error, "


@dataclass(frozen=True)
class ResolvedDescriptor:
    """The descriptor in force, the file it came from, and why that file won."""

    descriptor: ProjectDescriptor
    path: Path
    origin: str

    def as_summary(self) -> dict[str, str]:
        """The shape written into `summary.json`."""
        return {"origin": self.origin, "path": str(self.path)}


def harness_root() -> Path:
    """The harness checkout that is running, for the provider fallback."""
    return Path(__file__).resolve().parents[2]


def target_descriptor_path(root: Path) -> Path:
    return root / TARGET_RELATIVE_PATH


def provider_descriptor_path(harness_dir: Path, provider_id: str) -> Path:
    return harness_dir / "providers" / provider_id / "project.yaml"


def load_descriptor(path: Path) -> ProjectDescriptor:
    """Reads one descriptor, promoting a pydantic failure to a named rule code."""
    if not path.is_file():
        raise HarnessError(
            code="DESCRIPTOR_NOT_FOUND",
            message=f"descriptor not found: {path}",
            hint="pass --descriptor PATH, or create qa/project.yaml in the target repo",
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise HarnessError(
            code="DESCRIPTOR_UNREADABLE",
            message=f"descriptor is not readable YAML: {path}",
            hint="fix the YAML syntax and retry",
            details=(str(exc),),
        ) from exc
    if not isinstance(data, dict):
        raise HarnessError(
            code="DESCRIPTOR_UNREADABLE",
            message=f"descriptor must be a mapping: {path}",
            hint="a descriptor starts with `version:` and `project:`",
        )
    try:
        return ProjectDescriptor.model_validate(data)
    except ValidationError as exc:
        raise _promote(path, exc) from exc


def resolve_descriptor(
    root: Path,
    *,
    explicit: str | None = None,
    provider_id: str | None = None,
    harness_dir: Path | None = None,
) -> ResolvedDescriptor | None:
    """Returns the descriptor in force, or `None` when none was declared.

    `None` is not an error: a descriptor is optional until a case needs something
    only the descriptor knows. An explicit path that does not exist *is* an error,
    because the caller asked for a specific file and silently ignoring that is how
    a run reports green against the wrong API.
    """
    if explicit:
        path = Path(explicit)
        return ResolvedDescriptor(load_descriptor(path), path, ORIGIN_EXPLICIT)

    target = target_descriptor_path(root)
    if target.is_file():
        return ResolvedDescriptor(load_descriptor(target), target, ORIGIN_TARGET)

    if provider_id:
        base = harness_dir if harness_dir is not None else harness_root()
        fallback = provider_descriptor_path(base, provider_id)
        if fallback.is_file():
            return ResolvedDescriptor(load_descriptor(fallback), fallback, ORIGIN_PROVIDER)

    return None


def _promote(path: Path, exc: ValidationError) -> HarnessError:
    """Turns the first pydantic error into a `HarnessError` that names its rule."""
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(part) for part in first.get("loc") or ())
    message = str(first.get("msg") or "").removeprefix(_PYDANTIC_PREFIX)
    code = _rule_code(message) or "DESCRIPTOR_INVALID"
    if code != "DESCRIPTOR_INVALID":
        # The code is already the error's, so repeating it in the prose is noise.
        message = message.split(":", 1)[1].strip()
    location = f"{path}#{where}" if where else str(path)
    return HarnessError(
        code=code,
        message=f"{location}: {message}",
        hint="every rule is listed in docs/estudo-heimdall/02-descriptor-projeto.md §6",
    )


def _rule_code(message: str) -> str | None:
    head = message.split(":", 1)[0].strip()
    return head if head.startswith(_CODE_PREFIX) else None
