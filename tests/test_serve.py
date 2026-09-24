import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from heimdall_qa.serve.app import create_app
from heimdall_qa.session import RoundSession
from heimdall_qa.testing import config_for
from heimdall_qa.testing import project_at

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WALK_HN = FIXTURES / "rounds" / "walk-hn.yaml"
_DESCRIPTOR = FIXTURES / "qa" / "project.yaml"


def _handler(web_log: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        trace = request.headers.get("x-trace-id", "missing")
        body = json.loads(request.content.decode("utf-8") or "{}")
        if "note" in body:
            with web_log.open("a", encoding="utf-8") as handle:
                handle.write(
                    "2026-09-01 16:00:00 [vt] INFO  c.n.Qa [SANDBOX] - "
                    f"trace_id: [{trace}] - accepted\n"
                )
            #: The API answers with a credential it should not echo, so the page
            #: has something to prove it redacted. It is planted here and not in a
            #: committed file on purpose: `validate` refuses a credential in the
            #: round's own content, and this fixture has to stay valid.
            return httpx.Response(
                202,
                json={"status": "ACCEPTED", "api_key": "test_key_plantedkey"},
                headers={"X-Trace-Id": trace, "Content-Type": "application/json"},
            )
        return httpx.Response(
            400,
            json={"error": "note is required", "traceId": trace},
            headers={"X-Trace-Id": trace, "Content-Type": "application/json"},
        )

    return handler


def _client(tmp_path: Path) -> TestClient:
    web_log = tmp_path / "web.log"
    worker_log = tmp_path / "worker.log"
    web_log.write_text("", encoding="utf-8")
    worker_log.write_text("", encoding="utf-8")
    http = httpx.Client(
        transport=httpx.MockTransport(_handler(web_log)),
        timeout=10.0,
    )
    session = RoundSession(
        WALK_HN,
        root=FIXTURES,
        config=config_for(
            project_at(_DESCRIPTOR, web=str(web_log), worker=str(worker_log))
        ),
        client=http,
        runs_dir=tmp_path / "runs",
    )
    return TestClient(create_app(session=session), follow_redirects=False)


def test_openapi_and_docs_are_disabled(tmp_path: Path):
    client = _client(tmp_path)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/redoc").status_code == 404


def test_verdict_reject_without_comment_is_400(tmp_path: Path):
    client = _client(tmp_path)
    started = client.post("/start", data={"mode": "walk"})
    assert started.status_code == 302
    response = client.post(
        "/verdict",
        data={"status": "fail", "comment": "", "continue_round": "yes"},
    )
    assert response.status_code == 400
    assert "coment" in response.text.lower() or "Reprovar" in response.text
    assert client.get("/round").status_code == 200
    assert "note-H01" in client.get("/round").text


def test_verdict_reject_with_comment_continue_goes_next(tmp_path: Path):
    client = _client(tmp_path)
    client.post("/start", data={"mode": "walk"})
    response = client.post(
        "/verdict",
        data={
            "status": "fail",
            "comment": "envelope ok but the copy is a Java class name",
            "continue_round": "yes",
        },
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/round"
    page = client.get("/round")
    assert page.status_code == 200
    assert "note-N-omit-note" in page.text


def test_verdict_stop_redirects_to_done(tmp_path: Path):
    client = _client(tmp_path)
    client.post("/start", data={"mode": "walk"})
    response = client.post(
        "/verdict",
        data={
            "status": "fail",
            "comment": "stopping after the happy path",
            "continue_round": "no",
        },
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/done"
    done = client.get("/done")
    assert done.status_code == 200
    assert "counts" in done.text.lower() or "fail" in done.text.lower()
    runs = tmp_path / "runs"
    latest = (runs / "latest").resolve()
    assert str(latest) in done.text


def test_round_page_does_not_leak_planted_api_key(tmp_path: Path):
    client = _client(tmp_path)
    client.post("/start", data={"mode": "walk"})
    page = client.get("/round")
    assert page.status_code == 200
    assert "test_key_plantedkey" not in page.text
    assert "[REDACTED]" in page.text or "note-H01" in page.text


def test_round_page_shows_every_declared_log_source_and_why_it_is_empty(
    tmp_path: Path,
):
    """The page has to say which source owes a line, not render one blank panel.

    A reader who sees an empty log panel cannot tell "the file is not there" from
    "the project declared the trace does not reach it" — and those two readings
    lead to opposite conclusions about the product.
    """
    client = _client(tmp_path)
    client.post("/start", data={"mode": "walk"})
    page = client.get("/round")

    assert page.status_code == 200
    assert "Logs — 3 fonte(s) declarada(s)" in page.text
    assert "trace_id: [" in page.text
    # The worker owes a line (accepted work on an async endpoint) and has none.
    assert "missing" in page.text.lower() or "nenhuma linha" in page.text
    # The third source is declared and not instrumented, and says so.
    assert "o projeto declarou que o trace não chega aqui" in page.text


def test_start_page_shows_environment_and_queue_size(tmp_path: Path):
    client = _client(tmp_path)
    page = client.get("/")
    assert page.status_code == 200
    assert "sandbox" in page.text
    assert "walk — para em todo passo" in page.text
    assert "review — auto-avança quando packs passam" in page.text


def test_round_page_has_prev_next_and_can_revisit_previous_step(tmp_path: Path):
    client = _client(tmp_path)
    client.post("/start", data={"mode": "walk"})
    first = client.get("/round")
    assert first.status_code == 200
    assert "Anterior" in first.text
    assert "Próximo" in first.text
    assert "note-H01" in first.text
    client.post(
        "/verdict",
        data={"status": "pass", "comment": "", "continue_round": "yes"},
    )
    current = client.get("/round")
    assert current.status_code == 200
    assert "note-N-omit-note" in current.text
    assert "Aprovar" in current.text
    back = client.get("/round?step=0")
    assert back.status_code == 200
    assert "note-H01" in back.text
    assert "Ir ao passo atual" in back.text
    assert "Aprovar" not in back.text
    assert "/round?step=1" in back.text
    again = client.get("/round?step=1")
    assert again.status_code == 200
    assert "note-N-omit-note" in again.text
    assert "Aprovar" in again.text


def test_round_queue_marks_current_and_uses_portuguese_status(tmp_path: Path):
    client = _client(tmp_path)
    client.post("/start", data={"mode": "walk"})
    page = client.get("/round")
    assert 'class="pending current"' in page.text or "current" in page.text
    assert "Pendente" in page.text
    assert "Aprovar" in page.text
    assert "Reprovar e seguir" in page.text
    assert "Reprovar e parar" in page.text


def test_comment_required_message_on_form(tmp_path: Path):
    client = _client(tmp_path)
    client.post("/start", data={"mode": "walk"})
    response = client.post(
        "/verdict",
        data={"status": "fail", "comment": "", "continue_round": "yes"},
    )
    assert response.status_code == 400
    assert "Reprovar exige comentário" in response.text
    assert 'aria-invalid="true"' in response.text


def test_fail_stop_button_finishes_round(tmp_path: Path):
    client = _client(tmp_path)
    client.post("/start", data={"mode": "walk"})
    response = client.post(
        "/verdict",
        data={
            "status": "fail_stop",
            "comment": "stopping with the stop button",
            "continue_round": "yes",
        },
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/done"


def test_tree_case_click_after_done_shows_step_evidence(tmp_path: Path):
    client = _client(tmp_path)
    client.post("/start", data={"mode": "walk"})
    client.post(
        "/verdict",
        data={
            "status": "fail_stop",
            "comment": "stopping after the happy path",
            "continue_round": "yes",
        },
    )
    done = client.get("/done")
    assert done.status_code == 200
    assert "Fim da rodada" in done.text

    page = client.get("/?node=case:rounds/walk-hn.yaml:note-H01")
    assert page.status_code == 200
    assert "Fim da rodada" not in page.text
    assert "walk — para em todo passo" not in page.text
    assert "note-H01" in page.text
    assert "Body da request" in page.text
    assert "Body da response" in page.text
    assert "Veredito:" in page.text
    assert "Aprovar" not in page.text


def test_done_page_shows_kpis_without_raw_dump_in_fold(tmp_path: Path):
    client = _client(tmp_path)
    client.post("/start", data={"mode": "walk"})
    client.post(
        "/verdict",
        data={
            "status": "fail",
            "comment": "stopping after the happy path",
            "continue_round": "no",
        },
    )
    done = client.get("/done")
    assert done.status_code == 200
    assert "Passou" in done.text
    assert "Falhou" in done.text
    assert "summary.json" in done.text
    assert "round_id" in done.text
