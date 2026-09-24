from pathlib import Path

from heimdall_qa.descriptor import load_descriptor
from heimdall_qa.project import ProjectView

_DESCRIPTOR = Path(__file__).parent / "fixtures" / "qa" / "project.yaml"


def _project() -> ProjectView:
    return ProjectView(descriptor=load_descriptor(_DESCRIPTOR))


def test_auth_register_uses_onboarding_budget():
    budget = _project().budget("/auth/register")
    assert budget.budget == 8000
    assert budget.fail == 8000


def test_ingest_keeps_hot_path_budget():
    budget = _project().budget("/api/ingest")
    assert budget.budget == 50
    assert budget.fail == 1500


def test_undeclared_path_falls_back_to_the_default_budget():
    budget = _project().budget("/nothing/declared")
    assert budget.budget == 1500
    assert budget.fail == 1500
