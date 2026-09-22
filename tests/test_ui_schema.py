"""Gate 4 of E2: `ui` parses, `validate` accepts it, and kinds stay exclusive."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from nokr_qa.schema.models import SuiteStep
from nokr_qa.validate import validate_round
from support_ui import build_ui_tree

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_ui_step_loads_with_the_declared_fields():
    step = SuiteStep(
        ui={
            "id": "overview",
            "path": "/overview",
            "wait_for": "/platform/dashboard/metrics",
        }
    )
    assert step.ui is not None
    assert step.ui.id == "overview"
    assert step.ui.path == "/overview"
    assert step.ui.wait_for == "/platform/dashboard/metrics"
    # A.19/§6: the snapshot is scoped to a region, never the whole body.
    assert step.ui.region == "main"
    assert step.ui.timeout_ms is None


def test_unknown_key_inside_ui_is_rejected():
    # `SuiteStep` is `extra="ignore"`, so without `extra="forbid"` on `UiStep`
    # a typo would silently become a default instead of a validation error.
    with pytest.raises(ValidationError):
        SuiteStep(ui={"id": "overview", "path": "/overview", "waitfor": "/x"})


def test_unknown_key_inside_ui_is_still_rejected_next_to_baseline():
    # E3 added two fields; a typo in one of them must still be loud.
    with pytest.raises(ValidationError):
        SuiteStep(
            ui={
                "id": "overview",
                "path": "/overview",
                "baseline": "baselines/ui/overview.aria.yml",
                "waive": [],
                "baselines": "baselines/ui/other.aria.yml",
            }
        )


def test_baseline_and_waive_load_with_the_declared_fields():
    step = SuiteStep(
        ui={
            "id": "overview",
            "path": "/overview",
            "baseline": "baselines/ui/overview.aria.yml",
            "waive": [
                {
                    "pack": "ui.visual",
                    "reason": "pixel diff is not enabled in v1, so nothing to waive yet",
                }
            ],
        }
    )
    assert step.ui is not None
    assert step.ui.baseline == "baselines/ui/overview.aria.yml"
    assert [item.pack for item in step.ui.waive] == ["ui.visual"]


def test_baseline_defaults_to_none_and_waive_to_empty():
    # Absent must mean "declared nothing", not "pass": `ui.structure` reports
    # `skipped` on `None` instead of claiming a comparison it never ran.
    step = SuiteStep(ui={"id": "overview", "path": "/overview"})
    assert step.ui is not None
    assert step.ui.baseline is None
    assert step.ui.waive == []


@pytest.mark.parametrize(
    "escape",
    [
        "../secrets.local.yaml",
        "/etc/passwd",
        "baselines/../../etc/passwd",
        "..\\baselines\\ui\\overview.aria.yml",
    ],
)
def test_baseline_cannot_escape_the_run_root(escape: str):
    # The path is read from disk and it comes from YAML, so traversal is refused
    # by the contract instead of by a convention in `ui_step`.
    with pytest.raises(ValidationError):
        SuiteStep(ui={"id": "overview", "path": "/overview", "baseline": escape})


def test_a_waive_inside_ui_still_needs_a_reason_or_a_p_gap():
    # `UiStep.waive` reuses `Waive`, so the >= 40 char rule applies here too —
    # a UI waiver cannot be a three-word excuse.
    with pytest.raises(ValidationError):
        SuiteStep(
            ui={
                "id": "overview",
                "path": "/overview",
                "waive": [{"pack": "ui.visual", "reason": "too short"}],
            }
        )
    with pytest.raises(ValidationError):
        SuiteStep(
            ui={
                "id": "overview",
                "path": "/overview",
                "waive": [{"pack": "ui.visual", "reason": "too short", "p_gap": None}],
            }
        )


def test_a_p_gap_replaces_the_reason_for_a_ui_waive():
    step = SuiteStep(
        ui={
            "id": "overview",
            "path": "/overview",
            "waive": [{"pack": "ui.visual", "p_gap": "P-GAP-0042"}],
        }
    )
    assert step.ui is not None
    assert step.ui.waive[0].p_gap == "P-GAP-0042"


@pytest.mark.parametrize("missing", ["id", "path"])
def test_ui_step_requires_id_and_path(missing: str):
    payload = {"id": "overview", "path": "/overview"}
    payload.pop(missing)
    with pytest.raises(ValidationError):
        SuiteStep(ui=payload)


def test_ui_cannot_be_combined_with_loop():
    with pytest.raises(ValidationError):
        SuiteStep(
            ui={"id": "overview", "path": "/overview"},
            loop={"times": 1, "case": "cases/ingest/ingest-H01.yaml"},
        )


def test_validate_round_accepts_a_ui_step(tmp_path: Path):
    round_path = build_ui_tree(tmp_path)
    assert validate_round(round_path, tmp_path) == []


def test_validate_round_accepts_a_ui_step_with_baseline_and_waive(tmp_path: Path):
    # The two E3 fields go through `validate`, not only through the model: a
    # declaration that parses but fails validation is not a usable contract.
    round_path = build_ui_tree(
        tmp_path,
        baseline="baselines/ui/overview.aria.yml",
        waive=[
            {
                "pack": "ui.visual",
                "reason": "pixel diff is not enabled in v1, so nothing to waive yet",
            }
        ],
    )
    assert validate_round(round_path, tmp_path) == []


def test_the_shipped_ui_round_validates():
    # The demonstration round is part of the contract, not a scratch file.
    assert validate_round(REPO_ROOT / "rounds" / "ui-overview.yaml", REPO_ROOT) == []
