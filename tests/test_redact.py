from heimdall_qa.redact import redact_obj


def test_redact_removes_full_nk_test_token():
    payload = {
        "headers": {"Authorization": "Bearer nk_test_abc123"},
        "nested": {"key": "nk_test_abc123"},
    }
    redacted = redact_obj(payload)
    dumped = str(redacted)
    assert "nk_test_abc123" not in dumped
    assert "[REDACTED]" in dumped
    assert payload["headers"]["Authorization"] == "Bearer nk_test_abc123"


def test_redact_removes_jwt():
    token = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0In0."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    redacted = redact_obj({"Authorization": f"Bearer {token}"})
    assert token not in str(redacted)
    assert "[REDACTED]" in redacted["Authorization"]


def test_redact_refresh_token_field():
    payload = {
        "jwt": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aa.bb",
        "refresh_token": "BLdafboHnyAbp4iWQSl-SYJBhJTYsY8VMYq00-lgQd0",
        "tenant_id": "e257ce03-c471-4735-bce3-339a095aa94d",
    }
    redacted = redact_obj(payload)
    assert redacted["refresh_token"] == "[REDACTED]"
    assert redacted["jwt"] == "[REDACTED]"
    assert redacted["tenant_id"] == payload["tenant_id"]


def test_redact_masks_password_but_keeps_placeholder():
    redacted = redact_obj(
        {
            "password": "Aa1!real-secret",
            "email": "ops@nokr.dev",
            "unfilled": {"password": "replace-with-generate"},
        }
    )
    assert redacted["password"] == "[REDACTED]"
    assert redacted["email"] == "ops@nokr.dev"
    assert redacted["unfilled"]["password"] == "replace-with-generate"
