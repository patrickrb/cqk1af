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
    # webrtcvad aggressiveness 0..3. 3 (most aggressive) rejects SSB voice
    # outright — the narrow 2.7 kHz bandwidth and continuous band noise don't
    # match its telephone-voice model. 1 over-corrects: band noise itself
    # registers as speech, so VAD never sees a silent gap to close an
    # utterance. 2 is the working middle.
    vad_aggressiveness: int = Field(default=2, ge=0, le=3)
    vad_hangover_ms: int = 300
    # Hard cap on a single utterance so STT still gets fed when VAD never
    # sees silence (band noise = continuous "speech"). Real human turns on
    # SSB rarely exceed ~10 s before a pause; cap at 8 s.
    max_utterance_seconds: float = 8.0


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
    # Decoder fallback temperatures. faster-whisper steps through these when
    # compression_ratio_threshold or log_prob_threshold fails.
    temperatures: list[float] = Field(
        default_factory=lambda: [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    )
    # Stricter than the 2.4 default — Whisper loves to repeat itself
    # ("thank you thank you thank you...") on SSB static, and a tighter
    # compression-ratio guard catches it.
    compression_ratio_threshold: float = 2.2
    # Looser than the -1.0 default — clean speech under band noise legitimately
    # gets a worse logprob, and we don't want to nuke those segments.
    log_prob_threshold: float = -1.2
    # VAD already gates the audio that reaches Whisper, so we can be aggressive
    # about declaring no-speech and dropping the segment.
    no_speech_threshold: float = 0.7
    # Per-word timestamps enable per-word confidence downstream.
    word_timestamps: bool = True


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
    # .env is loaded explicitly in ``load_settings`` (which builds the layered
    # config dict and calls ``model_validate``), so we don't ask pydantic-
    # settings to do it here. Adding ``env_file`` here would mean
    # ``Settings()`` bare-constructor silently reads .env, which surprises
    # tests that expect pure defaults.
    model_config = SettingsConfigDict(
        env_prefix="CQK1AF_",
        env_nested_delimiter="__",
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


def _parse_env_file(path: Path) -> dict[str, str]:
    """Minimal .env reader (KEY=value, # comments, blank lines)."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip surrounding quotes, if any.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out


def _overlay_from_kv(
    items: dict[str, str], *, prefix: str = _ENV_PREFIX, sep: str = _ENV_NESTED_SEP
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, raw in items.items():
        if not key.startswith(prefix):
            continue
        tail = key[len(prefix) :]
        parts = [p.lower() for p in tail.split(sep)]
        cursor = out
        for p in parts[:-1]:
            cursor = cursor.setdefault(p, {})
            if not isinstance(cursor, dict):
                cursor = {}
        cursor[parts[-1]] = raw
    return out


def _env_overlay(prefix: str = _ENV_PREFIX, sep: str = _ENV_NESTED_SEP) -> dict[str, Any]:
    """Walk env vars matching ``prefix`` and build a nested dict.

    ``CQK1AF_OPERATOR__CALLSIGN=VE3ABC`` becomes ``{"operator": {"callsign": "VE3ABC"}}``.
    """
    return _overlay_from_kv(dict(os.environ), prefix=prefix, sep=sep)


def load_settings(
    *,
    default_yaml: Path | None = Path("config/default.yaml"),
    user_yaml: Path | None = None,
    env_file: Path | None = Path(".env"),
) -> Settings:
    """Layered config (highest precedence last):

    default YAML → user YAML → ``.env`` file → process env vars.

    Note: ``Settings.model_validate(layered)`` bypasses pydantic-settings'
    built-in env-file handling, so we read ``.env`` here explicitly. Otherwise
    the file referenced by ``.env.example`` silently does nothing.
    """
    if user_yaml is None:
        user_yaml = Path.home() / ".cqk1af" / "config.yaml"

    layered: dict[str, Any] = {}
    if default_yaml is not None:
        layered = _deep_merge(layered, _load_yaml(default_yaml))
    layered = _deep_merge(layered, _load_yaml(user_yaml))
    if env_file is not None:
        layered = _deep_merge(layered, _overlay_from_kv(_parse_env_file(env_file)))
    layered = _deep_merge(layered, _env_overlay())

    return Settings.model_validate(layered) if layered else Settings()
