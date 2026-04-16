"""Tests for strata.grounding.scaler — DPI coordinate scaling."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from hypothesis import given
from hypothesis import strategies as st

from strata.core.types import Coordinate
from strata.grounding.scaler import CoordinateScaler


def _make_gui(scale: float = 1.0) -> MagicMock:
    gui = MagicMock()
    gui.get_dpi_scale_for_point.return_value = scale
    return gui


class TestCoordinateScaler:
    def test_1x_identity(self) -> None:
        scaler = CoordinateScaler(_make_gui(1.0))
        c = Coordinate(x=100.0, y=200.0)
        assert scaler.logical_to_physical(c) == c
        assert scaler.physical_to_logical(c) == c

    def test_2x_doubles(self) -> None:
        scaler = CoordinateScaler(_make_gui(2.0))
        c = Coordinate(x=100.0, y=200.0)
        result = scaler.logical_to_physical(c)
        assert result.x == pytest.approx(200.0)
        assert result.y == pytest.approx(400.0)

    def test_2x_halves(self) -> None:
        scaler = CoordinateScaler(_make_gui(2.0))
        c = Coordinate(x=200.0, y=400.0)
        result = scaler.physical_to_logical(c)
        assert result.x == pytest.approx(100.0)
        assert result.y == pytest.approx(200.0)


@given(
    x=st.floats(min_value=0, max_value=4000, allow_nan=False),
    y=st.floats(min_value=0, max_value=4000, allow_nan=False),
    scale=st.floats(min_value=0.5, max_value=4.0, allow_nan=False),
)
def test_prop_dpi_roundtrip(x: float, y: float, scale: float) -> None:
    scaler = CoordinateScaler(_make_gui(scale))
    c = Coordinate(x=x, y=y)
    roundtripped = scaler.physical_to_logical(scaler.logical_to_physical(c))
    assert roundtripped.x == pytest.approx(c.x, rel=1e-6)
    assert roundtripped.y == pytest.approx(c.y, rel=1e-6)
