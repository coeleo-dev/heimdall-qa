"""The failure side, read from the code that decides it.

A contract's negative cases need one number each: what does *this* axis answer. In a
Spring application the answer is not in a document — it is in the `@ExceptionHandler`
methods, and the reference corpus is why this module exists at all: its descriptor
declared a single `validation_status: 422` for a year, over code that answers 400 for
a malformed body and 422 only for a product rule.

Three things shape what is written down here.

**The table is of framework names, never product names.** `MethodArgumentNotValid`
and `MissingRequestHeaderException` are Spring's; a reader that knew a project's own
exception classes by name would stop being reusable, so a product exception is only
ever reported — as a note saying it carries its own status — and never mapped.

**A disagreement is not resolved, it is reported.** Two handlers on one axis with two
different statuses do not get averaged: the axis stays unset and a note names both,
because a reader that picks the likelier number is a reader that lies quietly.

**A status chosen at runtime is not chosen here.** `ResponseEntity.status(ex.status())`
and a `switch` over four outcomes mean the code decides per call, so the reader says
so. There is no guess to make: the plan is to report the axis as unresolved and let
the human author the rule, which is exactly the residue `discover` predicts.
"""

import re
from dataclasses import dataclass
from dataclasses import field

from heimdall_qa_spring.java import JavaType
from heimdall_qa_spring.java import Member
from heimdall_qa_spring.java import generic_arguments
from heimdall_qa_spring.java import simple_name
from heimdall_qa_spring.java import type_head

#: Spring's `HttpStatus` names, as the numbers they stand for. Standard library
#: data, not product data: every Spring application spells these the same way.
HTTP_STATUS: dict[str, int] = {
    "OK": 200,
    "CREATED": 201,
    "ACCEPTED": 202,
    "NO_CONTENT": 204,
    "MOVED_PERMANENTLY": 301,
    "FOUND": 302,
    "SEE_OTHER": 303,
    "NOT_MODIFIED": 304,
    "TEMPORARY_REDIRECT": 307,
    "PERMANENT_REDIRECT": 308,
    "BAD_REQUEST": 400,
    "UNAUTHORIZED": 401,
    "PAYMENT_REQUIRED": 402,
    "FORBIDDEN": 403,
    "METHOD_NOT_ALLOWED": 405,
    "NOT_ACCEPTABLE": 406,
    "CONFLICT": 409,
    "GONE": 410,
    "LENGTH_REQUIRED": 411,
    "PRECONDITION_FAILED": 412,
    "PAYLOAD_TOO_LARGE": 413,
    "URI_TOO_LONG": 414,
    "UNSUPPORTED_MEDIA_TYPE": 415,
    "REQUESTED_RANGE_NOT_SATISFIABLE": 416,
    "EXPECTATION_FAILED": 417,
    "UNPROCESSABLE_ENTITY": 422,
    "LOCKED": 423,
    "FAILED_DEPENDENCY": 424,
    "TOO_MANY_REQUESTS": 429,
    "REQUEST_HEADER_FIELDS_TOO_LARGE": 431,
    "UNAVAILABLE_FOR_LEGAL_REASONS": 451,
    "INTERNAL_SERVER_ERROR": 500,
    "NOT_IMPLEMENTED": 501,
    "BAD_GATEWAY": 502,
    "SERVICE_UNAVAILABLE": 503,
    "GATEWAY_TIMEOUT": 504,
    "HTTP_VERSION_NOT_SUPPORTED": 505,
    "INSUFFICIENT_STORAGE": 507,
}

#: The shortcuts Spring offers instead of `status(...)`, as the status they mean.
_SHORTCUTS: dict[str, int] = {
    "ok": 200,
    "created": 201,
    "accepted": 202,
    "noContent": 204,
    "badRequest": 400,
    "unprocessableEntity": 422,
}

#: Exception -> axis, for the handlers whose meaning a framework fixes. The value
#: is the harness axis from `schema/descriptor.py`: this is the vocabulary the
#: generated cases ask about.
PRIMARY_AXIS: dict[str, str] = {
    # The shape of the request is wrong: a missing or malformed required field.
    "MethodArgumentNotValidException": "validation",
    "BindException": "validation",
    "ConstraintViolationException": "validation",
    "HandlerMethodValidationException": "validation",
    # A required header never arrived. This is the `I-missing` axis and nothing
    # else: a missing *query* parameter is a different failure with a different
    # status, and `coverage.expand` has no family for it.
    "MissingRequestHeaderException": "missing_header",
    # A credential that is absent, expired or wrong.
    "AuthenticationException": "auth",
    "BadCredentialsException": "auth",
    "InsufficientAuthenticationException": "auth",
    "AuthenticationCredentialsNotFoundException": "auth",
    "AccessDeniedException": "auth",
    "SecurityException": "auth",
    # A resource named in the path or the body that does not exist.
    "EntityNotFoundException": "not_found",
    "NoSuchElementException": "not_found",
    "EmptyResultDataAccessException": "not_found",
}

#: Handlers that answer on an axis but do not *define* it. A body that cannot be
#: parsed is a shape failure, and the reference API answers 400 for it — except when
#: the unreadable part is a control character, which is a 422. The handler is
#: reported, never promoted over a real validation handler.
SECONDARY_AXIS: dict[str, str] = {
    "HttpMessageNotReadableException": "validation",
}

#: Exceptions whose name is enough, because the framework does not own it and the
#: concept does. Kept narrow on purpose: an over-eager pattern maps somebody's
#: `ConflictException` to the wrong axis, which is worse than a reported unknown.
_NAME_AXIS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^Idempotenc.*Conflict.*Exception$"), "idempotency_conflict"),
    (re.compile(r"^Idempotency.*Exception$"), "idempotency_conflict"),
    (re.compile(r"^.*NotFound.*Exception$"), "not_found"),
)

#: The handler whose status must never be read as the `not_found` axis even though
#: its name says "NotFound": it answers for a *path* that matches no handler, and the
#: reference API answers 400 for that, not 404. Getting this one wrong is the
#: difference between a generated case that passes and one that proves nothing.
NEVER_AN_AXIS: frozenset[str] = frozenset({"NoHandlerFoundException"})

#: The names of the envelope shapes the harness can declare, in the order the
#: evidence is checked: a body is an RFC 7807 problem document, a Spring `error` +
#: `traceId` map, or a `code` + `message` pair.
ENVELOPE_RFC7807 = "rfc7807"
ENVELOPE_SPRING = "spring"
ENVELOPE_CODE_MESSAGE = "code_message"
ENVELOPE_NONE = "none"

_RFC7807_KEYS = ("type", "title", "instance", "detail")
_SPRING_KEYS = ("error", "traceid")
_CODE_MESSAGE_KEYS = ("code", "message")


@dataclass(frozen=True)
class Handler:
    """One `@ExceptionHandler`, as the reader could see it."""

    exception: str
    status: int | None
    where: str
    #: The response body type as written, head only: `Map<String, String>` -> `Map`.
    body_type: str | None = None
    #: The types the method body builds: `ApiErrorBody.of(...)` and
    #: `new ApiErrorBody(...)` both put `ApiErrorBody` here. It matters because the
    #: envelope is often built and then wrapped — the reference advice returns
    #: `ResponseEntity<Map<String, String>>` everywhere and every one of those bodies
    #: is an `ApiErrorBody` turned into a map, so the return type names the wrapper
    #: and only the construction names the shape.
    constructs: tuple[str, ...] = ()
    #: True when the code picks the status at runtime instead of naming one.
    dynamic: bool = False
    #: `global` or `controller <Name>`, which is what makes precedence explicable.
    origin: str = "global"
    #: Statuses the method mentions when it mentions more than one. They are the
    #: evidence behind `dynamic`: a `switch` over four outcomes names all four.
    candidates: tuple[int, ...] = ()


@dataclass(frozen=True)
class ErrorRead:
    """What the advice and the local handlers answer, per axis, plus what stays open."""

    statuses: dict[str, int] = field(default_factory=dict)
    envelope: str | None = None
    notes: tuple[str, ...] = ()
    handlers: tuple[Handler, ...] = ()


def handlers_of(
    declared: JavaType,
    *,
    origin: str = "global",
    with_annotation: str = "ExceptionHandler",
) -> tuple[Handler, ...]:
    """Every handler declared on a type, with the status it answers.

    A handler with no `@ExceptionHandler` argument is skipped: an annotation without
    a wrapped exception is not a handler, and inventing one would put a status on an
    axis nobody declared.
    """
    found: list[Handler] = []
    for method in declared.methods():
        annotation = method.annotation(with_annotation)
        if annotation is None:
            continue
        for exception in _exceptions_of(annotation.arguments or ""):
            status, dynamic, candidates = _status_of(declared, method)
            found.append(
                Handler(
                    exception=exception,
                    status=None if dynamic else status,
                    where=f"{declared.name}.{method.name}",
                    body_type=_body_type(method),
                    constructs=_constructs(method.body),
                    dynamic=dynamic,
                    origin=origin,
                    candidates=candidates,
                )
            )
    return tuple(found)


def read_errors(
    types: tuple[JavaType, ...],
    local: tuple[Handler, ...] = (),
) -> ErrorRead:
    """The axis table, the envelope, and everything that stays open.

    `types` is the whole tree the reader scanned and not only the advice classes,
    because the two halves live in different files: the handlers are on the advice,
    and the record they answer with is wherever the project put it. Passing only the
    advice would leave the envelope unknown on every project that has one.

    Local handlers are summed onto the advice with local precedence, which is what
    Spring does: a `@ExceptionHandler` on the controller answers before the global
    one for the same exception.
    """
    global_handlers = tuple(
        handler
        for declared in types
        if declared.has("RestControllerAdvice") or declared.has("ControllerAdvice")
        for handler in handlers_of(declared, origin="global")
    )
    ordered = _with_local_precedence(global_handlers, local)
    statuses = _axis_statuses(ordered)
    return ErrorRead(
        statuses=statuses,
        envelope=read_envelope(types, ordered),
        notes=_notes(ordered, statuses),
        handlers=ordered,
    )


def read_envelope(
    types: tuple[JavaType, ...],
    handlers: tuple[Handler, ...] = (),
) -> str | None:
    """The declared body shape, or `None` when no handler answers with a record.

    The candidates come from the handlers and not from a sweep of the tree: a record
    named `...Error...` that no handler ever returns is a *request* error type, and
    reading its components would declare an envelope the API never sends. A handler's
    own body counts, because building an `ApiErrorBody` and wrapping it in
    `ResponseEntity<Map<String, String>>` is the ordinary way to answer.

    Two records that fit is not an answer: the envelope stays `None` and the reader
    says so by name, because a body that is one shape on four axes and another on the
    fifth cannot be declared as either.
    """
    wanted: set[str] = set()
    for handler in handlers:
        if handler.status is None:
            continue
        wanted.update(handler.constructs)
        if handler.body_type:
            wanted.add(simple_name(handler.body_type))
    shapes: dict[str, str] = {}
    for declared in types:
        if declared.kind != "record" or not declared.components:
            continue
        if declared.name not in wanted:
            continue
        keys = tuple(component.name.lower() for component in declared.components)
        if any(key in _RFC7807_KEYS for key in keys):
            shapes[declared.name] = ENVELOPE_RFC7807
        elif all(key in keys for key in _SPRING_KEYS):
            shapes[declared.name] = ENVELOPE_SPRING
        elif all(key in keys for key in _CODE_MESSAGE_KEYS):
            shapes[declared.name] = ENVELOPE_CODE_MESSAGE
    if len(set(shapes.values())) == 1:
        return next(iter(shapes.values()))
    return None


def _with_local_precedence(
        global_handlers: tuple[Handler, ...],
        local: tuple[Handler, ...],
) -> tuple[Handler, ...]:
    """Local first, then the advice, with a locally handled exception removed once."""
    shadowed = {handler.exception for handler in local}
    return (*local, *(handler for handler in global_handlers if handler.exception not in shadowed))


def _axis_statuses(handlers: tuple[Handler, ...]) -> dict[str, int]:
    """Axis -> status, where every handler that speaks for an axis agrees.

    A primary handler beats a secondary one and is the only thing consulted when it
    exists, because `MethodArgumentNotValidException` *is* the shape axis while a
    message-not-readable handler merely also answers 400. Two primaries that disagree
    leave the axis out: the caller reports it.
    """
    primary: dict[str, set[int]] = {}
    secondary: dict[str, set[int]] = {}
    for handler in handlers:
        axis = _axis_of(handler.exception)
        if axis is None or handler.status is None:
            continue
        bucket = primary if handler.exception in PRIMARY_AXIS else secondary
        bucket.setdefault(axis, set()).add(handler.status)
    found: dict[str, int] = {}
    for axis, statuses in primary.items():
        if len(statuses) == 1:
            found[axis] = next(iter(statuses))
    for axis, statuses in secondary.items():
        if axis in found or len(statuses) != 1:
            continue
        found[axis] = next(iter(statuses))
    return found


def _axis_of(exception: str) -> str | None:
    """The harness axis an exception's handler answers for, or `None`."""
    name = simple_name(exception)
    if name in NEVER_AN_AXIS:
        return None
    if name in PRIMARY_AXIS:
        return PRIMARY_AXIS[name]
    if name in SECONDARY_AXIS:
        return SECONDARY_AXIS[name]
    for pattern, axis in _NAME_AXIS:
        if pattern.match(name):
            return axis
    return None


def _notes(handlers: tuple[Handler, ...], statuses: dict[str, int]) -> tuple[str, ...]:
    """What the table cannot hold, said out loud.

    Three kinds: an axis two handlers disagree about, a status the code picks at
    runtime, and a product exception that carries its own status. Each one is a case
    a human still has to author, so each one is named rather than smoothed over.
    """
    notes: list[str] = []
    notes.extend(_disagreements(handlers, statuses))
    dynamic = sorted(
        {
            f"{handler.where} answers for `{handler.exception}` with a status the code"
            " chooses at runtime"
            for handler in handlers
            if handler.dynamic
        }
    )
    notes.extend(f"{line} — author the rule, or declare the axis" for line in dynamic)
    product = sorted(
        {
            handler.exception
            for handler in handlers
            if _axis_of(handler.exception) is None
            and not handler.dynamic
            # A 5xx catch-all is infrastructure, not a case family: naming it would
            # bury the four 4xx handlers a human actually has to author rules for.
            and handler.status is not None
            and 400 <= handler.status < 500
        }
    )
    if product:
        notes.append(
            "no axis covers these handlers, so the failures they answer belong to"
            " contract rules a human authors: " + ", ".join(f"`{name}`" for name in product)
        )
    return tuple(notes)


def _disagreements(handlers: tuple[Handler, ...], statuses: dict[str, int]) -> list[str]:
    """Axes with two answers in the code, and axes whose only handler is dynamic."""
    by_axis: dict[str, set[int]] = {}
    dynamic_axes: set[str] = set()
    for handler in handlers:
        axis = _axis_of(handler.exception)
        if axis is None:
            continue
        if handler.status is None:
            if handler.dynamic:
                dynamic_axes.add(axis)
            continue
        by_axis.setdefault(axis, set()).add(handler.status)
    found: list[str] = []
    for axis, numbers in sorted(by_axis.items()):
        if len(numbers) > 1:
            found.append(
                f"axis `{axis}` has two answers in the code"
                f" ({', '.join(str(number) for number in sorted(numbers))}),"
                " so no case can be generated for it until a human picks one"
            )
    for axis in sorted(dynamic_axes - set(by_axis)):
        found.append(f"axis `{axis}` is answered with a runtime status")
    return found


def _exceptions_of(arguments: str) -> tuple[str, ...]:
    """The exception simple names a handler wraps, in source order.

    `@ExceptionHandler({A.class, B.class})`, `@ExceptionHandler(A.class)` and
    `@ExceptionHandler(SomeConstant.CLASS)` all reduce to the same read: the last
    segment of every `X.class` reference.
    """
    found = re.findall(r"([A-Za-z_$][\w$.]*)\s*\.\s*class", arguments)
    return tuple(simple_name(item) for item in found)


def _status_of(
        declared: JavaType,
        method: Member,
) -> tuple[int | None, bool, tuple[int, ...]]:
    """The status a handler answers with: one number, a runtime choice, or neither.

    The body is read in the order a person would: a named status first, then a
    shortcut like `ResponseEntity.ok`, then the same-class helper the handler
    delegates to (the reference advice does this twice, and a reader that stopped at
    the delegating method would report two axes as open that the code answers), and
    finally the two forms that mean "the status is not in this method" —
    `ex.getStatusCode()` and a `switch` with more than one outcome.
    """
    numbers = statuses_in(method.body)
    if len(numbers) == 1:
        return next(iter(numbers)), False, ()
    if len(numbers) > 1:
        return None, True, tuple(sorted(numbers))
    helper = _delegated_helper(declared, method.body)
    if helper is not None:
        return _status_of(declared, helper)
    if _reads_status_from_the_exception(method.body):
        return None, True, ()
    return None, False, ()


def statuses_in(body: str) -> set[int]:
    """Every HTTP status a fragment names: enum constant, shortcut, or literal."""
    found: set[int] = set()
    for name in re.findall(r"HttpStatus\.([A-Z_]+)", body):
        if name in HTTP_STATUS:
            found.add(HTTP_STATUS[name])
    for name in re.findall(r"ResponseEntity\.([A-Za-z]+)\s*\(", body):
        if name in _SHORTCUTS:
            found.add(_SHORTCUTS[name])
    for literal in re.findall(r"status\(\s*(\d{3})\s*[,)]", body):
        found.add(int(literal))
    return found


def _reads_status_from_the_exception(body: str) -> bool:
    """`status(ex.getStatusCode())` and friends: the code carries its own status."""
    return bool(
        re.search(r"status\s*\(\s*ex\.(getStatusCode|status)\s*\(", body)
        or re.search(r"\.(getStatusCode|status)\s*\(\s*\)", body)
    )


def _delegated_helper(declared: JavaType, body: str) -> Member | None:
    """The same-class method a handler returns from, when it returns from one.

    Only a single `return name(...)` counts: a body with branches that call helpers
    is a body whose answer depends on data, and reading one of them would be a guess.
    """
    returns = re.findall(r"return\s+([A-Za-z_$][\w$]*)\s*\(", body)
    if len(returns) != 1:
        return None
    wanted = returns[0]
    for candidate in declared.methods():
        if candidate.name == wanted and candidate.body.strip():
            return candidate
    return None


def _constructs(body: str) -> tuple[str, ...]:
    """The types a method body builds, in source order: `X.of(...)`, `new X(...)`.

    Capitalised on purpose. `ex.getStatusCode()` matches the shape of a static factory
    call, and reading the exception as the body would declare the wrong envelope; a
    type name is capitalised and a local variable in this corpus never is.
    """
    found = re.findall(r"\b([A-Z][\w$]*)\s*\.\s*(?:of|internal|from|create)\s*\(", body)
    found += re.findall(r"\bnew\s+([A-Z][\w$]*)\s*\(", body)
    return tuple(dict.fromkeys(found))


def _body_type(method: Member) -> str | None:
    """The name of the type a handler answers with: `Map`, `ApiErrorBody`.

    Only the head is kept, because the head is what identifies the shape: a handler
    that answers `Map<String, String>` declares no record and one that answers
    `ResponseEntity<ApiErrorBody>` declares `ApiErrorBody`, so the entity is unwrapped
    rather than mistaken for the body.
    """
    if not method.return_type:
        return None
    text = method.return_type.strip()
    if "ResponseEntity" in text:
        inner = generic_arguments(text)
        text = inner[0].strip() if inner else text
    return type_head(text) or None
