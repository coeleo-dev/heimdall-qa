from dataclasses import dataclass
from dataclasses import field
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BruRequest:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: Any | None = None


_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")


def parse_bru(path: Path) -> BruRequest:
    text = path.read_text(encoding="utf-8")
    method, url = _parse_method_url(text)
    headers = _parse_block_pairs(text, "headers")
    body = _parse_json_body(text)
    return BruRequest(method=method, url=url, headers=headers, body=body)


def _parse_method_url(text: str) -> tuple[str, str]:
    for name in _METHODS:
        block = _extract_block(text, name)
        if block is None:
            continue
        pairs = _parse_pairs(block)
        url = pairs.get("url", "")
        return name.upper(), url
    raise ValueError("Bruno file has no HTTP method block")


def _parse_block_pairs(text: str, name: str) -> dict[str, str]:
    block = _extract_block(text, name)
    if block is None:
        return {}
    return _parse_pairs(block)


def _parse_json_body(text: str) -> Any | None:
    block = _extract_block(text, "body:json")
    if block is None:
        return None
    brace = block.find("{")
    if brace < 0:
        stripped = block.strip()
        if not stripped:
            return None
        return json.loads(stripped)
    return json.loads(_extract_balanced(block, brace))


def _extract_block(text: str, name: str) -> str | None:
    token = f"{name} {{"
    start = text.find(token)
    if start < 0:
        return None
    open_brace = text.find("{", start)
    inner_start = open_brace + 1
    depth = 1
    index = inner_start
    while index < len(text) and depth:
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        index += 1
    return text[inner_start : index - 1]


def _extract_balanced(text: str, open_index: int) -> str:
    depth = 0
    in_string = False
    escape = False
    for index in range(open_index, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[open_index : index + 1]
    raise ValueError("unbalanced JSON in Bruno body")


def _parse_pairs(block: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        pairs[key.strip()] = value.strip()
    return pairs
