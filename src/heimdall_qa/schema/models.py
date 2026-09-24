from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

from heimdall_qa.step_kinds import registered_step_kinds
from heimdall_qa.step_kinds import unknown_step_kind_message
from heimdall_qa.step_kinds import unknown_step_kinds


class SaturateSpec(BaseModel):
    until_status: int
    max: int = Field(ge=1, le=50)
    unique_json: str | None = None


class WindowSpec(BaseModel):
    future_minutes: int | None = None
    past_hours: int | None = None


class FieldSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    required: bool
    json_name: str = Field(alias="json")
    max_length: int | None = None
    pattern: str | None = None
    window: WindowSpec | None = None
    flat: bool = False
    max_keys: int | None = None
    denylist: list[str] = Field(default_factory=list)
    json_alias: bool = False
    example: Any | None = None
    invalid: Any | None = None


class RuleSpec(BaseModel):
    id: str
    status: int
    code: str | None = None
    error: str | None = None
    when: str | None = None
    omit: list[str] = Field(default_factory=list)
    set: dict[str, Any] = Field(default_factory=dict)
    omit_headers: list[str] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)
    burst: int | None = Field(default=None, ge=1)
    saturate: SaturateSpec | None = None


class LiveOnlyRule(BaseModel):
    id: str
    status: int | None = None
    omit: list[str] = Field(default_factory=list)
    set: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)


class Waive(BaseModel):
    pack: str | None = None
    kind: str | None = None
    reason: str = ""
    p_gap: str | None = None

    @model_validator(mode="after")
    def reason_or_pgap(self) -> "Waive":
        if self.p_gap:
            return self
        if len(self.reason) < 40:
            raise ValueError(
                "waive reason must be at least 40 characters or set p_gap"
            )
        return self


class ExpectSpec(BaseModel):
    status: int | str
    code: str | None = None
    jsonpath: dict[str, Any] | None = None


class Contract(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    endpoint: str
    #: Provenance only: which DTO the contract was read from. It is optional
    #: because that is a fact about one target — a contract written by hand, or
    #: one read from an OpenAPI document, has no DTO to name, and requiring one
    #: made those targets undeclarable (estudo F3, achado A).
    dto: str | None = None
    auth: Literal["api_key", "jwt", "admin", "hmac", "none"] = "none"
    idempotency: Literal["header_uuid_v4", "none"] = "none"
    dedup: str | None = None
    async_mode: Literal["worker", "sync"] | None = Field(default=None, alias="async")
    baseline: str
    fields: dict[str, FieldSpec]
    rules: list[RuleSpec] = Field(default_factory=list)
    live_only_rules: list[LiveOnlyRule] = Field(default_factory=list)
    p_gaps: list[str] = Field(default_factory=list)
    resource_id_in_path: bool = False
    environment_conflict: bool = False
    area: str | None = None
    captures: dict[str, str] = Field(default_factory=dict)
    unique_json: str | None = None


class CaseFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    contract: str
    kind: str
    gate: str = "auto"
    tags: list[str] = Field(default_factory=list)
    bru: str | None = None
    diff: dict[str, Any] = Field(default_factory=dict)
    expect: ExpectSpec
    waive: list[Waive] = Field(default_factory=list)
    generate: dict[str, Any] | None = None
    capture: dict[str, str] | None = None
    capture_response: dict[str, str] | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    omit_headers: list[str] = Field(default_factory=list)
    wait_logs_ms: int | None = None
    burst: int | None = Field(default=None, ge=1)
    saturate: SaturateSpec | None = None
    path_values: dict[str, str] = Field(default_factory=dict)


class RoundFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    suite: str
    mode: str
    dimensions: list[str] = Field(default_factory=list)
    environment: str
    include: list[str]
    notes: str | None = None


MatrixSection = Literal["A0", "A1", "A2", "A3", "A4", "LIVE", "B", "B1", "B2", "B3", "B4", "B5", "B6"]


class CampaignExclude(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kinds: list[str] = Field(default_factory=lambda: ["D"])
    sections: list[str] = Field(default_factory=lambda: ["A5", "A6"])
    notes: str | None = None


class CampaignRound(BaseModel):
    model_config = ConfigDict(extra="ignore")

    round: str
    endpoint: str
    #: Copied into the campaign report when the round declares one; optional for
    #: the same reason `Contract.dto` is.
    dto: str | None = None
    matrix: MatrixSection
    auth: Literal["api_key", "jwt", "admin", "hmac", "none"] = "none"
    notes: str | None = None


class CampaignFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    environment: str
    exclude: CampaignExclude = Field(default_factory=CampaignExclude)
    rounds: list[CampaignRound]

    @model_validator(mode="after")
    def unique_rounds_and_endpoints(self) -> "CampaignFile":
        round_paths: set[str] = set()
        endpoints: set[str] = set()
        for entry in self.rounds:
            if entry.round in round_paths:
                raise ValueError(f"duplicate round path: {entry.round}")
            round_paths.add(entry.round)
            key = entry.endpoint.strip()
            if key in endpoints:
                raise ValueError(f"duplicate endpoint: {entry.endpoint}")
            endpoints.add(key)
        return self


class PollSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    until_jsonpath: str
    until_not: str
    timeout_ms: int | None = None
    get: str | None = None
    bru: str | None = None


class AfterEachSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    poll: PollSpec | None = None


class LoopSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    times: int = Field(ge=1)
    case: str
    generate: dict[str, Any] = Field(default_factory=dict)
    after_each: AfterEachSpec | None = None


class SurfaceSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    get: str
    jsonpath: str = Field(min_length=1)
    expect: Literal["exact", "increase", "unchanged"]
    timeout_ms: int | None = None
    min_delta: str | int | float | None = None


class ProbeSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    oracle: dict[str, Any] = Field(default_factory=dict)
    surfaces: list[SurfaceSpec] = Field(min_length=1)
    exclude: list[str] = Field(default_factory=list)


class SuiteStep(BaseModel):
    """One step of a suite: exactly one registered kind, nothing else.

    `extra="allow"` plus the validator below is what turns an unknown key into a
    named error. With the previous `extra="ignore"`, a suite could declare a kind
    nobody implemented and the key would be dropped in silence — the step would
    then run as a different kind, or not run at all.
    """

    model_config = ConfigDict(extra="allow")

    probe_begin: str | None = None
    loop: LoopSpec | None = None
    probe: ProbeSpec | None = None

    @model_validator(mode="before")
    @classmethod
    def every_declared_kind_is_registered(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        declared = [key for key in data if key not in registered_step_kinds()]
        unknown = unknown_step_kinds(declared)
        if unknown:
            raise ValueError(unknown_step_kind_message(unknown[0]))
        return data

    @model_validator(mode="after")
    def exactly_one_kind(self) -> "SuiteStep":
        present = [
            name
            for name, value in (
                ("probe_begin", self.probe_begin),
                ("loop", self.loop),
                ("probe", self.probe),
            )
            if value is not None
        ]
        if len(present) != 1:
            known = ", ".join(sorted(registered_step_kinds()))
            raise ValueError(f"suite step must set exactly one of {known}")
        return self


class SuiteFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    steps: list[SuiteStep] = Field(default_factory=list)
    catalog: dict[str, Any] = Field(default_factory=dict)
