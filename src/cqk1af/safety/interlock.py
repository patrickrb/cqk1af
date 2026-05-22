"""Safety interlock.

The interlock is the only entity that issues ``GateToken``s, and it is the only
path by which the radio adapter's ``set_ptt(True, ...)`` is callable. It enforces
three layers of gating:

  1. ``armed``: defaults False. The operator must arm a session before any TX.
  2. Per-action approval (if configured): a ``TxApprovalRequested`` event is
     published; the operator must approve via the dashboard within a timeout.
  3. Token lifecycle: each issued token is single-use, time-bounded, and bound
     to a slice + purpose. After ``set_ptt(True, token)`` the token is consumed.

The kill path is independent of approval — ``kill()`` forces PTT off
synchronously via the radio adapter, even from a non-async context.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from ..config import Settings
from ..events import (
    EventBus,
    KillSwitchTriggered,
    SessionArmed,
    TxApprovalRequested,
    TxApprovalResolved,
)
from ..radio.base import GateToken, RadioClient
from ..util.logging import get_logger
from ..util.time import utcnow

log = get_logger(__name__)


@dataclass
class ApprovalRequest:
    req_id: uuid.UUID
    summary: str
    payload: dict[str, Any]
    expires_at: Any  # datetime
    future: asyncio.Future
    slice_id: int
    purpose: str


class SafetyInterlock:
    """Multi-layered TX gate. Bound to a radio adapter; coordinates approvals."""

    def __init__(
        self,
        bus: EventBus,
        radio: RadioClient,
        settings: Settings,
        *,
        token_lifetime_s: float = 30.0,
    ) -> None:
        self.bus = bus
        self.radio = radio
        self.settings = settings
        self.token_lifetime = timedelta(seconds=token_lifetime_s)

        self._armed = settings.safety.armed_default
        self._pending: dict[uuid.UUID, ApprovalRequest] = {}
        self._issued_tokens: dict[uuid.UUID, GateToken] = {}
        self._consumed_token_ids: set[uuid.UUID] = set()
        self._lock = asyncio.Lock()

    @property
    def armed(self) -> bool:
        return self._armed

    async def arm(self, value: bool, *, source: str = "operator") -> None:
        async with self._lock:
            prev = self._armed
            self._armed = bool(value)
            if prev != self._armed:
                await self.bus.publish(SessionArmed(armed=self._armed))
                log.info("interlock.armed_changed", armed=self._armed, source=source)
            if not self._armed:
                # Denying all pending requests on disarm
                for req in list(self._pending.values()):
                    if not req.future.done():
                        req.future.set_result(False)
                        await self.bus.publish(
                            TxApprovalResolved(req_id=req.req_id, approved=False, by="system")
                        )
                self._pending.clear()

    async def request_tx(
        self,
        *,
        summary: str,
        slice_id: int,
        purpose: str,
        payload: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> GateToken | None:
        """Returns a fresh ``GateToken`` if TX is authorized; otherwise ``None``.

        - If not armed: returns ``None`` immediately.
        - If ``require_manual_tx_approval``: publishes ``TxApprovalRequested`` and
          awaits ``resolve_approval`` with a timeout (auto-deny). Returns a token
          only if the operator approves.
        - Otherwise: issues a token directly.
        """
        if not self._armed:
            log.info("interlock.deny.not_armed", summary=summary)
            return None

        approval_required = self.settings.safety.require_manual_tx_approval
        timeout = timeout_s if timeout_s is not None else float(self.settings.safety.approval_timeout_s)

        if approval_required:
            loop = asyncio.get_running_loop()
            req_id = uuid.uuid4()
            expires_at = utcnow() + timedelta(seconds=timeout)
            fut: asyncio.Future[bool] = loop.create_future()
            req = ApprovalRequest(
                req_id=req_id,
                summary=summary,
                payload=payload or {},
                expires_at=expires_at,
                future=fut,
                slice_id=slice_id,
                purpose=purpose,
            )
            self._pending[req_id] = req
            await self.bus.publish(
                TxApprovalRequested(
                    req_id=req_id,
                    summary=summary,
                    payload=payload or {},
                    expires_at=expires_at,
                )
            )
            try:
                approved = await asyncio.wait_for(fut, timeout=timeout)
            except asyncio.TimeoutError:
                self._pending.pop(req_id, None)
                await self.bus.publish(
                    TxApprovalResolved(req_id=req_id, approved=False, by="auto-deny")
                )
                return None
            finally:
                self._pending.pop(req_id, None)
            if not approved:
                return None

        return self._issue_token(slice_id=slice_id, purpose=purpose)

    async def resolve_approval(
        self, req_id: uuid.UUID, approved: bool, *, by: str = "operator"
    ) -> bool:
        """Resolve a pending approval. Returns True if the request was found and resolved."""
        req = self._pending.get(req_id)
        if req is None:
            return False
        if not req.future.done():
            req.future.set_result(bool(approved))
        await self.bus.publish(
            TxApprovalResolved(req_id=req_id, approved=bool(approved), by="operator" if by == "operator" else "system")
        )
        return True

    def consume_token(self, token: GateToken) -> None:
        """Mark a token as consumed. The radio adapter MUST call this after using it."""
        self._consumed_token_ids.add(token.id)
        self._issued_tokens.pop(token.id, None)

    def is_token_valid(self, token: GateToken) -> bool:
        if token.id in self._consumed_token_ids:
            return False
        if token.is_expired():
            return False
        return token.id in self._issued_tokens

    def kill(self, *, source: str = "interlock.kill") -> None:
        """Synchronous kill: force PTT off, disarm, deny all pending approvals.

        Publishes ``KillSwitchTriggered`` via ``publish_nowait`` so the caller is
        not blocked. Safe to call from any context.
        """
        try:
            self.radio.force_ptt_off()
        except Exception:
            log.exception("interlock.kill.force_ptt_failed")
        was_armed = self._armed
        self._armed = False
        # Invalidate every outstanding token
        for tid in list(self._issued_tokens.keys()):
            self._consumed_token_ids.add(tid)
        self._issued_tokens.clear()
        # Deny pending approvals (the awaiting coroutines pick this up)
        for req in list(self._pending.values()):
            if not req.future.done():
                req.future.set_result(False)
        self._pending.clear()
        self.bus.publish_nowait(KillSwitchTriggered(source=source))
        if was_armed:
            self.bus.publish_nowait(SessionArmed(armed=False))

    # ---- internals ----------------------------------------------------

    def _issue_token(self, *, slice_id: int, purpose: str) -> GateToken:
        now = utcnow()
        token = GateToken(
            id=uuid.uuid4(),
            slice_id=slice_id,
            purpose=purpose,
            issued_at=now,
            expires_at=now + self.token_lifetime,
        )
        self._issued_tokens[token.id] = token
        log.info(
            "interlock.token_issued",
            token_id=str(token.id),
            slice_id=slice_id,
            purpose=purpose,
            expires_at=token.expires_at.isoformat(),
        )
        return token
