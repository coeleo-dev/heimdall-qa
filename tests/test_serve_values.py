from pathlib import Path

from fastapi.testclient import TestClient

from heimdall_qa.serve.app import create_app
from heimdall_qa.session import RoundSession

from test_loop_probe import _config
from test_loop_probe import _write_values_tree
from test_values_packs import _add_probe
from test_values_packs import _client as _http
from test_values_packs import _stateful_handler
from test_values_packs import _values_secrets


def _session(tmp_path: Path) -> RoundSession:
    _write_values_tree(tmp_path, metering_times=1, ingest_times=1)
    _add_probe(tmp_path)
    return RoundSession(
        tmp_path / "round.yaml",
        root=tmp_path,
        config=_config(tmp_path),
        client=_http(
            _stateful_handler(
                wallet_after="0.98700",
                metrics_after="0.01300",
                tx_after=2,
                overview_after="2.5",
            )
        ),
        runs_dir=tmp_path / "runs",
        secrets=_values_secrets(),
    )


def test_walk_queue_lists_suite_steps_not_seventeen_h_rows(tmp_path: Path):
    session = _session(tmp_path)
    view = session.start("walk")
    labels = [item.case_id for item in view.queue]
    assert labels == [
        "loop metering ×1",
        "loop ingest ×1",
        "probe values-10m-7i",
    ]
    assert all("H01" not in label for label in labels)


def test_probe_page_shows_esperado_vs_lido(tmp_path: Path):
    client = TestClient(create_app(session=_session(tmp_path)), follow_redirects=False)
    started = client.post("/start", data={"mode": "walk"})
    assert started.status_code == 302
    first = client.get("/round")
    assert first.status_code == 200
    assert "loop metering ×1" in first.text
    assert "metering-H01" not in first.text or "loop metering" in first.text
    client.post("/verdict", data={"status": "pass", "comment": "", "continue_round": "yes"})
    second = client.get("/round")
    assert second.status_code == 200
    assert "loop ingest ×1" in second.text
    client.post("/verdict", data={"status": "pass", "comment": "", "continue_round": "yes"})
    probe = client.get("/round")
    assert probe.status_code == 200
    assert "esperado vs lido" in probe.text
    assert "esperado R$" in probe.text
    assert "lido R$" in probe.text
    assert "probe values-10m-7i" in probe.text
    assert "oráculo: esperado" not in probe.text
    assert "Este passo é conferência, não um POST." in probe.text
    assert probe.text.count("esperado vs lido") == 1
