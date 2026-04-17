"""Coordinate DPI scaling — logical ↔ physical conversion."""

from __future__ import annotations

import icontract

from strata.core.types import Coordinate
from strata.env.protocols import IGUIAdapter


class CoordinateScaler:
    """Converts between logical (user-space) and physical (pixel) coordinates."""

    def __init__(self, gui: IGUIAdapter) -> None:
        self._gui = gui

    @icontract.require(lambda coord: coord.x >= 0 and coord.y >= 0, "coords must be non-negative")
    @icontract.ensure(lambda result: result.x >= 0 and result.y >= 0, "result must be non-negative")
    def logical_to_physical(self, coord: Coordinate) -> Coordinate:
        """Scale logical coordinate to physical pixel coordinate."""
        scale = self._gui.get_dpi_scale_for_point(coord.x, coord.y)
        assert scale > 0, f"invariant: DPI scale must be positive, got {scale}"
        return Coordinate(x=coord.x * scale, y=coord.y * scale)

    @icontract.require(lambda coord: coord.x >= 0 and coord.y >= 0, "coords must be non-negative")
    @icontract.ensure(lambda result: result.x >= 0 and result.y >= 0, "result must be non-negative")
    def physical_to_logical(self, coord: Coordinate) -> Coordinate:
        """Scale physical pixel coordinate to logical user-space coordinate."""
        scale = self._gui.get_dpi_scale_for_point(coord.x, coord.y)
        assert scale > 0, f"invariant: DPI scale must be positive, got {scale}"
        return Coordinate(x=coord.x / scale, y=coord.y / scale)
