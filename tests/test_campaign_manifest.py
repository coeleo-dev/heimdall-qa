from pathlib import Path

from nokr_qa.schema.load import load_campaign

REPO = Path(__file__).resolve().parents[1]
CAMPAIGN = REPO / "campaigns" / "trilho-a-http.yaml"


def test_trilho_a_http_campaign_loads():
    campaign = load_campaign(CAMPAIGN)
    assert campaign.id == "trilho-a-http"
    assert campaign.environment == "sandbox"
    assert "D" in campaign.exclude.kinds
    assert "A5" in campaign.exclude.sections
    assert "A6" in campaign.exclude.sections
    assert campaign.rounds
    destructive = [
        "rounds/platform-billable-metrics-archive.yaml",
        "rounds/platform-rate-cards-archive.yaml",
        "rounds/platform-api-keys-delete.yaml",
    ]
    round_paths = [entry.round for entry in campaign.rounds]
    assert round_paths[-3:] == destructive
    prefix = [entry for entry in campaign.rounds if entry.round not in destructive]
    assert [entry.matrix for entry in prefix] == sorted(entry.matrix for entry in prefix)
    endpoints = [entry.endpoint for entry in campaign.rounds]
    assert "POST /api/ingest" in endpoints
    assert "POST /auth/register" in endpoints
    assert "POST /api/metering" in endpoints
    ingest = next(entry for entry in campaign.rounds if entry.endpoint == "POST /api/ingest")
    assert ingest.round == "rounds/piloto-ingest.yaml"
    assert "rounds/values-10m-7i.yaml" not in round_paths
    endpoints_joined = " ".join(endpoints)
    assert "/platform/tenants/activate" not in endpoints_joined
    assert not any(entry.matrix in {"A5", "A6", "LIVE"} for entry in campaign.rounds)
    assert (REPO / "rounds" / "piloto-ingest.yaml").is_file()


def test_trilho_a_live_campaign_loads():
    campaign = load_campaign(REPO / "campaigns" / "trilho-a-live.yaml")
    assert campaign.id == "trilho-a-live"
    assert campaign.environment == "production"
    assert "D" in campaign.exclude.kinds
    assert "A5" in campaign.exclude.sections
    assert "A6" in campaign.exclude.sections
    endpoints = [entry.endpoint for entry in campaign.rounds]
    assert "POST /platform/tenants/activate" in endpoints
    assert all(entry.matrix == "LIVE" for entry in campaign.rounds)
    round_paths = [entry.round for entry in campaign.rounds]
    assert "rounds/values-10m-7i.yaml" not in round_paths
    assert "rounds/piloto-ingest.yaml" not in round_paths
    assert not any(entry.matrix in {"A5", "A6"} for entry in campaign.rounds)
    keys_live = REPO / "rounds" / "live-platform-api-keys-post.yaml"
    keys_text = keys_live.read_text(encoding="utf-8")
    assert "api-keys-post-N-rule-PRODUCTION_LOCKED.yaml" not in keys_text
    assert "cases/api-keys-post-live/api-keys-post-P-PRODUCTION.yaml" in keys_text
    assert (REPO / "contracts" / "platform-api-keys-post-live.yaml").is_file()
    ingest_live = (REPO / "rounds" / "live-api-ingest.yaml").read_text(encoding="utf-8")
    assert "ingest-P-LIVE_NO_METRIC.yaml" in ingest_live
    assert (REPO / "rounds" / "piloto-ingest.yaml").read_text(encoding="utf-8").count(
        "cases/ingest/"
    ) == 43
