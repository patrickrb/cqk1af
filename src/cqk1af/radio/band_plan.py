from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..config import LicenseClass, RigMode


PhoneMode = Literal["USB", "LSB"]
DEFAULT_TX_FILTER = (200, 2900)
SPACING_HZ = 3000  # 3 kHz channel spacing for candidate frequencies
EDGE_GUARD_HZ = 1500  # back off this much from sub-band edges


@dataclass(frozen=True)
class AvoidZone:
    """Half-open: includes ``low_hz`` but excludes ``high_hz``.

    This lets adjacent avoid zones and phone segments share a boundary frequency
    without the avoid check shadowing the phone allocation. E.g. CW ends at
    14.150 and phone starts at 14.150 — 14.150 is phone.
    """

    low_hz: int
    high_hz: int
    reason: str

    def contains(self, freq_hz: int) -> bool:
        if self.low_hz == self.high_hz:
            # Single-point avoid zones (e.g. AM calling at 14.286) are inclusive.
            return freq_hz == self.low_hz
        return self.low_hz <= freq_hz < self.high_hz


@dataclass(frozen=True)
class PhoneSegment:
    low_hz: int
    high_hz: int
    mode: PhoneMode
    classes: tuple[LicenseClass, ...]

    def allows(self, license_class: LicenseClass) -> bool:
        return license_class in self.classes


@dataclass(frozen=True)
class Band:
    name: str
    low_hz: int
    high_hz: int
    phone_segments: tuple[PhoneSegment, ...]
    avoid: tuple[AvoidZone, ...]

    def contains(self, freq_hz: int) -> bool:
        return self.low_hz <= freq_hz <= self.high_hz


@dataclass(frozen=True)
class Allowed:
    band: str
    segment: PhoneSegment

    @property
    def ok(self) -> bool:
        return True


@dataclass(frozen=True)
class Denied:
    reason: str

    @property
    def ok(self) -> bool:
        return False


TxDecision = Allowed | Denied


class BandPlan:
    """Loads and queries a band-plan JSON file."""

    def __init__(self, bands: list[Band], country: str = "US", last_verified: str = "") -> None:
        self.country = country
        self.last_verified = last_verified
        self.bands = bands

    @classmethod
    def load(cls, path: Path) -> "BandPlan":
        data = json.loads(path.read_text(encoding="utf-8"))
        bands: list[Band] = []
        for b in data["bands"]:
            segs = tuple(
                PhoneSegment(
                    low_hz=s["low_hz"],
                    high_hz=s["high_hz"],
                    mode=s["mode"],
                    classes=tuple(s["classes"]),
                )
                for s in b.get("phone_segments", [])
            )
            avoid = tuple(
                AvoidZone(low_hz=a["low_hz"], high_hz=a["high_hz"], reason=a["reason"])
                for a in b.get("avoid", [])
            )
            bands.append(
                Band(
                    name=b["name"],
                    low_hz=b["low_hz"],
                    high_hz=b["high_hz"],
                    phone_segments=segs,
                    avoid=avoid,
                )
            )
        return cls(
            bands=bands,
            country=data.get("country", "US"),
            last_verified=data.get("last_verified", ""),
        )

    def find_band(self, freq_hz: int) -> Band | None:
        for b in self.bands:
            if b.contains(freq_hz):
                return b
        return None

    def is_tx_allowed(
        self, freq_hz: int, mode: RigMode, license_class: LicenseClass
    ) -> TxDecision:
        if mode not in ("USB", "LSB"):
            return Denied(reason=f"Mode {mode} is not voice/phone")
        band = self.find_band(freq_hz)
        if band is None:
            return Denied(reason=f"{freq_hz} Hz is not within any defined amateur band")
        for av in band.avoid:
            if av.contains(freq_hz):
                return Denied(reason=f"{band.name}: avoid zone ({av.reason})")
        for seg in band.phone_segments:
            if seg.low_hz <= freq_hz <= seg.high_hz and seg.mode == mode and seg.allows(license_class):
                return Allowed(band=band.name, segment=seg)
        # Find a helpful reason
        any_phone = any(
            seg.low_hz <= freq_hz <= seg.high_hz and seg.mode == mode
            for seg in band.phone_segments
        )
        if not any_phone:
            return Denied(reason=f"{band.name}: outside {mode} phone sub-band")
        return Denied(
            reason=f"{band.name}: {license_class} class not authorized at {freq_hz} Hz"
        )

    def find_candidate_frequencies(
        self,
        band_name: str,
        license_class: LicenseClass,
        mode: PhoneMode = "USB",
        spacing_hz: int = SPACING_HZ,
        edge_guard_hz: int = EDGE_GUARD_HZ,
    ) -> list[int]:
        """Return a list of candidate centre frequencies in the named band that:

        - lie inside a phone segment for the given mode + license class
        - are not inside an avoid zone
        - keep at least ``edge_guard_hz`` away from any avoid zone or segment edge
        """
        band = next((b for b in self.bands if b.name == band_name), None)
        if band is None:
            return []
        segments = [s for s in band.phone_segments if s.mode == mode and s.allows(license_class)]
        if not segments:
            return []

        candidates: list[int] = []
        for seg in segments:
            start = seg.low_hz + edge_guard_hz
            end = seg.high_hz - edge_guard_hz
            f = start
            # Snap to spacing grid
            if f % spacing_hz != 0:
                f += spacing_hz - (f % spacing_hz)
            while f <= end:
                if not any(
                    av.low_hz - edge_guard_hz <= f <= av.high_hz + edge_guard_hz
                    for av in band.avoid
                ):
                    candidates.append(f)
                f += spacing_hz
        # Deduplicate (overlapping segments) while preserving order
        seen: set[int] = set()
        deduped: list[int] = []
        for f in candidates:
            if f not in seen:
                seen.add(f)
                deduped.append(f)
        return deduped

    def band_for_freq(self, freq_hz: int) -> str | None:
        b = self.find_band(freq_hz)
        return b.name if b else None
