"""Settings endpoint — exposes the runtime-editable subset of config."""
from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException

from ...config import Settings
from ..schemas import SettingsUpdateRequest


# Mapping from flattened request keys -> dotted Settings paths.
_PATH_MAP = {
    "operator_callsign": ("operator", "callsign"),
    "operator_name": ("operator", "name"),
    "operator_qth": ("operator", "qth"),
    "operator_grid_square": ("operator", "grid_square"),
    "operator_license_class": ("operator", "license_class"),
    "safety_require_manual_tx_approval": ("safety", "require_manual_tx_approval"),
    "safety_approval_timeout_s": ("safety", "approval_timeout_s"),
    "cq_max_attempts": ("cq", "max_attempts"),
    "cq_listen_seconds": ("cq", "listen_seconds"),
    "cq_template_id": ("cq", "template_id"),
    "cloudlog_station_profile_id": ("cloudlog", "station_profile_id"),
}


def build_settings_router(settings_provider, *, user_yaml: Path | None = None) -> APIRouter:  # noqa: ANN001
    r = APIRouter(prefix="/api/settings", tags=["settings"])

    user_yaml = user_yaml or (Path.home() / ".cqk1af" / "config.yaml")

    def _get_settings() -> Settings:
        return settings_provider()

    @r.get("")
    async def get_settings(settings: Settings = Depends(_get_settings)) -> dict:
        # Return only the editable subset; full settings are config-file-only
        return {
            "operator": settings.operator.model_dump(),
            "safety": settings.safety.model_dump(),
            "cq": settings.cq.model_dump(),
            "cloudlog": {
                # never leak api_key
                "enabled": settings.cloudlog.enabled,
                "url": settings.cloudlog.url,
                "station_profile_id": settings.cloudlog.station_profile_id,
            },
            "operator_license_class_options": ["Extra", "Advanced", "General", "Technician", "Novice"],
        }

    @r.patch("")
    async def patch_settings(
        req: SettingsUpdateRequest, settings: Settings = Depends(_get_settings)
    ) -> dict:
        # Apply in-memory first
        patch: dict = {}
        for key, dotted in _PATH_MAP.items():
            value = getattr(req, key)
            if value is None:
                continue
            section, field = dotted
            cur = patch.setdefault(section, {})
            cur[field] = value
            # Mutate the live settings
            section_obj = getattr(settings, section)
            if not hasattr(section_obj, field):
                raise HTTPException(
                    status_code=400, detail={"message": f"unknown field {section}.{field}"}
                )
            setattr(section_obj, field, value)

        # Persist to user YAML (merge with whatever's there)
        try:
            existing: dict = {}
            if user_yaml.exists():
                existing = yaml.safe_load(user_yaml.read_text(encoding="utf-8")) or {}
            for section, fields in patch.items():
                cur = existing.setdefault(section, {})
                cur.update(fields)
            user_yaml.parent.mkdir(parents=True, exist_ok=True)
            user_yaml.write_text(yaml.safe_dump(existing, sort_keys=False), encoding="utf-8")
        except Exception as e:
            raise HTTPException(status_code=500, detail={"message": f"persist failed: {e}"})

        return {"applied": patch, "persisted_to": str(user_yaml)}

    return r
