import pytest
import httpx


@pytest.mark.slow
def test_live_health_optional():
    try:
        response = httpx.get("http://127.0.0.1:8080/health", timeout=1.0)
    except httpx.HTTPError:
        pytest.skip("NokrAPI not up")
    assert response.status_code == 200
