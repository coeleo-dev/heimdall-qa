from heimdall_qa.redact import redact_obj


def test_redact_removes_a_declared_api_key_token():
    """The key's shape is the project's, so `redact_obj` is told what it is."""
    payload = {
        "headers": {"Authorization": "Bearer test_key_abc123"},
        "nested": {"key": "test_key_abc123"},
    }
    redacted = redact_obj(payload, ("test_key_.*",))
    dumped = str(redacted)
    assert "test_key_abc123" not in dumped
    assert "[REDACTED]" in dumped
    assert payload["headers"]["Authorization"] == "Bearer test_key_abc123"


def test_redact_honours_a_shape_the_core_has_never_seen():
    """`sk_live_` belongs to a product this core has never heard of; it still works.

    This is the whole point of taking the shape as an argument: the core knows how to
    redact, not what any one product's keys look like.
    """
    redacted = redact_obj({"Authorization": "Bearer sk_live_abc123"}, ("sk_live_.*",))
    assert "sk_live_abc123" not in str(redacted)
    assert "[REDACTED]" in redacted["Authorization"]


def test_redact_without_declared_shapes_still_removes_the_shared_ones():
    """No declaration is not a licence to leak a JWT or a secret-named field."""
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV"
    redacted = redact_obj({"jwt": token, "Authorization": f"Bearer {token}"})
    assert token not in str(redacted)
    assert redacted["jwt"] == "[REDACTED]"


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
            "email": "ops@example.dev",
            "unfilled": {"password": "replace-with-generate"},
        }
    )
    assert redacted["password"] == "[REDACTED]"
    assert redacted["email"] == "ops@example.dev"
    assert redacted["unfilled"]["password"] == "replace-with-generate"
