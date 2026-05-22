"""ADIF (Amateur Data Interchange Format) serializer.

ADIF 3.1.0 fields used:
  CALL, QSO_DATE, TIME_ON, QSO_DATE_OFF, TIME_OFF, BAND, FREQ, MODE,
  RST_SENT, RST_RCVD, NAME, QTH, GRIDSQUARE, COMMENT, STATION_CALLSIGN,
  MY_NAME, MY_GRIDSQUARE, APP_CQK1AF_AI_ASSISTED.

Format reference: https://adif.org/315/ADIF_315.htm
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .sqlite_store import QSORecord

ADIF_VERSION = "3.1.0"


def _field(name: str, value: str | int) -> str:
    s = str(value)
    return f"<{name.upper()}:{len(s)}>{s}"


def _hz_to_mhz(hz: int) -> str:
    return f"{hz / 1_000_000:.5f}"


def _parse_ts(ts: str) -> tuple[str, str]:
    """ISO timestamp -> (YYYYMMDD, HHMMSS)."""
    if not ts:
        return ("", "")
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ("", "")
    return (dt.strftime("%Y%m%d"), dt.strftime("%H%M%S"))


def adif_header(*, programid: str = "CQK1AF", version: str = "0.1.0") -> str:
    return (
        "CQK1AF ADIF export\n"
        + _field("ADIF_VER", ADIF_VERSION)
        + " "
        + _field("PROGRAMID", programid)
        + " "
        + _field("PROGRAMVERSION", version)
        + " <EOH>\n"
    )


def adif_record(
    qso: QSORecord,
    *,
    station_callsign: str = "",
    my_name: str = "",
    my_grid: str = "",
) -> str:
    parts: list[str] = []
    date_on, time_on = _parse_ts(qso.start_ts)
    date_off, time_off = _parse_ts(qso.end_ts or qso.start_ts)
    parts.append(_field("CALL", qso.callsign))
    if date_on:
        parts.append(_field("QSO_DATE", date_on))
        parts.append(_field("TIME_ON", time_on))
    if date_off:
        parts.append(_field("QSO_DATE_OFF", date_off))
        parts.append(_field("TIME_OFF", time_off))
    if qso.band:
        parts.append(_field("BAND", qso.band))
    if qso.freq_hz:
        parts.append(_field("FREQ", _hz_to_mhz(qso.freq_hz)))
    if qso.mode:
        parts.append(_field("MODE", qso.mode))
    if qso.rst_sent:
        parts.append(_field("RST_SENT", qso.rst_sent))
    if qso.rst_rcvd:
        parts.append(_field("RST_RCVD", qso.rst_rcvd))
    if qso.name:
        parts.append(_field("NAME", qso.name))
    if qso.qth:
        parts.append(_field("QTH", qso.qth))
    if qso.grid:
        parts.append(_field("GRIDSQUARE", qso.grid))
    if qso.comment:
        parts.append(_field("COMMENT", qso.comment))
    if station_callsign:
        parts.append(_field("STATION_CALLSIGN", station_callsign))
    if my_name:
        parts.append(_field("MY_NAME", my_name))
    if my_grid:
        parts.append(_field("MY_GRIDSQUARE", my_grid))
    parts.append(_field("APP_CQK1AF_AI_ASSISTED", "Y" if qso.ai_assisted else "N"))
    return " ".join(parts) + " <EOR>\n"


def serialize(
    qsos: Iterable[QSORecord],
    *,
    station_callsign: str = "",
    my_name: str = "",
    my_grid: str = "",
) -> str:
    out = [adif_header()]
    for q in qsos:
        out.append(
            adif_record(q, station_callsign=station_callsign, my_name=my_name, my_grid=my_grid)
        )
    return "".join(out)
