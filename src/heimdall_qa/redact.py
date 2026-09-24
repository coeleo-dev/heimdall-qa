import copy
import re
from collections.abc import Iterable
from typing import Any

REDACTED = "[REDACTED]"

#: A JWT has one shape in every project (RFC 7519: three base64url segments), so it
#: needs no declaration. An API key does not: `test_key_` and `sk_live_` are the same
#: rule about different products, and which one is in force is the project's to say,
#: in `errors.redact`. A literal prefix here would be one product's name compiled
#: into the core.
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


def redact_obj(value: Any, patterns: Iterable[str] = ()) -> Any:
    """`value` with every declared credential shape replaced by `[REDACTED]`.

    `patterns` are the project's shapes — the regular expressions
    `ProjectView.redact_patterns()` reads out of `errors.redact`. A project that
    declares none still gets its JWTs and its secret-named fields redacted, because
    those two shapes belong to no product in particular.
    """
    return _redact(copy.deepcopy(value), _declared(patterns))


def credential_literals(text: str, patterns: Iterable[str] = ()) -> list[str]:
    """Every credential-shaped literal in `text`, in the order it appears.

    The runner redacts these out of an artifact, and `validate` refuses them in
    content. Both need the same two shapes, so both read them here: a rule that
    matched a credential the redactor does not know about would be a second opinion
    about what a credential looks like.
    """
    declared = _declared(patterns)
    found = [match.group(0) for match in declared.finditer(text)] if declared else []
    found.extend(match.group(0) for match in _JWT.finditer(text))
    return found


def _declared(patterns: Iterable[str]) -> re.Pattern[str] | None:
    """The project's shapes as one alternation, or `None` when it declares none."""
    declared = [pattern for pattern in patterns if pattern]
    if not declared:
        return None
    return re.compile("|".join(f"(?:{pattern})" for pattern in declared))


def _redact(value: Any, declared: re.Pattern[str] | None) -> Any:
    if isinstance(value, str):
        return _redact_text(value, declared)
    if isinstance(value, dict):
        return {key: _redact_entry(key, item, declared) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, declared) for item in value]
    return value


def _redact_text(value: str, declared: re.Pattern[str] | None) -> str:
    without_jwt = _JWT.sub(REDACTED, value)
    return declared.sub(REDACTED, without_jwt) if declared else without_jwt


def _redact_entry(key: str, item: Any, declared: re.Pattern[str] | None) -> Any:
    if key.lower() in _SECRET_KEYS and isinstance(item, str) and item:
        if item.startswith("replace-with-") or "{{" in item:
            return item
        return REDACTED
    return _redact(item, declared)
