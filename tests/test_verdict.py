from pathlib import Path

from heimdall_qa.packs import PackResult
from heimdall_qa.runner import StepResult
from heimdall_qa.runner import _auto_verdict
from heimdall_qa.schema.models import CaseFile
from heimdall_qa.schema.models import ExpectSpec
from heimdall_qa.schema.models import SaturateSpec


def _case(**overrides: object) -> CaseFile:
    payload: dict[str, object] = {
        "id": "example-H01",
        "contract": "contracts/qa-echo-post.yaml",
        "kind": "H01",
        "expect": ExpectSpec(status=201),
    }
    payload.update(overrides)
    return CaseFile.model_validate(payload)


def _result(
    tmp_path: Path,
    *,
    status_code: int,
    packs: list[PackResult],
) -> StepResult:
    step_dir = tmp_path / "step"
    step_dir.mkdir()
    return StepResult(
        step_dir=step_dir,
        status_code=status_code,
        packs=packs,
        trace_id="nokrqa-test",
    )


def test_pass_has_no_cause(tmp_path: Path):
    verdict = _auto_verdict(
        _case(),
        _result(tmp_path, status_code=201, packs=[PackResult("http.baseline", "pass")]),
    )
    assert verdict["status"] == "pass"
    assert "cause" not in verdict


def test_n_over_classified_as_rule_is_instrument(tmp_path: Path):
    verdict = _auto_verdict(
        _case(id="register-N-over-password", kind="N-over-password", expect=ExpectSpec(status=400)),
        _result(
            tmp_path,
            status_code=400,
            packs=[
                PackResult("http.baseline", "pass"),
                PackResult(
                    "business.rule",
                    "fail",
                    "business error WEAK_PASSWORD on N-over-password",
                ),
            ],
        ),
    )
    assert verdict["status"] == "fail"
    assert verdict["cause"] == "instrument"


def test_quota_n_rule_still_2xx_is_instrument(tmp_path: Path):
    verdict = _auto_verdict(
        _case(
            id="api-keys-post-N-rule-SANDBOX_LIMIT",
            kind="N-rule-SANDBOX_LIMIT",
            expect=ExpectSpec(status=409),
            saturate=SaturateSpec(until_status=409, max=6, unique_json="name"),
        ),
        _result(
            tmp_path,
            status_code=201,
            packs=[PackResult("http.baseline", "fail", "HTTP 201, expected 409")],
        ),
    )
    assert verdict["status"] == "fail"
    assert verdict["cause"] == "instrument"


def test_missing_trace_on_401_is_product(tmp_path: Path):
    verdict = _auto_verdict(
        _case(id="api-keys-post-N-auth", kind="N-auth", expect=ExpectSpec(status=401)),
        _result(
            tmp_path,
            status_code=401,
            packs=[
                PackResult("http.baseline", "fail", "missing X-Trace-Id response header"),
                PackResult("auth.surface", "pass"),
            ],
        ),
    )
    assert verdict["status"] == "fail"
    assert verdict["cause"] == "product"


def test_portuguese_error_is_product(tmp_path: Path):
    verdict = _auto_verdict(
        _case(
            id="api-keys-post-N-rule-NAME_TOO_SHORT",
            kind="N-rule-NAME_TOO_SHORT",
            expect=ExpectSpec(status=400),
        ),
        _result(
            tmp_path,
            status_code=400,
            packs=[
                PackResult("http.error", "fail", "error message looks Portuguese"),
                PackResult("business.rule", "fail", "unclassified 4xx, expected NAME_TOO_SHORT"),
            ],
        ),
    )
    assert verdict["status"] == "fail"
    assert verdict["cause"] == "product"


def test_residual_security_leak_is_pack(tmp_path: Path):
    verdict = _auto_verdict(
        _case(expect=ExpectSpec(status=201)),
        _result(
            tmp_path,
            status_code=201,
            packs=[PackResult("security.leak", "fail", "response leaked infrastructure or secret")],
        ),
    )
    assert verdict["status"] == "fail"
    assert verdict["cause"] == "pack"
