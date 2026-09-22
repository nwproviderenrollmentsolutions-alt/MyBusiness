"""Layered configuration: defaults -> yaml -> environment."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path(__file__).parent
REPO_ROOT = CONFIG_DIR.parent


class AutonomyMode(StrEnum):
    APPROVAL_REQUIRED = "approval_required"
    AUTONOMOUS = "autonomous"
    DISABLED = "disabled"


class Thresholds(BaseModel):
    deal_value_requires_approval_usd: float = 1000.0
    proposal_value_requires_approval_usd: float = 1000.0
    discount_pct_requires_approval: float = 10.0


class Limits(BaseModel):
    global_daily_outbound_max: int = 50
    campaign_daily_outbound_max: int = 25
    per_prospect_max_touches: int = 4
    per_prospect_cooldown_hours: int = 72


class CostCaps(BaseModel):
    global_daily_usd: float = 25.0
    per_run_usd: float = 1.0


class ApprovalPolicy(BaseModel):
    default_expiry_hours: int = 72
    on_expiry: Literal["reject", "escalate"] = "reject"

    @field_validator("on_expiry")
    @classmethod
    def _never_auto_approve(cls, v: str) -> str:
        # Guard against a config edit that would silently send on timeout.
        if v not in ("reject", "escalate"):
            raise ValueError("approval.on_expiry must be 'reject' or 'escalate'")
        return v


class PolicyConfig(BaseModel):
    version: int = 1
    autonomy: dict[str, AutonomyMode] = Field(default_factory=dict)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    limits: Limits = Field(default_factory=Limits)
    cost_caps: CostCaps = Field(default_factory=CostCaps)
    approval: ApprovalPolicy = Field(default_factory=ApprovalPolicy)

    def autonomy_for(self, action_type: str) -> AutonomyMode:
        """Unknown actions are approval-required, not allowed."""
        return self.autonomy.get(action_type, AutonomyMode.APPROVAL_REQUIRED)


class AgentEntry(BaseModel):
    module: str
    enabled: bool = True
    concurrency: int = 1
    subscribes_to: list[str] = Field(default_factory=list)


class AgentsConfig(BaseModel):
    agents: dict[str, AgentEntry] = Field(default_factory=dict)

    def subscribers_of(self, event_type: str) -> list[str]:
        return sorted(
            name
            for name, entry in self.agents.items()
            if entry.enabled and event_type in entry.subscribes_to
        )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql+psycopg://mybusiness:mybusiness_dev@localhost:5432/mybusiness"
    test_database_url: str = (
        "postgresql+psycopg://mybusiness:mybusiness_dev@localhost:5432/mybusiness_test"
    )

    dry_run: bool = True
    environment: str = "development"
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"

    llm_provider: str = "mock"
    web_research_provider: str = "mock"
    lead_discovery_provider: str = "mock"
    email_provider: str = "mock"
    crm_provider: str = "mock"
    calendar_provider: str = "mock"
    payment_provider: str = "mock"
    video_gen_provider: str = "mock"
    voice_provider: str = "mock"
    mock_seed: int = 1337

    anthropic_api_key: str = ""
    #: Only consulted when llm_provider=anthropic. See providers/anthropic_llm.py for
    #: which models this is known to behave correctly with.
    anthropic_model: str = "claude-opus-5"

    worker_poll_interval_seconds: float = 1.0
    worker_batch_size: int = 5
    task_lease_seconds: int = 300


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text())
    return loaded if isinstance(loaded, dict) else {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_policy_config() -> PolicyConfig:
    return PolicyConfig.model_validate(_load_yaml(CONFIG_DIR / "policies.yaml"))


@lru_cache(maxsize=1)
def get_agents_config() -> AgentsConfig:
    return AgentsConfig.model_validate(_load_yaml(CONFIG_DIR / "agents.yaml"))


def reload_config() -> None:
    """Drop cached config. Used by tests and by the CEO-facing reload endpoint."""
    get_settings.cache_clear()
    get_policy_config.cache_clear()
    get_agents_config.cache_clear()
