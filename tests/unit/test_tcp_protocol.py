from __future__ import annotations

from cqk1af.radio.tcp_protocol import (
    MessageKind,
    classify_line,
    cmd_client_bind,
    cmd_client_gui,
    cmd_client_program,
    cmd_client_set_send_reduced_bw_dax,
    cmd_client_station,
    cmd_client_udpport,
    cmd_keepalive_enable,
    cmd_mox,
    cmd_ping,
    cmd_set_filter,
    cmd_set_mic_source,
    cmd_set_mode,
    cmd_set_slice_audio_mute,
    cmd_subscribe_all,
    cmd_subscribe_clients,
    cmd_tune_slice,
    encode_command,
    iter_lines,
    parse_info_reply,
    parse_meter,
    parse_reply,
    parse_status,
)


def test_encode_command() -> None:
    assert encode_command(7, "info") == b"C7|info\n"


def test_encode_command_rejects_newline() -> None:
    import pytest

    with pytest.raises(ValueError):
        encode_command(1, "bad\ncommand")


def test_cmd_tune_slice_format() -> None:
    assert cmd_tune_slice(0, 14_250_000) == "slice tune 0 14.250000"


def test_cmd_set_mode_and_filter() -> None:
    assert cmd_set_mode(0, "USB") == "slice set 0 mode=USB"
    assert cmd_set_filter(0, 200, 2900) == "slice set 0 filter_lo=200 filter_hi=2900"


def test_cmd_mox() -> None:
    assert cmd_mox(True) == "xmit 1"
    assert cmd_mox(False) == "xmit 0"


def test_cmd_set_mic_source() -> None:
    assert cmd_set_mic_source("DAX") == "mic input DAX"
    assert cmd_set_mic_source("MIC") == "mic input MIC"


def test_cmd_client_gui_and_station() -> None:
    assert cmd_client_gui() == "client gui"
    assert cmd_client_station("CQK1AF") == "client station CQK1AF"
    # Strips unsafe characters
    assert cmd_client_station("CQK1AF | rogue") == "client station CQK1AFrogue"
    # Empty / all-bad falls back to a default
    assert cmd_client_station("!!!") == "client station CQK1AF"


def test_cmd_client_bind() -> None:
    assert (
        cmd_client_bind("11111111-2222-3333-4444-555555555555")
        == "client bind client_id=11111111-2222-3333-4444-555555555555"
    )
    # Strips surrounding whitespace.
    assert cmd_client_bind("  abc-def  ") == "client bind client_id=abc-def"


def test_cmd_subscribe_clients() -> None:
    assert cmd_subscribe_clients() == "sub client all"


def test_cmd_client_program() -> None:
    assert cmd_client_program("CQK1AF") == "client program CQK1AF"
    # Strips unsafe characters and falls back to the default
    assert cmd_client_program("foo|bar") == "client program foobar"
    assert cmd_client_program("!!!") == "client program CQK1AF"


def test_cmd_client_set_send_reduced_bw_dax() -> None:
    assert cmd_client_set_send_reduced_bw_dax() == "client set send_reduced_bw_dax=1"


def test_cmd_set_slice_audio_mute() -> None:
    assert cmd_set_slice_audio_mute(0, False) == "slice set 0 audio_mute=0"
    assert cmd_set_slice_audio_mute(2, True) == "slice set 2 audio_mute=1"


def test_cmd_client_udpport() -> None:
    assert cmd_client_udpport(54321) == "client udpport 54321"
    assert cmd_client_udpport(4991) == "client udpport 4991"


def test_cmd_keepalive_enable() -> None:
    assert cmd_keepalive_enable() == "keepalive enable"


def test_cmd_ping() -> None:
    assert cmd_ping(0) == "ping ms_timestamp=0"
    assert cmd_ping(1234567) == "ping ms_timestamp=1234567"


def test_cmd_subscribe_all_includes_required_streams() -> None:
    subs = cmd_subscribe_all()
    assert "sub radio all" in subs
    assert "sub slice all" in subs
    assert "sub meter all" in subs


def test_classify_lines() -> None:
    assert classify_line("H5C1B")[0] is MessageKind.HANDLE
    assert classify_line("V4.2.18.41174")[0] is MessageKind.VERSION
    assert classify_line("R1|0|")[0] is MessageKind.REPLY
    assert classify_line("S5C1B|slice 0 RF_frequency=14.250000")[0] is MessageKind.STATUS
    assert classify_line("M5C1B|0=512")[0] is MessageKind.METER
    assert classify_line("garbage")[0] is MessageKind.OTHER


def test_parse_reply_ok() -> None:
    r = parse_reply("R12|0|OK")
    assert r is not None
    assert r.seq == 12
    assert r.code == 0
    assert r.message == "OK"
    assert r.ok is True


def test_parse_reply_error() -> None:
    r = parse_reply("R5|50000018|invalid slice")
    assert r is not None
    assert r.ok is False
    assert r.code == 0x50000018


def test_parse_status_slice_v4_filter_names() -> None:
    s = parse_status("S5C1B|slice 0 in_use=1 RF_frequency=7.157000 mode=LSB filter_lo=-2800 filter_hi=-100")
    assert s is not None
    assert s.subsystem == "slice"
    assert s.instance == "0"
    assert s.fields["RF_frequency"] == "7.157000"
    assert s.fields["mode"] == "LSB"
    assert s.fields["filter_lo"] == "-2800"
    assert s.fields["filter_hi"] == "-100"


def test_parse_info_reply_extracts_model_serial_callsign() -> None:
    info = parse_info_reply(
        'model="FLEX-6400",chassis_serial="0524-1104-6400-1900",name="jigglebilly",'
        'callsign="K1AF",gps="Not Present",software_ver=4.2.18.41174,'
        'mac=00:1C:2D:05:2C:94,ip=192.168.86.159,location="",region="USA"'
    )
    assert info["model"] == "FLEX-6400"
    assert info["chassis_serial"] == "0524-1104-6400-1900"
    assert info["callsign"] == "K1AF"
    assert info["software_ver"] == "4.2.18.41174"
    assert info["mac"] == "00:1C:2D:05:2C:94"
    # Empty quoted value
    assert info["location"] == ""


def test_parse_info_reply_handles_commas_inside_quotes() -> None:
    info = parse_info_reply('model="X",name="some, name",x=1')
    assert info["name"] == "some, name"
    assert info["x"] == "1"


def test_parse_status_radio() -> None:
    s = parse_status('S5C1B|radio model="FLEX-6400" serial=1234-5678')
    assert s is not None
    assert s.subsystem == "radio"
    assert s.fields["model"] == '"FLEX-6400"'
    assert s.fields["serial"] == "1234-5678"


def test_parse_status_transmit_mox() -> None:
    s = parse_status("S5C1B|transmit mox=1")
    assert s is not None
    assert s.subsystem == "transmit"
    assert s.fields["mox"] == "1"


def test_parse_meter_samples() -> None:
    m = parse_meter("M5C1B|1=128 2=-64 5=1024")
    assert m is not None
    assert m.samples == {1: 128, 2: -64, 5: 1024}


def test_iter_lines_handles_partial_buffer() -> None:
    lines, remainder = iter_lines(b"H5C1B\nV4.2.18\nR1|0|OK\nincomplete")
    assert lines == ["H5C1B", "V4.2.18", "R1|0|OK"]
    assert remainder == b"incomplete"


def test_iter_lines_strips_cr() -> None:
    lines, _ = iter_lines(b"H5C1B\r\nV4.2.18\r\n")
    assert lines == ["H5C1B", "V4.2.18"]
