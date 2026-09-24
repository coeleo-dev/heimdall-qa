"""What the harness needs to know about itself.

Everything the harness needs to know about the *project* it reviews — origins,
auth, routing, budgets, log sources, request sources — lives in the project
descriptor, read by `descriptor.py` and asked through `project.py`. What is left
here is infrastructure: where the review UI binds, how long a probe waits, and how
long to wait between two registrations. None of it is a fact about an API.

`extra="forbid"` on purpose. `config.yaml` used to carry a web base_url, the log
file paths and the per-route budgets; a file that still declares them is a file
whose author believes they are doing something, and silently ignoring them is how
an origin ends up resolved from a stale copy of the truth.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from heimdall_qa.project import ProjectView


class UiConfig(BaseModel):
    """Where the human review UI binds.

    Kept here rather than in the descriptor because it is the harness's own
    address, not the product's: the descriptor says where the API is, not where
    the reviewer reads about it.
    """

    model_config = ConfigDict(extra="forbid")

    host: str = "127.0.0.1"
    port: int = 7878


class ProbeTimeouts(BaseModel):
    """How long a suite waits for a state change it cannot observe directly."""

    model_config = ConfigDict(extra="forbid")

    ingest_poll_ms: int = 8000
    ledger_ms: int = 8000
    overview_ms: int = 30000


class HarnessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ui: UiConfig = Field(default_factory=UiConfig)
    probes: ProbeTimeouts = Field(default_factory=ProbeTimeouts)
    #: The floor between two requests that share a declared route `pace_key`.
    #: 0 means nothing is throttled, which is also what an absent key means.
    pace_gap_ms: int = 0
    #: The project in force. The CLI fills this from `resolve_descriptor`; it is
    #: never read from `config.yaml`, because a descriptor has its own file, its
    #: own precedence rule and its own owner, and a second copy here would undo
    #: both.
    project: ProjectView = Field(default_factory=ProjectView)

    def with_project(self, project: ProjectView) -> "HarnessConfig":
        """The same harness settings, bound to a project."""
        return self.model_copy(update={"project": project})


def load_config(path: Path) -> HarnessConfig:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config must be a mapping: {path}")
    return HarnessConfig.model_validate(data)
