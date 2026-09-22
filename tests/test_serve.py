import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from heimdall_qa.config import HarnessConfig
from heimdall_qa.config import LogFiles
from heimdall_qa.serve.app import create_app
from heimdall_qa.session import RoundSession

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WALK_HN = FIXTURES / "rounds" / "walk-hn.yaml"


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
            return httpx.Response(
                202,
                json={"status": "ACCEPTED"},
                headers={"X-Trace-Id": trace, "Content-Type": "application/json"},
            )
        return httpx.Response(
            400,
            json={"error": "note is required", "traceId": trace},
            headers={"X-Trace-Id": trace, "Content-Type": "application/json"},
        )

    return handler


def _client(tmp_path: Path) -> TestClient:
    web_log = tmp_path / "nokr-web.log"
    worker_log = tmp_path / "nokr-worker.log"
    web_log.write_text("", encoding="utf-8")
    worker_log.write_text("", encoding="utf-8")
    http = httpx.Client(
        transport=httpx.MockTransport(_handler(web_log)),
        timeout=10.0,
    )
    session = RoundSession(
        WALK_HN,
        root=FIXTURES,
        config=HarnessConfig(
            log_files=LogFiles(web=str(web_log), worker=str(worker_log)),
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
    assert "nk_test_plantedkey" not in page.text
    assert "[REDACTED]" in page.text or "note-H01" in page.text


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
