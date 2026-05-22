"""Radio status + tuning endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ...mcp_server.tools import MCPTools, ToolError
from ..schemas import RadioStatus, SessionStateDTO, SliceDTO, TuneRequest


def build_radio_router(tools_provider) -> APIRouter:  # noqa: ANN001
    r = APIRouter(prefix="/api/radio", tags=["radio"])

    def _get_tools() -> MCPTools:
        return tools_provider()

    @r.get("/state", response_model=SessionStateDTO)
    async def state(tools: MCPTools = Depends(_get_tools)) -> SessionStateDTO:
        return await tools.get_session_state()

    @r.get("/status", response_model=RadioStatus)
    async def status(tools: MCPTools = Depends(_get_tools)) -> RadioStatus:
        return tools._radio_status()  # noqa: SLF001

    @r.post("/connect", response_model=RadioStatus)
    async def connect(tools: MCPTools = Depends(_get_tools)) -> RadioStatus:
        try:
            return await tools.connect_radio()
        except ToolError as e:
            raise HTTPException(status_code=500, detail={"code": e.code, "message": e.message})

    @r.post("/disconnect", response_model=RadioStatus)
    async def disconnect(tools: MCPTools = Depends(_get_tools)) -> RadioStatus:
        try:
            return await tools.disconnect_radio()
        except ToolError as e:
            raise HTTPException(status_code=500, detail={"code": e.code, "message": e.message})

    @r.post("/tune", response_model=SliceDTO)
    async def tune(req: TuneRequest, tools: MCPTools = Depends(_get_tools)) -> SliceDTO:
        try:
            return await tools.tune_slice(
                freq_hz=req.freq_hz,
                mode=req.mode,
                filter_low_hz=req.filter_low_hz,
                filter_high_hz=req.filter_high_hz,
                slice_id=req.slice_id,
            )
        except ToolError as e:
            raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message})

    @r.get("/candidates/{band}")
    async def candidates(band: str, mode: str = "USB", tools: MCPTools = Depends(_get_tools)) -> list[dict]:
        if mode not in ("USB", "LSB"):
            raise HTTPException(status_code=400, detail={"message": "mode must be USB or LSB"})
        cands = await tools.scan_for_open_frequency(band=band, mode=mode)  # type: ignore[arg-type]
        return [c.model_dump() for c in cands]

    return r
