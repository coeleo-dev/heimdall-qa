from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOKRAPI_AGENTS = ROOT.parent / "NokrAPI" / "AGENTS.md"
SKILL = ROOT / ".cursor" / "skills" / "heimdall-qa-round" / "SKILL.md"


def test_agents_md_covers_a14_bullets():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "docs/nokr-qa.md" in text
    assert "secrets.local.yaml" in text
    assert "DTO" in text
    assert "waive" in text.lower()
    assert "7878" in text
    assert "heimdall-qa fixture" in text
    assert "generate:" in text
    assert "fastapi" not in text.lower()


def test_skill_has_frontmatter_triggers_and_protocol():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "name: heimdall-qa-round" in text
    assert "crie uma rodada" in text
    assert "cubra o Trilho A HTTP" in text
    assert "gere a campanha trilho-a" in text
    assert "analise a campanha" in text
    assert "analise o último run" in text
    assert "heimdall-qa last-run" in text
    assert "heimdall-qa fixture" in text
    assert "generate:" in text
    assert "qa-trilho-a@nokr.dev" in text
    assert "scaffold-endpoint" in text
    assert "scaffold-round" in text
    assert "campaign validate" in text
    assert "campaign status" in text
    assert "campaigns/trilho-a-http.yaml" in text
    assert "validate" in text
    assert "127.0.0.1:7878" in text
    assert "heimdall-qa serve" in text
    assert "fila de `serve`" not in text
    assert "DTO" in text
    assert "analysis.md" in text
    assert "analysis-campanha.md" in text
    assert "loop" in text
    assert "probe" in text
    assert "17" in text
    assert "piloto-ingest.yaml" in text
    assert "values-10m-7i.yaml" in text
    assert "não executa" not in text
    assert "secrets.local.yaml" in text
    assert "disable-model-invocation" not in text
    assert "fastapi" not in text.lower()


def test_readme_documents_trilho_a_campaign():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "cubra o Trilho A HTTP" in text
    assert "campaigns/trilho-a-http.yaml" in text
    assert "heimdall-qa campaign validate campaigns/trilho-a-http.yaml" in text
    assert "heimdall-qa campaign status campaigns/trilho-a-http.yaml" in text
    assert "heimdall-qa serve\n" in text
    assert "heimdall-qa fixture" in text


def test_nokrapi_agents_points_to_sibling_harness():
    text = NOKRAPI_AGENTS.read_text(encoding="utf-8")
    assert "heimdall-qa" in text
    assert "7878" in text
    assert "heimdall-qa-round" in text
