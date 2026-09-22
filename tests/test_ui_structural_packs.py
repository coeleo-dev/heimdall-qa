"""Gate of E3: the structural packs judge, and a waive cannot buy silence.

Three planted fixtures, one per structural pack, each asserting that *exactly*
the intended pack fails. The cross-talk assertion is the important half: a
`ui.render` failure that also dragged `ui.structure` down would make the round
report a regression nobody can attribute.
"""

from nokr_qa.packs import NON_WAIVABLE_PACKS
from nokr_qa.packs import PackContext
from nokr_qa.packs import PackResult
from nokr_qa.packs import _apply_waive
from nokr_qa.packs import run_all
from nokr_qa.packs import run_ui

TRACE = "nokrqa-ui-demo-2"

CLEAN_STRUCTURE = {"enabled": True, "matched": True, "error": None}
DIVERGED_STRUCTURE = {"enabled": True, "matched": False, "error": "line 3: expected heading"}
DISABLED_STRUCTURE = {"enabled": False, "matched": None, "error": None}

CLEAN_A11Y = {"enabled": True, "violations": [], "count": 0}
DISABLED_A11Y = {"enabled": False, "violations": []}

CONTRAST_VIOLATION = {
    "id": "color-contrast",
    "impact": "serious",
    "help": "Elements must meet minimum color contrast ratio thresholds",
    "helpUrl": "https://dequeuniversity.com/rules/axe/4.10/color-contrast",
    "tags": ["cat.color", "wcag2aa", "wcag143"],
    "nodes": [
        {
            "target": ["main .kpi-value"],
            "html": '<span class="kpi-value">R$ 9,99</span>',
            "messages": ["Element has insufficient color contrast of 2.1"],
        }
    ],
}


def _ctx(**overrides) -> PackContext:
    """A green `ui` step; every fixture below perturbs exactly one thing."""
    base = dict(
        method="GET",
        url_path="/platform/dashboard/metrics",
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
        has_primary=True,
        ui_path="/overview",
        ui_region="main",
        ui_structure=dict(CLEAN_STRUCTURE),
        ui_a11y=dict(CLEAN_A11Y),
    )
    base.update(overrides)
    return PackContext(**base)


def _statuses(ctx: PackContext) -> dict[str, str]:
    return {item.pack_id: item.status for item in run_ui(ctx)}


def _failed(ctx: PackContext) -> set[str]:
    return {pack_id for pack_id, status in _statuses(ctx).items() if status == "fail"}


# ── fixture 1: ui.render ──────────────────────────────────────────────────────


def test_a_console_error_is_the_only_pack_ui_render_brings_down():
    ctx = _ctx(ui_console=[{"type": "error", "text": "TypeError: x is not a function"}])
    assert _failed(ctx) == {"ui.render"}


def test_a_page_error_recorded_as_pageerror_also_fails_ui_render():
    # `page.on("pageerror")` is recorded with `source: "pageerror"`; an uncaught
    # exception in the app is exactly the kind of silent breakage A.19 hunts.
    ctx = _ctx(
        ui_console=[{"type": "pageerror", "text": "ReferenceError: nokr is not defined"}]
    )
    assert _failed(ctx) == {"ui.render"}


def test_an_observed_5xx_fails_ui_render():
    ctx = _ctx(
        ui_network=[
            {"method": "GET", "path": "/platform/dashboard/metrics", "status": 503}
        ]
    )
    assert _failed(ctx) == {"ui.render"}


def test_a_console_warning_is_a_warn_not_a_fail():
    # Warnings go to `analysis.md` for a human; the verdict stays green.
    ctx = _ctx(ui_console=[{"type": "warning", "text": "deprecated API"}])
    assert _statuses(ctx)["ui.render"] == "warn"


def test_a_clean_console_keeps_ui_render_green():
    assert _statuses(_ctx())["ui.render"] == "pass"


# ── fixture 2: ui.structure ───────────────────────────────────────────────────


def test_a_diverged_aria_tree_is_the_only_pack_ui_structure_brings_down():
    ctx = _ctx(ui_structure=dict(DIVERGED_STRUCTURE))
    assert _failed(ctx) == {"ui.structure"}


def test_the_structure_detail_carries_the_playwright_diff():
    ctx = _ctx(ui_structure=dict(DIVERGED_STRUCTURE))
    detail = {item.pack_id: item.detail for item in run_ui(ctx)}["ui.structure"]
    assert "expected heading" in detail


def test_no_declared_baseline_is_skipped_with_a_hint_not_a_pass():
    # The distinction the emenda insists on: never a silent green.
    ctx = _ctx(ui_structure=dict(DISABLED_STRUCTURE))
    result = {item.pack_id: item for item in run_ui(ctx)}["ui.structure"]
    assert result.status == "skipped"
    assert "baseline" in result.detail


def test_a_matching_tree_is_green():
    assert _statuses(_ctx())["ui.structure"] == "pass"


# ── fixture 3: ui.a11y ────────────────────────────────────────────────────────


def test_a_wcag_violation_is_the_only_pack_ui_a11y_brings_down():
    ctx = _ctx(ui_a11y={"enabled": True, "violations": [CONTRAST_VIOLATION], "count": 1})
    assert _failed(ctx) == {"ui.a11y"}


def test_the_a11y_detail_names_the_offending_rule_and_node():
    ctx = _ctx(ui_a11y={"enabled": True, "violations": [CONTRAST_VIOLATION], "count": 1})
    detail = {item.pack_id: item.detail for item in run_ui(ctx)}["ui.a11y"]
    assert "color-contrast" in detail
    assert "main .kpi-value" in detail


def test_a_disabled_scan_is_skipped_with_the_reason():
    ctx = _ctx(ui_a11y=dict(DISABLED_A11Y))
    result = {item.pack_id: item for item in run_ui(ctx)}["ui.a11y"]
    assert result.status == "skipped"
    assert "ui.a11y: false" in result.detail


def test_a_violation_free_scan_is_green():
    assert _statuses(_ctx())["ui.a11y"] == "pass"


# ── ui.visual ─────────────────────────────────────────────────────────────────


def test_ui_visual_is_skipped_and_says_why():
    result = {item.pack_id: item for item in run_ui(_ctx())}["ui.visual"]
    assert result.status == "skipped"
    assert "pixel diff" in result.detail


def test_ui_visual_can_be_waived_without_a_p_gap():
    # The pack the emenda's §1 leaves out of v1, so a waiver is the expected
    # declaration rather than a debt being hidden.
    ctx = _ctx(waives=["ui.visual"])
    assert _statuses(ctx)["ui.visual"] == "skipped"


def test_waiving_ui_visual_does_not_touch_the_other_packs():
    ctx = _ctx(waives=["ui.visual"], ui_console=[{"type": "error", "text": "boom"}])
    statuses = _statuses(ctx)
    assert statuses["ui.render"] == "fail"
    assert statuses["ui.structure"] == "pass"


# ── waive policy ──────────────────────────────────────────────────────────────


def test_a_structural_failure_can_be_waived_without_a_p_gap():
    ctx = _ctx(waives=["ui.structure"], ui_structure=dict(DIVERGED_STRUCTURE))
    assert _statuses(ctx)["ui.structure"] == "waived"


def test_ui_value_is_not_waivable_and_the_refusal_is_the_failure():
    # There is no `ui.value` pack yet (E4 supplies it), so the rule is proven on
    # the policy itself: the id is registered as non-waivable.
    assert "ui.value" in NON_WAIVABLE_PACKS


def test_a_non_waivable_pack_reports_the_refusal_instead_of_going_green():
    # No `ui.value` pack exists until E4, so the rule is proven on `_apply_waive`
    # itself: the refusal is a `fail` carrying its reason, never a `waived`.
    ctx = _ctx(waives=["ui.value"])
    refused = _apply_waive(ctx, PackResult("ui.value", "fail", "value 9.99 != 10.00"))
    assert refused.status == "fail"
    assert "waive refused" in refused.detail
    assert "value 9.99 != 10.00" in refused.detail


def test_a_waivable_pack_becomes_waived_and_keeps_its_detail():
    ctx = _ctx(waives=["ui.structure"])
    waived = _apply_waive(ctx, PackResult("ui.structure", "fail", "line 3 diverges"))
    assert waived.status == "waived"
    assert waived.detail == "line 3 diverges"


def test_applying_a_waive_to_a_green_pack_changes_nothing():
    # Idempotent on purpose: `run_ui_transport` already applies waives, and
    # `run_ui` applies them again over the combined list.
    ctx = _ctx(waives=["ui.render"])
    assert _apply_waive(ctx, PackResult("ui.render", "pass", "")).status == "pass"


# ── dispatch ──────────────────────────────────────────────────────────────────


def test_run_all_dispatches_ui_steps_to_the_ui_packs():
    statuses = {item.pack_id: item.status for item in run_all(_ctx())}
    assert set(statuses) == {
        "http.baseline",
        "observability",
        "http.success",
        "ui.render",
        "ui.structure",
        "ui.a11y",
        "ui.visual",
    }


def test_the_structural_packs_run_even_when_no_response_was_traced():
    # The screen painted but made no API call this step: the ARIA tree, the
    # console and the a11y scan still exist and must be judged.
    ctx = _ctx(has_primary=False, status_code=0, expect_status=0, web_log_lines=[])
    statuses = {item.pack_id: item.status for item in run_all(ctx)}
    assert "ui.render" in statuses
    assert "ui.structure" in statuses
    assert "ui.a11y" in statuses
    # The transport packs are the ones that need a response.
    assert "http.baseline" not in statuses


def test_the_http_path_is_untouched_by_the_dispatch():
    ctx = _ctx(case_kind="")
    statuses = {item.pack_id: item.status for item in run_all(ctx)}
    assert "ui.render" not in statuses
    assert {"http.baseline", "security.leak", "performance", "observability"} <= set(statuses)
