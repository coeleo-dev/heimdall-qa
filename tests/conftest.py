import os
from pathlib import Path

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("HEIMDALL_QA_SLOW") == "1":
        return
    skip_slow = pytest.mark.skip(reason="slow: set HEIMDALL_QA_SLOW=1")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the projects registry at a throwaway file for every test.

    `ProjectsRegistry()` defaults to `~/.config/heimdall-qa/projects.yaml`, which is a
    *person's* memory of which projects they have open. A test that registers a
    directory — and several now do, because the client's whole point is that it can —
    would otherwise write into the home of whoever ran the suite, and a test that
    mutates the developer's real projects is worse than no test.

    Autouse rather than opt-in: hermeticity that has to be remembered is hermeticity
    that will be forgotten by the next test that touches the registry.
    """
    override = tmp_path / "projects.yaml"
    monkeypatch.setenv("HEIMDALL_QA_REGISTRY", str(override))
    return override
