from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from heimdall_qa.config import HarnessConfig
from heimdall_qa.config import LogFiles
from heimdall_qa.serve.app import create_app
from heimdall_qa.workspace import WorkspaceSession

FIXTURES = Path(__file__).resolve().parent / "fixtures"
H01_ONLY = FIXTURES / "rounds" / "h01-only.yaml"


def _workspace(tmp_path: Path, *, focus: Path | None = None) -> WorkspaceSession:
    return WorkspaceSession(
        root=FIXTURES,
        config=HarnessConfig(
            log_files=LogFiles(
                web=str(tmp_path / "web.log"),
                worker=str(tmp_path / "worker.log"),
            )
        ),
        client=httpx.Client(timeout=10.0),
        runs_dir=tmp_path / "runs",
        focus=focus,
    )


def test_workspace_home_lists_campaign(tmp_path: Path):
    client = TestClient(
        create_app(workspace=_workspace(tmp_path)),
        follow_redirects=False,
    )
    page = client.get("/")
    assert page.status_code == 200
    assert "Coleção" in page.text
    assert "example-campaign" in page.text
    assert "Redimensionar coleção" in page.text
    assert "queue-resizer" in page.text


def test_not_ready_round_cannot_start_from_ui(tmp_path: Path):
    client = TestClient(
        create_app(workspace=_workspace(tmp_path, focus=H01_ONLY)),
        follow_redirects=False,
    )
    response = client.post("/start", data={"mode": "walk"})
    assert response.status_code == 400
    assert "error[ROUND_INVALID]" in response.text
    assert "ROUND_BUSY" not in response.text
