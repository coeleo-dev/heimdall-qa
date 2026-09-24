from pathlib import Path

from heimdall_qa.schema.load import LoadedCase
from heimdall_qa.schema.load import load_campaign
from heimdall_qa.schema.models import CaseFile
from heimdall_qa.schema.models import Contract
from heimdall_qa.schema.models import ExpectSpec
from heimdall_qa.schema.models import FieldSpec
from heimdall_qa.schema.models import RoundFile
from heimdall_qa.session_validate import _unique_json_findings
from heimdall_qa.session_validate import validate_campaign_chain
from heimdall_qa.session_validate import validate_round_session
from heimdall_qa.testing import project_at

#: The descriptor in force. `catalog_unique` is a route property, so a test of
#: the rule needs the project that declares it — the same file the CLI resolves.
_PROJECT = project_at(
    Path(__file__).resolve().parent / "fixtures" / "qa" / "project.yaml"
)


def test_e_isolate_sandbox_200_fails_round_session():
    round_file = RoundFile.model_validate(
        {
            "id": "keys-get",
            "suite": "suites/keys.yaml",
            "mode": "review",
            "environment": "sandbox",
            "include": [],
        }
    )
    case = CaseFile.model_validate(
        {
            "id": "keys-E-isolate",
            "contract": "contracts/keys.yaml",
            "kind": "E-isolate",
            "expect": ExpectSpec(status=200),
        }
    )
    loaded = [LoadedCase("cases/keys.yaml#keys-E-isolate", case)]
    findings = validate_round_session(
        round_file, loaded, Path("/tmp/rounds/keys-get.yaml"), Path("/tmp")
    )
    isolate = [item for item in findings if item.code == "E_ISOLATE_SANDBOX_STATUS"]
    assert len(isolate) == 1
    assert "403" in isolate[0]


def test_e_isolate_sandbox_refuses_a_status_that_is_not_403():
    """It used to refuse only `200`, so a `404` isolation case validated clean.

    A refusal is the evidence that the boundary was tested; `200`, `404` or `201`
    are all the case asking the wrong namespace a question.
    """
    round_file = RoundFile.model_validate(
        {
            "id": "keys-get",
            "suite": "suites/keys.yaml",
            "mode": "review",
            "environment": "sandbox",
            "include": [],
        }
    )
    case = CaseFile.model_validate(
        {
            "id": "keys-E-isolate",
            "contract": "contracts/keys.yaml",
            "kind": "E-isolate",
            "expect": ExpectSpec(status=404),
        }
    )
    loaded = [LoadedCase("cases/keys.yaml#keys-E-isolate", case)]
    findings = validate_round_session(
        round_file, loaded, Path("/tmp/rounds/keys-get.yaml"), Path("/tmp")
    )
    isolate = [item for item in findings if item.code == "E_ISOLATE_SANDBOX_STATUS"]
    assert len(isolate) == 1
    assert "not 403" in isolate[0]


def test_unique_json_required_for_named_post():
    contract = Contract(
        endpoint="POST /platform/billable-metrics",
        dto="com.example.dto.CreateBillableMetricRequest",
        auth="jwt",
        idempotency="header_uuid_v4",
        baseline="baselines/x.json",
        fields={"name": FieldSpec(required=True, json="name")},
    )
    found = _unique_json_findings({Path("contracts/metrics.yaml"): contract}, _PROJECT)
    assert found
    assert found[0].code == "UNIQUE_JSON_MISSING"


def test_unique_json_produces_last_unique_json(tmp_path: Path):
    root = tmp_path
    (root / "contracts").mkdir()
    (root / "baselines").mkdir()
    (root / "cases").mkdir()
    (root / "rounds").mkdir()
    (root / "campaigns").mkdir()
    (root / "baselines" / "post.json").write_text('{"name": "Starter"}', encoding="utf-8")
    (root / "contracts" / "post.yaml").write_text(
        "\n".join(
            [
                "endpoint: POST /platform/entitlements/plans",
                "dto: com.example.dto.Create",
                "auth: jwt",
                "idempotency: header_uuid_v4",
                "unique_json: name",
                "baseline: baselines/post.json",
                "fields:",
                "  name:",
                "    required: true",
                "    json: name",
            ]
        ),
        encoding="utf-8",
    )
    (root / "cases" / "post.yaml").write_text(
        "\n".join(
            [
                "post-H01:",
                "  contract: contracts/post.yaml",
                "  kind: H01",
                "  expect:",
                "    status: 201",
                "post-N-rule-DUPLICATE_NAME:",
                "  contract: contracts/post.yaml",
                "  kind: N-rule-DUPLICATE_NAME",
                "  expect:",
                "    status: 422",
                "  generate:",
                "    name: captured.last_unique_json",
            ]
        ),
        encoding="utf-8",
    )
    (root / "rounds" / "post.yaml").write_text(
        "\n".join(
            [
                "id: post",
                "suite: suites/post.yaml",
                "mode: review",
                "environment: sandbox",
                "include:",
                "- cases/post.yaml",
            ]
        ),
        encoding="utf-8",
    )
    campaign = root / "campaigns" / "c.yaml"
    campaign.write_text(
        "\n".join(
            [
                "id: c",
                "environment: sandbox",
                "rounds:",
                "  - round: rounds/post.yaml",
                "    endpoint: POST /platform/entitlements/plans",
                "    dto: com.example.dto.Create",
                "    matrix: A3",
                "    auth: jwt",
            ]
        ),
        encoding="utf-8",
    )
    errors = validate_campaign_chain(load_campaign(campaign), root, _PROJECT)
    assert not any("last_unique_json" in item for item in errors)


def test_unique_json_not_required_for_metering_feature_key():
    contract = Contract(
        endpoint="POST /api/metering",
        dto="com.example.dto.MeteringRequest",
        auth="api_key",
        idempotency="header_uuid_v4",
        baseline="baselines/x.json",
        fields={"feature_key": FieldSpec(required=False, json="feature_key")},
    )
    assert (
        _unique_json_findings({Path("contracts/api-metering-post.yaml"): contract}, _PROJECT)
        == []
    )


def test_campaign_placeholder_without_producer(tmp_path: Path):
    root = tmp_path
    (root / "contracts").mkdir()
    (root / "baselines").mkdir()
    (root / "cases").mkdir()
    (root / "rounds").mkdir()
    (root / "campaigns").mkdir()
    (root / "baselines" / "put.json").write_text('{"name": "x"}', encoding="utf-8")
    (root / "contracts" / "put.yaml").write_text(
        "\n".join(
            [
                "endpoint: PUT /platform/billable-metrics/{{billable_metric_id}}",
                "dto: com.example.dto.Update",
                "auth: jwt",
                "baseline: baselines/put.json",
                "fields: {}",
                "resource_id_in_path: true",
            ]
        ),
        encoding="utf-8",
    )
    (root / "cases" / "put.yaml").write_text(
        "\n".join(
            [
                "put-H01:",
                "  contract: contracts/put.yaml",
                "  kind: H01",
                "  expect:",
                "    status: 200",
            ]
        ),
        encoding="utf-8",
    )
    (root / "rounds" / "put.yaml").write_text(
        "\n".join(
            [
                "id: put",
                "suite: suites/put.yaml",
                "mode: review",
                "environment: sandbox",
                "include:",
                "- cases/put.yaml",
            ]
        ),
        encoding="utf-8",
    )
    campaign = root / "campaigns" / "c.yaml"
    campaign.write_text(
        "\n".join(
            [
                "id: c",
                "environment: sandbox",
                "rounds:",
                "  - round: rounds/put.yaml",
                "    endpoint: PUT /platform/billable-metrics/{id}",
                "    dto: com.example.dto.Update",
                "    matrix: A2",
                "    auth: jwt",
            ]
        ),
        encoding="utf-8",
    )
    errors = validate_campaign_chain(load_campaign(campaign), root, _PROJECT)
    assert any("billable_metric_id" in item for item in errors)


def test_campaign_producer_then_consumer_passes(tmp_path: Path):
    root = tmp_path
    for name in ("contracts", "baselines", "cases", "rounds", "campaigns", "suites"):
        (root / name).mkdir()
    (root / "baselines" / "post.json").write_text('{"name": "llm"}', encoding="utf-8")
    (root / "baselines" / "put.json").write_text("{}", encoding="utf-8")
    (root / "contracts" / "post.yaml").write_text(
        "\n".join(
            [
                "endpoint: POST /platform/billable-metrics",
                "dto: com.example.dto.Create",
                "auth: jwt",
                "idempotency: header_uuid_v4",
                "baseline: baselines/post.json",
                "unique_json: name",
                "captures:",
                "  billable_metric_id: id",
                "fields:",
                "  name:",
                "    required: true",
                "    json: name",
            ]
        ),
        encoding="utf-8",
    )
    (root / "contracts" / "put.yaml").write_text(
        "\n".join(
            [
                "endpoint: PUT /platform/billable-metrics/{{billable_metric_id}}",
                "dto: com.example.dto.Update",
                "auth: jwt",
                "baseline: baselines/put.json",
                "fields: {}",
            ]
        ),
        encoding="utf-8",
    )
    (root / "cases" / "post.yaml").write_text(
        "\n".join(
            [
                "post-H01:",
                "  contract: contracts/post.yaml",
                "  kind: H01",
                "  expect:",
                "    status: 201",
                "  capture_response:",
                "    billable_metric_id: id",
            ]
        ),
        encoding="utf-8",
    )
    (root / "cases" / "put.yaml").write_text(
        "\n".join(
            [
                "put-H01:",
                "  contract: contracts/put.yaml",
                "  kind: H01",
                "  expect:",
                "    status: 200",
            ]
        ),
        encoding="utf-8",
    )
    (root / "rounds" / "post.yaml").write_text(
        "\n".join(
            [
                "id: post",
                "suite: suites/post.yaml",
                "mode: review",
                "environment: sandbox",
                "include:",
                "- cases/post.yaml",
            ]
        ),
        encoding="utf-8",
    )
    (root / "rounds" / "put.yaml").write_text(
        "\n".join(
            [
                "id: put",
                "suite: suites/put.yaml",
                "mode: review",
                "environment: sandbox",
                "include:",
                "- cases/put.yaml",
            ]
        ),
        encoding="utf-8",
    )
    campaign = root / "campaigns" / "c.yaml"
    campaign.write_text(
        "\n".join(
            [
                "id: c",
                "environment: sandbox",
                "rounds:",
                "  - round: rounds/post.yaml",
                "    endpoint: POST /platform/billable-metrics",
                "    dto: com.example.dto.Create",
                "    matrix: A2",
                "    auth: jwt",
                "  - round: rounds/put.yaml",
                "    endpoint: PUT /platform/billable-metrics/{id}",
                "    dto: com.example.dto.Update",
                "    matrix: A2",
                "    auth: jwt",
            ]
        ),
        encoding="utf-8",
    )
    errors = validate_campaign_chain(load_campaign(campaign), root, _PROJECT)
    chain = [
        item
        for item in errors
        if item.code in {"PLACEHOLDER_NO_PRODUCER", "UNIQUE_JSON_MISSING"}
    ]
    assert chain == []
