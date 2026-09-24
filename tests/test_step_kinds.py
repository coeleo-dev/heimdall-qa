"""The step-kind registry is the contract between a suite and the core.

Before it existed, `SuiteStep` was `extra="ignore"`: a suite could declare a kind
nobody implemented and the key was dropped in silence, so the step either ran as
another kind or did not run at all. These tests pin the two halves of the fix —
an unknown kind is a named error at load time, and the kinds the core ships still
load.
"""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from heimdall_qa.schema.load import load_suite
from heimdall_qa.schema.models import SuiteStep
from heimdall_qa.step_kinds import LOOP
from heimdall_qa.step_kinds import PROBE
from heimdall_qa.step_kinds import PROBE_BEGIN
from heimdall_qa.step_kinds import registered_step_kinds
from heimdall_qa.step_kinds import unknown_step_kind_message
from heimdall_qa.step_kinds import unknown_step_kinds


def test_the_core_ships_exactly_three_step_kinds():
    assert registered_step_kinds() == frozenset({PROBE_BEGIN, LOOP, PROBE})


def test_unknown_kinds_are_reported_in_a_stable_order():
    assert unknown_step_kinds(["ui", "proobe", "loop"]) == ["proobe", "ui"]


def test_the_message_names_the_kind_and_lists_the_registered_ones():
    message = unknown_step_kind_message("ui")
    assert "step kind not registered: ui" in message
    assert LOOP in message and PROBE in message and PROBE_BEGIN in message


def test_a_ui_step_is_rejected_with_an_actionable_message():
    with pytest.raises(ValidationError) as raised:
        SuiteStep(ui={"id": "overview", "path": "/overview"})

    assert "step kind not registered: ui" in str(raised.value)


def test_a_typo_in_a_step_kind_is_no_longer_swallowed():
    with pytest.raises(ValidationError) as raised:
        SuiteStep(proobe={"id": "x", "surfaces": []})

    assert "step kind not registered: proobe" in str(raised.value)


def test_a_suite_declaring_ui_fails_at_load_not_at_run(tmp_path: Path):
    suite_path = tmp_path / "ui-smoke.yaml"
    suite_path.write_text(
        yaml.safe_dump(
            {
                "id": "ui-smoke",
                "steps": [{"ui": {"id": "overview", "path": "/overview"}}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError) as raised:
        load_suite(suite_path)

    assert "step kind not registered: ui" in str(raised.value)


def test_a_step_with_no_kind_is_still_rejected():
    with pytest.raises(ValidationError) as raised:
        SuiteStep.model_validate({})

    assert "exactly one of" in str(raised.value)


def test_a_step_with_two_kinds_is_still_rejected():
    with pytest.raises(ValidationError) as raised:
        SuiteStep.model_validate(
            {
                "probe_begin": "values-10m-7i",
                "probe": {
                    "id": "x",
                    "oracle": {},
                    "surfaces": [
                        {
                            "id": "wallet",
                            "get": "/api/x",
                            "jsonpath": "$.balance",
                            "expect": "exact",
                        }
                    ],
                },
            }
        )

    assert "exactly one of" in str(raised.value)
