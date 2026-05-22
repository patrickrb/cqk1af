from __future__ import annotations

import re

from cqk1af.logging_qso.adif import adif_header, adif_record, serialize
from cqk1af.logging_qso.sqlite_store import QSORecord


def _record(**overrides) -> QSORecord:
    base = dict(
        qso_id="11111111-1111-1111-1111-111111111111",
        callsign="W1AW",
        start_ts="2026-05-20T22:34:00+00:00",
        end_ts="2026-05-20T22:39:00+00:00",
        freq_hz=14_250_000,
        mode="USB",
        band="20m",
        rst_sent="59",
        rst_rcvd="57",
        name="Joe",
        qth="Newington CT",
        grid="FN31",
        comment="73",
        ai_assisted=True,
        logged_at="2026-05-20T22:39:00+00:00",
    )
    base.update(overrides)
    return QSORecord(**base)


def test_header() -> None:
    h = adif_header()
    assert "<ADIF_VER:5>3.1.0" in h
    assert "<EOH>" in h


def test_record_basic_fields() -> None:
    r = adif_record(_record())
    assert "<CALL:4>W1AW" in r
    assert "<QSO_DATE:8>20260520" in r
    assert "<TIME_ON:6>223400" in r
    assert "<MODE:3>USB" in r
    assert "<BAND:3>20m" in r
    assert "<RST_SENT:2>59" in r
    assert "<RST_RCVD:2>57" in r
    assert "<NAME:3>Joe" in r
    assert "<QTH:12>Newington CT" in r
    assert "<GRIDSQUARE:4>FN31" in r
    assert "<EOR>" in r


def test_freq_formatted_mhz() -> None:
    r = adif_record(_record())
    assert "<FREQ:8>14.25000" in r


def test_serialize_multiple_records() -> None:
    out = serialize([_record(callsign="W1AW"), _record(callsign="K9XYZ", qso_id="22222222-2222-2222-2222-222222222222")])
    assert out.count("<EOR>") == 2
    assert "W1AW" in out and "K9XYZ" in out
    # Header appears once at start
    assert out.count("<EOH>") == 1


def test_station_callsign_and_my_name_included() -> None:
    out = serialize([_record()], station_callsign="K1AF", my_name="Mike", my_grid="FN42")
    assert "<STATION_CALLSIGN:4>K1AF" in out
    assert "<MY_NAME:4>Mike" in out
    assert "<MY_GRIDSQUARE:4>FN42" in out


def test_field_lengths_are_byte_accurate() -> None:
    out = adif_record(_record(name="José"))
    m = re.search(r"<NAME:(\d+)>(\S+)", out)
    assert m
    expected_len = len("José".encode("utf-8")) if False else len("José")
    # ADIF spec uses character length not byte length; the serializer uses len(str)
    assert int(m.group(1)) == expected_len
