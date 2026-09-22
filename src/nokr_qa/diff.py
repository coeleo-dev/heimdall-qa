import copy
from typing import Any


def apply_diff(baseline: dict[str, Any], diff: dict[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy(baseline)
    for key in diff.get("omit") or []:
        _delete_path(body, str(key))
    for key, value in (diff.get("set") or {}).items():
        set_path(body, str(key), value)
    return body


def get_path(body: dict[str, Any], path: str) -> Any:
    parent, leaf = _walk_parent(body, path)
    if parent is None:
        return None
    return parent.get(leaf)


def set_path(body: dict[str, Any], path: str, value: Any) -> None:
    parent, leaf = _walk_parent(body, path, create=True)
    if parent is not None:
        parent[leaf] = value


def _delete_path(body: dict[str, Any], path: str) -> None:
    parent, leaf = _walk_parent(body, path)
    if parent is not None:
        parent.pop(leaf, None)


def _walk_parent(
    body: dict[str, Any],
    path: str,
    *,
    create: bool = False,
) -> tuple[dict[str, Any] | None, str]:
    parts = path.split(".")
    cursor: Any = body
    for part in parts[:-1]:
        if not isinstance(cursor, dict):
            return None, parts[-1]
        if part not in cursor:
            if not create:
                return None, parts[-1]
            cursor[part] = {}
        cursor = cursor[part]
    if not isinstance(cursor, dict):
        return None, parts[-1]
    return cursor, parts[-1]
