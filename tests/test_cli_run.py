from pathlib import Path

import httpx

from nokr_qa.cli import build_parser
from nokr_qa.cli import main

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXAMPLE_ROUND = FIXTURES / "rounds" / "example.yaml"
WALK_HN = FIXTURES / "rounds" / "walk-hn.yaml"


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

    monkeypatch.setattr("nokr_qa.cli.httpx.Client", fake_client)
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


def test_cli_serve_without_round_prints_local_url(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: None)
    code = main(["serve"])
    captured = capsys.readouterr()
    assert code == 0
    assert "127.0.0.1:7878" in captured.out


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


def test_cli_serve_prints_local_url(monkeypatch, tmp_path: Path, capsys):
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


def test_cli_serve_campaign_prints_local_url(monkeypatch, tmp_path: Path, capsys):
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

    monkeypatch.setattr("nokr_qa.cli.execute_round", boom)
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
