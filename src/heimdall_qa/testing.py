"""Building a run from a test.

A round under test needs three things the CLI assembles for it: harness settings,
a credential set, and the project in force. Tests — the harness's own and every
provider's — kept rebuilding that by hand, and each rebuild was a place where a
test could drift from what the CLI actually does.

This module is not a mock: it is the same `HarnessConfig` and `ProjectView` the
CLI builds, with the log sources pointed at a temporary directory because a test
writes its own logs.
"""

from pathlib import Path

from heimdall_qa.config import HarnessConfig
from heimdall_qa.descriptor import load_descriptor
from heimdall_qa.project import ProjectView

#: Where the descriptors that describe a whole project live, relative to a test.
DESCRIPTOR_DIR = Path("fixtures") / "descriptors"


def project_at(descriptor_path: Path, **log_paths: str) -> ProjectView:
    """The view of one descriptor file, with log sources optionally redirected.

    The descriptor's own directory travels with the view, the way it does in the
    CLI, so a relative path a test writes into a descriptor resolves to the same
    place the CLI would have resolved it.
    """
    view = ProjectView(
        descriptor=load_descriptor(descriptor_path),
        descriptor_dir=descriptor_path.parent,
    )
    return view.with_log_paths(**log_paths) if log_paths else view


def config_for(project: ProjectView, **settings: object) -> HarnessConfig:
    """Harness settings bound to `project`, with any knob a test wants to move."""
    return HarnessConfig(**settings).with_project(project)


def content_at(repo_root: Path, **log_paths: str) -> Path:
    """Where the repo under test keeps its content, read from its descriptor.

    A repo names one descriptor for the whole tree, so a test that needs to reach
    a case or a campaign asks the same file the CLI asks instead of hardcoding the
    prefix the descriptor happens to declare today.
    """
    project = project_at(repo_root / "qa" / "project.yaml", **log_paths)
    return project.content_root(repo_root)


def stub_client(root: Path) -> Path:
    """A directory that passes the client check, for tests that are about the API.

    `create_app` refuses to build without a built bundle, which is right — the whole
    point of `WEBAPP_NOT_BUILT` is that a missing bundle must not open blank. But an
    API test does not exercise the bundle, and pointing every one of them at the real
    one would make the core's suite depend on `npm run build` having happened in this
    checkout. One file is all the check asks for.
    """
    directory = root / "webapp"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "index.html").write_text(
        "<!doctype html><title>stub</title>", encoding="utf-8"
    )
    return directory
