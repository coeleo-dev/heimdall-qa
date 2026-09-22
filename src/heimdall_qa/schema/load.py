from pathlib import Path
from typing import Any

import yaml

from heimdall_qa.schema.models import CampaignFile
from heimdall_qa.schema.models import CaseFile
from heimdall_qa.schema.models import Contract
from heimdall_qa.schema.models import RoundFile
from heimdall_qa.schema.models import SuiteFile


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"YAML must be a mapping: {path}")
    return data


def load_contract(path: Path) -> Contract:
    return Contract.model_validate(load_yaml(path))


def load_case(path: Path) -> CaseFile:
    return CaseFile.model_validate(load_yaml(path))


def load_round(path: Path) -> RoundFile:
    return RoundFile.model_validate(load_yaml(path))


def load_suite(path: Path) -> SuiteFile:
    return SuiteFile.model_validate(load_yaml(path))


def load_campaign(path: Path) -> CampaignFile:
    return CampaignFile.model_validate(load_yaml(path))
