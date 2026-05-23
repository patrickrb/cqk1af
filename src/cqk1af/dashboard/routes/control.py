"""Control endpoints (arm, kill, approve TX, select caller, etc.)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...mcp_server.tools import MCPTools, ToolError
from ...state.machine import IllegalTransition
from ...state.states import Event, State
from ..schemas import (
    ApproveTxRequest,
    ArmRequest,
    FieldUpdateRequest,
    KillRequest,
    SelectCallerRequest,
    SessionStateDTO,
)


class PileupAddRequest(BaseModel):
    callsign: str
    confidence: float = 1.0
    snr_dbm: float | None = None


class PileupEditRequest(BaseModel):
    old_callsign: str
    new_callsign: str


class PileupRemoveRequest(BaseModel):
    callsign: str


class PileupProbeRequest(BaseModel):
    callsign: str
    dry_run: bool = False
    text_override: str | None = None


class DispatchEventRequest(BaseModel):
    event: str
    reason: str = "manual"


def build_control_router(tools_provider) -> APIRouter:  # noqa: ANN001
    r = APIRouter(prefix="/api/control", tags=["control"])

    def _get_tools() -> MCPTools:
        return tools_provider()

    @r.post("/arm", response_model=SessionStateDTO)
    async def arm(req: ArmRequest, tools: MCPTools = Depends(_get_tools)) -> SessionStateDTO:
        await tools.app.interlock.arm(req.armed, source="rest")
        tools.app.fsm.session.armed = tools.app.interlock.armed
        return await tools.get_session_state()

    @r.post("/kill", response_model=SessionStateDTO)
    async def kill(req: KillRequest, tools: MCPTools = Depends(_get_tools)) -> SessionStateDTO:
        return await tools.emergency_stop(reason=req.reason)

    @r.post("/approve_tx")
    async def approve_tx(req: ApproveTxRequest, tools: MCPTools = Depends(_get_tools)) -> dict:
        import uuid as _uuid

        try:
            req_uuid = _uuid.UUID(req.req_id)
        except ValueError:
            raise HTTPException(status_code=400, detail={"message": "invalid req_id"})
        ok = await tools.app.interlock.resolve_approval(req_uuid, req.approved, by="operator")
        if not ok:
            raise HTTPException(status_code=404, detail={"message": "no such approval"})
        return {"req_id": req.req_id, "approved": req.approved}

    @r.post("/select_caller", response_model=SessionStateDTO)
    async def select_caller(
        req: SelectCallerRequest, tools: MCPTools = Depends(_get_tools)
    ) -> SessionStateDTO:
        try:
            return await tools.select_caller(callsign=req.callsign)
        except ToolError as e:
            raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message})

    @r.post("/qso/field")
    async def set_qso_field(
        req: FieldUpdateRequest, tools: MCPTools = Depends(_get_tools)
    ) -> dict:
        try:
            qso = await tools.update_qso_field(field=req.field, value=req.value)
            return qso.model_dump()
        except ToolError as e:
            raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message})

    @r.post("/require_approval")
    async def set_require_approval(
        req: ArmRequest, tools: MCPTools = Depends(_get_tools)
    ) -> dict:
        """Toggle ``safety.require_manual_tx_approval`` at runtime.

        With approval ON the operator approves each TX action via the modal.
        With approval OFF the assistant transmits autonomously once armed —
        the kill switch is the only operator gate.
        """
        # The schema reuses ArmRequest (single boolean field). When False, the
        # operator has opted into "trust the assistant"; the session must
        # still be armed and the kill switch still works.
        tools.app.settings.safety.require_manual_tx_approval = bool(req.armed)
        return {"require_manual_tx_approval": tools.app.settings.safety.require_manual_tx_approval}

    @r.post("/pileup/add", response_model=SessionStateDTO)
    async def pileup_add(
        req: PileupAddRequest, tools: MCPTools = Depends(_get_tools)
    ) -> SessionStateDTO:
        try:
            return await tools.add_pileup_entry(
                callsign=req.callsign, confidence=req.confidence, snr_dbm=req.snr_dbm
            )
        except ToolError as e:
            raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message})

    @r.post("/pileup/edit", response_model=SessionStateDTO)
    async def pileup_edit(
        req: PileupEditRequest, tools: MCPTools = Depends(_get_tools)
    ) -> SessionStateDTO:
        try:
            return await tools.edit_pileup_entry(
                old_callsign=req.old_callsign, new_callsign=req.new_callsign
            )
        except ToolError as e:
            raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message})

    @r.post("/pileup/remove", response_model=SessionStateDTO)
    async def pileup_remove(
        req: PileupRemoveRequest, tools: MCPTools = Depends(_get_tools)
    ) -> SessionStateDTO:
        try:
            return await tools.remove_pileup_entry(callsign=req.callsign)
        except ToolError as e:
            raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message})

    @r.post("/pileup/probe")
    async def pileup_probe(
        req: PileupProbeRequest, tools: MCPTools = Depends(_get_tools)
    ) -> dict[str, Any]:
        try:
            return await tools.probe_pileup_entry(
                callsign=req.callsign,
                dry_run=req.dry_run,
                text_override=req.text_override,
            )
        except ToolError as e:
            raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message})

    @r.post("/reset", response_model=SessionStateDTO)
    async def reset(tools: MCPTools = Depends(_get_tools)) -> SessionStateDTO:
        """Recover from ERROR or STOPPED back to IDLE.

        Operator-initiated; does NOT re-arm the session (deliberately).
        """
        fsm = tools.app.fsm
        if fsm.state is State.ERROR:
            await fsm.dispatch(Event.ACK, reason="operator_reset")
        elif fsm.state is State.STOPPED:
            await fsm.dispatch(Event.START_SESSION, reason="operator_reset")
            # START_SESSION lands in IDLE here (per the STOPPED transition table)
        else:
            # No-op for other states.
            pass
        fsm.session.reset_session()
        return await tools.get_session_state()

    @r.post("/dispatch_event", response_model=SessionStateDTO)
    async def dispatch_event(
        req: DispatchEventRequest, tools: MCPTools = Depends(_get_tools)
    ) -> SessionStateDTO:
        """Manually dispatch an FSM event from the operator UI.

        Only events currently valid from the present state are accepted; the
        underlying transition table is not bypassed.
        """
        try:
            event = Event(req.event)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"code": "unknown_event", "message": f"unknown event {req.event!r}"},
            )
        fsm = tools.app.fsm
        if not fsm.can(event):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "illegal_transition",
                    "message": f"event {event} is not valid from state {fsm.state}",
                },
            )
        try:
            await fsm.dispatch(event, reason=req.reason)
        except IllegalTransition as e:
            raise HTTPException(
                status_code=409,
                detail={"code": "illegal_transition", "message": str(e)},
            )
        return await tools.get_session_state()

    return r
