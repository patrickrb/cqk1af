"""TcpRadioClient against an asyncio stub server that emulates the SmartSDR protocol.

Covers:
  - connect handshake (handle + version)
  - subscribe commands are sent and acknowledged
  - tune / set_mode / set_filter round-trip
  - status updates from the server populate slice state and publish bus events
  - meter updates publish SMeterReading events
  - set_ptt(True) requires interlock token; set_ptt(False) is always OK
  - disconnect tears down cleanly
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest

from cqk1af.events import (
    EventBus,
    PttChanged,
    RadioConnected,
    RadioDisconnected,
    SliceUpdated,
    SMeterReading,
)
from cqk1af.radio.base import GateToken, TxNotPermitted
from cqk1af.radio.tcp_client import TcpRadioClient, TcpRadioConfig
from cqk1af.util.time import utcnow


class StubServer:
    """Asyncio TCP server that emulates the SmartSDR protocol.

    On connect: sends a version line + handle. Replies to every command with
    ``R<seq>|0|OK\\n``. Records commands so tests can assert them. Has a
    ``push()`` method to inject status/meter updates to all clients.
    """

    def __init__(self) -> None:
        self.server: asyncio.AbstractServer | None = None
        self.host = "127.0.0.1"
        self.port = 0
        self.commands: list[str] = []
        self._clients: set[asyncio.StreamWriter] = set()

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle_client, host=self.host, port=0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        for w in list(self._clients):
            try:
                w.close()
                await w.wait_closed()
            except Exception:
                pass
        self._clients.clear()

    async def push(self, line: str) -> None:
        line = line if line.endswith("\n") else line + "\n"
        for w in list(self._clients):
            try:
                w.write(line.encode("ascii"))
                await w.drain()
            except Exception:
                self._clients.discard(w)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._clients.add(writer)
        # Send handle + version line per real radio behaviour
        writer.write(b"V4.2.18.0\n")
        writer.write(b"H5C1B0000\n")
        await writer.drain()
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                text = line.decode("ascii", errors="replace").strip()
                self.commands.append(text)
                # Reply to every command with success
                if text.startswith("C"):
                    seq_str = text[1:].split("|", 1)[0]
                    try:
                        seq = int(seq_str)
                    except ValueError:
                        continue
                    writer.write(f"R{seq}|0|OK\n".encode("ascii"))
                    await writer.drain()
                    # When the client subscribes to GUI clients, replay an
                    # existing SmartSDR-like client so the MultiFLEX bind
                    # path completes. Real radios do this automatically.
                    body = text[1:].split("|", 1)[1] if "|" in text else ""
                    if body == "sub client all":
                        writer.write(
                            b"S5C1B0000|client A1B20000 connected "
                            b"client_id=11111111-2222-3333-4444-555555555555 "
                            b"program=SmartSDR station=Shack local_ptt=1\n"
                        )
                        await writer.drain()
        except (asyncio.CancelledError, ConnectionError):
            pass
        finally:
            self._clients.discard(writer)


@pytest.fixture
async def server():
    s = StubServer()
    await s.start()
    yield s
    await s.stop()


def _token(slice_id: int = 0) -> GateToken:
    now = utcnow()
    return GateToken(
        id=uuid.uuid4(),
        slice_id=slice_id,
        purpose="test",
        issued_at=now,
        expires_at=now + timedelta(seconds=30),
    )


@pytest.mark.asyncio
async def test_connect_publishes_radio_connected(server) -> None:
    bus = EventBus()
    seen: list = []

    async def collect(ev):
        seen.append(ev)

    await bus.subscribe("radio.*", collect, name="r")
    client = TcpRadioClient(bus, TcpRadioConfig(host=server.host, port=server.port))
    await client.connect()
    await asyncio.sleep(0.05)
    assert any(isinstance(e, RadioConnected) for e in seen)
    assert client.state.connected is True
    assert client.state.version == "4.2.18.0"
    await client.disconnect()
    await bus.close()


@pytest.mark.asyncio
async def test_connect_sends_subscriptions(server) -> None:
    bus = EventBus()
    client = TcpRadioClient(bus, TcpRadioConfig(host=server.host, port=server.port))
    await client.connect()
    await asyncio.sleep(0.05)
    bodies = [c.split("|", 1)[1] for c in server.commands if c.startswith("C")]
    assert "sub radio all" in bodies
    assert "sub slice all" in bodies
    assert "sub meter all" in bodies
    await client.disconnect()
    await bus.close()


@pytest.mark.asyncio
async def test_tune_sends_command_and_publishes_slice(server) -> None:
    bus = EventBus()
    seen: list[SliceUpdated] = []

    async def collect(ev):
        if isinstance(ev, SliceUpdated):
            seen.append(ev)

    await bus.subscribe("radio.slice", collect, name="r")
    client = TcpRadioClient(bus, TcpRadioConfig(host=server.host, port=server.port))
    await client.connect()
    s = await client.tune(0, 14_250_000)
    assert s.freq_hz == 14_250_000
    await asyncio.sleep(0.05)
    assert any(c.endswith("|slice tune 0 14.250000") for c in server.commands)
    assert any(e.freq_hz == 14_250_000 for e in seen)
    await client.disconnect()
    await bus.close()


@pytest.mark.asyncio
async def test_set_mode_and_filter(server) -> None:
    bus = EventBus()
    client = TcpRadioClient(bus, TcpRadioConfig(host=server.host, port=server.port))
    await client.connect()
    await client.set_mode(0, "USB")
    await client.set_filter(0, 200, 2900)
    await asyncio.sleep(0.05)
    bodies = [c.split("|", 1)[1] for c in server.commands]
    assert "slice set 0 mode=USB" in bodies
    assert "slice set 0 filter_lo=200 filter_hi=2900" in bodies
    await client.disconnect()
    await bus.close()


@pytest.mark.asyncio
async def test_status_update_populates_slice(server) -> None:
    bus = EventBus()
    seen: list[SliceUpdated] = []

    async def collect(ev):
        if isinstance(ev, SliceUpdated):
            seen.append(ev)

    await bus.subscribe("radio.slice", collect, name="r")
    client = TcpRadioClient(bus, TcpRadioConfig(host=server.host, port=server.port))
    await client.connect()
    await asyncio.sleep(0.05)
    await server.push(
        "S5C1B0000|slice 0 RF_frequency=7.200000 mode=LSB filter_lo=-2800 filter_hi=-100"
    )
    await asyncio.sleep(0.05)
    s = client.state.slices[0]
    assert s.freq_hz == 7_200_000
    assert s.mode == "LSB"
    assert s.filter_low_hz == -2800
    assert s.filter_high_hz == -100
    assert any(e.mode == "LSB" for e in seen)
    await client.disconnect()
    await bus.close()


@pytest.mark.asyncio
async def test_meter_update_publishes_s_meter(server) -> None:
    bus = EventBus()
    seen: list[SMeterReading] = []

    async def collect(ev):
        if isinstance(ev, SMeterReading):
            seen.append(ev)

    await bus.subscribe("radio.s_meter", collect, name="r")
    client = TcpRadioClient(bus, TcpRadioConfig(host=server.host, port=server.port))
    await client.connect()
    # Seed slice 0
    await server.push("S5C1B0000|slice 0 RF_frequency=14.250000 mode=USB")
    await asyncio.sleep(0.05)
    # meter_id 0 maps to slice 0 (id & 0x7); value is dBm * 128
    await server.push("M5C1B0000|0=-12800")  # -100 dBm
    await asyncio.sleep(0.05)
    assert seen, "expected an SMeterReading"
    assert abs(seen[-1].dbm + 100.0) < 0.1
    await client.disconnect()
    await bus.close()


@pytest.mark.asyncio
async def test_set_ptt_requires_token(server) -> None:
    bus = EventBus()
    client = TcpRadioClient(bus, TcpRadioConfig(host=server.host, port=server.port))
    await client.connect()
    with pytest.raises(TxNotPermitted):
        await client.set_ptt(True)
    await client.set_ptt(False)  # release always OK
    await client.disconnect()
    await bus.close()


@pytest.mark.asyncio
async def test_set_ptt_with_interlock(server) -> None:
    from cqk1af.config import Settings
    from cqk1af.safety.interlock import SafetyInterlock

    bus = EventBus()
    settings = Settings()
    settings.safety.require_manual_tx_approval = False
    client = TcpRadioClient(bus, TcpRadioConfig(host=server.host, port=server.port))
    interlock = SafetyInterlock(bus, client, settings)
    client.bind_interlock(interlock)
    await client.connect()
    await interlock.arm(True)
    token = await interlock.request_tx(summary="t", slice_id=0, purpose="t")
    assert token is not None

    seen_ptt: list[PttChanged] = []
    async def collect(ev):
        if isinstance(ev, PttChanged):
            seen_ptt.append(ev)
    await bus.subscribe("radio.ptt", collect, name="p")

    await client.set_ptt(True, gate_token=token)
    await client.set_ptt(False)
    await asyncio.sleep(0.05)
    assert [p.on for p in seen_ptt] == [True, False]
    bodies = [c.split("|", 1)[1] for c in server.commands]
    assert "xmit 1" in bodies
    assert "xmit 0" in bodies
    await client.disconnect()
    await bus.close()


@pytest.mark.asyncio
async def test_unkey_restores_slice_audio_mute(server) -> None:
    """On un-key, restore audio_mute=0 on each known slice. This is the
    workaround for SmartSDR auto-muting its PC Audio when our bound profile
    MOXes and failing to un-mute on ``xmit 0``.
    """
    from cqk1af.config import Settings
    from cqk1af.safety.interlock import SafetyInterlock

    bus = EventBus()
    settings = Settings()
    settings.safety.require_manual_tx_approval = False
    client = TcpRadioClient(bus, TcpRadioConfig(host=server.host, port=server.port))
    interlock = SafetyInterlock(bus, client, settings)
    client.bind_interlock(interlock)
    await client.connect()
    await interlock.arm(True)

    # Seed slice 0 and slice 1 so the un-key handler has slices to un-mute.
    await server.push("S5C1B0000|slice 0 RF_frequency=14.250000 mode=USB")
    await server.push("S5C1B0000|slice 1 RF_frequency=7.150000 mode=LSB")
    await asyncio.sleep(0.05)

    token = await interlock.request_tx(summary="t", slice_id=0, purpose="t")
    assert token is not None

    await client.set_ptt(True, gate_token=token)
    await client.set_ptt(False)
    await asyncio.sleep(0.05)

    bodies = [c.split("|", 1)[1] for c in server.commands]
    # Audio_mute restore must happen for every seeded slice, only on un-key.
    assert "slice set 0 audio_mute=0" in bodies
    assert "slice set 1 audio_mute=0" in bodies
    # And it must be sent *after* xmit 0, not before.
    xmit_off_idx = bodies.index("xmit 0")
    mute0_idx = bodies.index("slice set 0 audio_mute=0")
    assert mute0_idx > xmit_off_idx, "audio_mute restore must follow xmit 0"

    await client.disconnect()
    await bus.close()


@pytest.mark.asyncio
async def test_disconnect_publishes_event(server) -> None:
    bus = EventBus()
    seen: list = []

    async def collect(ev):
        if isinstance(ev, RadioDisconnected):
            seen.append(ev)

    await bus.subscribe("radio.disconnected", collect, name="r")
    client = TcpRadioClient(bus, TcpRadioConfig(host=server.host, port=server.port))
    await client.connect()
    await client.disconnect()
    await asyncio.sleep(0.05)
    assert seen
    assert client.state.connected is False
    await bus.close()


@pytest.mark.asyncio
async def test_connect_refused() -> None:
    bus = EventBus()
    # Port 1 is reserved; connection refused.
    client = TcpRadioClient(bus, TcpRadioConfig(host="127.0.0.1", port=1, connect_timeout_s=0.5))
    with pytest.raises(ConnectionError):
        await client.connect()
    await bus.close()


@pytest.mark.asyncio
async def test_connect_binds_to_existing_gui_client(server) -> None:
    """Mirrors smartsdr-mcp's ``BoundClientID`` pattern: we are a non-GUI
    session that binds to SmartSDR's existing GUI client so ``xmit 1`` keys
    SmartSDR's TX profile (which the operator has set up with DAX as the
    mic source). Without binding, audio written to the DAX TX device goes
    nowhere because nothing on the radio is configured to read from it.
    """
    bus = EventBus()
    client = TcpRadioClient(bus, TcpRadioConfig(host=server.host, port=server.port))
    await client.connect()
    await asyncio.sleep(0.05)
    bodies = [c.split("|", 1)[1] for c in server.commands]

    # We must NOT create a new GUI client — that allocates a fresh TX
    # profile whose mic_selection isn't reliably DAX on this firmware.
    assert "client gui" not in bodies

    # Bind handshake order: sub client all → client bind → sub radio all
    assert "sub client all" in bodies
    expected_bind = "client bind client_id=11111111-2222-3333-4444-555555555555"
    assert expected_bind in bodies
    assert "sub radio all" in bodies

    sub_client_idx = bodies.index("sub client all")
    bind_idx = bodies.index(expected_bind)
    sub_radio_idx = bodies.index("sub radio all")
    assert sub_client_idx < bind_idx < sub_radio_idx
    assert client._bound_client_id == "11111111-2222-3333-4444-555555555555"

    # UDP listener should be open and registered with the radio between the
    # bind and the rest of the subscriptions. Without this the radio
    # mishandles audio routing on MOX from a bound non-GUI client.
    assert client._udp_port is not None and client._udp_port > 0
    expected_udpport = f"client udpport {client._udp_port}"
    assert expected_udpport in bodies
    udpport_idx = bodies.index(expected_udpport)
    assert bind_idx < udpport_idx < sub_radio_idx

    await client.disconnect()
    await bus.close()


@pytest.mark.asyncio
async def test_disconnect_closes_udp_listener(server) -> None:
    bus = EventBus()
    client = TcpRadioClient(bus, TcpRadioConfig(host=server.host, port=server.port))
    await client.connect()
    assert client._udp_transport is not None
    assert client._udp_port is not None
    await client.disconnect()
    assert client._udp_transport is None
    assert client._udp_port is None
    await bus.close()


@pytest.mark.asyncio
async def test_keepalive_enabled_and_ping_loop_runs(server) -> None:
    """Without periodic pings the radio tags us as stale during long voice
    TXes and SmartSDR's audio doesn't recover on un-key. Verify we send
    ``keepalive enable`` on connect and that the ping loop actually emits.
    """
    bus = EventBus()
    client = TcpRadioClient(bus, TcpRadioConfig(host=server.host, port=server.port))
    # Speed up the test — production uses 1 s.
    client._ping_interval_s = 0.05
    await client.connect()
    assert client._ping_task is not None and not client._ping_task.done()

    # Give the ping loop a few cycles to fire.
    await asyncio.sleep(0.2)

    bodies = [c.split("|", 1)[1] for c in server.commands]
    assert "keepalive enable" in bodies
    assert any(b.startswith("ping ms_timestamp=") for b in bodies)

    # `keepalive enable` must precede the first ping.
    keepalive_idx = bodies.index("keepalive enable")
    first_ping_idx = next(i for i, b in enumerate(bodies) if b.startswith("ping ms_timestamp="))
    assert keepalive_idx < first_ping_idx

    await client.disconnect()
    assert client._ping_task is None
    await bus.close()


@pytest.mark.asyncio
async def test_connect_without_gui_client_warns_and_continues() -> None:
    """When no GUI client (SmartSDR) is on the radio, connect() should not
    hang past the 3 s bind timeout — it should log and continue without a
    binding. Uses a custom stub server that ignores ``sub client all``.
    """

    class SilentClientServer(StubServer):
        async def _handle_client(self, reader, writer):  # type: ignore[no-untyped-def]
            self._clients.add(writer)
            writer.write(b"V4.2.18.0\n")
            writer.write(b"H5C1B0000\n")
            await writer.drain()
            try:
                while True:
                    line = await reader.readline()
                    if not line:
                        break
                    text = line.decode("ascii", errors="replace").strip()
                    self.commands.append(text)
                    if text.startswith("C"):
                        seq_str = text[1:].split("|", 1)[0]
                        try:
                            seq = int(seq_str)
                        except ValueError:
                            continue
                        writer.write(f"R{seq}|0|OK\n".encode("ascii"))
                        await writer.drain()
                        # Intentionally do NOT push a client status update.
            except (asyncio.CancelledError, ConnectionError):
                pass
            finally:
                self._clients.discard(writer)

    s = SilentClientServer()
    await s.start()
    try:
        bus = EventBus()
        client = TcpRadioClient(bus, TcpRadioConfig(host=s.host, port=s.port))
        # Patch the bind wait to 0.1 s so the test doesn't take 3 s.
        original_wait_for = asyncio.wait_for

        async def fast_wait_for(coro, timeout):  # type: ignore[no-untyped-def]
            return await original_wait_for(coro, timeout=min(timeout, 0.1))

        import cqk1af.radio.tcp_client as tc

        tc.asyncio.wait_for = fast_wait_for  # type: ignore[attr-defined]
        try:
            await client.connect()
        finally:
            tc.asyncio.wait_for = original_wait_for  # type: ignore[attr-defined]

        bodies = [c.split("|", 1)[1] for c in s.commands]
        assert "sub client all" in bodies
        assert not any(b.startswith("client bind ") for b in bodies)
        assert client._bound_client_id is None
        # Other subscriptions still happen.
        assert "sub radio all" in bodies

        await client.disconnect()
        await bus.close()
    finally:
        await s.stop()


@pytest.mark.asyncio
async def test_connect_sends_station_name(server) -> None:
    bus = EventBus()
    client = TcpRadioClient(bus, TcpRadioConfig(host=server.host, port=server.port))
    await client.connect()
    await asyncio.sleep(0.05)
    bodies = [c.split("|", 1)[1] for c in server.commands]
    assert any(b.startswith("client station ") for b in bodies)
    await client.disconnect()
    await bus.close()
