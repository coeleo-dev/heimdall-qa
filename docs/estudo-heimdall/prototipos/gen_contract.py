#!/usr/bin/env python3
"""Disposable study prototype (F3) — generate a harness `contract` from OpenAPI 3.1.

Not production code. Lives under docs/ so it is never imported by the package.

What it does: reads an OpenAPI document, picks one operation, and emits the
harness contract YAML shape that `contracts/*.yaml` uses today. Then it diffs
what it emitted against the human-written contract for the same route.

Run from the repo root:
    .venv/bin/python docs/estudo-heimdall/prototipos/gen_contract.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

PROTO = Path(__file__).parent
REPO = PROTO.parent.parent.parent

# Keywords that JSON Schema carries and that map 1:1 onto a contract field.
SCHEMA_TO_CONTRACT = (
    ("maxLength", "max_length"),
    ("maxProperties", "max_keys"),
    ("pattern", "pattern"),
    ("required", "required"),
)


def load_document(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def pick_operation(document: dict, path: str, method: str) -> tuple[dict, dict]:
    operation = document["paths"][path][method.lower()]
    schema_ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    name = schema_ref.rsplit("/", 1)[-1]
    return operation, document["components"]["schemas"][name]


def resolve_auth(operation: dict, document: dict) -> str:
    """Only 'none' and 'api_key' are derivable; anything else needs the project."""
    schemes = operation.get("security") or []
    if not schemes:
        return "none"
    declared = set(schemes[0])
    if "apiKey" in declared:
        return "api_key"
    # `jwt`, `admin`, `hmac` are harness vocabulary the schema cannot supply.
    return "UNKNOWN"


def derive_fields(schema: dict) -> dict:
    required = set(schema.get("required") or [])
    fields: dict[str, dict] = {}
    for name, spec in (schema.get("properties") or {}).items():
        entry: dict = {"required": name in required, "json": name}
        if spec.get("maxLength") is not None:
            entry["max_length"] = spec["maxLength"]
        if spec.get("maxProperties") is not None:
            entry["max_keys"] = spec["maxProperties"]
        if spec.get("pattern") is not None:
            entry["pattern"] = spec["pattern"]
        fields[name] = entry
    return fields


def generate(document: dict, path: str, method: str) -> dict:
    operation, schema = pick_operation(document, path, method)
    contract: dict = {
        "endpoint": f"{method.upper()} {path}",
        "dto": "GENERATED — no DTO provenance in the schema",
        "auth": resolve_auth(operation, document),
        "fields": derive_fields(schema),
    }
    # Nothing below is derivable; the generator marks it so a human fills it in.
    contract["idempotency"] = "TODO not in schema"
    contract["dedup"] = "TODO not in schema"
    contract["async"] = "TODO not in schema"
    contract["rules"] = []
    contract["live_only_rules"] = []
    contract["p_gaps"] = []
    return contract


def diff_against(contract: dict, human_path: Path) -> list[str]:
    human = yaml.safe_load(human_path.read_text(encoding="utf-8"))
    lines: list[str] = []

    def cmp(key: str) -> None:
        got = contract.get(key, "<absent>")
        want = human.get(key, "<absent>")
        mark = "SAME" if got == want else "DIFF"
        lines.append(f"  [{mark}] {key}: generated={got!r} human={want!r}")

    for key in ("endpoint", "auth", "idempotency", "dedup", "async"):
        cmp(key)

    gen_fields, human_fields = contract["fields"], human.get("fields", {})
    lines.append(f"  fields: generated={len(gen_fields)} human={len(human_fields)}")
    for name in sorted(set(gen_fields) | set(human_fields)):
        g = gen_fields.get(name, "<absent>")
        h = human_fields.get(name, "<absent>")
        if g == h:
            lines.append(f"    [SAME] {name}")
        else:
            lines.append(f"    [DIFF] {name}\n      generated={g}\n      human={h}")
    lines.append(f"  [DIFF] rules: generated={len(contract['rules'])} human={len(human.get('rules', []))}")
    lines.append(f"  [DIFF] p_gaps: generated={len(contract['p_gaps'])} human={len(human.get('p_gaps', []))}")
    return lines


def main() -> int:
    document = load_document(PROTO / "openapi-ingest-3.1.yaml")
    contract = generate(document, "/api/ingest", "post")

    print("=== GENERATED CONTRACT ===")
    print(yaml.safe_dump(contract, sort_keys=False, allow_unicode=True))

    human_path = REPO / "contracts" / "api-ingest-post.yaml"
    print(f"=== DIFF vs {human_path.relative_to(REPO)} ===")
    for line in diff_against(contract, human_path):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
