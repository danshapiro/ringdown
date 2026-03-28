"""Pydantic models describing the structure of `config.yaml`.

The schema keeps contributor-facing configuration self-documenting while
providing actionable validation errors when fields are missing or malformed.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ToolRunnerConfig(BaseModel):
    """Configuration for the audible tool runner loop."""

    interval_sec: int = Field(2, ge=0)
    status_messages: dict[str, str] = Field(default_factory=dict)
    thinking_sounds: dict[str, list[str]] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


class ServerVADConfig(BaseModel):
    """Configuration for server-side voice activity detection."""

    activation_threshold: float = Field(0.6, ge=0.0, le=1.0)
    silence_duration_ms: int = Field(400, ge=0)
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class RealtimeAgentConfig(BaseModel):
    """Realtime transport configuration for an agent."""

    model: str | None = None
    voice: str | None = None
    server_vad: ServerVADConfig | dict[str, Any] | None = Field(default=None, alias="serverVad")

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class DefaultsConfig(BaseModel):
    """Top-level defaults inherited by every agent."""

    timezone: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    temperature: float = Field(1.0, ge=0.0, le=2.0)
    max_tokens: int = Field(..., gt=0)
    language: str = Field(..., min_length=2)
    bot_name: str = Field(..., min_length=1)
    default_email: str = Field(..., min_length=3)
    email_greenlist_enforced: bool = False
    admin_emails: list[str] = Field(default_factory=list)
    project_name: str = Field(..., min_length=1)
    calendar_user_name: str = Field(..., min_length=1)

    backup_model: str | None = None
    backup_temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    backup_max_tokens: int | None = Field(default=None, gt=0)

    max_history: int = Field(20, ge=1)

    voice: str | None = None
    tts_provider: str | None = None
    max_disconnect_seconds: int = Field(60, ge=0)
    welcome_greeting: str = Field(..., min_length=1)

    tts_prosody: dict[str, str] = Field(default_factory=dict)
    tool_runner: ToolRunnerConfig = Field(default_factory=ToolRunnerConfig)
    tool_header: str = ""
    tools: list[str] = Field(default_factory=list)
    max_tool_iterations: int = Field(6, ge=1)

    transcription_provider: str = Field(..., min_length=1)
    speech_model: str = Field(..., min_length=1)

    tool_prompts: dict[str, str] = Field(default_factory=dict)
    realtime: RealtimeAgentConfig | None = None

    model_config = ConfigDict(extra="allow")


class AgentConfig(BaseModel):
    """Per-agent configuration overlaying :class:`DefaultsConfig`."""

    bot_name: str = Field(..., min_length=1)
    prompt: str | None = None
    phone_numbers: list[str] | None = None
    continue_conversation: bool = False
    continuation_greeting: str | None = None
    welcome_greeting: str | None = None
    max_disconnect_seconds: int | None = Field(default=None, ge=0)
    max_history: int | None = Field(default=None, ge=1)
    tools: list[str] | None = None
    email_greenlist_enforced: bool | None = None
    email_greenlist: list[str] | None = None
    docs_folder_greenlist: list[str] | None = None
    tool_header: str | None = None
    tool_prompts: dict[str, str] = Field(default_factory=dict)
    realtime: RealtimeAgentConfig | None = None

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    def _validate_phone_numbers(
        cls,
        data: AgentConfig | dict[str, Any] | object,
    ) -> AgentConfig | dict[str, Any] | object:
        if isinstance(data, AgentConfig):
            payload = data.model_dump()
        elif isinstance(data, dict):
            payload = data
        elif hasattr(data, "data"):
            payload = data.data or {}
        else:
            payload = {}

        numbers = payload.get("phone_numbers") or []
        if len(numbers) != len(set(numbers)):
            raise ValueError("Agent phone_numbers must be unique per agent")
        return data

    @model_validator(mode="before")
    def _validate_tools(
        cls,
        data: AgentConfig | dict[str, Any] | object,
    ) -> AgentConfig | dict[str, Any] | object:
        if isinstance(data, AgentConfig):
            payload = data.model_dump()
        elif isinstance(data, dict):
            payload = data
        elif hasattr(data, "data"):
            payload = data.data or {}
        else:
            payload = {}

        tools = payload.get("tools")
        if tools is not None and not all(isinstance(t, str) for t in tools):
            raise TypeError("tools entries must be strings")
        return data


class MobileDeviceConfig(BaseModel):
    """Configuration entry describing a mobile client registered with the backend."""

    label: str = Field(..., min_length=1)
    agent: str = Field(..., min_length=1)
    enabled: bool = False
    created_at: datetime | None = Field(default=None, alias="createdAt")
    last_seen: datetime | None = Field(default=None, alias="lastSeen")
    notes: str | None = None
    poll_after_seconds: int | None = Field(default=None, ge=1, le=300, alias="pollAfterSeconds")
    blocked_reason: str | None = Field(default=None, alias="blockedReason")
    auth_token: str | None = Field(default=None, alias="authToken", min_length=8)
    tls_pins: list[str] = Field(default_factory=list, alias="tlsPins")
    session_resume_ttl_seconds: int = Field(
        default=300, ge=60, le=3600, alias="sessionResumeTtlSeconds"
    )

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class MobileTextConfig(BaseModel):
    """Configuration describing text streaming behaviour for mobile clients."""

    websocket_path: str = Field(
        default="/v1/mobile/text/session", alias="websocketPath", min_length=1
    )
    session_ttl_seconds: int = Field(default=900, ge=60, le=7200, alias="sessionTtlSeconds")
    resume_ttl_seconds: int = Field(default=300, ge=60, le=3600, alias="resumeTtlSeconds")
    heartbeat_interval_seconds: int = Field(
        default=15, ge=5, le=180, alias="heartbeatIntervalSeconds"
    )
    heartbeat_timeout_seconds: int = Field(
        default=45, ge=10, le=600, alias="heartbeatTimeoutSeconds"
    )
    tls_pins: list[str] = Field(default_factory=list, alias="tlsPins")

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class BackendOnlyConfigModel(BaseModel):
    """Backend-only configuration schema used to preserve the cleaned-main contract."""

    defaults: DefaultsConfig
    agents: dict[str, AgentConfig]

    allow_ssml: bool = True
    debug: str | None = None
    hints: str | None = None
    docs_folder_greenlist_defaults: list[str] = Field(default_factory=list)
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    @model_validator(mode="before")
    def _check_agents(cls, values: dict[str, Any]) -> dict[str, Any]:
        agents = values.get("agents") or {}

        if "unknown-caller" not in agents:
            raise ValueError("Configuration must include an 'unknown-caller' agent")

        seen: dict[str, str] = {}
        for agent_name, agent_cfg in agents.items():
            if isinstance(agent_cfg, dict):
                numbers = agent_cfg.get("phone_numbers") or []
            elif isinstance(agent_cfg, AgentConfig):
                numbers = agent_cfg.phone_numbers or []
            else:
                numbers = getattr(agent_cfg, "phone_numbers", None) or []

            for number in numbers:
                if number in seen:
                    other = seen[number]
                    raise ValueError(
                        f"Duplicate phone number {number!r} defined for agents "
                        f"{agent_name!r} and {other!r}"
                    )
                seen[number] = agent_name
        return values


class ConfigModel(BackendOnlyConfigModel):
    """Complete Ringdown configuration schema."""

    mobile_devices: dict[str, MobileDeviceConfig] = Field(
        default_factory=dict,
        alias="mobileDevices",
    )
    mobile_text: MobileTextConfig = Field(alias="mobileText")

    @model_validator(mode="before")
    def _check_mobile_devices(cls, values: dict[str, Any]) -> dict[str, Any]:
        agents = values.get("agents") or {}
        mobile_devices = values.get("mobile_devices")
        if mobile_devices is None:
            mobile_devices = values.get("mobileDevices") or {}

        for device_id, device_cfg in mobile_devices.items():
            if isinstance(device_cfg, MobileDeviceConfig):
                agent = device_cfg.agent
            elif isinstance(device_cfg, dict):
                agent = device_cfg.get("agent")
            else:
                agent = getattr(device_cfg, "agent", None)

            if not agent:
                raise ValueError(f"Mobile device '{device_id}' is missing required 'agent' field")
            if agent not in agents:
                raise ValueError(
                    f"Mobile device '{device_id}' references unknown agent '{agent}'. "
                    "Add the agent to config.yaml before assigning devices."
                )
        return values


def _coerce_truthy(flag: str | None) -> bool:
    """Return True when *flag* represents an enabled boolean string."""

    if flag is None:
        return False

    return flag.strip().lower() in {"1", "true", "yes", "on"}


def resolve_config_path(
    explicit: str | None,
    *,
    allow_example_fallback: bool | None = None,
    project_root: Path | None = None,
) -> Path:
    """Resolve configuration path from an explicit value or fallback defaults."""

    if explicit:
        return Path(explicit).expanduser().resolve()

    root = project_root or Path(__file__).resolve().parent.parent
    default_path = root / "config.yaml"
    if default_path.exists():
        return default_path

    allow_fallback = allow_example_fallback
    if allow_fallback is None:
        allow_fallback = _coerce_truthy(os.getenv("RINGDOWN_ALLOW_CONFIG_EXAMPLE"))

    if allow_fallback:
        return (root / "config.example.yaml").resolve()

    raise FileNotFoundError(
        "config.yaml was not found and fallback to config.example.yaml is disabled. "
        "Set RINGDOWN_ALLOW_CONFIG_EXAMPLE=1 to reuse the example file or point "
        "RINGDOWN_CONFIG_PATH at a specific configuration."
    )
