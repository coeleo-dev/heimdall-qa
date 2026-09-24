"""The two strings three modules have to agree about.

`{{name}}` is a value the harness resolves before it sends a request, and
`replace-with-…` is the sentinel the scaffold writes where it could not derive one.
Three modules read them for three different reasons:

* `runner` resolves what it can and refuses what is left, before the request leaves;
* `redact` keeps a sentinel out of the artifacts, where it would read as a value;
* `validate` refuses both ahead of time, so the run-time refusal is a finding.

The shape lived in all three, which is how the same rule drifts into three opinions.
It lives here once.
"""

from __future__ import annotations

import re
from typing import Any

#: One placeholder, anywhere in a string. The group is the name, for the callers
#: that need to know which value is missing rather than only that one is.
PLACEHOLDER = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")

#: The scaffolding's seed sentinel: `replace-with-generate` means "the case fills
#: this in". Nothing resolves it at run time, so a seed that survives reaches the API
#: as a literal string.
SEED_PREFIX = "replace-with-"


def placeholder_names(value: Any) -> set[str]:
    """Every placeholder name in `value`, walking nested strings, keys and lists."""
    found: set[str] = set()
    if value is None:
        return found
    if isinstance(value, str):
        found.update(PLACEHOLDER.findall(value))
        return found
    if isinstance(value, dict):
        for item in value.values():
            found |= placeholder_names(item)
        for key in value:
            found |= placeholder_names(str(key))
        return found
    if isinstance(value, (list, tuple)):
        for item in value:
            found |= placeholder_names(item)
    return found


def placeholder_name(value: str) -> str | None:
    """The name when the whole string is one placeholder, else `None`."""
    text = value.strip()
    match = PLACEHOLDER.fullmatch(text)
    return None if match is None else match.group(1)


def is_seed(value: str) -> bool:
    """Whether `value` is a seed nothing filled in."""
    return value.startswith(SEED_PREFIX)


def unresolved(value: Any) -> bool:
    """Whether any string inside `value` would reach the API unresolved."""
    if value is None:
        return False
    if isinstance(value, str):
        return is_seed(value) or PLACEHOLDER.search(value) is not None
    if isinstance(value, dict):
        return any(unresolved(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(unresolved(item) for item in value)
    return False
