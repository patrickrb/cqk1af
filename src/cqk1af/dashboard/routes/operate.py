"""Operating endpoints — drive the session through the FSM.

Mirrors the MCP tools that mutate state machine progress so the dashboard
operator can run a QSO without needing an MCP client.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...mcp_server.tools import (
    CheckResultDTO,
    CQResultDTO,
    HeardCallsignDTO,
    MCPTools,
    QSOContextDTO,
    ToolError,
)


class CheckFrequencyRequest(BaseModel):
    freq_hz: int
    attempts: int = 3


class CallCqRequest(BaseModel):
    template_id: str | None = None
    attempts: int = 1


class CompleteQsoRequest(BaseModel):
    closing_phrase: str | None = None


class ExchangeReportRequest(BaseModel):
    rst_sent: str
    expect_rst: bool = True


class ConfirmLogRequest(BaseModel):
    edits: dict[str, Any] | None = None


class SimulateCallerRequest(BaseModel):
    callsign: str
    confidence: float = 0.9
    snr_dbm: float | None = -55.0


def build_operate_router(tools_provider) -> APIRouter:  # noqa: ANN001
    r = APIRouter(prefix="/api/operate", tags=["operate"])

    def _get_tools() -> MCPTools:
        return tools_provider()

    def _raise_tool_error(e: ToolError) -> None:
        raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message})

    @r.post("/scan/{band}")
    async def scan(
        band: str,
        mode: Literal["USB", "LSB"] = "USB",
        tools: MCPTools = Depends(_get_tools),
    ) -> list[dict]:
        cands = await tools.scan_for_open_frequency(band=band, mode=mode)
        return [c.model_dump() for c in cands]

    @r.post("/check_frequency", response_model=CheckResultDTO)
    async def check_frequency(
        req: CheckFrequencyRequest, tools: MCPTools = Depends(_get_tools)
    ) -> CheckResultDTO:
        try:
            return await tools.check_frequency_in_use(
                freq_hz=req.freq_hz, attempts=req.attempts
            )
        except ToolError as e:
            _raise_tool_error(e)
            raise  # for type checker

    @r.post("/call_cq", response_model=CQResultDTO)
    async def call_cq(
        req: CallCqRequest, tools: MCPTools = Depends(_get_tools)
    ) -> CQResultDTO:
        try:
            return await tools.call_cq(template_id=req.template_id, attempts=req.attempts)
        except ToolError as e:
            _raise_tool_error(e)
            raise

    @r.post("/listen_for_reply")
    async def listen_for_reply(
        timeout_s: float = 7.0, tools: MCPTools = Depends(_get_tools)
    ) -> list[HeardCallsignDTO]:
        try:
            return await tools.listen_for_reply(timeout_s=timeout_s)
        except ToolError as e:
            _raise_tool_error(e)
            raise

    @r.post("/exchange_signal_report")
    async def exchange_signal_report(
        req: ExchangeReportRequest, tools: MCPTools = Depends(_get_tools)
    ) -> dict:
        try:
            return await tools.exchange_signal_report(
                rst_sent=req.rst_sent, expect_rst=req.expect_rst
            )
        except ToolError as e:
            _raise_tool_error(e)
            raise

    @r.post("/complete_qso", response_model=QSOContextDTO)
    async def complete_qso(
        req: CompleteQsoRequest, tools: MCPTools = Depends(_get_tools)
    ) -> QSOContextDTO:
        try:
            return await tools.complete_qso(closing_phrase=req.closing_phrase)
        except ToolError as e:
            _raise_tool_error(e)
            raise

    @r.post("/confirm_log", response_model=QSOContextDTO)
    async def confirm_log(
        req: ConfirmLogRequest, tools: MCPTools = Depends(_get_tools)
    ) -> QSOContextDTO:
        try:
            return await tools.confirm_and_log_qso(edits=req.edits)
        except ToolError as e:
            _raise_tool_error(e)
            raise

    @r.post("/simulate_caller")
    async def simulate_caller(
        req: SimulateCallerRequest, tools: MCPTools = Depends(_get_tools)
    ) -> dict:
        """Mock-mode helper: injects a CallsignHeard so the pileup populates.

        Refuses to run when the radio is real (mock_mode=false) — for a real
        radio you should be listening to actual RX audio via Phase 7 STT.
        """
        if not tools.app.settings.radio.mock_mode:
            raise HTTPException(
                status_code=400,
                detail={"code": "not_mock_mode", "message": "simulate_caller only works in mock_mode"},
            )
        from cqk1af.events import CallsignHeard

        await tools.app.bus.publish(
            CallsignHeard(
                callsign=req.callsign.upper(),
                confidence=req.confidence,
                snr_dbm=req.snr_dbm,
                source_text=f"(simulated) {req.callsign}",
            )
        )
        return {"ok": True}

    return r
