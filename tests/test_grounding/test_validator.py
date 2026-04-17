"""Tests for strata.grounding.validator — coordinate boundary checks."""

from __future__ import annotations

from unittest.mock import MagicMock

import icontract
import pytest

from strata.core.errors import InvalidCoordinateError
from strata.core.types import Coordinate
from strata.grounding.validator import ActionValidator


def _make_gui(w: int = 1920, h: int = 1080) -> MagicMock:
    gui = MagicMock()
    gui.get_screen_size.return_value = (w, h)
    return gui


class TestActionValidator:
    def test_valid_coordinate(self) -> None:
        v = ActionValidator(_make_gui())
        v.validate_coordinates_in_screen(Coordinate(x=100.0, y=200.0))

    def test_origin_valid(self) -> None:
        v = ActionValidator(_make_gui())
        v.validate_coordinates_in_screen(Coordinate(x=0.0, y=0.0))

    def test_x_out_of_range(self) -> None:
        v = ActionValidator(_make_gui(1920, 1080))
        with pytest.raises(InvalidCoordinateError):
            v.validate_coordinates_in_screen(Coordinate(x=2000.0, y=200.0))

    def test_y_out_of_range(self) -> None:
        v = ActionValidator(_make_gui(1920, 1080))
        with pytest.raises(InvalidCoordinateError):
            v.validate_coordinates_in_screen(Coordinate(x=100.0, y=1200.0))

    def test_negative_x(self) -> None:
        v = ActionValidator(_make_gui())
        with pytest.raises(icontract.ViolationError):
            v.validate_coordinates_in_screen(Coordinate(x=-1.0, y=100.0))

    def test_edge_invalid(self) -> None:
        v = ActionValidator(_make_gui(1920, 1080))
        with pytest.raises(InvalidCoordinateError):
            v.validate_coordinates_in_screen(Coordinate(x=1920.0, y=0.0))
