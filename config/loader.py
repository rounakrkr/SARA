"""Configuration loader for SARA."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv


@dataclass(slots=True)
class AssistantConfig:
    name: str
    user_name: str


@dataclass(slots=True)
class InterfaceConfig:
    prompt: str


@dataclass(slots=True)
class LoggingConfig:
    level: str


@dataclass(slots=True)
class ServicesConfig:
    openrouter_api_key: str | None
    groq_api_key: str | None
    elevenlabs_api_key: str | None
    default_llm_engine: str
    default_tts_engine: str


@dataclass(slots=True)
class AppConfig:
    assistant: AssistantConfig
    interface: InterfaceConfig
    logging: LoggingConfig
    services: ServicesConfig


def load_config(path: Path) -> AppConfig:
    # Load env vars from the .env file (relative to settings.json or project root)
    dotenv_path = path.parent / ".env"
    if not dotenv_path.exists():
        dotenv_path = path.parent.parent / ".env"
    
    if dotenv_path.exists():
        load_dotenv(dotenv_path=dotenv_path)
    else:
        load_dotenv()

    payload = json.loads(path.read_text(encoding="utf-8"))

    def get_val(key: str, section: str | None = None, default: str | None = None) -> str | None:
        # Check env first (uppercase then lowercase)
        env_val = os.getenv(key.upper())
        if env_val is None:
            env_val = os.getenv(key.lower())
        if env_val is not None:
            return env_val
        # Check settings.json
        if payload:
            if section and section in payload and key in payload[section]:
                return payload[section][key]
            if key in payload:
                return payload[key]
        return default

    services = ServicesConfig(
        openrouter_api_key=get_val("openrouter_api_key", "services"),
        groq_api_key=get_val("groq_api_key", "services"),
        elevenlabs_api_key=get_val("elevenlabs_api_key", "services"),
        default_llm_engine=get_val("default_llm_engine", "services", "openrouter") or "openrouter",
        default_tts_engine=get_val("default_tts_engine", "services", "mock") or "mock",
    )

    return AppConfig(
        assistant=AssistantConfig(**payload["assistant"]),
        interface=InterfaceConfig(**payload["interface"]),
        logging=LoggingConfig(**payload["logging"]),
        services=services,
    )

