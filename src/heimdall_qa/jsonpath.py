from typing import Any

MISSING = object()

_DIGITS = set("0123456789")


def lookup(body: Any, path: str) -> Any:
    if path == "$":
        return body
    if not path.startswith("$"):
        return MISSING
    current = body
    index = 1
    length = len(path)
    while index < length:
        char = path[index]
        if char == ".":
            index += 1
            name, index = _read_name(path, index)
            if not isinstance(current, dict) or name not in current:
                return MISSING
            current = current[name]
            continue
        if char == "[":
            index += 1
            number, index = _read_index(path, index)
            if index >= length or path[index] != "]":
                return MISSING
            index += 1
            if not isinstance(current, list) or number >= len(current):
                return MISSING
            current = current[number]
            continue
        return MISSING
    return current


def _read_name(path: str, index: int) -> tuple[str, int]:
    start = index
    while index < len(path) and path[index] not in ".[":
        index += 1
    return path[start:index], index


def _read_index(path: str, index: int) -> tuple[int, int]:
    start = index
    while index < len(path) and path[index] in _DIGITS:
        index += 1
    return int(path[start:index] or "0"), index
