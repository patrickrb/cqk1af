"""FastMCP server registration for the 14 CQK1AF tools."""
from __future__ import annotations

from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from ..app import App
from ..util.logging import get_logger
from .system_prompt import build_system_prompt
from .tools import (
    CandidateFreqDTO,
    CheckResultDTO,
    CQResultDTO,
    HeardCallsignDTO,
    MCPTools,
    QSOContextDTO,
    RadioStatus,
    SessionStateDTO,
    SliceDTO,
    ToolError,
)

log = get_logger(__name__)


def build_mcp(app: App) -> FastMCP:
    """Create a FastMCP server with all 14 tools registered against ``app``."""
    instructions = build_system_prompt(app.settings)
    mcp = FastMCP(
        "cqk1af",
        instructions=instructions,
        host=app.settings.mcp.host,
        port=app.settings.mcp.port,
    )
    tools = MCPTools(app)

    def _wrap_errors(e: Exception) -> dict[str, Any]:
        if isinstance(e, ToolError):
            return {"error": {"code": e.code, "message": e.message}}
        log.exception("mcp.tool_unhandled_error")
        return {"error": {"code": "internal_error", "message": str(e)}}

    # ---- READ -------------------------------------------------------

    @mcp.tool(description="Return current FSM state, slice info, QSO context, pileup, and counters.")
    async def get_session_state() -> SessionStateDTO:
        return await tools.get_session_state()

    @mcp.tool(description="List recent QSOs from the SQLite store, newest first.")
    async def get_recent_qsos(limit: int = 20) -> list[QSOContextDTO]:
        return await tools.get_recent_qsos(limit=limit)

    @mcp.tool(description="Export logged QSOs as ADIF 3.1.0 text.")
    async def export_adif(start: str | None = None, end: str | None = None) -> str:
        return await tools.export_adif(start=start, end=end)

    # ---- RADIO ------------------------------------------------------

    @mcp.tool(description="Connect to the configured radio. In mock mode this is in-memory.")
    async def connect_radio(host: str | None = None) -> RadioStatus | dict[str, Any]:
        try:
            return await tools.connect_radio(host=host)
        except ToolError as e:
            return _wrap_errors(e)

    @mcp.tool(description="Disconnect from the radio.")
    async def disconnect_radio() -> RadioStatus | dict[str, Any]:
        try:
            return await tools.disconnect_radio()
        except ToolError as e:
            return _wrap_errors(e)

    @mcp.tool(description="Tune the slice. Updates RX freq/mode/filter and the session's tracked band.")
    async def tune_slice(
        freq_hz: int,
        mode: Literal["USB", "LSB"],
        filter_low_hz: int = 200,
        filter_high_hz: int = 2900,
        slice_id: int = 0,
    ) -> SliceDTO | dict[str, Any]:
        try:
            return await tools.tune_slice(
                freq_hz=freq_hz,
                mode=mode,
                filter_low_hz=filter_low_hz,
                filter_high_hz=filter_high_hz,
                slice_id=slice_id,
            )
        except ToolError as e:
            return _wrap_errors(e)

    @mcp.tool(description="Return ranked candidate frequencies in the given band for the operator's class.")
    async def scan_for_open_frequency(
        band: str,
        mode: Literal["USB", "LSB"] = "USB",
    ) -> list[CandidateFreqDTO]:
        return await tools.scan_for_open_frequency(band=band, mode=mode)

    # ---- TX (gated) -------------------------------------------------

    @mcp.tool(
        description=(
            "Run the three-attempt courtesy check on ``freq_hz``. Gated by the safety "
            "interlock and band-plan privileges. TX speaks via Piper out the DAX device "
            "when the audio pipeline is online and falls back to narration-only "
            "(transcript event, no PTT) otherwise. The S-meter is consulted for clearance."
        )
    )
    async def check_frequency_in_use(
        freq_hz: int, attempts: int = 3, listen_seconds: float = 5.0
    ) -> CheckResultDTO | dict[str, Any]:
        try:
            return await tools.check_frequency_in_use(
                freq_hz=freq_hz, attempts=attempts, listen_seconds=listen_seconds
            )
        except ToolError as e:
            return _wrap_errors(e)

    @mcp.tool(description="Transmit a CQ call using the configured template. Advances FSM to RECEIVING_REPLY.")
    async def call_cq(
        template_id: str | None = None, attempts: int = 1
    ) -> CQResultDTO | dict[str, Any]:
        try:
            return await tools.call_cq(template_id=template_id, attempts=attempts)
        except ToolError as e:
            return _wrap_errors(e)

    @mcp.tool(description="Send signal report during an active QSO.")
    async def exchange_signal_report(
        rst_sent: str, expect_rst: bool = True
    ) -> dict[str, Any]:
        try:
            return await tools.exchange_signal_report(rst_sent=rst_sent, expect_rst=expect_rst)
        except ToolError as e:
            return _wrap_errors(e)

    @mcp.tool(description="Send the closing phrase and mark the QSO complete. Advances to CONFIRMING_LOG.")
    async def complete_qso(
        closing_phrase: str | None = None,
    ) -> QSOContextDTO | dict[str, Any]:
        try:
            return await tools.complete_qso(closing_phrase=closing_phrase)
        except ToolError as e:
            return _wrap_errors(e)

    # ---- WORKFLOW ---------------------------------------------------

    @mcp.tool(
        description=(
            "Listen for callers up to ``timeout_s`` seconds and return callsigns heard. "
            "Pileup is populated by the live STT path (Whisper -> callsign extractor). "
            "After the first caller, an additional ``settle_window_s`` is allowed so "
            "simultaneous callers are returned together."
        )
    )
    async def listen_for_reply(
        timeout_s: float = 7.0, settle_window_s: float = 1.5
    ) -> list[HeardCallsignDTO] | dict[str, Any]:
        try:
            return await tools.listen_for_reply(
                timeout_s=timeout_s, settle_window_s=settle_window_s
            )
        except ToolError as e:
            return _wrap_errors(e)

    @mcp.tool(description="Select one callsign from the pileup queue to work next.")
    async def select_caller(callsign: str) -> SessionStateDTO | dict[str, Any]:
        try:
            return await tools.select_caller(callsign=callsign)
        except ToolError as e:
            return _wrap_errors(e)

    @mcp.tool(description="Update one field on the active QSO (callsign, name, qth, grid, rst, comment).")
    async def update_qso_field(
        field: str, value: str
    ) -> QSOContextDTO | dict[str, Any]:
        try:
            return await tools.update_qso_field(field=field, value=value)
        except ToolError as e:
            return _wrap_errors(e)

    @mcp.tool(
        description="Confirm and log the QSO. Optional ``edits`` dict patches fields before logging. Advances back to IDLE."
    )
    async def confirm_and_log_qso(
        edits: dict[str, Any] | None = None,
    ) -> QSOContextDTO | dict[str, Any]:
        try:
            return await tools.confirm_and_log_qso(edits=edits)
        except ToolError as e:
            return _wrap_errors(e)

    # ---- SAFETY -----------------------------------------------------

    @mcp.tool(
        description="Emergency stop. Forces PTT off, disarms the session, and transitions FSM to STOPPED."
    )
    async def emergency_stop(reason: str = "operator requested") -> SessionStateDTO:
        return await tools.emergency_stop(reason=reason)

    return mcp


async def run_mcp_server(app: App, *, transport: str = "streamable-http") -> None:
    """Run the MCP server.

    Transports:
      - ``streamable-http`` (default, recommended): mounts at ``/mcp`` on the configured port
      - ``sse``: legacy SSE transport
      - ``stdio``: process stdio (for editors that spawn the server)
    """
    mcp = build_mcp(app)
    if transport == "streamable-http":
        await mcp.run_streamable_http_async()
    elif transport == "sse":
        await mcp.run_sse_async()
    elif transport == "stdio":
        await mcp.run_stdio_async()
    else:
        raise ValueError(f"Unknown MCP transport: {transport}")
