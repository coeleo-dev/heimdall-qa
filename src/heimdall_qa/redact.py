import copy
import re
from typing import Any

REDACTED = "[REDACTED]"
_API_KEY = re.compile(r"nk_(?:test|live)_[A-Za-z0-9]+")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_SECRET_KEYS = frozenset(
    {
        "jwt",
        "refresh_token",
        "access_token",
        "id_token",
        "api_key",
        "password",
        "admin_secret",
        "secret",
    }
)


def redact_obj(value: Any) -> Any:
    return _redact(copy.deepcopy(value))


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return _JWT.sub(REDACTED, _API_KEY.sub(REDACTED, value))
    if isinstance(value, dict):
        return {key: _redact_entry(key, item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _redact_entry(key: str, item: Any) -> Any:
    if key.lower() in _SECRET_KEYS and isinstance(item, str) and item:
        if item.startswith("replace-with-") or "{{" in item:
            return item
        return REDACTED
    return _redact(item)
