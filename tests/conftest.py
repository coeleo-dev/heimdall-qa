import os

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("HEIMDALL_QA_SLOW") == "1":
        return
    skip_slow = pytest.mark.skip(reason="slow: set HEIMDALL_QA_SLOW=1")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)
