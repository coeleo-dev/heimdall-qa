from heimdall_qa.errors import HarnessError
from heimdall_qa.errors import format_cli
from heimdall_qa.errors import to_dict


def test_format_cli_includes_code_message_and_hint():
    err = HarnessError(
        code="HTTP_UNREACHABLE",
        message="cannot connect to http://127.0.0.1:8080",
        hint="start NokrAPI profile web, or point nokr_web in config.yaml",
    )
    text = format_cli(err)
    assert "error[HTTP_UNREACHABLE]" in text
    assert "cannot connect to http://127.0.0.1:8080" in text
    assert "hint:" in text
    assert "start NokrAPI" in text


def test_to_dict_exposes_fields_for_ui():
    err = HarnessError(
        code="LAST_RUN_MISSING",
        message="no runs/latest symlink",
        hint="run a round first: heimdall-qa run ROUND --mode headless",
        details=("runs/latest",),
        exit_code=1,
    )
    payload = to_dict(err)
    assert payload["code"] == "LAST_RUN_MISSING"
    assert payload["message"] == "no runs/latest symlink"
    assert payload["hint"].startswith("run a round")
    assert payload["details"] == ["runs/latest"]
    assert payload["exit_code"] == 1
