from __future__ import annotations

from pathlib import Path

import pytest

from cqk1af.config import Settings, load_settings


def test_defaults() -> None:
    s = Settings()
    assert s.operator.callsign == "K1AF"
    assert s.operator.license_class == "Extra"
    assert s.radio.mock_mode is True
    assert s.safety.require_manual_tx_approval is True
    assert s.safety.armed_default is False
    assert s.safety.id_interval_seconds == 600


def test_layered_load(tmp_path: Path) -> None:
    default = tmp_path / "default.yaml"
    default.write_text(
        "operator:\n  callsign: W1AW\n  license_class: General\n"
        "radio:\n  mock_mode: false\n",
        encoding="utf-8",
    )
    user = tmp_path / "user.yaml"
    user.write_text("operator:\n  callsign: K1AF\n", encoding="utf-8")

    s = load_settings(default_yaml=default, user_yaml=user)
    # User overrides default for callsign...
    assert s.operator.callsign == "K1AF"
    # ...but other default fields are preserved.
    assert s.operator.license_class == "General"
    assert s.radio.mock_mode is False


def test_env_var_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    default = tmp_path / "default.yaml"
    default.write_text("operator:\n  callsign: K1AF\n", encoding="utf-8")
    monkeypatch.setenv("CQK1AF_OPERATOR__CALLSIGN", "VE3ABC")
    s = load_settings(default_yaml=default, user_yaml=tmp_path / "nope.yaml")
    assert s.operator.callsign == "VE3ABC"


def test_license_class_validation() -> None:
    with pytest.raises(ValueError):
        Settings.model_validate({"operator": {"license_class": "Bogus"}})
