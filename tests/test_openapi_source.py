"""OpenAPI is read into the IR, and what the format cannot say becomes a gap.

The document under `fixtures/openapi/toy.json` is deliberately ordinary: a body
behind `$ref`, an `allOf`, an `enum`, a `maxLength`, a required idempotency header
and one operation that declares no 2xx. Those are the six shapes that decide whether
a reader is useful, and each one has a test that names the fact it carries.

The reader's contract is as much about refusal as about reading. A missing document,
a document that is not OpenAPI and a document with no paths are three different
failures with three different fixes, and none of them may resolve to an empty schema
— "I could not read it" and "there is nothing to read" are the confusion this whole
seam exists to prevent.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from heimdall_qa.contract_source import load_contract_source
from heimdall_qa.errors import HarnessError
from heimdall_qa.sources.openapi import reader

_FIXTURE = Path(__file__).parent / "fixtures" / "openapi" / "toy.json"


@pytest.fixture
def schema():
    return reader().read(str(_FIXTURE))


@pytest.fixture
def document() -> dict[str, Any]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _write(tmp_path: Path, payload: Any, name: str = "doc.json") -> str:
    path = tmp_path / name
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _paths() -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "paths": {"/a": {"get": {"responses": {"200": {"description": "ok"}}}}},
    }


# ── what the reader derives ───────────────────────────────────────────────────


def test_it_reads_every_operation_in_document_order(schema):
    """Document order, not sorted: the report should look like the document."""
    assert schema.names() == (
        "GET /toy/health",
        "POST /toy/items",
        "GET /toy/items/{id}",
        "DELETE /toy/items/{id}",
        "POST /toy/convert",
    )


def test_an_operation_without_a_body_has_none(schema):
    health = schema.find("GET /toy/health")
    assert health is not None
    assert health.has_body is False
    assert health.fields == ()


def test_a_body_behind_a_ref_is_resolved(schema):
    """Without `$ref` support the most common document shape yields no fields."""
    item = schema.find("POST /toy/items")
    assert item is not None
    assert [field.name for field in item.fields] == [
        "name",
        "amount",
        "currency",
        "label",
        "tax_id",
        "metadata",
    ]


def test_required_is_read_from_the_schema_not_the_property(schema):
    item = schema.find("POST /toy/items")
    assert item is not None
    assert [field.name for field in item.fields if field.required] == ["name", "amount"]


def test_max_length_becomes_the_bound_a_b_max_case_needs(schema):
    name = schema.find("POST /toy/items").field("name")
    assert name is not None
    assert name.max_length == 40


def test_max_properties_becomes_the_bound_a_max_keys_case_needs(schema):
    metadata = schema.find("POST /toy/items").field("metadata")
    assert metadata is not None
    assert metadata.max_keys == 5


def test_pattern_is_carried(schema):
    label = schema.find("POST /toy/items").field("label")
    assert label is not None
    assert label.pattern == "^[A-Z]{3}$"


def test_an_example_is_used_before_an_enum(schema):
    name = schema.find("POST /toy/items").field("name")
    assert name is not None
    assert name.example == "widget"


def test_an_enum_first_value_is_a_seed_of_last_resort(schema):
    currency = schema.find("POST /toy/items").field("currency")
    assert currency is not None
    assert currency.example == "BRL"


def test_a_field_with_no_example_gets_none(schema):
    """No example is a `TODO` in the generated case, never a fabricated seed."""
    tax_id = schema.find("POST /toy/items").field("tax_id")
    assert tax_id is not None
    assert tax_id.example is None


def test_all_of_is_merged(schema):
    convert = schema.find("POST /toy/convert")
    assert convert is not None
    assert [field.name for field in convert.fields] == [
        "scheduled_for",
        "name",
        "amount",
        "currency",
        "label",
        "tax_id",
        "metadata",
    ]
    assert [field.name for field in convert.fields if field.required] == [
        "scheduled_for",
        "name",
        "amount",
    ]


def test_a_required_header_is_carried(schema):
    item = schema.find("POST /toy/items")
    assert item is not None
    assert item.required_headers == ("X-Idempotency-Key",)


def test_an_optional_header_is_not_required(schema):
    item = schema.find("POST /toy/items")
    assert "X-Tenant" not in item.required_headers


def test_the_idempotency_header_becomes_the_mode_the_runner_implements(schema):
    item = schema.find("POST /toy/items")
    assert item is not None
    assert item.idempotency == "header_uuid_v4"


def test_no_idempotency_header_means_none(schema):
    convert = schema.find("POST /toy/convert")
    assert convert is not None
    assert convert.idempotency == "none"


def test_the_lowest_2xx_wins_and_not_the_first_written(document, tmp_path):
    """`responses` is a mapping: its order means nothing, 200 before 202 does."""
    document["paths"]["/toy/health"]["get"]["responses"] = {
        "202": {"description": "later"},
        "200": {"description": "sooner"},
    }
    health = reader().read(_write(tmp_path, document)).find("GET /toy/health")
    assert health is not None
    assert health.success_status == 200


def test_an_operation_without_a_2xx_says_so(schema):
    """`None` is the reader declining to choose, and `discover` reports it."""
    delete = schema.find("DELETE /toy/items/{id}")
    assert delete is not None
    assert delete.success_status is None


def test_a_path_parameter_is_carried(schema):
    item = schema.find("GET /toy/items/{id}")
    assert item is not None
    assert item.path_params == ("id",)
    assert item.resource_id_in_path is True


def test_a_route_without_a_parameter_has_no_resource_id(schema):
    items = schema.find("POST /toy/items")
    assert items is not None
    assert items.resource_id_in_path is False


def test_route_lookup_ignores_case_and_a_trailing_slash(schema):
    assert schema.find("post   /toy/items/") is not None


def test_a_route_that_is_not_in_the_document_is_none_not_an_error(schema):
    assert schema.find("POST /toy/nothing") is None


# ── what the format cannot say becomes a gap ──────────────────────────────────


def _axes(schema, endpoint: str) -> set[str]:
    return {gap.axis for gap in schema.gaps if gap.endpoint == endpoint}


def test_every_gap_names_the_axis_it_leaves_uncovered(schema):
    """A gap with no axis is not actionable: one case or twelve, nobody can tell."""
    assert all(gap.axis for gap in schema.gaps)


def test_every_gap_says_why_and_how_to_fix_it(schema):
    assert all(gap.why and gap.fix for gap in schema.gaps)


def test_an_endpoint_without_a_body_reports_only_the_happy_status(schema):
    """The body axes are statements about a body; there is none here."""
    assert _axes(schema, "GET /toy/health") == set()


def test_a_body_reports_the_axes_the_format_cannot_spell(schema):
    assert _axes(schema, "POST /toy/items") == {
        "O-alias-*",
        "window / B-max-*-future / B-min-*-past",
        "N-denylist-*",
        "N-rule-*",
    }


def test_a_missing_happy_status_is_reported_for_the_endpoint(schema):
    assert _axes(schema, "DELETE /toy/items/{id}") == {"H01"}


def test_a_body_with_no_described_properties_is_reported(document, tmp_path):
    """A free-form body is the ordinary FastAPI `dict[str, str]`: no properties.

    Every generated case would then send `{}`, and pass or fail for a reason the
    contract never stated, so the endpoint says so instead of looking finished.
    """
    document["paths"]["/toy/health"]["get"]["requestBody"] = {
        "content": {
            "application/json": {
                "schema": {"type": "object", "additionalProperties": {"type": "string"}}
            }
        }
    }
    schema = reader().read(_write(tmp_path, document))
    health = schema.find("GET /toy/health")
    assert health is not None
    assert health.has_body is True
    assert health.fields == ()
    assert (
        "O-* / B-* / N-omit-* / N-over-* / N-pattern-*" in _axes(schema, "GET /toy/health")
    )


def test_a_described_body_is_not_reported_that_way(schema):
    """The gap is about a body nobody described, not about having a body."""
    assert _axes(schema, "POST /toy/items") != {
        "O-* / B-* / N-omit-* / N-over-* / N-pattern-*"
    }


def test_a_gap_never_names_an_alias_or_a_denylist(schema):
    """Inventing one would generate a case that is green and verifies nothing."""
    item = schema.find("POST /toy/items")
    assert item is not None
    assert item.field("name").denylist == ()
    assert item.field("name").json_alias is False


# ── the three failures, each with its own fix ─────────────────────────────────


def test_an_empty_location_is_refused():
    with pytest.raises(HarnessError) as raised:
        reader().read("  ")
    assert raised.value.code == "CONTRACT_SOURCE_FAILED"
    assert "location" in raised.value.message


def test_a_missing_document_is_refused(tmp_path: Path):
    """ "Not found" must not resolve to "empty", which would read as "no endpoints"."""
    with pytest.raises(HarnessError) as raised:
        reader().read(str(tmp_path / "absent.json"))
    assert raised.value.code == "CONTRACT_SOURCE_FAILED"
    assert "could not be read" in raised.value.message


def test_a_document_that_is_not_openapi_is_refused(tmp_path: Path):
    with pytest.raises(HarnessError) as raised:
        reader().read(_write(tmp_path, {"hello": "world"}))
    assert raised.value.code == "CONTRACT_SOURCE_FAILED"
    assert "openapi" in raised.value.message


def test_a_document_with_no_paths_is_refused(tmp_path: Path):
    with pytest.raises(HarnessError) as raised:
        reader().read(_write(tmp_path, {"openapi": "3.1.0"}))
    assert raised.value.code == "CONTRACT_SOURCE_FAILED"
    assert "no paths" in raised.value.message


def test_a_document_that_is_not_a_mapping_is_refused(tmp_path: Path):
    with pytest.raises(HarnessError) as raised:
        reader().read(_write(tmp_path, "- just\n- a list\n", name="doc.yaml"))
    assert raised.value.code == "CONTRACT_SOURCE_FAILED"
    assert "mapping" in raised.value.message


def test_an_unparsable_document_is_refused(tmp_path: Path):
    with pytest.raises(HarnessError) as raised:
        reader().read(_write(tmp_path, "openapi: [3.1\n", name="doc.yaml"))
    assert raised.value.code == "CONTRACT_SOURCE_FAILED"
    assert "YAML" in raised.value.message


def test_the_failure_names_the_location(tmp_path: Path):
    with pytest.raises(HarnessError) as raised:
        reader().read(_write(tmp_path, {"hello": "world"}))
    assert "doc.json" in raised.value.message


def test_the_failure_says_what_to_change():
    with pytest.raises(HarnessError) as raised:
        reader().read("  ")
    assert "contract.location" in raised.value.hint


# ── the yaml spelling, and the registry that finds the reader ────────────────


def test_a_yaml_document_reads_the_same_as_a_json_one(tmp_path: Path):
    """OpenAPI is JSON-shaped but usually written as YAML, so both must work."""
    yaml_document = """
openapi: 3.1.0
paths:
  /toy/health:
    get:
      responses:
        '200':
          description: ready
"""
    schema = reader().read(_write(tmp_path, yaml_document, name="doc.yaml"))
    assert schema.names() == ("GET /toy/health",)


def test_a_remote_or_circular_ref_yields_no_fields_rather_than_hanging(tmp_path: Path):
    document = _paths()
    document["paths"]["/a"]["get"]["requestBody"] = {
        "content": {"application/json": {"schema": {"$ref": "https://elsewhere/schema.json"}}}
    }
    schema = reader().read(_write(tmp_path, document))
    endpoint = schema.find("GET /a")
    assert endpoint is not None
    assert endpoint.has_body is True
    assert endpoint.fields == ()


def test_the_reader_is_reachable_through_the_registry():
    """The core resolves its own readers without an entry point, installed or not."""
    assert load_contract_source("openapi").name == "openapi"


def test_the_reader_declares_the_name_it_answers_to():
    assert reader().name == "openapi"
