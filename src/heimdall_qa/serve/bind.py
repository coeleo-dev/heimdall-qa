"""Local bind guard for the review UI."""

from heimdall_qa.errors import HarnessError

_ALLOWED = frozenset({"127.0.0.1", "localhost"})


def assert_local_bind(host: str) -> None:
    normalized = (host or "").strip().lower()
    if normalized in _ALLOWED:
        return
    raise HarnessError(
        code="CONFIG_INVALID",
        message=f"refusing to bind UI on {host or '(empty)'}",
        hint="ui.host must be 127.0.0.1",
    )
