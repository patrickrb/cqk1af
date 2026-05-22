from __future__ import annotations

from pathlib import Path

import pytest

from cqk1af.radio.band_plan import Allowed, BandPlan, Denied

BAND_PLAN_PATH = Path("config/band_plan_us.json")


@pytest.fixture(scope="module")
def bp() -> BandPlan:
    return BandPlan.load(BAND_PLAN_PATH)


def test_extra_can_tx_at_20m_general_segment(bp: BandPlan) -> None:
    d = bp.is_tx_allowed(14_250_000, "USB", "Extra")
    assert isinstance(d, Allowed)
    assert d.band == "20m"


def test_general_blocked_below_14225(bp: BandPlan) -> None:
    d = bp.is_tx_allowed(14_200_000, "USB", "General")
    assert isinstance(d, Denied)
    assert "General" in d.reason or "outside" in d.reason


def test_extra_can_tx_in_extra_segment_14150(bp: BandPlan) -> None:
    d = bp.is_tx_allowed(14_150_000, "USB", "Extra")
    assert isinstance(d, Allowed)


def test_cw_segment_blocked_for_phone(bp: BandPlan) -> None:
    d = bp.is_tx_allowed(14_050_000, "USB", "Extra")
    assert isinstance(d, Denied)


def test_lsb_segment_on_40m(bp: BandPlan) -> None:
    # 40m phone is LSB
    d_usb = bp.is_tx_allowed(7_200_000, "USB", "Extra")
    assert isinstance(d_usb, Denied)
    d_lsb = bp.is_tx_allowed(7_200_000, "LSB", "Extra")
    assert isinstance(d_lsb, Allowed)


def test_general_40m_above_7175_ok(bp: BandPlan) -> None:
    d = bp.is_tx_allowed(7_200_000, "LSB", "General")
    assert isinstance(d, Allowed)


def test_avoid_zone_blocks_am_calling(bp: BandPlan) -> None:
    # 14.286 MHz is an AM calling frequency; SSB should be denied.
    d = bp.is_tx_allowed(14_286_000, "USB", "Extra")
    assert isinstance(d, Denied)
    assert "avoid" in d.reason.lower() or "AM" in d.reason


def test_unknown_frequency_denied(bp: BandPlan) -> None:
    d = bp.is_tx_allowed(100_000_000, "USB", "Extra")
    assert isinstance(d, Denied)


def test_unsupported_mode_denied(bp: BandPlan) -> None:
    d = bp.is_tx_allowed(14_250_000, "FM", "Extra")
    assert isinstance(d, Denied)


def test_candidates_20m_extra_in_range(bp: BandPlan) -> None:
    cands = bp.find_candidate_frequencies("20m", "Extra", "USB")
    assert cands, "expected candidate frequencies on 20m for Extra"
    for f in cands:
        d = bp.is_tx_allowed(f, "USB", "Extra")
        assert isinstance(d, Allowed), f"{f} should be allowed but got {d}"


def test_candidates_skip_avoid_zones(bp: BandPlan) -> None:
    cands = bp.find_candidate_frequencies("20m", "Extra", "USB")
    # 14.286 (AM calling) +/- guard should be excluded
    assert not any(14_284_000 <= f <= 14_288_000 for f in cands)


def test_candidates_for_general_narrower_than_extra(bp: BandPlan) -> None:
    extra = bp.find_candidate_frequencies("20m", "Extra", "USB")
    general = bp.find_candidate_frequencies("20m", "General", "USB")
    assert len(general) < len(extra)
    # General must not include any sub-14.225 frequency
    assert all(f >= 14_225_000 for f in general)


def test_band_for_freq(bp: BandPlan) -> None:
    assert bp.band_for_freq(14_250_000) == "20m"
    assert bp.band_for_freq(7_200_000) == "40m"
    assert bp.band_for_freq(28_400_000) == "10m"
    assert bp.band_for_freq(50_000_000) is None
