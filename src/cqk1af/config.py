from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


LicenseClass = Literal["Extra", "Advanced", "General", "Technician", "Novice"]
RigMode = Literal["USB", "LSB", "AM", "FM", "CW", "DIGU", "DIGL"]


class OperatorCfg(BaseModel):
    callsign: str = "K1AF"
    name: str = ""
    qth: str = ""
    grid_square: str = ""
    license_class: LicenseClass = "Extra"
    phonetic_alphabet: Literal["NATO"] = "NATO"


class RadioCfg(BaseModel):
    model: Literal["FlexRadio 6400"] = "FlexRadio 6400"
    # New backend selector. "mock" wins regardless of mock_mode for back-compat.
    # SmartSDR v4 dropped the Windows DAX TX device shim, so the tcp backend
    # can no longer modulate via sounddevice → DAX. Use "flexlib" for v4.
    backend: Literal["mock", "tcp", "flexlib"] = "tcp"
    mock_mode: bool = True
    host: str = "auto"  # "auto" = network discovery
    port: int = 4992
    flexlib_path: Path = Path("runtime/flexlib")
    target_serial: str = ""  # blank = pick first discovered radio


class AudioCfg(BaseModel):
    rx_device: str = "DAX Audio RX 1"
    tx_device: str = "DAX Audio TX"
    sample_rate: int = 48000
    frame_ms: int = 20
    vad_aggressiveness: int = Field(default=3, ge=0, le=3)
    vad_hangover_ms: int = 300


class SttCfg(BaseModel):
    enabled: bool = True
    model: str = "distil-large-v3"
    device: Literal["auto", "cpu", "cuda"] = "auto"
    compute_type: Literal["auto", "int8", "int8_float16", "float16", "float32"] = "auto"
    beam_size: int = 1
    initial_prompt_extra: str = (
        "Amateur radio QSO. Callsigns like W1AW, K1AF, KC1ABC. "
        "Signal report five nine. QSL, QSB, QRM, 73, CQ, QRZ."
    )


class TtsCfg(BaseModel):
    enabled: bool = True
    voice_path: Path = Path("runtime/piper/en_US-amy-medium.onnx")
    rate: float = 1.0
    # Mirror synthesized TX audio to the PC default output device so the
    # operator hears what's being transmitted (in addition to going to DAX TX).
    pc_monitor_enabled: bool = True


class SafetyCfg(BaseModel):
    require_manual_tx_approval: bool = True
    approval_timeout_s: int = 15
    max_transmit_seconds: int = 30
    kill_releases_ptt: bool = True
    armed_default: bool = False
    id_interval_seconds: int = 600  # FCC §97.119


class CqCfg(BaseModel):
    template_id: str = "default"
    max_attempts: int = 5
    listen_seconds: int = 7
    inter_call_min_delay_s: float = 1.0
    inter_call_max_delay_s: float = 2.0


class CloudlogCfg(BaseModel):
    enabled: bool = False
    url: str = ""
    api_key: SecretStr = SecretStr("")
    station_profile_id: int = 1


class McpCfg(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765


class DashboardCfg(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"])


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CQK1AF_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    operator: OperatorCfg = Field(default_factory=OperatorCfg)
    radio: RadioCfg = Field(default_factory=RadioCfg)
    audio: AudioCfg = Field(default_factory=AudioCfg)
    stt: SttCfg = Field(default_factory=SttCfg)
    tts: TtsCfg = Field(default_factory=TtsCfg)
    safety: SafetyCfg = Field(default_factory=SafetyCfg)
    cq: CqCfg = Field(default_factory=CqCfg)
    cloudlog: CloudlogCfg = Field(default_factory=CloudlogCfg)
    mcp: McpCfg = Field(default_factory=McpCfg)
    dashboard: DashboardCfg = Field(default_factory=DashboardCfg)

    band_plan_path: Path = Path("config/band_plan_us.json")
    data_dir: Path = Path("data")
    log_level: str = "INFO"
    log_json: bool = False

    # Master switch for the live audio pipeline (capture+VAD+STT, TTS+playback).
    # When False, the assistant runs in narration-only mode (no PTT, no audio).
    # If PortAudio init crashes the backend on this host, set this to False (or
    # pass CQK1AF_AUDIO_PIPELINE_ENABLED=false) until you've cleared up DAX /
    # multi-host-API conflicts.
    audio_pipeline_enabled: bool = True


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a YAML mapping at the top level")
    return data


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Overlay wins, but nested dicts are merged."""
    out = dict(base)
    for k, v in overlay.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


_ENV_PREFIX = "CQK1AF_"
_ENV_NESTED_SEP = "__"


def _env_overlay(prefix: str = _ENV_PREFIX, sep: str = _ENV_NESTED_SEP) -> dict[str, Any]:
    """Walk env vars matching ``prefix`` and build a nested dict.

    ``CQK1AF_OPERATOR__CALLSIGN=VE3ABC`` becomes ``{"operator": {"callsign": "VE3ABC"}}``.
    """
    out: dict[str, Any] = {}
    for key, raw in os.environ.items():
        if not key.startswith(prefix):
            continue
        tail = key[len(prefix) :]
        parts = [p.lower() for p in tail.split(sep)]
        cursor = out
        for p in parts[:-1]:
            cursor = cursor.setdefault(p, {})
            if not isinstance(cursor, dict):
                # Conflict; skip rather than crash.
                cursor = {}
        cursor[parts[-1]] = raw
    return out


def load_settings(
    *,
    default_yaml: Path | None = Path("config/default.yaml"),
    user_yaml: Path | None = None,
) -> Settings:
    """Layered config (highest precedence last): default YAML → user YAML → env vars."""
    if user_yaml is None:
        user_yaml = Path.home() / ".cqk1af" / "config.yaml"

    layered: dict[str, Any] = {}
    if default_yaml is not None:
        layered = _deep_merge(layered, _load_yaml(default_yaml))
    layered = _deep_merge(layered, _load_yaml(user_yaml))
    layered = _deep_merge(layered, _env_overlay())

    return Settings.model_validate(layered) if layered else Settings()
