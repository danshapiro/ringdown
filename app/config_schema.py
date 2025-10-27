"""Pydantic models describing the structure of `config.yaml`.

The schema keeps contributor-facing configuration self-documenting while
providing actionable validation errors when fields are missing or malformed.
"""

from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Union, Literal

from pydantic import BaseModel, Field, ConfigDict, model_validator


class ToolRunnerConfig(BaseModel):
    """Configuration for the audible tool runner loop."""

    interval_sec: int = Field(2, ge=0)
    status_messages: Dict[str, str] = Field(default_factory=dict)
    thinking_sounds: Dict[str, List[str]] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


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
    admin_emails: List[str] = Field(default_factory=list)
    project_name: str = Field(..., min_length=1)
    calendar_user_name: str = Field(..., min_length=1)

    backup_model: Optional[str] = None
    backup_temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    backup_max_tokens: Optional[int] = Field(default=None, gt=0)

    max_history: int = Field(20, ge=1)

    voice: Optional[str] = None
    tts_provider: Optional[str] = None
    max_disconnect_seconds: int = Field(60, ge=0)
    welcome_greeting: str = Field(..., min_length=1)

    tts_prosody: Dict[str, str] = Field(default_factory=dict)
    tool_runner: ToolRunnerConfig = Field(default_factory=ToolRunnerConfig)
    tool_header: str = ""
    tools: List[str] = Field(default_factory=list)
    max_tool_iterations: int = Field(6, ge=1)

    transcription_provider: str = Field(..., min_length=1)
    speech_model: str = Field(..., min_length=1)

    tool_prompts: Dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


class AgentConfig(BaseModel):
    """Per-agent configuration overlaying :class:`DefaultsConfig`."""

    bot_name: str = Field(..., min_length=1)
    prompt: Optional[str] = None
    phone_numbers: Optional[List[str]] = None
    continue_conversation: bool = False
    continuation_greeting: Optional[str] = None
    welcome_greeting: Optional[str] = None
    max_disconnect_seconds: Optional[int] = Field(default=None, ge=0)
    max_history: Optional[int] = Field(default=None, ge=1)
    tools: Optional[List[str]] = None
    email_greenlist_enforced: Optional[bool] = None
    email_greenlist: Optional[List[str]] = None
    docs_folder_greenlist: Optional[List[str]] = None
    tool_header: Optional[str] = None
    tool_prompts: Dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    def _validate_phone_numbers(
        cls,
        data: "AgentConfig" | Dict[str, Any] | object,
    ) -> "AgentConfig" | Dict[str, Any] | object:
        if isinstance(data, AgentConfig):
            payload = data.model_dump()
        elif isinstance(data, dict):
            payload = data
        elif hasattr(data, "data"):
            payload = getattr(data, "data") or {}
        else:
            payload = {}

        numbers = payload.get("phone_numbers") or []
        if len(numbers) != len(set(numbers)):
            raise ValueError("Agent phone_numbers must be unique per agent")
        return data

    @model_validator(mode="before")
    def _validate_tools(
        cls,
        data: "AgentConfig" | Dict[str, Any] | object,
    ) -> "AgentConfig" | Dict[str, Any] | object:
        if isinstance(data, AgentConfig):
            payload = data.model_dump()
        elif isinstance(data, dict):
            payload = data
        elif hasattr(data, "data"):
            payload = getattr(data, "data") or {}
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
    notes: Optional[str] = None
    poll_after_seconds: Optional[int] = Field(default=None, ge=1, le=300, alias="pollAfterSeconds")
    blocked_reason: Optional[str] = Field(default=None, alias="blockedReason")

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class MobileManagedAVConfig(BaseModel):
    """Configuration describing the managed audio/video provider for mobile clients."""

    provider: str = Field(..., min_length=1)
    api_base_url: str = Field(..., alias="apiBaseUrl", min_length=1)
    pipeline_handle: str = Field(..., alias="pipelineHandle", min_length=1)
    room_domain: str = Field(..., alias="roomDomain", min_length=1)
    session_ttl_seconds: int = Field(600, ge=60, le=7200, alias="sessionTtlSeconds")
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class ConfigModel(BaseModel):
    """Complete Ringdown configuration schema."""

    defaults: DefaultsConfig
    agents: Dict[str, AgentConfig]

    allow_ssml: bool = True
    debug: str | None = None
    hints: str | None = None
    docs_folder_greenlist_defaults: List[str] = Field(default_factory=list)
    mobile_devices: Dict[str, MobileDeviceConfig] = Field(default_factory=dict, alias="mobileDevices")
    mobile_managed_av: MobileManagedAVConfig = Field(alias="mobileManagedAv")

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    @model_validator(mode="before")
    def _check_agents(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        agents = values.get("agents") or {}

        if "unknown-caller" not in agents:
            raise ValueError("Configuration must include an 'unknown-caller' agent")

        seen: Dict[str, str] = {}
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

        mobile_devices = values.get("mobile_devices") or {}
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
