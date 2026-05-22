"""QSO listing + ADIF export endpoints. Wired more fully in Phase 10."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from ...mcp_server.tools import MCPTools


def build_qso_router(tools_provider) -> APIRouter:  # noqa: ANN001
    r = APIRouter(prefix="/api/qsos", tags=["qsos"])

    def _get_tools() -> MCPTools:
        return tools_provider()

    @r.get("")
    async def list_qsos(limit: int = 50, tools: MCPTools = Depends(_get_tools)) -> list[dict]:
        qsos = await tools.get_recent_qsos(limit=limit)
        return [q.model_dump() for q in qsos]

    @r.get("/export.adi", response_class=PlainTextResponse)
    async def export_adif(tools: MCPTools = Depends(_get_tools)) -> str:
        return await tools.export_adif()

    return r
