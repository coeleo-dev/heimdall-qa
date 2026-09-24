"""The failure side, read from the code that decides it.

The rule the whole module turns on: a framework exception has a meaning the framework
fixes, and a product exception does not. So `MethodArgumentNotValidException` is the
shape axis and `CatalogContractException` is a note, and the difference is the reason
this distribution exists — the reference descriptor declared `validation_status: 422`
for a year while the code answered 400.
"""

from heimdall_qa_spring import errors
from heimdall_qa_spring.java import parse_java

ADVICE = """
    package com.example.web;

    @RestControllerAdvice
    public class GlobalExceptionHandler {

        @ExceptionHandler(MethodArgumentNotValidException.class)
        public ResponseEntity<Map<String, Object>> handleValidation(MethodArgumentNotValidException ex) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(ApiErrorBody.of("bad", traceId).toMap());
        }

        @ExceptionHandler(MissingRequestHeaderException.class)
        public ResponseEntity<Map<String, String>> handleHeader(MissingRequestHeaderException ex) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(ApiErrorBody.of("header", traceId).toMap());
        }

        @ExceptionHandler(AuthenticationException.class)
        public ResponseEntity<Map<String, String>> handleAuth(AuthenticationException ex) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body(ApiErrorBody.of("no", traceId).toMap());
        }
    }
"""

ERROR_BODY = """
    package com.example.web;

    public record ApiErrorBody(String error, String traceId, String code) {
    }
"""


def _types(*sources: str):
    found = []
    for index, source in enumerate(sources):
        found.extend(parse_java(f"T{index}.java", source).all_types())
    return tuple(found)


def test_each_framework_exception_lands_on_its_axis_with_its_status():
    read = errors.read_errors(_types(ADVICE, ERROR_BODY))

    assert read.statuses == {
        "validation": 400,
        "missing_header": 400,
        "auth": 401,
    }


def test_the_envelope_is_the_record_the_handlers_build_not_the_wrapper_they_return():
    read = errors.read_errors(_types(ADVICE, ERROR_BODY))

    assert read.envelope == errors.ENVELOPE_SPRING


def test_a_handler_that_answers_a_bare_map_declares_no_envelope():
    advice = ADVICE.replace("ApiErrorBody.of(\"bad\", traceId).toMap()", "Map.of(\"error\", \"bad\")")

    read = errors.read_errors(_types(advice))

    assert read.envelope is None


def test_a_product_exception_is_reported_and_never_mapped_to_an_axis():
    advice = """
        @RestControllerAdvice
        public class A {
            @ExceptionHandler(CatalogContractException.class)
            public ResponseEntity<Map<String, String>> handle(CatalogContractException ex) {
                return ResponseEntity.status(HttpStatus.UNPROCESSABLE_ENTITY).body(Map.of());
            }
        }
    """

    read = errors.read_errors(_types(advice))

    assert read.statuses == {}
    assert any("CatalogContractException" in note for note in read.notes)


def test_no_handler_found_answers_400_and_is_not_the_not_found_axis():
    advice = """
        @RestControllerAdvice
        public class A {
            @ExceptionHandler(NoHandlerFoundException.class)
            public ResponseEntity<Map<String, String>> handle(NoHandlerFoundException ex) {
                return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(Map.of());
            }
        }
    """

    read = errors.read_errors(_types(advice))

    assert "not_found" not in read.statuses
    assert read.handlers[0].status == 400


def test_two_handlers_on_one_axis_leave_it_unset_and_say_so():
    advice = """
        @RestControllerAdvice
        public class A {
            @ExceptionHandler(MethodArgumentNotValidException.class)
            public ResponseEntity<Map<String, String>> one(MethodArgumentNotValidException ex) {
                return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(Map.of());
            }
            @ExceptionHandler(BindException.class)
            public ResponseEntity<Map<String, String>> two(BindException ex) {
                return ResponseEntity.status(HttpStatus.UNPROCESSABLE_ENTITY).body(Map.of());
            }
        }
    """

    read = errors.read_errors(_types(advice))

    assert "validation" not in read.statuses
    assert any("two answers" in note for note in read.notes)


def test_a_status_the_code_chooses_is_never_read_as_the_answer():
    advice = """
        @RestControllerAdvice
        public class A {
            @ExceptionHandler(ResponseStatusException.class)
            public ResponseEntity<Map<String, String>> handle(ResponseStatusException ex) {
                return ResponseEntity.status(ex.getStatusCode()).body(Map.of());
            }
        }
    """

    read = errors.read_errors(_types(advice))

    assert read.statuses == {}
    assert read.handlers[0].dynamic
    assert any("chooses at runtime" in note for note in read.notes)


def test_a_status_can_be_named_through_the_same_class_helper_it_delegates_to():
    advice = """
        @RestControllerAdvice
        public class A {
            @ExceptionHandler(IllegalArgumentException.class)
            public ResponseEntity<Map<String, String>> handle(IllegalArgumentException ex) {
                return unprocessable(ex);
            }
            private ResponseEntity<Map<String, String>> unprocessable(Exception ex) {
                return ResponseEntity.status(HttpStatus.UNPROCESSABLE_ENTITY).body(Map.of());
            }
        }
    """

    read = errors.read_errors(_types(advice))

    assert read.handlers[0].status == 422


def test_a_handler_with_a_switch_over_several_statuses_names_them_all():
    advice = """
        @RestControllerAdvice
        public class A {
            @ExceptionHandler(Exception.class)
            public ResponseEntity<Map<String, String>> handle(Exception ex) {
                return switch (kind(ex)) {
                    case "a" -> ResponseEntity.status(HttpStatus.PAYMENT_REQUIRED).body(Map.of());
                    case "b" -> ResponseEntity.status(HttpStatus.TOO_MANY_REQUESTS).body(Map.of());
                    default -> ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(Map.of());
                };
            }
        }
    """

    read = errors.read_errors(_types(advice))

    assert read.handlers[0].dynamic
    assert read.handlers[0].candidates == (402, 429, 500)


def test_a_local_handler_wins_over_the_advice_for_the_same_exception():
    advice = """
        @RestControllerAdvice
        public class A {
            @ExceptionHandler(IllegalArgumentException.class)
            public ResponseEntity<Map<String, String>> handle(IllegalArgumentException ex) {
                return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(Map.of());
            }
        }
    """
    controller = """
        @RestController
        public class C {
            @ExceptionHandler(IllegalArgumentException.class)
            public ResponseEntity<Map<String, String>> local(IllegalArgumentException ex) {
                return ResponseEntity.status(HttpStatus.UNPROCESSABLE_ENTITY).body(Map.of());
            }
        }
    """
    advice_types = _types(advice)
    local = tuple(
        handler
        for declared in _types(controller)
        for handler in errors.handlers_of(declared, origin="controller C")
    )

    read = errors.read_errors(advice_types, local=local)

    assert [handler.status for handler in read.handlers if handler.exception == "IllegalArgumentException"] == [422]


def test_a_named_status_shortcut_counts_as_the_status():
    body = "return ResponseEntity.unprocessableEntity().body(Map.of());"

    assert errors.statuses_in(body) == {422}


def test_a_literal_status_is_read_too():
    assert errors.statuses_in("return ResponseEntity.status(409).body(Map.of());") == {409}
