from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_log_capture(caplog):
    # Keep tests quiet by default; tests that care about logs opt in.
    caplog.set_level("WARNING")
    yield
