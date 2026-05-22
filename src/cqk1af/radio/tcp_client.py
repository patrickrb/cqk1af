"""SmartSDR TCP control-protocol radio client.

Implements ``RadioClient`` against the radio's native text protocol on port
4992. No SmartSDR dependency, no pythonnet, no embedded .NET — just asyncio
+ sockets.

Connection lifecycle:

  1. ``connect()`` opens TCP to ``host:4992``, reads the ``V`` version line and
     ``H<handle>`` line.
  2. Subscriptions are sent for radio, slice, meter, and TX status streams.
  3. A background reader task consumes lines and dispatches replies, status
     updates, and meter packets onto the asyncio event bus.

UDP-stream details (VITA-49 panadapter / IQ / DAX audio) are out of scope —
DAX audio is reached via the WASAPI device that SmartSDR exposes, which the
``AudioPipeline`` already handles.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from ..config import RigMode
from ..events import (
    EventBus,
    PttChanged,
    RadioConnected,
    RadioDisconnected,
    SliceUpdated,
    SMeterReading,
)
from ..util.logging import get_logger
from .base import GateToken, KILL_TOKEN, RadioClient, TxNotPermitted
from .slice_model import RadioState, SliceState
from .tcp_protocol import (
    MessageKind,
    Reply,
    StatusUpdate,
    classify_line,
    cmd_client_bind,
    cmd_client_program,
    cmd_client_set_send_reduced_bw_dax,
    cmd_client_station,
    cmd_client_udpport,
    cmd_info,
    cmd_keepalive_enable,
    cmd_mox,
    cmd_ping,
    cmd_set_filter,
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


class _UdpDrainProtocol(asyncio.DatagramProtocol):
    """Bind a UDP port and silently drop every packet.

    The radio sends VITA-49 audio/stream packets to the port we register
    via ``client udpport``. We don't need them — we're a control-plane
    client only — but the OS would otherwise emit ICMP port-unreachable
    back to the radio for each packet, which the radio treats as a sign
    the client is gone and can break SmartSDR's parallel audio routing
    on the shared bound profile.
    """

    def datagram_received(self, _data: bytes, _addr: tuple[str, int]) -> None:  # noqa: D401
        return

    def error_received(self, exc: Exception) -> None:  # noqa: D401
        log.debug("tcp_radio.udp_drain_error", error=str(exc))

log = get_logger(__name__)

DEFAULT_PORT = 4992


@dataclass
class TcpRadioConfig:
    host: str
    port: int = DEFAULT_PORT
    connect_timeout_s: float = 5.0
    command_timeout_s: float = 5.0


class TcpRadioClient(RadioClient):
    """Real-radio adapter via the SmartSDR TCP protocol."""

    def __init__(self, bus: EventBus, cfg: TcpRadioConfig) -> None:
        self.bus = bus
        self.cfg = cfg
        self._state = RadioState()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task | None = None
        self._connect_lock = asyncio.Lock()
        self._seq = 0
        self._pending: dict[int, asyncio.Future[Reply]] = {}
        self._handle: str | None = None
        self._interlock = None  # type: ignore[assignment]
        # The mic source the radio had before we took control. We switch it to
        # DAX during our session and restore the operator's original on
        # disconnect.
        self._original_mic_source: str | None = None
        # Tracked from transmit-subsystem status updates.
        self._current_mic_source: str | None = None
        # MultiFLEX: we connect as a non-GUI session and bind to SmartSDR's
        # existing GUI client so TX uses its already-configured DAX mic
        # source. Populated from ``S<h>|client <ch> connected client_id=...``
        # status updates after we send ``sub client all``.
        self._bound_client_id: str | None = None
        self._client_seen_event: asyncio.Event | None = None
        # UDP listener for `client udpport` registration. We don't process
        # the packets — see `_UdpDrainProtocol`.
        self._udp_transport: asyncio.DatagramTransport | None = None
        self._udp_port: int | None = None
        # Keepalive ping loop. Without periodic pings the radio tags us as
        # stale during multi-second TX silences and won't restore SmartSDR's
        # `remote_audio_rx` flow on un-key.
        self._ping_task: asyncio.Task | None = None
        self._ping_interval_s: float = 1.0

    def bind_interlock(self, interlock) -> None:  # type: ignore[no-untyped-def]
        self._interlock = interlock

    @property
    def state(self) -> RadioState:
        return self._state

    # ---- lifecycle -------------------------------------------------

    async def connect(self) -> None:
        async with self._connect_lock:
            if self._state.connected:
                return
            try:
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self.cfg.host, self.cfg.port),
                    timeout=self.cfg.connect_timeout_s,
                )
            except (asyncio.TimeoutError, OSError) as e:
                raise ConnectionError(
                    f"Could not connect to {self.cfg.host}:{self.cfg.port}: {e}"
                ) from e
            self._state.connected = True
            self._bound_client_id = None
            self._client_seen_event = asyncio.Event()
            self._reader_task = asyncio.create_task(self._read_loop(), name="tcp_radio.reader")

            # Open a passive UDP listener BEFORE binding so the radio knows
            # our packet endpoint before any TX state changes. Failure here
            # is non-fatal — we just won't send ``client udpport`` later.
            await self._open_udp_listener()

            # FlexLib's very first command after the socket opens is
            # ``client program <ProgramName>`` (see ``Radio.cs:1877``). The
            # radio uses this to identify the client by program name, and
            # SmartSDR keys its "remote client active" behavior off it —
            # without ``client program`` SmartSDR mutes its PC Audio during
            # our MOX and doesn't restore it on un-key. Send it before
            # anything else, including the bind dance.
            try:
                await self._send_command(cmd_client_program("CQK1AF"), await_reply=False)
            except Exception:
                log.exception("tcp_radio.client_program_failed")

            # MultiFLEX bind path (mirrors smartsdr-mcp's
            # ``RadioManager.OnGuiClientAdded`` → ``radio.BoundClientID``).
            # We connect as a non-GUI client and bind to SmartSDR's existing
            # GUI client. Subsequent ``xmit 1`` operates on SmartSDR's TX
            # profile, which the operator has already configured with DAX as
            # the mic source — so audio written to the Windows "DAX Audio TX"
            # device actually modulates the carrier.
            #
            # Sequence: subscribe to clients → wait for the radio to replay
            # the existing GUI client list → grab the first ``client_id`` we
            # see → ``client bind client_id=<id>`` → continue with the rest
            # of the subscriptions.
            try:
                await self._send_command(cmd_subscribe_clients(), await_reply=False)
            except Exception:
                log.exception("tcp_radio.sub_client_failed")

            try:
                await asyncio.wait_for(self._client_seen_event.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                log.warning(
                    "tcp_radio.no_gui_client_found",
                    hint=(
                        "No GUI client (SmartSDR) reported within 3s. TX will be "
                        "silent because nothing has DAX as its mic source. Start "
                        "SmartSDR + DAX before connecting CQK1AF."
                    ),
                )

            if self._bound_client_id is not None:
                try:
                    bind_reply = await self._send_command(
                        cmd_client_bind(self._bound_client_id)
                    )
                    if bind_reply is not None and bind_reply.ok:
                        log.info(
                            "tcp_radio.bound_to_gui_client",
                            client_id=self._bound_client_id,
                        )
                    else:
                        log.warning(
                            "tcp_radio.client_bind_failed",
                            client_id=self._bound_client_id,
                            code=bind_reply.code if bind_reply else None,
                            message=bind_reply.message if bind_reply else None,
                        )
                except Exception:
                    log.exception("tcp_radio.client_bind_error")

            # Identify ourselves so the operator can see who we are in
            # SmartSDR's client list. Failure here is non-fatal.
            try:
                await self._send_command(cmd_client_station("CQK1AF"))
            except Exception:
                pass

            # FlexLib sends ``client set send_reduced_bw_dax=1`` after
            # binding (Radio.cs:1943). Cheap to send; SmartSDR may use the
            # presence of this flag to decide we're an audio-aware client.
            try:
                await self._send_command(
                    cmd_client_set_send_reduced_bw_dax(), await_reply=False
                )
            except Exception:
                log.exception("tcp_radio.send_reduced_bw_dax_failed")

            # Tell the radio our UDP port (mirrors FlexLib Radio.cs:1951).
            # Without this the radio appears to mishandle the shared bound
            # profile's audio routing on MOX — SmartSDR's `remote_audio_rx`
            # stream stops flowing until PC Audio is toggled off/on.
            if self._udp_port is not None:
                try:
                    await self._send_command(
                        cmd_client_udpport(self._udp_port), await_reply=False
                    )
                except Exception:
                    log.exception("tcp_radio.client_udpport_failed")

            # Keepalive: tell the radio to watch for our pings, then start a
            # background task that sends ``ping ms_timestamp=<ms>`` every
            # second. Mirrors FlexLib's ``StartKeepAlive`` (Radio.cs:1963 /
            # :2196 / :2225). Required for the radio to keep audio routing
            # healthy during multi-second voice TX silences on this TCP
            # control session.
            try:
                await self._send_command(cmd_keepalive_enable(), await_reply=False)
            except Exception:
                log.exception("tcp_radio.keepalive_enable_failed")
            self._start_ping_loop()

            # Subscribe to everything else.
            for sub in cmd_subscribe_all():
                await self._send_command(sub, await_reply=False)
            # ``info`` returns a CSV blob with model/serial/callsign/etc.
            try:
                info_reply = await self._send_command(cmd_info())
                if info_reply is not None and info_reply.ok:
                    info = parse_info_reply(info_reply.message)
                    if "model" in info:
                        self._state.model = info["model"]
                    if "chassis_serial" in info:
                        self._state.serial = info["chassis_serial"]
                    if "software_ver" in info:
                        self._state.version = info["software_ver"]
            except (TimeoutError, Exception):
                log.warning("tcp_radio.info_failed")

            await self.bus.publish(
                RadioConnected(
                    model=self._state.model or "FlexRadio (TCP)",
                    version=self._state.version,
                    serial=self._state.serial,
                )
            )

    async def disconnect(self) -> None:
        async with self._connect_lock:
            if not self._state.connected:
                return
            # Stop the ping loop first so it can't race a sync write from
            # `force_ptt_off` or a cancelled `_send_command` from later steps.
            await self._stop_ping_loop()
            try:
                # Best-effort: ensure PTT is released on the way out.
                self.force_ptt_off()
            except Exception:
                pass
            # No mic-source restore needed: as a GUI client we have our own
            # per-client transmit profile, and the radio cleans it up when our
            # connection drops.
            if self._reader_task is not None:
                self._reader_task.cancel()
                try:
                    await self._reader_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._reader_task = None
            if self._writer is not None:
                try:
                    self._writer.close()
                    await self._writer.wait_closed()
                except Exception:
                    pass
            self._writer = None
            self._reader = None
            self._close_udp_listener()
            self._state.connected = False
            self._handle = None
            await self.bus.publish(RadioDisconnected(reason="requested"))

    # ---- slice control --------------------------------------------

    async def list_slices(self) -> list[SliceState]:
        return list(self._state.slices.values())

    async def tune(self, slice_id: int, freq_hz: int) -> SliceState:
        await self._send_command(cmd_tune_slice(slice_id, freq_hz))
        s = self._state.slices.setdefault(slice_id, SliceState(slice_id=slice_id, freq_hz=freq_hz, mode="USB"))
        s.freq_hz = freq_hz
        await self._publish_slice(s)
        return s

    async def set_mode(self, slice_id: int, mode: RigMode) -> SliceState:
        await self._send_command(cmd_set_mode(slice_id, mode))
        s = self._state.slices.setdefault(slice_id, SliceState(slice_id=slice_id, freq_hz=0, mode=mode))
        s.mode = mode
        await self._publish_slice(s)
        return s

    async def set_filter(self, slice_id: int, low_hz: int, high_hz: int) -> SliceState:
        await self._send_command(cmd_set_filter(slice_id, low_hz, high_hz))
        s = self._state.slices.setdefault(slice_id, SliceState(slice_id=slice_id, freq_hz=0, mode="USB"))
        s.filter_low_hz = low_hz
        s.filter_high_hz = high_hz
        await self._publish_slice(s)
        return s

    async def read_s_meter(self, slice_id: int) -> float:
        s = self._state.slices.get(slice_id)
        if s is None:
            return -120.0
        return s.s_meter_dbm

    # ---- TX -------------------------------------------------------

    async def set_ptt(self, on: bool, *, gate_token: GateToken | None = None) -> None:
        if on:
            if gate_token is None or gate_token is KILL_TOKEN:
                raise TxNotPermitted("set_ptt(True) requires a GateToken from the interlock")
            if gate_token.is_expired():
                raise TxNotPermitted("GateToken expired")
            if self._interlock is not None and not self._interlock.is_token_valid(gate_token):
                raise TxNotPermitted("GateToken not issued by interlock or already consumed")
            if self._interlock is not None:
                self._interlock.consume_token(gate_token)
        await self._send_command(cmd_mox(on))
        self._state.ptt_on = on
        await self.bus.publish(PttChanged(on=on))

        # On un-key, restore the bound slice's audio output. When CQK1AF
        # (a non-GUI bound client) MOXes, the radio sets ``audio_mute=1``
        # on SmartSDR's bound slice and doesn't reliably clear it on
        # ``xmit 0`` — which manifests as SmartSDR's "PC Audio" button
        # going off and staying off until the operator clicks it back on.
        # Sending ``audio_mute=0`` is exactly what that button does.
        if not on:
            for slice_id in list(self._state.slices.keys()):
                try:
                    await self._send_command(
                        cmd_set_slice_audio_mute(slice_id, False),
                        await_reply=False,
                    )
                except Exception:
                    log.exception(
                        "tcp_radio.audio_mute_restore_failed",
                        slice_id=slice_id,
                    )

    def force_ptt_off(self) -> None:
        """Synchronous best-effort PTT release. Used by the kill switch.

        We can't await here (the kill path may not have an event loop
        available), so we write directly to the stream and rely on TCP
        buffering. If the radio is gone we swallow the error.
        """
        if self._writer is None:
            return
        try:
            self._writer.write(encode_command(self._next_seq(), cmd_mox(False)))
            # Don't await drain — the writer.write call is non-blocking up to
            # the TCP buffer.
        except Exception:
            log.exception("tcp_radio.force_ptt_off_failed")
        self._state.ptt_on = False
        self.bus.publish_nowait(PttChanged(on=False))

    # ---- internals: I/O -------------------------------------------

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _start_ping_loop(self) -> None:
        if self._ping_task is not None and not self._ping_task.done():
            return
        self._ping_task = asyncio.create_task(self._ping_loop(), name="tcp_radio.ping")

    async def _stop_ping_loop(self) -> None:
        task = self._ping_task
        self._ping_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def _ping_loop(self) -> None:
        """Send ``ping ms_timestamp=<elapsed_ms>`` every second while connected.

        Mirrors ``KeepaliveTimerLoopTask`` in ``Radio.cs:2204``. We use
        ``await_reply=False`` — the radio will reply, but we don't need to
        match the response; ``_handle_line`` drops unmatched replies. The
        important thing is that bytes flow on the TCP session so the radio
        doesn't tag us as stale during a long TX.
        """
        start = time.monotonic()
        try:
            while True:
                await asyncio.sleep(self._ping_interval_s)
                if not self._state.connected or self._writer is None:
                    return
                elapsed_ms = int((time.monotonic() - start) * 1000)
                try:
                    await self._send_command(
                        cmd_ping(elapsed_ms), await_reply=False
                    )
                except Exception:
                    log.exception("tcp_radio.ping_failed")
                    return
        except asyncio.CancelledError:
            return

    async def _open_udp_listener(self) -> None:
        """Bind a local UDP port so we have an endpoint to register with the
        radio via ``client udpport``. Best-effort: on failure we log and
        proceed without registering — the rest of the client still works.
        """
        try:
            loop = asyncio.get_running_loop()
            transport, _proto = await loop.create_datagram_endpoint(
                _UdpDrainProtocol,
                local_addr=("0.0.0.0", 0),
            )
        except Exception:
            log.exception("tcp_radio.udp_open_failed")
            self._udp_transport = None
            self._udp_port = None
            return
        sockname = transport.get_extra_info("sockname")
        port = int(sockname[1]) if sockname else 0
        if port == 0:
            transport.close()
            log.warning("tcp_radio.udp_open_no_port")
            self._udp_transport = None
            self._udp_port = None
            return
        self._udp_transport = transport
        self._udp_port = port
        log.info("tcp_radio.udp_listener_ready", port=port)

    def _close_udp_listener(self) -> None:
        if self._udp_transport is not None:
            try:
                self._udp_transport.close()
            except Exception:
                log.exception("tcp_radio.udp_close_failed")
        self._udp_transport = None
        self._udp_port = None

    async def _send_command(self, command: str, *, await_reply: bool = True) -> Reply | None:
        if self._writer is None:
            raise RuntimeError("Radio not connected")
        seq = self._next_seq()
        fut: asyncio.Future[Reply] | None = None
        if await_reply:
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            self._pending[seq] = fut
        try:
            self._writer.write(encode_command(seq, command))
            await self._writer.drain()
        except Exception:
            if fut is not None:
                self._pending.pop(seq, None)
            raise
        if fut is None:
            return None
        try:
            return await asyncio.wait_for(fut, timeout=self.cfg.command_timeout_s)
        except asyncio.TimeoutError:
            self._pending.pop(seq, None)
            raise TimeoutError(f"No reply to command (seq={seq}): {command}")

    async def _read_loop(self) -> None:
        assert self._reader is not None
        buf = b""
        try:
            while True:
                chunk = await self._reader.read(4096)
                if not chunk:
                    break
                buf += chunk
                lines, buf = iter_lines(buf)
                for line in lines:
                    await self._handle_line(line)
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("tcp_radio.read_loop_error")
        finally:
            # Connection lost.
            self._state.connected = False
            if self._ping_task is not None and not self._ping_task.done():
                self._ping_task.cancel()
            self._close_udp_listener()
            await self.bus.publish(RadioDisconnected(reason="connection_lost"))

    async def _handle_line(self, line: str) -> None:
        kind, _payload = classify_line(line)
        if kind is MessageKind.HANDLE:
            self._handle = line[1:]
            log.info("tcp_radio.handle", handle=self._handle)
            return
        if kind is MessageKind.VERSION:
            self._state.version = line[1:]
            log.info("tcp_radio.version", version=self._state.version)
            return
        if kind is MessageKind.REPLY:
            reply = parse_reply(line)
            if reply is None:
                return
            fut = self._pending.pop(reply.seq, None)
            if fut is not None and not fut.done():
                fut.set_result(reply)
            if not reply.ok:
                log.warning("tcp_radio.cmd_failed", seq=reply.seq, code=reply.code, msg=reply.message)
            return
        if kind is MessageKind.STATUS:
            su = parse_status(line)
            if su is not None:
                await self._handle_status(su)
            return
        if kind is MessageKind.METER:
            mu = parse_meter(line)
            if mu is not None:
                await self._handle_meter(mu)
            return
        # Unknown — log and move on.
        log.debug("tcp_radio.unknown_line", line=line)

    async def _handle_status(self, su: StatusUpdate) -> None:
        if su.subsystem == "slice" and su.instance is not None:
            try:
                slice_id = int(su.instance)
            except ValueError:
                return
            s = self._state.slices.setdefault(
                slice_id, SliceState(slice_id=slice_id, freq_hz=0, mode="USB")
            )
            if "RF_frequency" in su.fields:
                try:
                    s.freq_hz = int(round(float(su.fields["RF_frequency"]) * 1_000_000))
                except ValueError:
                    pass
            if "freq" in su.fields:
                try:
                    s.freq_hz = int(round(float(su.fields["freq"]) * 1_000_000))
                except ValueError:
                    pass
            if "mode" in su.fields:
                s.mode = su.fields["mode"]
            # Filter field names: v4 firmware uses ``filter_lo`` / ``filter_hi`` (in Hz
            # relative to carrier; LSB values are negative). Older docs say
            # ``RF_filter_low`` / ``RF_filter_high`` — accept either for safety.
            for low_key in ("filter_lo", "RF_filter_low"):
                if low_key in su.fields:
                    try:
                        s.filter_low_hz = int(su.fields[low_key])
                    except ValueError:
                        pass
                    break
            for high_key in ("filter_hi", "RF_filter_high"):
                if high_key in su.fields:
                    try:
                        s.filter_high_hz = int(su.fields[high_key])
                    except ValueError:
                        pass
                    break
            if "rxant" in su.fields:
                s.rxant = su.fields["rxant"]
            await self._publish_slice(s)
            return
        if su.subsystem == "radio":
            if "model" in su.fields:
                self._state.model = su.fields["model"]
            if "serial" in su.fields:
                self._state.serial = su.fields["serial"]
            return
        if su.subsystem == "client":
            # Status form: ``S<our_h>|client <gui_handle> connected client_id=<id>
            # program=<name> station=<station> local_ptt=<0|1>``. The radio
            # replays the current GUI client list when we ``sub client all``.
            # We bind to the first one we see (SmartSDR, in single-GUI setups).
            client_id = su.fields.get("client_id")
            if client_id and self._bound_client_id is None:
                self._bound_client_id = client_id
                log.info(
                    "tcp_radio.gui_client_seen",
                    client_handle=su.instance,
                    client_id=client_id,
                    program=su.fields.get("program", ""),
                    station=su.fields.get("station", ""),
                )
                if self._client_seen_event is not None:
                    self._client_seen_event.set()
            return
        if su.subsystem == "transmit" or su.subsystem == "interlock":
            # ``transmit`` is per-client — each S-line is addressed to one
            # client handle. Only accept updates addressed to OUR handle so
            # another SmartSDR client's MIC selection doesn't overwrite our
            # DAX selection in the status report.
            addressed_to_us = self._handle is not None and (
                su.handle.upper() == self._handle.upper()
            )
            if "mox" in su.fields and (su.subsystem == "interlock" or addressed_to_us):
                on = su.fields["mox"].lower() in ("1", "true")
                if on != self._state.ptt_on:
                    self._state.ptt_on = on
                    await self.bus.publish(PttChanged(on=on))
            if "mic_selection" in su.fields:
                log.info(
                    "tcp_radio.mic_selection_seen",
                    handle=su.handle,
                    our_handle=self._handle,
                    addressed_to_us=addressed_to_us,
                    value=su.fields["mic_selection"],
                )
            if "mic_selection" in su.fields and addressed_to_us:
                self._current_mic_source = su.fields["mic_selection"]
                self._state.mic_source = su.fields["mic_selection"]
            return
        # Other subsystems (waveform, profiles, etc.) — ignore for now.

    async def _handle_meter(self, mu) -> None:  # noqa: ANN001
        # Some firmware emits ASCII slice S-meter updates as part of the
        # ``slice`` status stream; the binary VITA-49 meter stream is over UDP.
        # When we see ASCII meter samples we map by convention: meter_id mod 8
        # ≈ slice index. This is a heuristic and will be replaced once we wire
        # the UDP meter listener; for now it lets the dashboard show something.
        for meter_id, value in mu.samples.items():
            slice_id = meter_id & 0x7
            s = self._state.slices.get(slice_id)
            if s is None:
                continue
            # FlexRadio S-meter wire value is dBm * 128 (Q7 signed). Decode.
            dbm = value / 128.0
            s.s_meter_dbm = dbm
            self.bus.publish_nowait(SMeterReading(slice_id=slice_id, dbm=dbm))

    async def _publish_slice(self, s: SliceState) -> None:
        await self.bus.publish(
            SliceUpdated(
                slice_id=s.slice_id,
                freq_hz=s.freq_hz,
                mode=s.mode,
                filter_low_hz=s.filter_low_hz,
                filter_high_hz=s.filter_high_hz,
                rxant=s.rxant,
            )
        )
