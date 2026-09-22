"""Gate 2 of E2: the transport packs cover browser traffic unchanged."""

from nokr_qa.packs import PackContext
from nokr_qa.packs import run_all
from nokr_qa.packs import run_ui_transport

TRACE = "nokrqa-ui-overview-2"
PRIMARY = "/platform/dashboard/metrics"


def _ctx(**overrides) -> PackContext:
    base = dict(
        method="GET",
        url_path=PRIMARY,
        request_headers={},
        request_body=None,
        status_code=200,
        response_headers={"X-Trace-Id": TRACE, "Content-Type": "application/json"},
        response_text='{"gross_volume": 1234.5}',
        elapsed_ms=310.0,
        expect_status=200,
        trace_sent=TRACE,
        environment="sandbox",
        budget_ms=15000,
        fail_ms=15000,
        web_log_lines=[
            "2026-09-16 12:00:00.123 INFO 1 --- [io-8080-exec-1] "
            f"c.n.c.DashboardController.metrics - request handled - trace_id: [{TRACE}]"
        ],
        case_kind="ui",
    )
    base.update(overrides)
    return PackContext(**base)


def _statuses(ctx: PackContext) -> dict[str, str]:
    return {item.pack_id: item.status for item in run_ui_transport(ctx)}


def test_browser_traffic_lands_on_the_three_transport_packs():
    assert _statuses(_ctx()) == {
        "http.baseline": "pass",
        "observability": "pass",
        "http.success": "pass",
    }


def test_functions_of_the_packs_are_reused_not_reimplemented():
    # The emenda's claim is that no transport pack function changes for the
    # browser. `run_all` now dispatches on the step kind, so the HTTP side is
    # built with an explicit empty kind — otherwise both sides would resolve to
    # the UI set and the subset assertion would compare a set with itself.
    ui_only = {item.pack_id for item in run_ui_transport(_ctx())}
    http_only = {item.pack_id for item in run_all(_ctx(case_kind=""))}
    assert {"http.baseline", "observability", "http.success"} == ui_only
    assert ui_only < http_only


def test_missing_trace_header_fails_the_baseline():
    statuses = _statuses(_ctx(response_headers={"Content-Type": "application/json"}))
    assert statuses["http.baseline"] == "fail"


def test_mismatched_trace_header_fails_the_baseline():
    statuses = _statuses(
        _ctx(response_headers={"X-Trace-Id": "someone-else", "Content-Type": "application/json"})
    )
    assert statuses["http.baseline"] == "fail"


def test_5xx_is_a_hard_fail_even_on_a_browser_step():
    statuses = _statuses(
        _ctx(
            status_code=500,
            expect_status=500,
            response_text='{"error": "boom", "traceId": "' + TRACE + '"}',
        )
    )
    assert statuses["http.baseline"] == "fail"


def test_missing_web_log_line_fails_observability():
    statuses = _statuses(_ctx(web_log_lines=[]))
    assert statuses["observability"] == "fail"
    assert statuses["http.success"] == "fail"


def test_an_error_line_for_the_trace_fails_http_success():
    statuses = _statuses(
        _ctx(
            web_log_lines=[
                "2026-09-16 12:00:00.123 ERROR 1 --- [io-8080-exec-1] "
                f"c.n.c.DashboardController.metrics - blew up - trace_id: [{TRACE}]"
            ]
        )
    )
    assert statuses["http.success"] == "fail"
    assert statuses["observability"] == "pass"


def test_a_warn_line_only_warns_http_success():
    statuses = _statuses(
        _ctx(
            web_log_lines=[
                "2026-09-16 12:00:00.123 WARN 1 --- [io-8080-exec-1] "
                f"c.n.c.DashboardController.metrics - slow query - trace_id: [{TRACE}]"
            ]
        )
    )
    assert statuses["http.success"] == "warn"


def test_the_page_budget_replaces_the_hot_path_budget():
    # A ClickHouse overview query is not a hot-path transaction: 900 ms is fine
    # against the 15 s page budget even though the default HTTP budget is 1.5 s.
    statuses = _statuses(_ctx(elapsed_ms=900.0))
    assert statuses["http.baseline"] == "pass"

    # With the HTTP budget in force the same read warns, which is exactly the
    # noise E2 avoids by handing the step the page budget.
    hot_path = _ctx(elapsed_ms=900.0, budget_ms=500, fail_ms=1500)
    assert _statuses(hot_path)["http.baseline"] == "warn"


def test_elapsed_beyond_the_page_budget_is_a_hard_fail():
    assert _statuses(_ctx(elapsed_ms=20000.0))["http.baseline"] == "fail"


def test_a_waive_still_applies_on_the_browser_path():
    statuses = _statuses(_ctx(elapsed_ms=20000.0, waives=["http.baseline"]))
    assert statuses["http.baseline"] == "waived"


def test_unparseable_json_body_fails_the_baseline():
    statuses = _statuses(
        _ctx(
            response_headers={"X-Trace-Id": TRACE, "Content-Type": "application/json"},
            response_text="<html>oops</html>",
        )
    )
    assert statuses["http.baseline"] == "fail"


def test_http_success_is_not_evaluated_for_a_non_2xx_primary():
    statuses = _statuses(_ctx(status_code=401, expect_status=401))
    assert "http.success" not in statuses
