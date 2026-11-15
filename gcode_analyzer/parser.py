"""Streaming RS-274 / Marlin-flavoured G-code parser.

Produces :class:`Move` events (linear, arc, dwell, barrier) while keeping the
full modal state a real controller keeps: units, absolute/incremental,
work offsets (G92), planes, feedrate mode, extruder mode and firmware
acceleration overrides.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Iterator
from typing import TextIO

from .geometry import CENTER_WORDS, PLANES
from .models import Config, FileInfo, Move

_WORD_RE = re.compile(r"([A-Za-z])\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))")

_AXIS_LETTERS = ("X", "Y", "Z")
_SLICER_HINTS = (
    ("prusaslicer", "PrusaSlicer"),
    ("superslicer", "SuperSlicer"),
    ("orcaslicer", "OrcaSlicer"),
    ("bambustudio", "Bambu Studio"),
    ("cura", "Cura"),
    ("simplify3d", "Simplify3D"),
    ("slic3r", "Slic3r"),
    ("ideamaker", "ideaMaker"),
    ("kisslicer", "KISSlicer"),
)


def strip_comments(line: str) -> tuple[str, str]:
    """Return ``(code, comment)`` for a raw G-code line."""
    comment_parts: list[str] = []
    if ";" in line:
        line, _, tail = line.partition(";")
        comment_parts.append(tail.strip())
    if "(" in line:
        out = []
        depth = 0
        start = 0
        for idx, ch in enumerate(line):
            if ch == "(":
                if depth == 0:
                    out.append(line[start:idx])
                    start = idx + 1
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
                if depth == 0:
                    comment_parts.append(line[start:idx].strip())
                    start = idx + 1
        out.append(line[start:] if depth == 0 else "")
        line = "".join(out)
    if "*" in line:
        line = line.split("*", 1)[0]
    return line, " ".join(p for p in comment_parts if p)


class GCodeParser:
    """Stateful, streaming parser. Call :meth:`parse` with a text stream."""

    def __init__(self, config: Config | None = None) -> None:
        self.cfg = config or Config()
        self.info = FileInfo()

        # Modal state -----------------------------------------------------
        self.x = self.y = self.z = 0.0
        self.e = 0.0
        self.feed = 1500.0 / 60.0 * 1.0  # mm/s
        self.absolute_xyz = True
        self.absolute_e = True
        self.plane = 17
        self.scale = 1.0
        self.feed_mode = 94
        self.motion = 0
        # The configured machine acceleration is a ceiling: an in-file M204 can
        # ask for less (the slicer's choice), never for more than the machine.
        self.accel = self.cfg.accel
        self.accel_travel = self.cfg.travel_accel
        self._file_accel: float | None = None
        self._file_accel_travel: float | None = None
        self.volumetric = False
        self.tool = 0
        self._seen_slicer = False
        self._warned: set[str] = set()

    # ------------------------------------------------------------------
    def _warn(self, message: str) -> None:
        if message in self._warned or len(self.info.warnings) >= 25:
            return
        self._warned.add(message)
        self.info.warnings.append(message)

    def _note_comment(self, comment: str) -> None:
        if not comment:
            return
        low = comment.lower()
        if not self._seen_slicer and len(comment) < 120:
            for needle, name in _SLICER_HINTS:
                if needle in low:
                    self.info.slicer = name
                    self._seen_slicer = True
                    break

    # ------------------------------------------------------------------
    def parse(self, stream: Iterable[str] | TextIO) -> Iterator[Move]:
        for lineno, raw in enumerate(stream, 1):
            if lineno == 1 and raw.startswith("\ufeff"):
                raw = raw[1:]
            code, comment = strip_comments(raw)
            if comment:
                self._note_comment(comment)
            code = code.strip()
            if not code:
                continue
            self.info.lines = lineno

            gcodes: list[float] = []
            mcodes: list[float] = []
            vals: dict[str, float] = {}
            for letter, number in _WORD_RE.findall(code):
                upper = letter.upper()
                try:
                    value = float(number)
                except ValueError:
                    continue
                if upper == "G":
                    gcodes.append(value)
                elif upper == "M":
                    mcodes.append(value)
                elif upper in "XYZEFIJKRSTPQD":
                    vals[upper] = value

            consume_coordinates = False
            for g in gcodes:
                if int(round(g)) in (28, 92):
                    consume_coordinates = True
                for event in self._handle_g(g, vals, lineno):
                    yield event
            for m in mcodes:
                for event in self._handle_m(m, vals, lineno):
                    yield event

            # A tool change word (T0 ...) is a hard stop for motion planning.
            # Inside an M-code, T is a parameter (``M204 T2500``), not a tool.
            if "T" in vals and not mcodes:
                new_tool = int(round(vals["T"]))
                if new_tool != self.tool:
                    self.tool = new_tool
                    self.info.tool_changes += 1
                    yield Move(
                        kind=3,
                        dwell=self.cfg.tool_change_time,
                        line=lineno,
                    )

            # M-codes carry parameters that look like coordinates
            # (``M201 X1000 Y1000``); they must never produce motion.
            explicit_motion = any(int(round(g)) in (0, 1, 2, 3) for g in gcodes)
            if mcodes and not explicit_motion:
                continue

            motion_requested = any(axis in vals for axis in _AXIS_LETTERS)
            if consume_coordinates and not ("E" in vals):
                continue
            if not (motion_requested or "E" in vals or "F" in vals):
                continue

            move = self._make_move(vals, lineno, gcodes)
            if move is not None:
                yield move

    # ------------------------------------------------------------------
    def _handle_g(self, g: float, vals: dict[str, float], lineno: int) -> Iterator[Move]:
        code = int(round(g))
        if code in (0, 1, 2, 3):
            self.motion = code
            return
        if code == 4:
            # Dwell: P is milliseconds, S is seconds.
            seconds = 0.0
            if "P" in vals:
                seconds = vals["P"] / 1000.0
            elif "S" in vals:
                seconds = vals["S"]
            if seconds > 0:
                yield Move(kind=2, dwell=seconds, line=lineno)
        elif code == 20:
            self.scale = 25.4
        elif code == 21:
            self.scale = 1.0
        elif code == 90:
            self.absolute_xyz = True
        elif code == 91:
            self.absolute_xyz = False
        elif code == 17:
            self.plane = 17
        elif code == 18:
            self.plane = 18
        elif code == 19:
            self.plane = 19
        elif code == 92:
            for axis in _AXIS_LETTERS:
                if axis in vals:
                    setattr(self, axis.lower(), vals[axis] * self.scale)
            if "E" in vals:
                self.e = self._e_value(vals["E"], absolute=True)
            yield Move(kind=3, line=lineno)
        elif code == 28:
            homed = [axis for axis in _AXIS_LETTERS if axis in vals]
            for axis in homed or _AXIS_LETTERS:
                setattr(self, axis.lower(), 0.0)
            self._warn("G28 homing move: homing time is not included in the estimate")
            yield Move(kind=3, line=lineno)
        elif code == 93:
            self.feed_mode = 93
        elif code == 94:
            self.feed_mode = 94
        elif code in (10, 11):
            self._warn("G10/G11 firmware retraction: retract distance is not modelled")
        return

    def _handle_m(self, m: float, vals: dict[str, float], lineno: int) -> Iterator[Move]:
        code = int(round(m))
        if code == 82:
            self.absolute_e = True
        elif code == 83:
            self.absolute_e = False
        elif code == 200:
            if "D" in vals:
                diameter = vals["D"]
                if diameter <= 0:
                    self.volumetric = False
                else:
                    self.volumetric = True
                    self.info.volumetric_e = True
                    self._warn(
                        "M200 volumetric extrusion detected: E values are mm^3 "
                        "and were converted to filament length"
                    )
        elif code == 204:
            requested = None
            requested_travel = None
            if "S" in vals:
                requested = requested_travel = max(vals["S"], 1.0)
            if "P" in vals:
                requested = max(vals["P"], 1.0)
            if "T" in vals:
                requested_travel = max(vals["T"], 1.0)
            if requested is not None:
                self._file_accel = requested
                self.accel = min(self.cfg.accel, requested)
            if requested_travel is not None:
                self._file_accel_travel = requested_travel
                self.accel_travel = min(self.cfg.travel_accel, requested_travel)
            if self._file_accel is not None or self._file_accel_travel is not None:
                self.info.file_accel = self._file_accel
                self.info.file_accel_travel = self._file_accel_travel
                self._warn(
                    "M204 in the file requests "
                    f"{self._file_accel or self._file_accel_travel:g}"
                    f"/{self._file_accel_travel or self._file_accel:g} mm/s^2: "
                    "the configured machine acceleration is used as a ceiling"
                )
        elif code == 205:
            if "J" in vals:  # Marlin: junction deviation
                self._warn("M205 J overrides the junction deviation setting from the file")
        elif code in (0, 1, 25, 226):
            self._warn("Programmed pause (M0/M1/M25): wait time is not included")
            yield Move(kind=3, line=lineno)
        elif code == 600:
            self._warn("M600 filament change: operator time is not included")
            yield Move(kind=3, line=lineno)
        elif code in (104, 109):
            if "S" in vals:
                self.info.nozzle_temp = vals["S"]
        elif code in (140, 190):
            if "S" in vals:
                self.info.bed_temp = vals["S"]
        elif code == 106:
            if "S" in vals:
                self.info.fan_max = max(self.info.fan_max or 0.0, vals["S"])
            elif self.info.fan_max is None:
                self.info.fan_max = 255.0
        return

    # ------------------------------------------------------------------
    def _e_value(self, raw: float, absolute: bool) -> float:
        """Convert an E word to filament millimetres."""
        factor = 1.0 / self.cfg.filament_area if self.volumetric else 1.0
        value = raw * factor
        if absolute:
            return value
        return self.e + value

    def _make_move(
        self, vals: dict[str, float], lineno: int, gcodes: list[float]
    ) -> Move | None:
        if "F" in vals:
            new_feed = vals["F"] * self.scale / 60.0
            if new_feed > 0:
                self.feed = new_feed

        def target(axis: str) -> float:
            letter = axis.upper()
            if letter not in vals:
                return getattr(self, axis.lower())
            value = vals[letter] * self.scale
            if self.absolute_xyz:
                return value
            return getattr(self, axis.lower()) + value

        x1, y1, z1 = target("x"), target("y"), target("z")
        e1 = self.e
        de = 0.0
        if "E" in vals:
            e1 = self._e_value(vals["E"], self.absolute_e)
            de = e1 - self.e

        start = (self.x, self.y, self.z)
        end = (x1, y1, z1)
        distance = math.dist(start, end)
        has_ijk = any(k in vals for k in ("I", "J", "K"))

        feed = self.feed
        if self.feed_mode == 93 and "F" in vals and vals["F"] > 0:
            feed = max(distance * vals["F"] / 60.0, 1e-3)

        kind = self.motion
        move: Move
        if kind in (2, 3) and (has_ijk or "R" in vals):
            ia, ib, _ = PLANES[self.plane]
            a_letter, b_letter = CENTER_WORDS[self.plane]
            center = [0.0, 0.0, 0.0]
            center[ia] = start[ia] + vals.get(a_letter, 0.0) * self.scale
            center[ib] = start[ib] + vals.get(b_letter, 0.0) * self.scale
            move = Move(
                kind=1,
                x0=start[0], y0=start[1], z0=start[2],
                x1=end[0], y1=end[1], z1=end[2],
                feed=feed,
                e=de,
                line=lineno,
                cw=(kind == 2),
                plane=self.plane,
                cx=center[0], cy=center[1], cz=center[2],
                radius=vals["R"] * self.scale if "R" in vals else None,
            )
        else:
            if kind in (2, 3):
                self._warn(
                    "Arc command without I/J/K or R: interpreted as a straight move"
                )
            move = Move(
                kind=0,
                x0=start[0], y0=start[1], z0=start[2],
                x1=end[0], y1=end[1], z1=end[2],
                feed=feed,
                e=de,
                line=lineno,
            )

        # Acceleration depends on whether material is being deposited.
        move.accel = self.accel if de > 0 else self.accel_travel

        self.x, self.y, self.z, self.e = x1, y1, z1, e1
        self.info.parsed_moves += 1
        return move
