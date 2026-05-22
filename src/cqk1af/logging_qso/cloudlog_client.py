"""Cloudlog / Wavelog HTTP API client.

Endpoint: ``POST {url}/index.php/api/qso``
Body:
{
  "key": "<api_key>",
  "station_profile_id": "<id>",
  "type": "adif",
  "string": "<adif record>"
}

Response on success contains a ``status`` field (`"created"`) and a
``messages`` array. We treat any 2xx with a non-error body as success.
"""
from __future__ import annotations

import httpx

from ..config import CloudlogCfg
from ..util.logging import get_logger
from .adif import adif_header, adif_record
from .sqlite_store import QSORecord

log = get_logger(__name__)


class CloudlogError(RuntimeError):
    pass


class CloudlogClient:
    def __init__(self, cfg: CloudlogCfg, *, timeout_s: float = 10.0) -> None:
        self.cfg = cfg
        self.timeout_s = timeout_s

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.enabled and self.cfg.url and self.cfg.api_key.get_secret_value())

    async def upload(self, qso: QSORecord, *, station_callsign: str = "") -> str:
        if not self.enabled:
            raise CloudlogError("Cloudlog not configured (url/api_key/enabled)")
        adif_text = adif_header() + adif_record(qso, station_callsign=station_callsign)
        url = self.cfg.url.rstrip("/") + "/index.php/api/qso"
        payload = {
            "key": self.cfg.api_key.get_secret_value(),
            "station_profile_id": str(self.cfg.station_profile_id),
            "type": "adif",
            "string": adif_text,
        }
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            r = await client.post(url, json=payload)
        if r.status_code >= 400:
            raise CloudlogError(f"HTTP {r.status_code}: {r.text[:300]}")
        try:
            body = r.json()
        except Exception:
            body = {}
        # Cloudlog returns {"status":"created", ...}; Wavelog mirrors this.
        if isinstance(body, dict) and str(body.get("status", "")).lower() in ("created", "ok", "success"):
            return str(body.get("logbook_id") or body.get("id") or "")
        # Some installs return plain text "QSO Successfully Added!"
        text_lower = r.text.lower()
        if "added" in text_lower or "successfully" in text_lower or r.status_code == 200:
            return ""
        raise CloudlogError(f"Unexpected response: {r.text[:300]}")
