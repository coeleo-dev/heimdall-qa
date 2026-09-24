"""OpenAPI -> the neutral IR, with a YAML parser and nothing else.

An OpenAPI document is plain data, and PyYAML is already a dependency, so a
dedicated library would add a dependency to read a mapping — and it would still not
answer the questions the harness asks. Those questions are the mapping in this
module: which field is `required`, which unlock a `B-max` or an `N-pattern`, and
what the happy status is. Keeping it visible means a reader for another format can
be read side by side with this one and disagree only about the format.

What the format cannot express is reported, never invented. `window` ("not in the
future", "not older than N hours"), `json_alias` (a second accepted spelling) and a
PII `denylist` have no OpenAPI spelling at all, so they leave a `Gap` naming the
axis that stays uncovered. Inventing a denylist would generate a case that is green
and verifies nothing, which is worse than a gap.

The reader accepts a path or an `http(s)` URL. A URL is read with `httpx` — already
a dependency — because `/openapi.json` from a running service is the common case,
and `discover` is an explicit command rather than something a run does on its own.
"""

from pathlib import Path
from typing import Any

import httpx
import yaml

from heimdall_qa.contract_source import CONTRACT_SOURCE_FAILED
from heimdall_qa.contract_source import OPENAPI
from heimdall_qa.contract_source import ApiSchema
from heimdall_qa.contract_source import EndpointSchema
from heimdall_qa.contract_source import FieldSchema
from heimdall_qa.contract_source import Gap
from heimdall_qa.errors import HarnessError

#: The methods that describe an operation. `head`/`options`/`trace` are excluded
#: because no case axis in `coverage.expand` has anything to say about them.
_METHODS = ("get", "post", "put", "patch", "delete")

#: The media type a case sends, and therefore the one the reader prefers.
_JSON = "application/json"

#: The header the harness's `idempotency: header_uuid_v4` means. It is the
#: harness's own convention — the runner stamps it on every mutating case — so the
#: core naming it is not a product leak, the way `X-Trace-Id` is not.
_IDEMPOTENCY_HEADER = "x-idempotency-key"

#: What no OpenAPI document can say, the axis it leaves uncovered, and how a human
#: closes it. The axis is named because a gap that names none is not actionable:
#: nobody can tell whether the missing fact costs one case or twelve.
_UNSPELLABLE: tuple[tuple[str, str, str], ...] = (
    (
        "O-alias-*",
        "OpenAPI allows one name per property, so a second accepted spelling"
        " cannot be seen from the document",
        "set `json_alias: true` on the fields that accept a second spelling",
    ),
    (
        "window / B-max-*-future / B-min-*-past",
        "the format has no keyword for a bound on a timestamp",
        "declare `window: {future_minutes, past_hours}` on the timestamp field",
    ),
    (
        "N-denylist-*",
        "a PII denylist is a policy about what the API refuses, not a schema fact"
        " about what it accepts",
        "declare `denylist:` on the fields the API must refuse, or waive the axis",
    ),
)


class OpenApiSource:
    """The reader for an OpenAPI 3.1 or 3.0 document, in its YAML or JSON spelling."""

    name = OPENAPI

    def read(self, location: str | None) -> ApiSchema:
        document = _document(_require_location(location))
        endpoints: list[EndpointSchema] = []
        gaps: list[Gap] = []
        for path, method, operation in _operations(document):
            body = _body_schema(document, operation)
            fields = _fields(body)
            endpoints.append(_endpoint(document, path, method, body, fields))
            gaps.extend(
                _gaps(
                    path,
                    method,
                    operation,
                    has_body=body is not None,
                    field_count=len(fields),
                )
            )
        return ApiSchema(source=OPENAPI, endpoints=tuple(endpoints), gaps=tuple(gaps))


def reader() -> OpenApiSource:
    """The factory the registry builds."""
    return OpenApiSource()


def _require_location(location: str | None) -> str:
    if not location or not location.strip():
        raise HarnessError(
            code=CONTRACT_SOURCE_FAILED,
            message="contract.source is openapi but contract.location is empty",
            hint=(
                "set `contract.location` in the project descriptor, e.g."
                " `location: ./openapi.json`, or point it at the running"
                " service's /openapi.json"
            ),
        )
    return location.strip()


def _document(location: str) -> dict[str, Any]:
    """The parsed document, or the failure that names why it is unreadable."""
    try:
        loaded = yaml.safe_load(_text(location))
    except yaml.YAMLError as exc:
        raise _failed(location, f"is not readable as YAML or JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise _failed(location, "must be a mapping")
    if not (loaded.get("openapi") or loaded.get("swagger")):
        raise _failed(
            location,
            "has no `openapi:` or `swagger:` key, so it does not look like an"
            " OpenAPI document",
        )
    paths = loaded.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise _failed(location, "declares no paths")
    return loaded


def _text(location: str) -> str:
    if location.startswith(("http://", "https://")):
        try:
            response = httpx.get(location, timeout=10.0, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise _failed(location, f"could not be fetched: {exc}") from exc
        return response.text
    try:
        return Path(location).read_text(encoding="utf-8")
    except OSError as exc:
        raise _failed(location, f"could not be read: {exc}") from exc


def _failed(location: str, why: str) -> HarnessError:
    return HarnessError(
        code=CONTRACT_SOURCE_FAILED,
        message=f"the OpenAPI document at {location} {why}",
        hint="fix the document, or change contract.source in the project descriptor",
    )


def _operations(document: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Every (path, method, operation) in document order.

    Document order and not a sort: the report a human reads should look like the
    document they wrote, and a reader that reorders silently makes a diff harder
    to review than it has to be.
    """
    found: list[tuple[str, str, dict[str, Any]]] = []
    for path, item in document["paths"].items():
        if not isinstance(item, dict):
            continue
        for method in _METHODS:
            operation = item.get(method)
            if isinstance(operation, dict):
                found.append((str(path), method, operation))
    return found


def _endpoint(
    document: dict[str, Any],
    path: str,
    method: str,
    body: dict[str, Any] | None,
    fields: tuple[FieldSchema, ...],
) -> EndpointSchema:
    operation = _operation(document, path, method)
    parameters = _parameters(document, path, method)
    return EndpointSchema(
        method=method,
        path=path,
        has_body=body is not None,
        fields=fields,
        required_headers=tuple(
            _name(parameter)
            for parameter in parameters
            if parameter.get("in") == "header" and parameter.get("required") is True
        ),
        path_params=tuple(
            _name(parameter) for parameter in parameters if parameter.get("in") == "path"
        ),
        success_status=_success_status(operation),
        idempotency=(
            "header_uuid_v4"
            if _has_required_header(parameters, _IDEMPOTENCY_HEADER)
            else "none"
        ),
        resource_id_in_path="{" in path,
    )


def _operation(document: dict[str, Any], path: str, method: str) -> dict[str, Any]:
    return document["paths"][path][method]


def _parameters(document: dict[str, Any], path: str, method: str) -> list[dict[str, Any]]:
    """The parameters of the operation, with the path item's merged in.

    Both levels are legal in OpenAPI and a real document uses both, so a reader
    that looks at only one of them loses parameters without saying so.
    """
    item = document["paths"][path]
    found: list[dict[str, Any]] = []
    for source in (item.get("parameters"), _operation(document, path, method).get("parameters")):
        if isinstance(source, list):
            found.extend(parameter for parameter in source if isinstance(parameter, dict))
    return found


def _name(parameter: dict[str, Any]) -> str:
    return str(parameter.get("name"))


def _has_required_header(parameters: list[dict[str, Any]], wanted: str) -> bool:
    return any(
        _name(parameter).lower() == wanted
        and parameter.get("in") == "header"
        and parameter.get("required") is True
        for parameter in parameters
    )


def _body_schema(document: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any] | None:
    body = operation.get("requestBody")
    if not isinstance(body, dict):
        return None
    content = body.get("content")
    if not isinstance(content, dict) or not content:
        return None
    media = content.get(_JSON) if isinstance(content.get(_JSON), dict) else None
    if media is None:
        media = next((item for item in content.values() if isinstance(item, dict)), {})
    schema = media.get("schema")
    if not isinstance(schema, dict):
        return None
    return _resolve(document, schema)


def _resolve(document: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Follow a local `$ref` and merge `allOf`, so a `components/schemas` body reads.

    Almost every real document points the body at `#/components/schemas/X`, so a
    reader that does not follow the reference sees no fields at all — the most
    common document shape would produce the emptiest possible contract.

    A remote or circular reference is left unresolved and yields no properties, and
    the generator reports the resulting empty body as `TODO` rather than pretending
    the body has none. Fetching a second document is not this reader's job.
    """
    seen: set[str] = set()
    current = schema
    while isinstance(current.get("$ref"), str):
        reference = current["$ref"]
        if not reference.startswith("#/") or reference in seen:
            return {}
        seen.add(reference)
        target: Any = document
        for part in reference[2:].split("/"):
            if not isinstance(target, dict) or part not in target:
                return {}
            target = target[part]
        if not isinstance(target, dict):
            return {}
        current = target
    composed = current.get("allOf")
    if not isinstance(composed, list):
        return current
    merged: dict[str, Any] = {"properties": {}, "required": []}
    for part in composed:
        if not isinstance(part, dict):
            continue
        resolved = _resolve(document, part)
        merged["properties"].update(resolved.get("properties") or {})
        merged["required"].extend(resolved.get("required") or [])
    for key, value in current.items():
        if key != "allOf":
            merged[key] = value
    return merged


def _fields(schema: dict[str, Any] | None) -> tuple[FieldSchema, ...]:
    """The body fields, in document order.

    A body that is a free-form `object` has no properties, and that is reported as
    no fields rather than as a field called anything: the generator then leaves the
    body as `TODO`, which is the truth about what the document said.
    """
    if not schema:
        return ()
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return ()
    required = schema.get("required")
    needed = {str(name) for name in required} if isinstance(required, list) else set()
    return tuple(
        _field(str(name), spec, str(name) in needed)
        for name, spec in properties.items()
        if isinstance(spec, dict)
    )


def _field(name: str, spec: dict[str, Any], required: bool) -> FieldSchema:
    return FieldSchema(
        name=name,
        required=required,
        json_name=name,
        max_length=_integer(spec.get("maxLength")),
        max_keys=_integer(spec.get("maxProperties")),
        pattern=_string(spec.get("pattern")),
        example=_example(spec),
    )


def _example(spec: dict[str, Any]) -> Any:
    """The sample the document offers, without inventing one.

    `example` first, then the only entry of `examples`, then `default`, then the
    first `enum` value: all four are the document saying "a value looks like this",
    which is what keeps a generated `B-max` from fabricating a seed of its own.
    """
    if spec.get("example") is not None:
        return spec["example"]
    examples = spec.get("examples")
    if isinstance(examples, dict) and examples:
        first = next(iter(examples.values()))
        if isinstance(first, dict) and first.get("value") is not None:
            return first["value"]
        return first
    if spec.get("default") is not None:
        return spec["default"]
    values = spec.get("enum")
    if isinstance(values, list) and values:
        return values[0]
    return None


def _success_status(operation: dict[str, Any]) -> int | None:
    """The lowest declared 2xx, or `None` when the document declares none.

    Lowest and not first: `responses` is a mapping whose order carries no meaning,
    and 200 before 202 is a stable rule where document order is not.
    """
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        return None
    codes = sorted(
        int(code)
        for code in (str(key) for key in responses)
        if code.isdigit() and 200 <= int(code) < 300
    )
    return codes[0] if codes else None


def _gaps(
    path: str,
    method: str,
    operation: dict[str, Any],
    *,
    has_body: bool,
    field_count: int,
) -> list[Gap]:
    """What this endpoint leaves for a human, by axis.

    The happy status is checked first because it is the one fact whose absence
    breaks every axis at once: with no declared 2xx the generator can only write
    `status: TODO`, and `validate` refuses that. Naming it here is how the report
    says which endpoint to open first.

    An endpoint with no body gets nothing else: `O-alias`, `window`, `denylist` and
    `N-rule` are all statements about a body, and reporting them where there is none
    would train the reader to ignore the report.
    """
    endpoint = f"{method.upper()} {path}"
    gaps: list[Gap] = []
    if _success_status(operation) is None:
        gaps.append(
            Gap(
                endpoint=endpoint,
                axis="H01",
                why="the document declares no 2xx response for this operation",
                fix="declare the happy status in the contract's `expect.status`",
            )
        )
    if not has_body:
        return gaps
    if not field_count:
        gaps.append(
            Gap(
                endpoint=endpoint,
                axis="O-* / B-* / N-omit-* / N-over-* / N-pattern-*",
                why=(
                    "the document declares a body but describes no properties, so"
                    " every generated case would send the empty object"
                ),
                fix=(
                    "declare `fields:` in the contract, or put the body every case"
                    " starts from in the baseline the contract points at"
                ),
            )
        )
    gaps.extend(
        Gap(endpoint=endpoint, axis=axis, why=why, fix=how)
        for axis, why, how in _UNSPELLABLE
    )
    gaps.append(
        Gap(
            endpoint=endpoint,
            axis="N-rule-*",
            why="a product rule is code, not schema",
            fix=(
                "author `rules:` in the contract, with the status each rule"
                " answers — the status is per rule, never one number"
            ),
        )
    )
    return gaps


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
