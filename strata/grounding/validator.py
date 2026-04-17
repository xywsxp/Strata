"""Coordinate boundary validation — screen bounds check."""

from __future__ import annotations

import icontract

from strata.core.errors import InvalidCoordinateError
from strata.core.types import Coordinate
from strata.env.protocols import IGUIAdapter


class ActionValidator:
    """Validates that coordinates fall within the visible screen area."""

    def __init__(self, gui: IGUIAdapter) -> None:
        self._gui = gui

    @icontract.require(
        lambda coord: coord.x >= 0 and coord.y >= 0,
        "coordinates must be non-negative",
    )
    def validate_coordinates_in_screen(self, coord: Coordinate) -> None:
        """Raise InvalidCoordinateError if coord is outside screen bounds."""
        w, h = self._gui.get_screen_size()
        if not (0 <= coord.x < w and 0 <= coord.y < h):
            raise InvalidCoordinateError(
                f"coordinate ({coord.x}, {coord.y}) outside screen [{w}x{h}]"
            )
