"""Pydantic schemas for the dashboard REST + WS interface."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..mcp_server.tools import HeardCallsignDTO, QSOContextDTO, RadioStatus, SessionStateDTO, SliceDTO


# ---- REST ---------------------------------------------------------------


class ArmRequest(BaseModel):
    armed: bool


class KillRequest(BaseModel):
    reason: str = "operator requested"


class ApproveTxRequest(BaseModel):
    req_id: str
    approved: bool


class SelectCallerRequest(BaseModel):
    callsign: str


class TuneRequest(BaseModel):
    freq_hz: int
    mode: Literal["USB", "LSB"]
    filter_low_hz: int = 200
    filter_high_hz: int = 2900
    slice_id: int = 0


class FieldUpdateRequest(BaseModel):
    field: str
    value: str


class SettingsUpdateRequest(BaseModel):
    """Subset of Settings that the dashboard is allowed to edit at runtime."""

    operator_callsign: str | None = None
    operator_name: str | None = None
    operator_qth: str | None = None
    operator_grid_square: str | None = None
    operator_license_class: Literal["Extra", "Advanced", "General", "Technician", "Novice"] | None = (
        None
    )
    safety_require_manual_tx_approval: bool | None = None
    safety_approval_timeout_s: int | None = None
    cq_max_attempts: int | None = None
    cq_listen_seconds: int | None = None
    cq_template_id: str | None = None
    cloudlog_station_profile_id: int | None = None


# ---- WS payloads (server -> client) -------------------------------------

WSType = Literal[
    "hello",
    "state_changed",
    "radio_slice",
    "s_meter",
    "transcript",
    "callsigns_heard",
    "tx_approval_requested",
    "qso_started",
    "qso_updated",
    "qso_logged",
    "error",
]


class WSMessage(BaseModel):
    type: WSType
    payload: dict[str, Any] = Field(default_factory=dict)


# ---- WS payloads (client -> server) -------------------------------------


class WSClientMessage(BaseModel):
    type: Literal[
        "approve_tx",
        "kill",
        "arm_session",
        "stop_session",
        "set_field",
        "select_caller",
    ]
    payload: dict[str, Any] = Field(default_factory=dict)


# ---- Response models ----------------------------------------------------


class HelloPayload(BaseModel):
    session_id: str
    state: SessionStateDTO
    radio: RadioStatus


# Re-export for convenience
__all__ = [
    "ApproveTxRequest",
    "ArmRequest",
    "FieldUpdateRequest",
    "HelloPayload",
    "HeardCallsignDTO",
    "KillRequest",
    "QSOContextDTO",
    "RadioStatus",
    "SelectCallerRequest",
    "SessionStateDTO",
    "SettingsUpdateRequest",
    "SliceDTO",
    "TuneRequest",
    "WSClientMessage",
    "WSMessage",
]
