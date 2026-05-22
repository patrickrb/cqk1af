"""SmartSDR control-protocol framing.

Wire format (ASCII, newline-terminated):

  Outbound command:   ``C<seq>|<command>\n``
  Inbound reply:      ``R<seq>|<hex-code>|<message>\n``
  Status broadcast:   ``S<handle>|<update>\n``
  Meter broadcast:    ``M<handle>|<meter-data>\n``
  Handle assignment:  ``H<handle>\n`` (first message after connect)
  Version line:       ``V<major>.<minor>.<patch>.<build>\n``

Status updates are key=value tokens separated by spaces, sometimes nested with
``subsystem instance key=value`` form, e.g.:

  S5C1B|slice 0 RF_frequency=14.250000 mode=USB RF_filter=2700

Commands the assistant uses (subset):

  - ``sub slice all`` / ``sub meter all`` / ``sub radio all`` — subscribe to status streams
  - ``slice tune <id> <freq_mhz>``
  - ``slice set <id> mode=<mode> filter_lo=<hz> filter_hi=<hz>``
  - ``xmit <0|1>`` — PTT (MOX)
  - ``info`` — radio info

This module is wire-format only. The asyncio I/O + state lives in tcp_client.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterator


class MessageKind(str, Enum):
    HANDLE = "H"
    VERSION = "V"
    REPLY = "R"
    STATUS = "S"
    METER = "M"
    OTHER = "?"


@dataclass(frozen=True)
class Reply:
    seq: int
    code: int  # 0 = success
    message: str

    @property
    def ok(self) -> bool:
        return self.code == 0


@dataclass(frozen=True)
class StatusUpdate:
    """Parsed S-line payload.

    ``handle`` is the client handle the update is addressed to (hex string;
    ``"0"`` for radio-global updates). ``subsystem`` is e.g. ``slice``,
    ``radio``, ``transmit``, ``interlock``, ``meter``. ``instance`` is the
    index or sub-key (None if absent). ``fields`` holds the key=value pairs.
    """

    handle: str
    subsystem: str
    instance: str | None
    fields: dict[str, str]
    raw: str


@dataclass(frozen=True)
class MeterUpdate:
    raw: str
    # Decoded meter samples: meter_id -> raw_value. Type and unit are
    # established by a prior ``meter list`` query.
    samples: dict[int, int]


# ---------------------------------------------------------------------------
# Outbound
# ---------------------------------------------------------------------------


def encode_command(seq: int, command: str) -> bytes:
    """Encode an outbound command line."""
    if "\n" in command:
        raise ValueError("command may not contain newline")
    return f"C{seq}|{command}\n".encode("ascii", errors="replace")


def cmd_subscribe_all() -> list[str]:
    # Mirrors FlexLib's full subscription list (Radio.cs:1914-1937). Subscribing
    # to ``audio_stream`` / ``dax`` in particular is what tells the radio we're
    # an audio-aware client — without those subs, SmartSDR auto-mutes its PC
    # Audio whenever our bound profile MOXes.
    return [
        "sub radio all",
        "sub slice all",
        "sub meter all",
        "sub tx all",
        "sub atu all",
        "sub amplifier all",
        "sub pan all",
        "sub gps all",
        "sub audio_stream all",
        "sub cwx all",
        "sub xvtr all",
        "sub memories all",
        "sub dax all",
        "sub spot all",
    ]


def cmd_client_set_send_reduced_bw_dax() -> str:
    """Mirror FlexLib's ``client set send_reduced_bw_dax=1`` (Radio.cs:1943).

    Sent on every FlexLib session including non-GUI bound ones; harmless when
    we're not using FlexLib's own DAX TX stream and may be one of the markers
    SmartSDR uses to recognize a known/audio-aware client.
    """
    return "client set send_reduced_bw_dax=1"


def cmd_client_udpport(port: int) -> str:
    """Register our local UDP port with the radio (mirrors FlexLib's
    ``client udpport <port>`` at ``Radio.cs:1951``).

    The radio uses this to know where to send VITA-49 audio/stream packets
    for our client. Even though we don't consume RX audio, without this the
    radio appears to mishandle audio routing on the shared bound profile
    when MOX fires — SmartSDR's own ``remote_audio_rx`` stream stops
    flowing until the operator toggles PC Audio off/on.
    """
    return f"client udpport {int(port)}"


def cmd_keepalive_enable() -> str:
    """Tell the radio to watch for our keepalive pings (Radio.cs:2196).

    Sent once right after connect. Pairs with periodic ``ping
    ms_timestamp=...`` from a background task — without it, the radio
    will tag this client as stale during the multi-second pause that
    SSB voice TX introduces (no other TCP traffic), and SmartSDR's
    ``remote_audio_rx`` stream on the bound profile gets stranded
    when we un-key.
    """
    return "keepalive enable"


def cmd_ping(timestamp_ms: int) -> str:
    """One periodic keepalive ping (Radio.cs:2225).

    FlexLib fires this every 1000ms with a monotonic millisecond
    timestamp. The radio just echoes it back in the reply for RTT
    measurement; the timestamp value itself is opaque to the protocol.
    """
    return f"ping ms_timestamp={int(timestamp_ms)}"


def cmd_set_slice_audio_mute(slice_id: int, muted: bool) -> str:
    """Toggle slice audio_mute (mirrors ``Slice.cs:765``). This is the per-slice
    audio output flag the SmartSDR "PC Audio" UI ends up flipping — the radio
    sets it to 1 on the bound slice when a remote/non-GUI client MOXes, and
    doesn't always clear it on un-key. We restore it ourselves after TX.
    """
    return f"slice set {slice_id} audio_mute={1 if muted else 0}"


def cmd_tune_slice(slice_id: int, freq_hz: int) -> str:
    mhz = freq_hz / 1_000_000.0
    return f"slice tune {slice_id} {mhz:.6f}"


def cmd_set_mode(slice_id: int, mode: str) -> str:
    return f"slice set {slice_id} mode={mode}"


def cmd_set_filter(slice_id: int, low_hz: int, high_hz: int) -> str:
    # v4 firmware uses ``filter_lo`` / ``filter_hi`` (Hz relative to carrier).
    return f"slice set {slice_id} filter_lo={low_hz} filter_hi={high_hz}"


def cmd_mox(on: bool) -> str:
    # v3+ firmware uses the bare ``xmit 1`` / ``xmit 0`` form. The older
    # ``mixer tx mox`` form is accepted (returns 0|OK) on v4 but doesn't
    # actually key MOX — silent failure mode.
    return f"xmit {1 if on else 0}"


def cmd_set_mic_source(source: str) -> str:
    """Set which audio source the radio modulator uses for TX.

    Note: in SmartSDR v4 the mic source is per-client. Registering as a GUI
    client via ``client gui`` automatically gives the new client a transmit
    profile with ``mic_selection=DAX``. The ``mic input`` command exists but
    appears to be a no-op once the per-client profile is set.
    """
    return f"mic input {source}"


def cmd_client_gui() -> str:
    """Register as a GUI client (creates a new per-client TX profile).

    Kept for backwards-compat. Prefer ``cmd_client_bind`` for the MultiFLEX
    pattern: connect as a non-GUI client and bind to SmartSDR's existing GUI
    client. That way TX operations use SmartSDR's already-configured DAX mic
    source instead of a brand-new profile whose ``mic_selection`` the radio
    doesn't always populate.
    """
    return "client gui"


def cmd_client_bind(client_id: str) -> str:
    """Bind this non-GUI session to an existing GUI client's TX profile.

    Mirrors FlexLib's ``Radio.BoundClientID`` setter (see
    ``FlexLib_Core/Radio.cs:13350``). After binding, ``xmit 1``/``xmit 0``
    operates on the bound GUI client's TX profile — so the radio modulates
    from that client's mic source (DAX, when SmartSDR is the bound client
    and the operator has DAX as the mic input).
    """
    return f"client bind client_id={client_id.strip()}"


def cmd_subscribe_clients() -> str:
    """Subscribe to GUI client add/remove/update status broadcasts.

    Must be sent before binding so we receive the existing GUI client list
    (the radio replays it on subscribe).
    """
    return "sub client all"


def cmd_client_station(station: str) -> str:
    """Set our station name (shows up in SmartSDR's client list)."""
    # Strip anything that could mess up the line framing.
    safe = "".join(c for c in station if c.isalnum() or c in "-_") or "CQK1AF"
    return f"client station {safe}"


def cmd_client_program(program: str) -> str:
    """Identify our program to the radio (mirrors ``API.ProgramName`` in
    FlexLib, which is sent as the very first command after the TCP socket
    opens — see ``FlexLib_Core/Radio.cs:1877``).

    Empirically, SmartSDR uses this to decide how to treat MOX events from
    bound clients. Without it, SmartSDR mutes its PC Audio when our bound
    profile MOXes and doesn't auto-restore it on un-key.
    """
    safe = "".join(c for c in program if c.isalnum() or c in "-_") or "CQK1AF"
    return f"client program {safe}"


def cmd_info() -> str:
    return "info"


# ---------------------------------------------------------------------------
# Inbound
# ---------------------------------------------------------------------------


_HANDLE_RE = re.compile(r"^H([0-9A-Fa-f]+)$")
_VERSION_RE = re.compile(r"^V(.+)$")
_REPLY_RE = re.compile(r"^R(\d+)\|([0-9A-Fa-f]+)\|(.*)$")
_STATUS_RE = re.compile(r"^S([0-9A-Fa-f]+)\|(.+)$")
_METER_RE = re.compile(r"^M([0-9A-Fa-f]+)\|(.+)$")


def classify_line(line: str) -> tuple[MessageKind, str]:
    """Classify a single line; return (kind, payload-without-prefix)."""
    if not line:
        return (MessageKind.OTHER, "")
    head = line[0]
    if head == "H":
        m = _HANDLE_RE.match(line)
        return (MessageKind.HANDLE, m.group(1) if m else "")
    if head == "V":
        m = _VERSION_RE.match(line)
        return (MessageKind.VERSION, m.group(1) if m else "")
    if head == "R":
        m = _REPLY_RE.match(line)
        return (MessageKind.REPLY, line if m else "")
    if head == "S":
        m = _STATUS_RE.match(line)
        return (MessageKind.STATUS, line if m else "")
    if head == "M":
        m = _METER_RE.match(line)
        return (MessageKind.METER, line if m else "")
    return (MessageKind.OTHER, line)


def parse_reply(line: str) -> Reply | None:
    m = _REPLY_RE.match(line)
    if not m:
        return None
    return Reply(seq=int(m.group(1)), code=int(m.group(2), 16), message=m.group(3))


def parse_status(line: str) -> StatusUpdate | None:
    m = _STATUS_RE.match(line)
    if not m:
        return None
    handle = m.group(1)
    body = m.group(2)
    tokens = _split_status_tokens(body)
    if not tokens:
        return StatusUpdate(handle=handle, subsystem="", instance=None, fields={}, raw=line)
    subsystem = tokens[0]
    # Some updates have an instance index (digit), some have a sub-key word.
    instance: str | None = None
    field_start = 1
    if len(tokens) > 1 and "=" not in tokens[1]:
        instance = tokens[1]
        field_start = 2
    fields: dict[str, str] = {}
    for tok in tokens[field_start:]:
        if "=" not in tok:
            continue
        k, _, v = tok.partition("=")
        fields[k] = v
    return StatusUpdate(
        handle=handle, subsystem=subsystem, instance=instance, fields=fields, raw=line
    )


def parse_info_reply(message: str) -> dict[str, str]:
    """Parse the comma-separated key=value blob returned by ``info``.

    Example payload (after stripping the ``R<seq>|0|`` prefix):

        model="FLEX-6400",chassis_serial="0524-1104-6400-1900",name="jigglebilly",
        callsign="K1AF",software_ver=4.2.18.41174,mac=00:1C:2D:05:2C:94,...

    Quotes around values are stripped.
    """
    out: dict[str, str] = {}
    cur_key: list[str] = []
    cur_val: list[str] = []
    in_quote = False
    have_eq = False
    i = 0
    while i < len(message):
        ch = message[i]
        if ch == '"':
            in_quote = not in_quote
            i += 1
            continue
        if ch == "," and not in_quote:
            if have_eq:
                out[("".join(cur_key)).strip()] = "".join(cur_val).strip()
            cur_key.clear()
            cur_val.clear()
            have_eq = False
            i += 1
            continue
        if ch == "=" and not in_quote and not have_eq:
            have_eq = True
            i += 1
            continue
        if have_eq:
            cur_val.append(ch)
        else:
            cur_key.append(ch)
        i += 1
    if have_eq:
        out[("".join(cur_key)).strip()] = "".join(cur_val).strip()
    return out


def parse_meter(line: str) -> MeterUpdate | None:
    """Meter packets are usually binary VITA-49 over UDP, not over TCP.
    Some firmware versions emit ASCII summaries on the TCP stream as a
    convenience. We parse what we can and pass the rest through unchanged.
    """
    m = _METER_RE.match(line)
    if not m:
        return None
    body = m.group(2)
    samples: dict[int, int] = {}
    for tok in body.split():
        if "=" in tok:
            k, _, v = tok.partition("=")
            try:
                samples[int(k)] = int(v)
            except ValueError:
                pass
    return MeterUpdate(raw=line, samples=samples)


def _split_status_tokens(body: str) -> list[str]:
    """Split a status body into tokens, respecting double-quoted strings."""
    out: list[str] = []
    cur: list[str] = []
    in_quote = False
    for ch in body:
        if ch == '"':
            in_quote = not in_quote
            cur.append(ch)
            continue
        if ch.isspace() and not in_quote:
            if cur:
                out.append("".join(cur))
                cur.clear()
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


def iter_lines(buffer: bytes) -> tuple[list[str], bytes]:
    """Pull complete \\n-terminated lines from a buffer; return (lines, remainder).

    Discards \\r. Empty lines are skipped.
    """
    lines: list[str] = []
    parts = buffer.split(b"\n")
    remainder = parts[-1]
    for raw in parts[:-1]:
        line = raw.replace(b"\r", b"").decode("ascii", errors="replace").strip()
        if line:
            lines.append(line)
    return lines, remainder
