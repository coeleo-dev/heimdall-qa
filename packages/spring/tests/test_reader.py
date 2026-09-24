"""The reader, end to end, on a source tree the test writes itself.

Nothing here is a product: the tree is a package with a controller, a record and an
advice class, which is the smallest thing a Spring API can be. Everything the reader
claims — the route, the wire names, the four mechanical attributes, the status table,
and every gap for what the code does not say — is asserted against that tree, so a
regression is a failing test and not a contract somebody notices is wrong later.
"""

from pathlib import Path

import pytest

from heimdall_qa.contract_source import CONTRACT_SOURCE_FAILED
from heimdall_qa.contract_source import SURFACE
from heimdall_qa.errors import HarnessError
from heimdall_qa_spring.reader import SPRING
from heimdall_qa_spring.reader import SpringSource

CONTROLLER = """
    package com.example.metering.controller;

    @RestController
    @RequestMapping("/api/ingest")
    public class IngestController {

        @PostMapping
        public ResponseEntity<Map<String, Object>> ingest(
                @Valid @RequestBody IngestRequest request,
                @RequestHeader(value = "X-Example-Environment", required = false) String environment) {
            return ResponseEntity.status(HttpStatus.ACCEPTED).body(Map.of());
        }

        @GetMapping("/{transactionId}")
        public ResponseEntity<Map<String, Object>> read(@PathVariable String transactionId) {
            return ResponseEntity.ok(Map.of());
        }
    }
"""

REQUEST = """
    package com.example.metering.dto;

    public record IngestRequest(
            @NotBlank
            @Size(max = 128)
            @Pattern(regexp = "^[A-Za-z0-9._:-]+$", message = "invalid characters")
            @JsonProperty("transaction_id")
            String transactionId,

            @NotNull
            @Size(max = 32)
            Map<String, Object> properties,

            @Valid @NotNull @JsonProperty("kyc_profile") KycProfile kycProfile,
            String note) {
    }
"""

KYC = """
    package com.example.metering.dto;

    public record KycProfile(
            @NotBlank String name,
            @JsonProperty("birth_date") @Pattern(regexp = "\\\\d{4}-\\\\d{2}-\\\\d{2}") String birthDate,
            String country) {
    }
"""

ADVICE = """
    package com.example.metering.web;

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

        @ExceptionHandler(IdempotencyConflictException.class)
        public ResponseEntity<Map<String, String>> handleReplay(IdempotencyConflictException ex) {
            return ResponseEntity.status(HttpStatus.CONFLICT).body(ApiErrorBody.of("replay", traceId).toMap());
        }
    }
"""

ERROR_BODY = """
    package com.example.metering.web;

    public record ApiErrorBody(String error, String traceId, String code) {
    }
"""


def _tree(root: Path, **files: str) -> Path:
    """A source tree from `name -> text`, so a test reads what it just wrote."""
    for name, text in files.items():
        path = root / f"{name}.java"
        path.write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def schema(tmp_path: Path):
    root = _tree(
        tmp_path,
        IngestController=CONTROLLER,
        IngestRequest=REQUEST,
        KycProfile=KYC,
        GlobalExceptionHandler=ADVICE,
        ApiErrorBody=ERROR_BODY,
    )
    return SpringSource().read(str(root))


def test_the_reader_says_which_reader_it_is(schema):
    assert schema.source == SPRING


def test_a_class_prefix_and_a_method_path_are_joined(schema):
    assert schema.names() == ("POST /api/ingest", "GET /api/ingest/{transactionId}")


def test_the_status_the_handler_answers_is_read_from_the_return(schema):
    post = schema.find("POST /api/ingest")
    get = schema.find("GET /api/ingest/{transactionId}")

    assert post.success_status == 202
    assert post.async_mode is False  # a 202 is async only when it tells you where to look
    assert get.success_status == 200
    assert get.async_mode is False


def test_the_four_mechanical_attributes_come_from_the_record(schema):
    fields = {field.name: field for field in schema.find("POST /api/ingest").fields}

    assert (fields["transactionId"].required, fields["transactionId"].max_length) == (True, 128)
    assert fields["transactionId"].pattern == "^[A-Za-z0-9._:-]+$"
    assert fields["transactionId"].json_name == "transaction_id"
    assert fields["note"].required is False


def test_a_size_on_a_map_counts_keys_and_a_size_on_a_string_counts_characters(schema):
    fields = {field.name: field for field in schema.find("POST /api/ingest").fields}

    assert fields["properties"].max_keys == 32
    assert fields["properties"].max_length is None
    assert fields["transactionId"].max_keys is None


def test_a_valid_nested_record_is_read_one_level_down_in_both_spellings(schema):
    fields = {field.name: field for field in schema.find("POST /api/ingest").fields}

    assert fields["kycProfile"].json_name == "kyc_profile"
    assert fields["kycProfile.name"].required is True
    assert fields["kycProfile.birthDate"].json_name == "kyc_profile.birth_date"
    assert fields["kycProfile.birthDate"].pattern == r"\d{4}-\d{2}-\d{2}"
    assert fields["kycProfile.country"].required is False


def test_an_optional_header_is_not_a_requirement(schema):
    post = schema.find("POST /api/ingest")

    assert post.required_headers == ()
    assert not any(gap.axis == "I-missing" for gap in schema.gaps if gap.endpoint == post.endpoint())


def test_a_path_parameter_is_named_and_marks_the_resource_in_the_path(schema):
    get = schema.find("GET /api/ingest/{transactionId}")

    assert get.path_params == ("transactionId",)
    assert get.resource_id_in_path is True
    assert get.has_body is False


def test_the_error_table_carries_the_status_and_the_envelope(schema):
    assert schema.errors.statuses == {
        "validation": 400,
        "missing_header": 400,
        "idempotency_conflict": 409,
    }
    assert schema.errors.envelope == "spring"


def test_every_route_carries_the_rule_gap_because_rules_are_authoring(schema):
    for endpoint in schema.endpoints:
        axes = {gap.axis for gap in schema.gaps if gap.endpoint == endpoint.endpoint()}
        assert "N-rule-*" in axes


def test_the_surface_gaps_are_the_facts_no_controller_can_answer(schema):
    axes = {gap.axis for gap in schema.gaps if gap.endpoint == SURFACE}

    assert "N-auth" in axes


def test_a_body_without_valid_says_the_shape_axes_are_not_refused(tmp_path: Path):
    controller = CONTROLLER.replace("@Valid @RequestBody", "@RequestBody")
    root = _tree(tmp_path, IngestController=controller, IngestRequest=REQUEST)

    schema = SpringSource().read(str(root))

    axes = {gap.axis for gap in schema.gaps if gap.endpoint == "POST /api/ingest"}

    assert "N-omit / B-max / N-pattern / O-*" in axes


def test_a_profile_gated_controller_is_read_and_marked_conditional(tmp_path: Path):
    controller = CONTROLLER.replace(
        '@RequestMapping("/api/ingest")',
        '@Profile({"admin", "test"})\n@RequestMapping("/api/ingest")',
    )
    root = _tree(tmp_path, IngestController=controller, IngestRequest=REQUEST)

    schema = SpringSource().read(str(root))

    assert schema.find("POST /api/ingest").conditional == "admin, test"
    assert any(gap.axis == "profile" for gap in schema.gaps)


def test_a_body_the_index_cannot_resolve_leaves_a_gap_and_no_fields(tmp_path: Path):
    controller = CONTROLLER.replace("IngestRequest request", "MissingRequest request")
    root = _tree(tmp_path, IngestController=controller)

    schema = SpringSource().read(str(root))

    post = schema.find("POST /api/ingest")
    assert post.fields == ()
    assert any(
        gap.axis == "N-omit / B-max / N-pattern / O-*" and "not in the source tree" in gap.why
        for gap in schema.gaps
    )


def test_an_opaque_body_is_a_gap_and_not_a_set_of_invented_fields(tmp_path: Path):
    controller = """
        @RestController
        public class C {
            @PostMapping("/simulate")
            public ResponseEntity<Map<String, Object>> simulate(@RequestBody Map<String, Object> body) {
                return ResponseEntity.ok(Map.of());
            }
        }
    """
    root = _tree(tmp_path, C=controller)

    schema = SpringSource().read(str(root))

    assert schema.find("POST /simulate").fields == ()
    assert any("whose properties the code does not declare" in gap.why for gap in schema.gaps)


def test_a_status_the_code_chooses_leaves_the_happy_status_open(tmp_path: Path):
    controller = """
        @RestController
        public class C {
            @PostMapping("/x")
            public ResponseEntity<Map<String, Object>> x() {
                return switch (kind()) {
                    case "a" -> ResponseEntity.ok(Map.of());
                    default -> ResponseEntity.status(HttpStatus.PAYMENT_REQUIRED).body(Map.of());
                };
            }
        }
    """
    root = _tree(tmp_path, C=controller)

    schema = SpringSource().read(str(root))

    assert schema.find("POST /x").success_status is None
    assert any(gap.axis == "H01" for gap in schema.gaps)


def test_a_denylist_declared_once_in_the_tree_is_read_and_marked_for_confirmation(tmp_path: Path):
    helper = """
        public final class IngestDenylist {
            private static final Set<String> DENIED_KEYS = Set.of("cpf", "cnpj");
        }
    """
    root = _tree(tmp_path, IngestDenylist=helper, IngestRequest=REQUEST, IngestController=CONTROLLER)

    schema = SpringSource().read(str(root))

    assert schema.find("POST /api/ingest").field("properties").denylist == ("cpf", "cnpj")
    assert any(gap.axis == "N-denylist-*" for gap in schema.gaps)


def test_a_missing_tree_is_a_named_failure_and_not_an_empty_surface(tmp_path: Path):
    with pytest.raises(HarnessError) as raised:
        SpringSource().read(str(tmp_path / "absent"))

    assert raised.value.code == CONTRACT_SOURCE_FAILED
    assert "no source tree" in raised.value.message


def test_no_location_at_all_says_what_to_declare():
    with pytest.raises(HarnessError) as raised:
        SpringSource().read(None)

    assert "contract" in raised.value.hint
