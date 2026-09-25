from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from heimdall_qa.cli import build_parser
from heimdall_qa.cli import main

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXAMPLE_ROUND = FIXTURES / "rounds" / "example.yaml"
WALK_HN = FIXTURES / "rounds" / "walk-hn.yaml"

_STUB_INDEX = '<!doctype html><div id="root"></div>'


def _stub_bundle(tmp_path: Path) -> Path:
    """A stand-in for the built client, for the tests that are about the CLI.

    `serve` mounts the committed bundle and refuses to start without it, which is the
    behaviour `test_cli_serve_refuses_an_unbuilt_client` pins. A test about argument
    handling and the printed URL should not also be a statement about whether Node has
    run, so those point the mount at a stub: the same seam `tests/test_serve_api.py`
    uses, reached here through the module the CLI imports from.
    """
    bundle = tmp_path / "webapp"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "index.html").write_text(_STUB_INDEX, encoding="utf-8")
    return bundle


@pytest.fixture
def stub_bundle(monkeypatch, tmp_path: Path) -> Path:
    bundle = _stub_bundle(tmp_path)
    monkeypatch.setattr("heimdall_qa.serve.webapp.webapp_dir", lambda *a, **k: bundle)
    return bundle


def _echo_handler(request: httpx.Request) -> httpx.Response:
    trace = request.headers.get("x-trace-id", "missing")
    return httpx.Response(
        202,
        json={"status": "ACCEPTED"},
        headers={"X-Trace-Id": trace, "Content-Type": "application/json"},
    )


def test_help_lists_run_and_last_run(capsys):
    build_parser().print_help()
    text = capsys.readouterr().out.lower()
    assert "run" in text.split()
    assert "last-run" in text


def test_cli_run_creates_run_dir(monkeypatch, tmp_path: Path, capsys):
    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        return real_client(
            transport=httpx.MockTransport(_echo_handler),
            timeout=kwargs.get("timeout", 10.0),
        )

    monkeypatch.setattr("heimdall_qa.cli.httpx.Client", fake_client)
    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "run",
            str(EXAMPLE_ROUND),
            "--root",
            str(FIXTURES),
            "--mode",
            "headless",
        ]
    )
    assert code in {0, 1}
    stdout = capsys.readouterr().out.strip()
    run_dir = Path(stdout)
    assert run_dir.is_dir()
    assert (run_dir / "summary.json").is_file()
    latest = tmp_path / "runs" / "latest"
    assert latest.is_symlink()
    assert latest.resolve() == run_dir.resolve()

    last_code = main(["last-run", "--runs-dir", str(tmp_path / "runs")])
    last_out = capsys.readouterr().out.strip()
    assert last_code == 0
    assert last_out == str(run_dir.resolve())


def test_cli_serve_without_round_prints_local_url(monkeypatch, tmp_path: Path, capsys, stub_bundle):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: None)
    code = main(["serve"])
    captured = capsys.readouterr()
    assert code == 0
    assert "127.0.0.1:7878" in captured.out


def test_the_printed_url_carries_a_token_the_api_accepts(monkeypatch, tmp_path: Path, capsys, stub_bundle):
    """The URL `serve` prints is one a browser can actually use.

    Every `/api/*` route demands a per-run token, so a printed `http://host:port` with
    no token renders an empty shell and a 401 per call — which is what this used to
    print, and no test noticed because none of them opened the URL. This one does: it
    takes the fragment out of what `serve` printed and asks the app it built for the
    bootstrap, exactly as the browser would.
    """
    monkeypatch.chdir(tmp_path)
    captured_app: dict[str, object] = {}

    def fake_run(app, **kwargs):
        captured_app["app"] = app

    monkeypatch.setattr("uvicorn.run", fake_run)
    code = main(["serve"])
    assert code == 0

    url = capsys.readouterr().out.strip().splitlines()[0]
    assert url.startswith("http://127.0.0.1:7878/")
    token = url.split("#token=", 1)[1]
    assert token, "the printed URL has no token in it"

    client = TestClient(captured_app["app"], follow_redirects=False)
    assert client.post("/api/select", json={"key": "round:walk-hn"}).status_code == 401
    response = client.get("/api/bootstrap", headers={"X-Heimdall-Token": token})
    assert response.status_code == 200, response.text
    # An empty collection, because `serve` was started in a directory with no project —
    # which is fine and is not what this asserts. The token is: the same call without it
    # is a 401, and the payload is the client's contract and not an error body.
    body = response.json()
    assert body["labels"]["kind_label"]["round"] == "Endpoint"

    # And the surface the browser loads is the bundle, not a 404: `serve` is the same
    # client as the window, reached over a port instead of through pywebview.
    index = client.get("/")
    assert index.status_code == 200
    assert 'id="root"' in index.text


def test_the_printed_url_is_readable_before_the_server_blocks(monkeypatch, tmp_path: Path, stub_bundle):
    """The URL has to reach the reader who did not get a terminal.

    `print` is block-buffered whenever stdout is not a tty — a pipe, a log file, a
    process manager — and `uvicorn.run` below it never returns, so an unflushed line
    sits in the buffer until a process end that does not come. The reviewer is left
    with a port and no token, and every `/api/*` call answers 401.

    `capsys` cannot see this: pytest's capture writes through, so buffering never
    happens and the test would pass on the broken code. This one puts a real file in
    `sys.stdout` and reads it *from inside* the blocking call, which is the moment the
    claim is about — what a person has in front of them once the server is up.
    """
    monkeypatch.chdir(tmp_path)
    log = tmp_path / "serve.log"
    seen: dict[str, str] = {}

    def fake_run(app, **kwargs):
        # The server is blocking now. Anything still in the process's buffer is, for
        # the reader, not written at all.
        seen["log"] = log.read_text(encoding="utf-8")

    monkeypatch.setattr("uvicorn.run", fake_run)
    with log.open("w", encoding="utf-8") as handle:
        monkeypatch.setattr("sys.stdout", handle)
        code = main(["serve"])

    assert code == 0
    assert "http://127.0.0.1:7878/#token=" in seen["log"], (
        "the URL was still buffered when the server started blocking"
    )


def test_cli_serve_refuses_invalid_round(tmp_path: Path, capsys, monkeypatch):
    called = {"run": False}

    def fake_run(*args, **kwargs):
        called["run"] = True

    monkeypatch.setattr("uvicorn.run", fake_run)
    broken = tmp_path / "broken.yaml"
    broken.write_text("not: [valid: yaml: [", encoding="utf-8")
    code = main(["serve", str(broken), "--root", str(FIXTURES)])
    captured = capsys.readouterr()
    assert code == 1
    assert "error[ROUND_INVALID]" in captured.err
    assert called["run"] is False
    assert "Fase 7 — UI is not implemented yet." not in captured.out


def test_cli_serve_prints_local_url(monkeypatch, tmp_path: Path, capsys, stub_bundle):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: None)
    code = main(
        [
            "serve",
            str(WALK_HN),
            "--root",
            str(FIXTURES),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "127.0.0.1:7878" in captured.out
    assert "Fase 7 — UI is not implemented yet." not in captured.out


def test_cli_serve_campaign_prints_local_url(monkeypatch, tmp_path: Path, capsys, stub_bundle):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: None)
    code = main(
        [
            "serve",
            str(FIXTURES / "campaigns" / "ok.yaml"),
            "--root",
            str(FIXTURES),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "127.0.0.1:7878" in captured.out


def test_cli_serve_refuses_an_unbuilt_client(monkeypatch, tmp_path: Path, capsys):
    """No bundle, no server — and the refusal names the two commands that fix it.

    `serve` is a convenience over the same client the window shows, not a second
    surface, so starting it without a client would print a URL that 404s. The failure a
    reader meets instead has to be actionable: `WEBAPP_NOT_BUILT` and the `npm --prefix
    desktop/webapp …` line, never a stack trace from `StaticFiles`.
    """
    monkeypatch.setattr("heimdall_qa.serve.webapp.WEBAPP", tmp_path / "never-built")
    code = main(["serve"])
    captured = capsys.readouterr()
    assert code == 1
    assert "WEBAPP_NOT_BUILT" in captured.err
    assert "npm --prefix desktop/webapp" in captured.err


def test_last_run_missing_is_typed_error(tmp_path: Path, capsys):
    code = main(["last-run", "--runs-dir", str(tmp_path / "no-runs")])
    captured = capsys.readouterr()
    assert code == 1
    assert "error[LAST_RUN_MISSING]" in captured.err
    assert "hint:" in captured.err
    assert "Traceback" not in captured.err


def test_mode_review_is_typed_error(tmp_path: Path, capsys):
    code = main(
        [
            "run",
            str(EXAMPLE_ROUND),
            "--root",
            str(FIXTURES),
            "--mode",
            "review",
        ]
    )
    captured = capsys.readouterr()
    assert code == 1
    assert "error[MODE_REQUIRES_UI]" in captured.err
    assert "hint:" in captured.err
    assert "Traceback" not in captured.err


def test_verbose_unexpected_prints_traceback(monkeypatch, tmp_path: Path, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    #: The seam moved to the operations layer in the MCP refactor: the CLI delegates
    #: and no longer holds the runner, so this is where the call is intercepted.
    monkeypatch.setattr("heimdall_qa.operations.execute_round", boom)
    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "--verbose",
            "run",
            str(EXAMPLE_ROUND),
            "--root",
            str(FIXTURES),
            "--mode",
            "headless",
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "error[STEP_INTERNAL]" in captured.err
    assert "Traceback" in captured.err
    assert "synthetic failure" in captured.err


def test_cli_run_refuses_unreadable_round(tmp_path: Path, capsys):
    broken = tmp_path / "broken.yaml"
    broken.write_text("not: [valid: yaml: [", encoding="utf-8")
    code = main(["run", str(broken), "--root", str(FIXTURES), "--mode", "headless"])
    captured = capsys.readouterr()
    assert code == 1
    assert "error[ROUND_INVALID]" in captured.err
