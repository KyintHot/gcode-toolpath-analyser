"""Orchestration: parse -> interpolate -> plan -> aggregate engineering metrics."""

from __future__ import annotations

import io
import math
import statistics
from dataclasses import dataclass
from typing import Any

from .geometry import ArcError, build_arc
from .models import Config, FileInfo, Segment
from .parser import GCodeParser
from .planner import plan, speed_limit

EPS = 1e-7
LAYER_EPS = 0.05  # mm of Z rise that counts as a new layer
ARC_MAX_POINTS = 8192


@dataclass
class AnalysisResult:
    metrics: dict[str, Any]
    path: dict[str, Any]
    segments: list[Segment]
    info: FileInfo


def _round(value: float, digits: int = 3) -> float:
    if not math.isfinite(value):
        return 0.0
    return round(value, digits)


def analyze(text: str, cfg: Config, filename: str = "") -> AnalysisResult:
    parser = GCodeParser(cfg)
    segments: list[Segment] = []

    run = 0
    layer = -1
    layer_z: float | None = None
    layer_z_map: dict[int, float] = {}
    dwell_time = 0.0
    retract_time = 0.0
    e_pos = 0.0
    e_neg = 0.0
    e_prime = 0.0
    retractions = 0
    arc_count = 0
    arc_length_total = 0.0
    area = cfg.filament_area

    minx = miny = minz = math.inf
    maxx = maxy = maxz = -math.inf
    pminx = pminy = pminz = math.inf
    pmaxx = pmaxy = pmaxz = -math.inf
    warnings: list[str] = []

    def warn(message: str) -> None:
        if message not in warnings and len(warnings) < 30:
            warnings.append(message)

    def touch(points: list[tuple[float, float, float]], deposited: bool) -> None:
        """Track the full motion envelope and the deposited-part bounding box."""
        nonlocal minx, miny, minz, maxx, maxy, maxz
        nonlocal pminx, pminy, pminz, pmaxx, pmaxy, pmaxz
        for x, y, z in points:
            if x < minx:
                minx = x
            if x > maxx:
                maxx = x
            if y < miny:
                miny = y
            if y > maxy:
                maxy = y
            if z < minz:
                minz = z
            if z > maxz:
                maxz = z
            if deposited:
                if x < pminx:
                    pminx = x
                if x > pmaxx:
                    pmaxx = x
                if y < pminy:
                    pminy = y
                if y > pmaxy:
                    pmaxy = y
                if z < pminz:
                    pminz = z
                if z > pmaxz:
                    pmaxz = z

    for move in parser.parse(io.StringIO(text)):
        if move.kind in (2, 3):
            dwell_time += move.dwell
            run += 1
            continue

        start = (move.x0, move.y0, move.z0)
        end = (move.x1, move.y1, move.z1)
        straight = math.dist(start, end)

        # Zero-length moves can still retract / prime filament: they cost time
        # but deposit no material, so they must not inflate the volume figure.
        if straight < EPS and move.kind == 0:
            if move.e:
                retract_time += abs(move.e) / max(move.feed, 1e-3) + cfg.retract_time
                if move.e < 0:
                    e_neg += -move.e
                    retractions += 1
                else:
                    e_prime += move.e
            continue

        if move.e > 0:
            if layer_z is None or end[2] > layer_z + LAYER_EPS:
                layer_z = end[2]
                layer += 1
                layer_z_map[layer] = end[2]

        if move.accel <= 0:
            move.accel = cfg.accel if move.e > 0 else cfg.travel_accel

        if move.kind == 1:
            try:
                geom = build_arc(
                    start,
                    end,
                    (move.cx, move.cy, move.cz),
                    move.plane,
                    move.cw,
                    cfg.arc_tolerance,
                    radius=move.radius,
                )
            except (ArcError, ValueError) as exc:
                warn(f"Line {move.line}: invalid arc ({exc}); drew it as a straight line")
                geom = None
            if geom is not None:
                points = geom.points
                arc_count += 1
                arc_length_total += geom.length
        else:
            points = None

        if points is None:
            points = [start, end]
            chord_count = 1
        else:
            chord_count = len(points) - 1

        touch(points, move.e > 0)
        total_len = 0.0
        lengths: list[float] = []
        for i in range(chord_count):
            p0 = points[i]
            p1 = points[i + 1]
            d = math.dist(p0, p1)
            lengths.append(d)
            total_len += d

        if total_len < EPS:
            if move.e:
                retract_time += abs(move.e) / max(move.feed, 1e-3) + cfg.retract_time
                if move.e < 0:
                    e_neg += -move.e
                    retractions += 1
                else:
                    e_prime += move.e
            continue

        extrude = move.e > 0
        seg_layer = layer
        for i, d in enumerate(lengths):
            if d < EPS:
                continue
            p0 = points[i]
            p1 = points[i + 1]
            share = move.e * (d / total_len) if chord_count > 1 else move.e
            if share > 0:
                e_pos += share
            elif share < 0:
                e_neg += -share
            segments.append(
                Segment(
                    x0=p0[0], y0=p0[1], z0=p0[2],
                    x1=p1[0], y1=p1[1], z1=p1[2],
                    length=d,
                    feed=move.feed,
                    accel=move.accel,
                    e=share,
                    extrude=extrude,
                    arc=points is not None and chord_count > 1,
                    layer=max(seg_layer, 0),
                    run=run,
                    line=move.line,
                )
            )

    plan(segments, cfg)

    # ---------------------------------------------------------------- totals
    motion_time = 0.0
    print_time = 0.0
    path_total = 0.0
    path_print = 0.0
    naive_time = 0.0
    max_speed = 0.0
    weighted_speed = 0.0
    max_flow = 0.0
    layer_stats: dict[int, dict[str, float]] = {}

    for seg in segments:
        motion_time += seg.t
        path_total += seg.length
        naive_time += seg.length / speed_limit(seg, cfg) if speed_limit(seg, cfg) > 0 else 0.0
        if seg.extrude:
            print_time += seg.t
            path_print += seg.length
            weighted_speed += seg.v * seg.length
            flow = (seg.e * area) / seg.t if seg.t > 0 else 0.0
            max_flow = max(max_flow, flow)
        if seg.v > max_speed:
            max_speed = seg.v
        bucket = layer_stats.setdefault(seg.layer, {"t": 0.0, "e": 0.0, "len": 0.0})
        bucket["t"] += seg.t
        bucket["e"] += max(seg.e, 0.0)
        bucket["len"] += seg.length

    total_time = motion_time + dwell_time + retract_time
    if path_total <= 0:
        # Nothing moved: skip the engine but still return a usable payload.
        total_time = dwell_time
    travel_time = max(0.0, motion_time - print_time)

    volume_mm3 = e_pos * area
    mass_g = volume_mm3 / 1000.0 * cfg.density
    extruded_volume_per_s = volume_mm3 / print_time if print_time > 0 else 0.0

    if minx is math.inf:
        minx = miny = minz = maxx = maxy = maxz = 0.0
    # The part's dimensions come from deposited moves only; a fallback to the
    # full motion envelope covers files that never extrude (lasers, mills).
    if pminx is not math.inf:
        minx, miny, minz, maxx, maxy, maxz = pminx, pminy, pminz, pmaxx, pmaxy, pmaxz
    size = (maxx - minx, maxy - miny, maxz - minz)

    z_values = sorted(layer_z_map.values())
    layer_height = 0.0
    if len(z_values) > 1:
        diffs = [b - a for a, b in zip(z_values, z_values[1:]) if b - a > 1e-4]
        if diffs:
            layer_height = statistics.median(diffs)

    # ------------------------------------------------------- layer breakdown
    layer_rows = []
    for index in sorted(layer_stats):
        row = layer_stats[index]
        layer_rows.append(
            {
                "i": index,
                "z": _round(layer_z_map.get(index, 0.0), 3),
                "time_s": _round(row["t"], 3),
                "filament_mm": _round(row["e"], 2),
                "length_mm": _round(row["len"], 1),
            }
        )

    # ----------------------------------------------------- feedrate analysis
    feed_buckets: dict[float, dict[str, float]] = {}
    for seg in segments:
        if not seg.extrude:
            continue
        key = round(seg.feed * 2.0) / 2.0
        entry = feed_buckets.setdefault(key, {"t": 0.0, "len": 0.0})
        entry["t"] += seg.t
        entry["len"] += seg.length
    feed_rows = [
        {"feed_mm_s": round(k, 2), "time_s": round(v["t"], 3), "length_mm": round(v["len"], 1)}
        for k, v in sorted(feed_buckets.items())
        if v["t"] > 0
    ]

    info = parser.info
    info.arcs = arc_count
    info.arc_length = arc_length_total
    info.retractions = retractions
    all_warnings = info.warnings + [w for w in warnings if w not in info.warnings]

    metrics: dict[str, Any] = {
        "file": {
            "name": filename or "uploaded.gcode",
            "lines": info.lines,
            "slicer": info.slicer,
            "nozzle_temp": info.nozzle_temp,
            "bed_temp": info.bed_temp,
            "file_accel": info.file_accel,
            "file_accel_travel": info.file_accel_travel,
            "fan_max": info.fan_max,
            "volumetric_e": info.volumetric_e,
        },
        "time": {
            "total_s": _round(total_time, 2),
            "motion_s": _round(motion_time, 2),
            "print_s": _round(print_time, 2),
            "travel_s": _round(travel_time, 2),
            "dwell_s": _round(dwell_time, 2),
            "retract_s": _round(retract_time, 2),
            "naive_s": _round(naive_time + dwell_time + retract_time, 2),
            "accel_overhead_pct": _round(
                (total_time / (naive_time + dwell_time + retract_time) - 1.0) * 100.0, 1
            )
            if (naive_time + dwell_time + retract_time) > 0
            else 0.0,
        },
        "filament": {
            "length_mm": _round(e_pos, 2),
            "retracted_mm": _round(abs(e_neg), 2),
            "primed_mm": _round(e_prime, 2),
            "volume_cm3": _round(volume_mm3 / 1000.0, 4),
            "mass_g": _round(mass_g, 4),
            "diameter_mm": cfg.filament_diameter,
            "density_g_cm3": cfg.density,
        },
        "bbox": {
            "min": [_round(minx), _round(miny), _round(minz)],
            "max": [_round(maxx), _round(maxy), _round(maxz)],
            "size": [_round(size[0]), _round(size[1]), _round(size[2])],
            "center": [
                _round((minx + maxx) / 2.0),
                _round((miny + maxy) / 2.0),
                _round((minz + maxz) / 2.0),
            ],
        },
        "motion": {
            "segments": len(segments),
            "arcs": arc_count,
            "arc_length_mm": _round(arc_length_total, 1),
            "path_mm": _round(path_total, 1),
            "print_mm": _round(path_print, 1),
            "travel_mm": _round(path_total - path_print, 1),
            "layers": len(layer_z_map),
            "layer_height_mm": _round(layer_height, 3),
            "max_speed_mm_s": _round(max_speed, 1),
            "mean_print_speed_mm_s": _round(weighted_speed / path_print, 1)
            if path_print > 0
            else 0.0,
            "max_flow_mm3_s": _round(max_flow, 2),
            "mean_flow_mm3_s": _round(extruded_volume_per_s, 2),
            "retractions": retractions,
            "tool_changes": info.tool_changes,
        },
        "layers": layer_rows,
        "feeds": feed_rows,
        "config": cfg.as_dict(),
        "warnings": all_warnings,
    }

    path = _build_path(segments, cfg)
    return AnalysisResult(metrics=metrics, path=path, segments=segments, info=info)


def _build_path(segments: list[Segment], cfg: Config) -> dict[str, Any]:
    """Decimate the toolpath to a rendering budget while keeping its shape.

    A point survives if it is the first/last point, if the move type or layer
    changes, if the direction changes noticeably (corners and arcs), or if it
    falls on the uniform sampling grid used for long straight runs.
    """
    n = len(segments)
    if n == 0:
        return {
            "x": [], "y": [], "z": [], "speed": [], "extrude": [], "layer": [],
            "count": 0, "total_points": 0,
        }

    budget = max(1000, int(cfg.max_points))
    step = max(1, math.ceil((n + 1) / budget))
    keep = bytearray(n + 1)
    keep[0] = 1
    keep[n] = 1
    prev: Segment | None = None
    for i in range(n):
        seg = segments[i]
        if prev is None:
            keep[i] = 1
        elif (
            i % step == 0
            or seg.extrude != prev.extrude
            or seg.layer != prev.layer
            or (seg.ux * prev.ux + seg.uy * prev.uy + seg.uz * prev.uz) < 0.9995
        ):
            keep[i] = 1
        prev = seg

    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    speeds: list[float] = []
    extrude_flags: list[int] = []
    layers: list[int] = []

    for i in range(n + 1):
        if not keep[i]:
            continue
        if i == 0:
            seg = segments[0]
            point = (seg.x0, seg.y0, seg.z0)
            speed, extrude, layer = seg.v, seg.extrude, seg.layer
        elif i == n:
            seg = segments[-1]
            point = (seg.x1, seg.y1, seg.z1)
            speed, extrude, layer = seg.v, seg.extrude, seg.layer
        else:
            seg = segments[i - 1]
            point = (seg.x1, seg.y1, seg.z1)
            speed, extrude, layer = seg.v, seg.extrude, seg.layer
        xs.append(_round(point[0], 3))
        ys.append(_round(point[1], 3))
        zs.append(_round(point[2], 3))
        speeds.append(_round(speed, 2))
        extrude_flags.append(1 if extrude else 0)
        layers.append(layer)

    return {
        "x": xs,
        "y": ys,
        "z": zs,
        "speed": speeds,
        "extrude": extrude_flags,
        "layer": layers,
        "count": len(xs),
        "total_points": n + 1,
    }
