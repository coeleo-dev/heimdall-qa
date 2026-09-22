from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic import Field


class BudgetPair(BaseModel):
    budget: int
    fail: int


class BudgetsMs(BaseModel):
    hot_path: BudgetPair = BudgetPair(budget=50, fail=1500)
    default: BudgetPair = BudgetPair(budget=1500, fail=1500)
    kyc: BudgetPair = BudgetPair(budget=8000, fail=8000)


class UiConfig(BaseModel):
    """Review UI bind plus the browser-step policy (A.19, fase A5/A6).

    The block keeps the literal `ui:` name the emenda uses, so the page timeout
    lives next to the bind instead of inventing a second `ui:` section.
    """

    host: str = "127.0.0.1"
    port: int = 7878
    page_ms: int = 15000
    logs_ms: int = 1500
    screenshot: bool = True
    # E3: run axe-core on every `ui` step. Off is a deliberate choice, not a
    # silent default — the pack reports `skipped` with the reason when disabled.
    a11y: bool = True


class LogFiles(BaseModel):
    web: str = "../NokrAPI/logs/nokr-web.log"
    worker: str = "../NokrAPI/logs/nokr-worker.log"
    admin: str = "../NokrAPI/logs/nokr-admin.log"


class ProbeTimeouts(BaseModel):
    ingest_poll_ms: int = 8000
    ledger_ms: int = 8000
    overview_ms: int = 30000


class HarnessConfig(BaseModel):
    bruno_collection: str = ""
    nokr_web: str = "http://127.0.0.1:8080"
    nokr_admin: str = "http://127.0.0.1:9090"
    nokr_dashboard: str = "http://localhost:4200"
    ui: UiConfig = Field(default_factory=UiConfig)
    log_files: LogFiles = Field(default_factory=LogFiles)
    budgets_ms: BudgetsMs = Field(default_factory=BudgetsMs)
    probes: ProbeTimeouts = Field(default_factory=ProbeTimeouts)
    register_gap_ms: int = 0


def load_config(path: Path) -> HarnessConfig:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config must be a mapping: {path}")
    return HarnessConfig.model_validate(data)
