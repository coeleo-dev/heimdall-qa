import pytest

from nokr_qa.errors import HarnessError
from nokr_qa.serve.bind import assert_local_bind


def test_assert_local_bind_rejects_all_interfaces():
    for host in ("0.0.0.0", "::", ""):
        with pytest.raises(HarnessError) as caught:
            assert_local_bind(host)
        assert caught.value.code == "CONFIG_INVALID"
        assert "127.0.0.1" in caught.value.hint


def test_assert_local_bind_allows_loopback():
    assert_local_bind("127.0.0.1")
    assert_local_bind("localhost")
