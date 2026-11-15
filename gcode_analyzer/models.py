"""Core data models and configuration for the G-code analyser."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping


class ConfigError(ValueError):
    """Raised when a supplied analysis configuration is invalid."""


def _num(raw: Any, name: str) -> float:
    if isinstance(raw, bool):
        raise ConfigError(f"{name} must be a number")
    if isinstance(raw, (int, float)):
        value = float(raw)
    elif isinstance(raw, str):
        try:
            value = float(raw.strip())
        except ValueError as exc:
            raise ConfigError(f"{name} must be a number") from exc
    else:
        raise ConfigError(f"{name} must be a number")
    if not math.isfinite(value):
        raise ConfigError(f"{name} must be a finite number")
    return value


@dataclass(slots=True)
class Config:
    """Machine / material parameters used for the physics and metrics.

    All lengths are millimetres, velocities mm/s, accelerations mm/s^2.
    """

    # Material ---------------------------------------------------------------
    filament_diameter: float = 1.75
    density: float = 1.24  # g/cm^3 (PLA)

    # Kinematics -------------------------------------------------------------
    accel: float = 3000.0          # printing acceleration
    travel_accel: float = 3000.0   # non-extruding acceleration
    junction_deviation: float = 0.013
    jerk: float = 8.0              # used when junction_model == "jerk"
    junction_model: str = "deviation"

    # Axis velocity ceilings (mechanical limits of the machine) --------------
    max_vx: float = 500.0
    max_vy: float = 500.0
    max_vz: float = 10.0

    # Geometry / rendering ---------------------------------------------------
    arc_tolerance: float = 0.01    # max chord deviation from true arc, mm
    max_points: int = 80000        # rendering budget sent to the browser
    build_x: float = 220.0
    build_y: float = 220.0
    build_z: float = 250.0

    # Machine overheads ------------------------------------------------------
    tool_change_time: float = 0.0  # seconds per tool change
    retract_time: float = 0.0      # extra seconds added per retraction

    model_label: str = "Custom"

    # ----------------------------------------------------------------------
    @property
    def filament_area(self) -> float:
        """Cross-sectional area of the filament in mm^2."""
        return math.pi * (self.filament_diameter / 2.0) ** 2

    def as_dict(self) -> dict[str, Any]:
        return {
            "filament_diameter": self.filament_diameter,
            "density": self.density,
            "accel": self.accel,
            "travel_accel": self.travel_accel,
            "junction_deviation": self.junction_deviation,
            "jerk": self.jerk,
            "junction_model": self.junction_model,
            "max_vx": self.max_vx,
            "max_vy": self.max_vy,
            "max_vz": self.max_vz,
            "arc_tolerance": self.arc_tolerance,
            "max_points": self.max_points,
            "build_x": self.build_x,
            "build_y": self.build_y,
            "build_z": self.build_z,
            "tool_change_time": self.tool_change_time,
            "retract_time": self.retract_time,
            "model_label": self.model_label,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "Config":
        """Build a validated config from loose JSON/query input."""
        cfg = cls()
        if not data:
            return cfg

        numeric = {
            "filament_diameter": (0.5, 5.0),
            "density": (0.1, 30.0),
            "accel": (10.0, 200000.0),
            "travel_accel": (10.0, 200000.0),
            "junction_deviation": (0.0, 5.0),
            "jerk": (0.0, 200.0),
            "max_vx": (1.0, 5000.0),
            "max_vy": (1.0, 5000.0),
            "max_vz": (0.1, 2000.0),
            "arc_tolerance": (0.0005, 1.0),
            "max_points": (1000, 400000),
            "build_x": (1.0, 5000.0),
            "build_y": (1.0, 5000.0),
            "build_z": (1.0, 5000.0),
            "tool_change_time": (0.0, 600.0),
            "retract_time": (0.0, 60.0),
        }
        for key, (low, high) in numeric.items():
            if key in data and data[key] is not None and data[key] != "":
                value = _num(data[key], key)
                if value < low or value > high:
                    raise ConfigError(
                        f"{key} must be between {low:g} and {high:g}"
                    )
                setattr(cfg, key, value)

        if "junction_model" in data and data["junction_model"]:
            model = str(data["junction_model"]).strip().lower()
            if model not in ("deviation", "jerk"):
                raise ConfigError("junction_model must be 'deviation' or 'jerk'")
            cfg.junction_model = model

        label = data.get("model_label")
        if label:
            cfg.model_label = str(label)[:64]

        cfg.max_points = int(cfg.max_points)
        return cfg


@dataclass(slots=True)
class Segment:
    """A single interpolated motion block (arcs are pre-chords, like firmware)."""

    x0: float
    y0: float
    z0: float
    x1: float
    y1: float
    z1: float
    length: float
    feed: float      # commanded speed cap for this block, mm/s
    accel: float     # acceleration for this block, mm/s^2
    e: float         # filament consumed, mm (negative = retract)
    extrude: bool
    arc: bool
    layer: int
    run: int         # discontinuous motion group (split at dwells/tool changes)
    line: int

    # Filled in by the planner
    ux: float = 0.0
    uy: float = 0.0
    uz: float = 0.0
    t: float = 0.0
    v: float = 0.0   # mean speed, mm/s


@dataclass(slots=True)
class Move:
    """A parsed logical move, before arc interpolation."""

    kind: int             # 0 = linear, 1 = arc, 2 = dwell, 3 = barrier
    x0: float = 0.0
    y0: float = 0.0
    z0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    z1: float = 0.0
    feed: float = 0.0     # mm/s
    e: float = 0.0        # filament mm delta
    line: int = 0
    cw: bool = True
    plane: int = 17
    cx: float = 0.0
    cy: float = 0.0
    cz: float = 0.0
    radius: float | None = None
    accel: float = 0.0
    dwell: float = 0.0    # seconds, for kind == 2


@dataclass(slots=True)
class ArcGeom:
    points: list[tuple[float, float, float]]
    length: float         # true arc length (helix included), mm
    sweep: float          # radians, signed
    radius: float
    full_circle: bool


@dataclass
class FileInfo:
    """Machine-relevant values scraped from the file."""

    slicer: str | None = None
    nozzle_temp: float | None = None
    bed_temp: float | None = None
    file_accel: float | None = None
    file_accel_travel: float | None = None
    fan_max: float | None = None
    tool_changes: int = 0
    retractions: int = 0
    arcs: int = 0
    arc_length: float = 0.0
    lines: int = 0
    parsed_moves: int = 0
    warnings: list[str] = field(default_factory=list)
    volumetric_e: bool = False
