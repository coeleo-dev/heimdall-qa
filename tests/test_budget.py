from nokr_qa.config import HarnessConfig
from nokr_qa.runner import _budget_for


def test_auth_register_uses_onboarding_budget():
    pair = _budget_for("/auth/register", HarnessConfig())
    assert pair.budget == 8000
    assert pair.fail == 8000


def test_ingest_keeps_hot_path_budget():
    pair = _budget_for("/api/ingest", HarnessConfig())
    assert pair.budget == 50
    assert pair.fail == 1500
