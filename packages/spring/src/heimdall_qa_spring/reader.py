"""The Spring reader: a controller, a record and an advice class, read as a contract.

It answers the questions `coverage.expand` asks and refuses to answer the ones the
code does not: which routes exist, what each one takes, what each one declares about
its fields, which status it answers, and which failures the advice maps to which
axis. Everything else — a business rule, a constraint a custom annotation performs,
the value a path parameter takes — becomes a `Gap` with the axis it costs.

The reader is a distribution of its own on purpose. A Spring reader has nothing to do
with any product, and the alternative — living inside a product's provider — would
mean a second Java project had to install that product to read its own source.

Decisions worth keeping:

**A controller with no class-level `@RequestMapping` is normal, not broken.** Several
reference controllers put the whole path on the method, and a reader that expected a
class prefix would generate contracts for routes that do not exist.

**A status the code chooses at runtime is `None`.** A `switch` over five outcomes has
no answer this reader may pick: picking one would make every generated case for that
route agree with our guess instead of with the API.

**A `@Profile`-gated controller is read, and its cases are not generated.** The route
is real, so it appears in the report with the profile it needs; generating cases for a
route that is disabled in the target deployment would produce failures that say
nothing about the product.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from heimdall_qa.contract_source import CONTRACT_SOURCE_FAILED
from heimdall_qa.contract_source import SURFACE
from heimdall_qa.contract_source import ApiSchema
from heimdall_qa.contract_source import EndpointSchema
from heimdall_qa.contract_source import ErrorTable
from heimdall_qa.contract_source import FieldSchema
from heimdall_qa.contract_source import Gap
from heimdall_qa.errors import HarnessError
from heimdall_qa_spring import errors as error_reading
from heimdall_qa_spring.java import Annotation
from heimdall_qa_spring.java import JavaType
from heimdall_qa_spring.java import Member
from heimdall_qa_spring.java import Parameter
from heimdall_qa_spring.java import find_matching
from heimdall_qa_spring.java import parse_java
from heimdall_qa_spring.java import simple_name
from heimdall_qa_spring.java import type_head
from heimdall_qa_spring.java import unquote

#: The name this reader registers under, which is what `contract.source` says.
SPRING = "spring"

#: The mapping annotations, as the method each stands for. `RequestMapping` is absent
#: on purpose: it names its method instead of implying one, and it is what every
#: controller in the reference corpus puts on the *class*.
MAPPING_METHODS: dict[str, str] = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "PatchMapping": "PATCH",
    "DeleteMapping": "DELETE",
}

#: Annotations that make a field required. `AssertTrue` is here because it is how Java
#: declares "this flag must be true", and a contract that missed it would generate no
#: case for the one value the API refuses.
REQUIRED_ANNOTATIONS: frozenset[str] = frozenset(
    {"NotBlank", "NotNull", "NotEmpty", "AssertTrue", "AssertFalse"}
)

#: Annotations a contract can carry something for. Anything else on a component is a
#: constraint `coverage.expand` has no family for, so it becomes a gap.
SPEAKABLE_ANNOTATIONS: frozenset[str] = frozenset(
    {
        "NotBlank",
        "NotNull",
        "NotEmpty",
        "AssertTrue",
        "AssertFalse",
        "Size",
        "Pattern",
        "JsonProperty",
        "JsonAlias",
        "Valid",
        "Deprecated",
        "Schema",
        "JsonPropertyDescription",
    }
)

#: A body or component whose type says nothing about its fields. Each one leaves the
#: fields empty and a gap behind, and never a set of invented properties.
OPAQUE_TYPES: frozenset[str] = frozenset(
    {
        "Map",
        "HashMap",
        "LinkedHashMap",
        "TreeMap",
        "ConcurrentHashMap",
        "Object",
        "JsonNode",
        "ObjectNode",
        "ArrayNode",
        "MultiValueMap",
        "String",
        "InputStream",
        "MultipartFile",
    }
)

#: Collection bodies: the field axes describe an object's properties, not elements.
COLLECTION_TYPES: frozenset[str] = frozenset({"List", "Set", "Collection", "Iterable"})

#: A *field* whose `@Size(max)` bounds a count and not a length. It is not
#: `OPAQUE_TYPES`: that set describes a whole body and holds `String` — a `String`
#: request body has no properties, while a `String` *field* is a scalar whose
#: `@Size(max = 128)` is 128 characters, the most ordinary constraint in the corpus.
COUNTED_TYPES: frozenset[str] = frozenset(
    COLLECTION_TYPES
    | {"Map", "HashMap", "LinkedHashMap", "TreeMap", "ConcurrentHashMap", "MultiValueMap"}
)

#: A field whose keys are the caller's to choose, which is the only shape a PII
#: denylist describes: the corpus declares `denylist` on a free-form `properties`
#: map. Attaching it to a `String` field would deny the string "cpf" — a different
#: policy than the one the constant holds.
OBJECT_TYPES: frozenset[str] = frozenset(
    {"Map", "HashMap", "LinkedHashMap", "TreeMap", "ConcurrentHashMap", "MultiValueMap",
     "Object", "JsonNode", "ObjectNode"}
)

#: Headers the harness already knows how to wear, so requiring one is not a gap:
#: `Authorization` is what a credential header is, and the idempotency key is a
#: contract attribute (`idempotency: header_uuid_v4`).
KNOWN_HEADERS: frozenset[str] = frozenset({"authorization", "x-idempotency-key"})

#: A header whose name says this is the replay key, whatever the project calls it.
_IDEMPOTENCY_HEADER = re.compile(r"idempotency", re.I)

#: Directories that hold something other than the shipped surface. A build directory
#: would double every route, and a test tree is where MockMvc tests live.
_SKIP_DIRECTORIES: frozenset[str] = frozenset(
    {"target", "build", "out", "bin", "generated", ".git", "node_modules", "test", "tests"}
)

_STRING_LITERAL = re.compile(r'"(?:[^"\\]|\\.)*"')
_DENYLIST_NAME = re.compile(r"(denylist|denied|blocked)", re.I)

#: The status that means "accepted for later processing".
ACCEPTED = 202

#: The axis a shape gap reports under. `coverage.expand` derives all three families
#: from the four mechanical attributes, so a body whose shape cannot be read costs all
#: three at once and the gap says so.
SHAPE_AXES = "N-omit / B-max / N-pattern / O-*"


class SpringSource:
    """The reader the entry point builds."""

    name = SPRING

    def read(self, location: str | None) -> ApiSchema:
        """Read every `.java` file under `location` as an API surface.

        A missing directory is a named failure and never an empty schema: a project
        that points the reader at the wrong path has to hear about it, because the
        alternative is a report saying "this API has no endpoints" about an API that
        has 93.
        """
        root = _root(location)
        types = tuple(_read_types(root))
        if not types:
            # A directory with no `.java` under it is the wrong directory, not an API
            # with nothing in it. Saying so is the whole point of the seam: an empty
            # schema here would be reported by `discover` as "this API has no
            # endpoints", which is the one sentence that must never be wrong.
            raise HarnessError(
                code=CONTRACT_SOURCE_FAILED,
                message=f"the spring reader found no `.java` file under '{root}'",
                hint=(
                    "`location` is the root of a Java source tree — usually"
                    " `src/main/java`, where the packages start — and a build or"
                    " resource directory has none"
                ),
            )
        index = _Index(types)
        local = _local_handlers(types)
        read = error_reading.read_errors(types, local=local)
        denylist = _denylist(index)
        table = ErrorTable(
            statuses=read.statuses,
            envelope=read.envelope,
            notes=_error_notes(read),
        )
        gaps: list[Gap] = list(_surface_gaps(denylist))
        endpoints: list[EndpointSchema] = []
        for controller in types:
            if not controller.has("RestController"):
                continue
            built, missing = _endpoints_of(controller, index, denylist)
            endpoints.extend(built)
            gaps.extend(missing)
        return ApiSchema(
            source=SPRING,
            endpoints=tuple(endpoints),
            gaps=tuple(gaps),
            errors=table,
        )


def reader() -> SpringSource:
    """The factory this distribution registers."""
    return SpringSource()


@dataclass(frozen=True)
class _Path:
    """How deep a nested component sits, in both spellings at once.

    `name` follows the Java components and `wire` follows the JSON properties, and
    they differ exactly where `@JsonProperty` does. Carrying them together keeps the
    recursion from having to remember which is which at each level.
    """

    name: str = ""
    wire: str = ""


#: The root of a body: no prefix, in either spelling.
_ROOT = _Path()


@dataclass(frozen=True)
class _Index:
    """Every declared type, looked up by the name a declaration would use.

    A body is usually named by its simple name and imported, so the simple name is
    what the reader has; two packages may declare the same one, which is why the
    ambiguous case returns nothing rather than the first hit.
    """

    types: tuple[JavaType, ...]

    def by_simple(self, name: str) -> tuple[JavaType, ...]:
        wanted = simple_name(name)
        return tuple(declared for declared in self.types if declared.name == wanted)


def _root(location: str | None) -> Path:
    """The directory to read, or the failure that names what was wrong with it."""
    if not location or not str(location).strip():
        raise HarnessError(
            code=CONTRACT_SOURCE_FAILED,
            message="the spring reader has no source tree to read",
            hint=(
                "declare `contract: {source: spring, location: ../src/main/java}` in"
                " the project descriptor, or pass --location PATH for one run"
            ),
        )
    root = Path(str(location).strip())
    if not root.is_dir():
        raise HarnessError(
            code=CONTRACT_SOURCE_FAILED,
            message=f"the spring reader found no source tree at '{root}'",
            hint=(
                "`location` is a *directory* of `.java` files — usually"
                " `src/main/java` — not a file and not a URL"
            ),
        )
    return root


def _read_types(root: Path) -> Iterator[JavaType]:
    """Every type declared in the tree, in a stable order."""
    for path in sorted(root.rglob("*.java")):
        relative = path.relative_to(root)
        if _SKIP_DIRECTORIES.intersection(relative.parts[:-1]):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            # A file the reader cannot decode is skipped, and its absence shows up as
            # a body the index cannot resolve — a gap — rather than a crash.
            continue
        yield from parse_java(str(relative), text).all_types()


def _local_handlers(types: tuple[JavaType, ...]) -> tuple[error_reading.Handler, ...]:
    """`@ExceptionHandler` methods declared on the controllers themselves.

    They matter because Spring runs them before the advice, and the reference corpus
    has one: a metering controller that answers 402 for a denied entitlement, where a
    reader looking only at the advice would report the axis as unanswered.
    """
    found: list[error_reading.Handler] = []
    for declared in types:
        if not declared.has("RestController") or declared.has("RestControllerAdvice"):
            continue
        found.extend(
            error_reading.handlers_of(declared, origin=f"controller {declared.name}")
        )
    return tuple(found)


def _error_notes(read: error_reading.ErrorRead) -> tuple[str, ...]:
    """The table's own notes, plus what the reader noticed about uniformity.

    Uniformity is where it bites: an advice class that answers a typed error body in
    22 handlers and a bare string map in the 23rd produces evidence that cannot be
    decoded one way, and the reference corpus does exactly that in its 429 path.

    The unanswered axes are named for the same reason a gap is: an axis with no
    handler is not an axis the API does not have, it is one this reader could not
    read, and the difference decides whether a human has to author the case. Falling
    back to the descriptor's default silently is how a status stays wrong for a year.
    """
    notes = list(read.notes)
    bodies = sorted(
        {
            handler.body_type
            for handler in read.handlers
            if handler.body_type and handler.status is not None
        }
    )
    if len(bodies) > 1:
        notes.append(
            "the error body is not uniform across handlers: "
            + ", ".join(f"`{body}`" for body in bodies)
        )
    local = sorted(
        {handler.origin for handler in read.handlers if handler.origin != "global"}
    )
    if local:
        notes.append(
            "a controller declares its own handler, which overrides the advice for"
            " the whole surface: " + ", ".join(local)
        )
    unanswered = sorted(set(error_reading.PRIMARY_AXIS.values()) - set(read.statuses))
    if unanswered:
        notes.append(
            "no handler answers for "
            + ", ".join(f"`{axis}`" for axis in unanswered)
            + ", so those cases fall back to the descriptor and then to the harness"
            " default"
        )
    return tuple(notes)


def _surface_gaps(denylist: tuple[str, ...]) -> list[Gap]:
    """Gaps about the whole surface rather than about one route."""
    auth = Gap(
        endpoint=SURFACE,
        axis="N-auth",
        why=(
            "`auth` is the one contract attribute no source can be asked: a controller"
            " declares *that* a credential is required, never which scheme the harness"
            " should wear"
        ),
        fix="declare routes[].auth in the project descriptor, which discover reads",
    )
    if not denylist:
        return [auth]
    return [
        auth,
        Gap(
            endpoint=SURFACE,
            axis="N-denylist-*",
            why=(
                "the PII denylist was read from a declared constant"
                f" ({', '.join(denylist[:3])}, …): it is read, not assumed"
            ),
            fix="confirm the list is the policy, or replace it in the contract",
        ),
    ]


def _endpoints_of(
        controller: JavaType,
        index: _Index,
        denylist: tuple[str, ...],
) -> tuple[list[EndpointSchema], list[Gap]]:
    """Every route a controller declares, and everything each one leaves open."""
    base = _class_path(controller)
    profile = _profile(controller)
    found: list[EndpointSchema] = []
    gaps: list[Gap] = []
    for method in controller.methods():
        mapping = _mapping_of(method)
        if mapping is None:
            continue
        http_method, paths = mapping
        for path in paths:
            route = f"{http_method} {_join(base, path)}"
            built, missing = _endpoint(
                controller=controller,
                method=method,
                http_method=http_method,
                path=_join(base, path),
                index=index,
                profile=profile,
                denylist=denylist,
                route=route,
            )
            found.append(built)
            gaps.extend(missing)
    return found, gaps


def _class_path(controller: JavaType) -> str:
    """The prefix a class-level `@RequestMapping` declares, or nothing.

    Several reference controllers declare none and put the full path on each method,
    so "no prefix" is a normal answer and never a failure.
    """
    annotation = controller.annotation("RequestMapping")
    return "" if annotation is None else _first_path(annotation)


def _profile(controller: JavaType) -> str | None:
    """The profiles a controller needs, joined, or `None` when it always runs."""
    annotation = controller.annotation("Profile")
    if annotation is None:
        return None
    values = annotation.unquote()
    if not values:
        values = tuple(
            part.strip().strip("{}").strip('"')
            for part in (annotation.arguments or "").split(",")
            if part.strip()
        )
    joined = ", ".join(value for value in values if value)
    return joined or None


def _mapping_of(method: Member) -> tuple[str, tuple[str, ...]] | None:
    """The HTTP method and the paths a handler declares, or `None` for no handler."""
    for annotation in method.annotations:
        http_method = MAPPING_METHODS.get(annotation.name)
        if http_method is not None:
            return http_method, _paths(annotation)
    request = method.annotation("RequestMapping")
    if request is None:
        return None
    declared = tuple(
        simple_name(name)
        for name in re.findall(r"RequestMethod\.([A-Z]+)", request.arguments or "")
    )
    methods = tuple(name for name in declared if name in MAPPING_METHODS.values())
    if len(methods) != 1:
        return None
    return methods[0], _paths(request)


def _paths(annotation: Annotation) -> tuple[str, ...]:
    """Every path an annotation maps, because one mapping may name more than one."""
    values: tuple[str, ...] = ()
    positional = annotation.value()
    if positional is not None:
        values = (positional,)
    values += annotation.named_strings("value") + annotation.named_strings("path")
    if not values:
        values = tuple(
            value for value in annotation.unquote() if value.startswith("/")
        )
    return tuple(value for value in values if value) or ("",)


def _first_path(annotation: Annotation) -> str:
    """The first path an annotation names, or nothing."""
    paths = _paths(annotation)
    return paths[0] if paths and paths[0].startswith("/") else ""


def _join(base: str, path: str) -> str:
    """`/api/ingest` + `/{id}` -> `/api/ingest/{id}`, and nothing -> `/`."""
    joined = f"{base.rstrip('/')}/{path.lstrip('/')}".rstrip("/")
    return joined or "/"


def _endpoint(
        *,
        controller: JavaType,
        method: Member,
        http_method: str,
        path: str,
        index: _Index,
        profile: str | None,
        denylist: tuple[str, ...],
        route: str,
) -> tuple[EndpointSchema, list[Gap]]:
    """One route, as a contract, plus the gaps it opens."""
    gaps: list[Gap] = []
    idempotency, idempotency_gap = _idempotency(controller, method, route)
    if idempotency_gap is not None:
        gaps.append(idempotency_gap)
    headers = _required_headers(controller, method, route, gaps)
    fields, body_gaps = _body_fields(controller, method, index, route, denylist)
    gaps.extend(body_gaps)
    if profile:
        gaps.append(
            Gap(
                endpoint=route,
                axis="profile",
                why=(
                    f"`{controller.name}` is enabled by `@Profile({profile})`, so this"
                    " route exists only where that profile is active"
                ),
                fix=(
                    "run the round against a deployment with the profile on, or waive"
                    " the coverage for this route"
                ),
            )
        )
    success, status_gap = _success_status(method, route)
    if status_gap is not None:
        gaps.append(status_gap)
    gaps.extend(_query_gaps(method, route))
    gaps.append(_rule_gap(method, route))
    path_params = tuple(
        parameter.name
        for parameter in method.parameters
        if parameter.has("PathVariable")
    )
    return (
        EndpointSchema(
            method=http_method,
            path=path,
            has_body=method.parameter_has("RequestBody"),
            fields=fields,
            required_headers=headers,
            path_params=path_params,
            success_status=success,
            idempotency=idempotency,
            resource_id_in_path=bool(path_params),
            async_mode=_async_mode(success, method),
            conditional=profile,
        ),
        gaps,
    )


def _required_headers(
        controller: JavaType,
        method: Member,
        route: str,
        gaps: list[Gap],
) -> tuple[str, ...]:
    """The headers a handler requires, and a gap for each the harness cannot wear.

    `@RequestHeader(value = "X", required = false)` is how the reference API takes its
    environment header, so an optional header is never a requirement: a generated
    `I-missing` case for one would expect a failure the API does not produce.
    """
    found: list[str] = []
    for parameter in method.parameters:
        annotation = parameter.annotation("RequestHeader")
        if annotation is None or annotation.named_bool("required") is False:
            continue
        name = _header_name(controller, annotation)
        if name is None:
            gaps.append(
                Gap(
                    endpoint=route,
                    axis="I-missing",
                    why=(
                        "a `@RequestHeader` names its header through a constant this"
                        " reader could not resolve"
                    ),
                    fix="declare the header name as a literal, or author the case",
                )
            )
            continue
        found.append(name)
        if name.lower() in KNOWN_HEADERS or _IDEMPOTENCY_HEADER.search(name):
            continue
        gaps.append(
            Gap(
                endpoint=route,
                axis="I-missing",
                why=(
                    f"`{name}` is required but the harness has no case family for a"
                    " required header other than the idempotency key"
                ),
                fix=f"declare `omit_headers: [{name}]` on the case that must be refused",
            )
        )
    return tuple(found)


def _header_name(controller: JavaType, annotation: Annotation) -> str | None:
    """The literal header name, resolving one constant reference if it must.

    `@RequestHeader(IDEMPOTENCY_KEY_HEADER)` is how the reference metering controller
    names its key, and a reader that gave up at the constant would miss the attribute
    that makes four cases per route possible.
    """
    literal = annotation.value()
    if literal is not None:
        return literal
    named = annotation.named_string("name") or annotation.named_string("value")
    if named is not None:
        return named
    raw = (annotation.arguments or "").strip()
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", raw):
        return None
    return _constant_string(controller, raw)


def _constant_string(controller: JavaType, name: str) -> str | None:
    """A `static final String NAME = "value"` on the class the handler lives in."""
    for field in controller.fields():
        if field.name != name:
            continue
        match = _STRING_LITERAL.search(field.body or "")
        return None if match is None else unquote(match.group(0))
    return None


def _idempotency(
        controller: JavaType,
        method: Member,
        route: str,
) -> tuple[str, Gap | None]:
    """Whether the route requires a replay key, and what that costs when it does not.

    An accepted-but-optional key is reported: the `I-missing` family expects a refusal,
    and a request without an optional header is not refused.
    """
    for parameter in method.parameters:
        annotation = parameter.annotation("RequestHeader")
        if annotation is None:
            continue
        name = _header_name(controller, annotation) or ""
        if not _IDEMPOTENCY_HEADER.search(name):
            continue
        if annotation.named_bool("required") is False:
            return "none", Gap(
                endpoint=route,
                axis="I-missing",
                why=(
                    f"`{name}` is accepted but not required, so a request without it"
                    " is not refused"
                ),
                fix="waive the `I-missing` case, or author the rule that refuses it",
            )
        return "header_uuid_v4", None
    return "none", None


def _body_fields(
        controller: JavaType,
        method: Member,
        index: _Index,
        route: str,
        denylist: tuple[str, ...],
) -> tuple[tuple[FieldSchema, ...], list[Gap]]:
    """The body's fields, or the gap that says why there are none.

    `@Valid` is checked first because its absence changes what the fields mean: a
    record full of `@NotBlank` that is never validated would produce a contract whose
    `N-omit-*` cases the API does not refuse, and generating them is worse than
    generating nothing.
    """
    parameter = _body_parameter(method)
    if parameter is None:
        return (), []
    gaps: list[Gap] = []
    if not parameter.has("Valid"):
        gaps.append(
            Gap(
                endpoint=route,
                axis=SHAPE_AXES,
                why=(
                    "`@RequestBody` carries no `@Valid`, so Bean Validation never runs"
                    " and the shape axes are not refused"
                ),
                fix=(
                    "waive the shape cases for this route, or author the rule that"
                    " rejects the request instead"
                ),
            )
        )
    erasure = type_head(parameter.type)
    if erasure in OPAQUE_TYPES or erasure in COLLECTION_TYPES or parameter.type.endswith("[]"):
        gaps.append(
            Gap(
                endpoint=route,
                axis=SHAPE_AXES,
                why=(
                    f"the body is `{parameter.type}`, whose properties the code does"
                    " not declare"
                ),
                fix=(
                    "declare the body's fields in the contract by hand"
                    + (
                        f" (a denylist constant is declared: {', '.join(denylist[:3])}, …)"
                        if denylist
                        else ""
                    )
                ),
            )
        )
        return (), gaps
    candidates = index.by_simple(erasure)
    records = tuple(declared for declared in candidates if declared.kind == "record")
    if len(records) != 1:
        if not candidates:
            why = f"`{erasure}` is not in the source tree this reader was pointed at"
        elif not records:
            why = f"`{erasure}` is not a `record`, so it declares no components"
        else:
            why = f"`{erasure}` is declared more than once, so the reader cannot pick"
        gaps.append(
            Gap(
                endpoint=route,
                axis=SHAPE_AXES,
                why=why,
                fix="declare the body's fields in the contract by hand",
            )
        )
        return (), gaps
    return _fields_of(records[0], index, denylist, route, gaps), gaps


def _body_parameter(method: Member) -> Parameter | None:
    for parameter in method.parameters:
        if parameter.has("RequestBody"):
            return parameter
    return None


def _fields_of(
        record: JavaType,
        index: _Index,
        denylist: tuple[str, ...],
        route: str,
        gaps: list[Gap],
        prefix: _Path = _ROOT,
) -> tuple[FieldSchema, ...]:
    """A record's components, as fields, with a gap per constraint nothing carries.

    A `@Valid` component is a record of its own and its components are read too, at
    one dotted level under it: Bean Validation cascades through `@Valid`, so the API
    really does refuse a missing `kyc_profile.name`, and a contract that stopped at
    the parent would generate no case for a rule the code states.

    The dotted path becomes both the field name and the wire path, which is how the
    corpus spells a nested target (`json: kyc_profile.country`). Naming the leaf alone
    would let two records that both declare `country` collapse onto one key and one
    case id. The two paths are carried separately because they diverge exactly where
    `@JsonProperty` does: the name follows the Java component, the wire path follows
    the JSON property.
    """
    found: list[FieldSchema] = []
    for component in record.components:
        found.append(_field(component, index, denylist, route, gaps, prefix))
        if not component.has("Valid"):
            continue
        nested = _nested_record(component, index)
        if nested is None:
            gaps.append(
                Gap(
                    endpoint=route,
                    axis=SHAPE_AXES,
                    why=(
                        f"`{component.name}` is `@Valid` and `{type_head(component.type)}`,"
                        " which is not a record this reader found, so its own components"
                        " are not known"
                    ),
                    fix="declare the nested object's fields in the contract by hand",
                )
            )
            continue
        wire = _json_name(component.annotation("JsonProperty"), component.name)
        found.extend(
            _fields_of(
                nested,
                index,
                denylist,
                route,
                gaps,
                prefix=_Path(name=f"{prefix.name}{component.name}.", wire=f"{prefix.wire}{wire}."),
            )
        )
    return tuple(found)


def _nested_record(component: Parameter, index: _Index) -> JavaType | None:
    """The record a `@Valid` component refers to, when exactly one is declared.

    A collection of `@Valid` elements (`List<@Valid Item>`) is deliberately not
    followed: the field axes describe the *object* the harness sends, and a list's
    elements are not reachable by the dotted path a case uses for set and omit.
    """
    erasure = type_head(component.type)
    if erasure in OPAQUE_TYPES or erasure in COLLECTION_TYPES:
        return None
    records = tuple(
        declared for declared in index.by_simple(erasure) if declared.kind == "record"
    )
    return records[0] if len(records) == 1 else None


def _field(
        component: Parameter,
        index: _Index,
        denylist: tuple[str, ...],
        route: str,
        gaps: list[Gap],
        prefix: _Path,
) -> FieldSchema:
    """One component, as a field: the four mechanical attributes and nothing more.

    `example` and `invalid` stay unset: they are values and not declarations, and a
    reader that invented one would generate a case whose request nobody wrote.
    """
    by_name = {annotation.name: annotation for annotation in component.annotations}
    for annotation in component.annotations:
        if annotation.name in SPEAKABLE_ANNOTATIONS:
            continue
        gaps.append(
            Gap(
                endpoint=route,
                axis="N-rule-*",
                why=(
                    f"`@{annotation.name}` on `{component.name}` is a constraint"
                    " `coverage.expand` has no family for"
                ),
                fix=(
                    "author a rule with the status the advice answers for it — a"
                    " custom validator throws whatever its own code chooses"
                ),
            )
        )
    size = by_name.get("Size")
    pattern = by_name.get("Pattern")
    head = type_head(component.type)
    regexp = _unquoted(pattern.named_string("regexp")) if pattern is not None else None
    bound = size.named_int("max") if size is not None else None
    # `@Size(max = 32)` bounds a *count* on a collection or a map and a *length* on a
    # string. Reading it as `max_length` always would generate a `B-max` case that
    # sends one 33-character value where the API counts keys — a case that fails, and
    # fails for the wrong reason.
    counted = head in COUNTED_TYPES or component.type.strip().endswith("[]")
    length = None if counted else bound
    if length is None and not counted:
        length = _pattern_length(regexp)
    return FieldSchema(
        name=f"{prefix.name}{component.name}",
        required=any(name in by_name for name in REQUIRED_ANNOTATIONS),
        json_name=f"{prefix.wire}{_json_name(by_name.get('JsonProperty'), component.name)}",
        json_alias="JsonAlias" in by_name,
        max_length=length,
        max_keys=bound if counted else None,
        pattern=regexp,
        denylist=denylist if head in OBJECT_TYPES else (),
    )


def _json_name(annotation: Annotation | None, fallback: str) -> str:
    """The wire name: `@JsonProperty("transaction_id")`, or the Java name."""
    if annotation is None:
        return fallback
    return annotation.value() or fallback


def _unquoted(value: str | None) -> str | None:
    """A regex as Java would hand it to the validator, unescaped once."""
    return None if value is None else value.replace("\\\\", "\\")


#: A pattern that bounds its own length: anchored, one repeated atom, a `{n,m}` count.
#: `^[a-z0-9_]{3,128}$` is the whole pattern and 128 is therefore the longest string it
#: can match. This is the only shape from which a length follows *as a fact* — a
#: pattern with two quantifiers says nothing about the total (`^\d{3}-\d{4}$` matches
#: eight characters and its last quantifier says four), so everything else is left
#: alone and only the pattern is written down.
_BOUNDED_PATTERN = re.compile(r"^\^(\[[^\]]+\]|\\?[^\\{]){(\d+),(\d+)}\$$")


def _pattern_length(regexp: str | None) -> int | None:
    """The `max` a self-bounding pattern implies, or `None` when it does not bound.

    It is read because the reference corpus relies on it: a `featureKey` declares
    `@Pattern("^[a-z0-9_]{3,128}$")` and no `@Size`, so a reader that looked only for
    `@Size` would leave the contract's `max_length: 128` unread — and the `B-max` case
    for that field would never be generated.
    """
    if regexp is None:
        return None
    found = _BOUNDED_PATTERN.match(regexp)
    return int(found.group(3)) if found else None

def _denylist(index: _Index) -> tuple[str, ...]:
    """The denylist a declared constant holds, when the code declares exactly one.

    It is read and not assumed: a project with no such constant gets no denylist and a
    gap, and one with two gets neither, because choosing between them would be the
    reader writing policy.
    """
    found: list[tuple[str, ...]] = []
    for declared in index.types:
        for field in declared.fields():
            if not _DENYLIST_NAME.search(field.name):
                continue
            values = tuple(
                unquote(literal)
                for literal in _STRING_LITERAL.findall(field.body or "")
            )
            if values:
                found.append(values)
    return found[0] if len(found) == 1 else ()


def _success_status(method: Member, route: str) -> tuple[int | None, Gap | None]:
    """The status the happy path answers, or the gap that says why it is unknown.

    Only `return` statements count. A `throw new ResponseStatusException(NOT_FOUND)` in
    the same method is a failure path, and reading it as the success status would
    generate an `H01` that expects the wrong number for the wrong reason — the
    reference `UserController` does exactly that in two of its three handlers.
    """
    if not method.return_type:
        return None, None
    if "ResponseEntity" not in method.return_type:
        return 200, None
    statuses = _returned_statuses(method.body)
    if len(statuses) == 1:
        return next(iter(statuses)), None
    if not statuses:
        return None, Gap(
            endpoint=route,
            axis="H01",
            why="the handler returns a `ResponseEntity` but no `return` names a status",
            fix="declare the happy status in the contract by hand",
        )
    return None, Gap(
        endpoint=route,
        axis="H01",
        why=(
            "the handler answers more than one status ("
            + ", ".join(str(number) for number in sorted(statuses))
            + "), and which is the happy path is a runtime decision"
        ),
        fix="author the route's cases with the status each branch answers",
    )


def _returned_statuses(body: str) -> set[int]:
    """Every distinct HTTP status the `return` statements of a body name."""
    found: set[int] = set()
    for statement in _return_statements(body):
        found |= error_reading.statuses_in(statement)
    return found


def _return_statements(body: str) -> tuple[str, ...]:
    """Each returned expression as text.

    A `return switch (...) { ... };` is taken whole — the first `;` inside it ends an
    *arm*, not the statement — because a single arm's status is not the method's: the
    reference metering handler answers 202, 402, 403, 429 and 500 from one switch.
    """
    found: list[str] = []
    for match in re.finditer(r"\breturn\b", body):
        tail = body[match.end() :]
        stripped = tail.lstrip()
        if stripped.startswith("switch"):
            open_index = tail.index("{", tail.index("switch"))
            try:
                close = find_matching(tail, open_index)
            except ValueError:
                found.append(tail)
                continue
            found.append(tail[:close])
            continue
        semicolon = tail.find(";")
        found.append(tail[:semicolon] if semicolon != -1 else tail)
    return tuple(found)


def _query_gaps(method: Member, route: str) -> list[Gap]:
    """A gap per handler that reads a query parameter, because no family covers one."""
    names = sorted(
        {
            name
            for name in (
                _query_name(parameter)
                for parameter in method.parameters
                if parameter.has("RequestParam")
            )
            if name
        }
    )
    if not names:
        return []
    return [
        Gap(
            endpoint=route,
            axis="query",
            why=(
                "the handler reads "
                + ", ".join(f"`{name}`" for name in names)
                + ", and `coverage.expand` has no family for a query parameter"
            ),
            fix="author the cases for this route by hand, or waive the axis",
        )
    ]


def _query_name(parameter: Parameter) -> str | None:
    annotation = parameter.annotation("RequestParam")
    if annotation is None:
        return None
    return (
        annotation.value()
        or annotation.named_string("name")
        or annotation.named_string("value")
        or parameter.name
    )


def _rule_gap(method: Member, route: str) -> Gap:
    """The one gap every route carries: the product rules are authoring, not reading.

    The `throw new X(...)` sites are named because they are the only honest hint the
    reader can give. The reference corpus authors 71 rule cases and throws three of
    them directly from a handler, which is exactly why emitting those three as the
    rule set would look like coverage and be the opposite of it.
    """
    thrown = sorted(set(re.findall(r"throw\s+new\s+([A-Za-z_$][\w$]*)", method.body)))
    detail = (
        " (`" + "`, `".join(thrown) + "` is thrown here, but the set is the product's)"
        if thrown
        else ""
    )
    return Gap(
        endpoint=route,
        axis="N-rule-*",
        why=(
            "business rules are not derivable from the handler: they live in"
            " validators and services" + detail
        ),
        fix=(
            "author the rule set and its statuses from the product's own"
            " documentation; the advice table says what a rule exception answers"
        ),
    )


def _async_mode(success: int | None, method: Member) -> bool | None:
    """A 202 that sets a `Location` header is settled later; anything else is not.

    `None` when the status is unknown, which is the honest answer: a route whose status
    the code chooses at runtime may or may not be asynchronous.
    """
    if success is None:
        return None
    if success != ACCEPTED:
        return False
    return "LOCATION" in method.body.upper()


