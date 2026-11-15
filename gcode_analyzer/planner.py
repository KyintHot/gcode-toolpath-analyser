"""Acceleration-limited motion planning.

Implements the same structure a real firmware planner uses:

1. every block gets a unit direction vector and a speed ceiling
   (commanded feed, reduced by the per-axis velocity limits),
2. cornering speed at each junction from either *junction deviation*
   (Marlin 2 / PrusaSlicer) or *classic per-axis jerk*,
3. a forward pass that limits each junction by how much the machine can
   accelerate out of it, and a backward pass that limits it by how much it can
   decelerate into it,
4. per-block trapezoidal (or triangular, for short blocks) time integration.

The result is a physically plausible time that is always longer than
``distance / feedrate`` -- which is exactly the error the naive estimate makes.
"""

from __future__ import annotations

import math

from .models import Config, Segment

INF = float("inf")


def axis_limited(feed: float, ux: float, uy: float, uz: float, cfg: Config) -> float:
    """Clip a feedrate so no single axis exceeds its mechanical limit."""
    limit = feed
    for u, axis_max in ((ux, cfg.max_vx), (uy, cfg.max_vy), (uz, cfg.max_vz)):
        if abs(u) > 1e-9:
            limit = min(limit, axis_max / abs(u))
    return max(limit, 0.0)


def speed_limit(segment: Segment, cfg: Config) -> float:
    """Commanded feed of a block, clipped by the axis velocity limits.

    Works on freshly built segments too (unit vectors are derived here), so the
    naive ``distance / feedrate`` baseline uses exactly the same ceilings.
    """
    length = segment.length
    if length > 1e-12:
        ux = (segment.x1 - segment.x0) / length
        uy = (segment.y1 - segment.y0) / length
        uz = (segment.z1 - segment.z0) / length
    else:
        ux, uy, uz = segment.ux, segment.uy, segment.uz
    return axis_limited(segment.feed, ux, uy, uz, cfg)


def plan(segments: list[Segment], cfg: Config) -> None:
    """Fill ``t`` and ``v`` for every segment (in place)."""
    n = len(segments)
    if n == 0:
        return

    limit = [0.0] * n
    for i, seg in enumerate(segments):
        length = seg.length
        if length > 1e-12:
            seg.ux = (seg.x1 - seg.x0) / length
            seg.uy = (seg.y1 - seg.y0) / length
            seg.uz = (seg.z1 - seg.z0) / length
        limit[i] = axis_limited(seg.feed, seg.ux, seg.uy, seg.uz, cfg)

    # Junction speeds: v[i] is the speed at the boundary before segment i.
    v = [0.0] * (n + 1)
    for i in range(1, n):
        prev, nxt = segments[i - 1], segments[i]
        if prev.run != nxt.run:
            v[i] = 0.0
        else:
            v[i] = min(
                junction_speed(prev, nxt, cfg),
                limit[i - 1],
                limit[i],
            )
    v[0] = 0.0
    v[n] = 0.0

    # Forward pass: can we accelerate to this speed in the previous block?
    for i in range(1, n + 1):
        seg = segments[i - 1]
        reachable = math.sqrt(v[i - 1] * v[i - 1] + 2.0 * seg.accel * seg.length)
        if v[i] > reachable:
            v[i] = reachable

    # Backward pass: can we decelerate from this speed into the next block?
    for i in range(n - 1, -1, -1):
        seg = segments[i]
        reachable = math.sqrt(v[i + 1] * v[i + 1] + 2.0 * seg.accel * seg.length)
        if v[i] > reachable:
            v[i] = reachable

    for i, seg in enumerate(segments):
        seg.t = _block_time(seg.length, v[i], v[i + 1], limit[i], seg.accel)
        seg.v = seg.length / seg.t if seg.t > 0 else 0.0


def junction_speed(prev: Segment, nxt: Segment, cfg: Config) -> float:
    """Highest speed physically allowed across the corner between two blocks.

    ``junction_model == "deviation"`` uses the chord-error model from Marlin 2
    and PrusaSlicer: v^2 = a * delta * sin(theta/2) / (1 - sin(theta/2)), where
    theta is the interior angle between the two path directions. A straight
    junction (theta = 180 deg) allows the feedrate, a full reversal stops.

    ``junction_model == "jerk"`` uses the classic per-axis jerk limit
    v <= jerk / max(|u_prev_axis|, |u_next_axis|).
    """
    dot = prev.ux * nxt.ux + prev.uy * nxt.uy + prev.uz * nxt.uz
    dot = max(-1.0, min(1.0, dot))

    if cfg.junction_model == "jerk":
        speed = INF
        for ua, ub in ((prev.ux, nxt.ux), (prev.uy, nxt.uy), (prev.uz, nxt.uz)):
            m = max(abs(ua), abs(ub))
            if m > 1e-9:
                speed = min(speed, cfg.jerk / m)
        return speed

    sin_half = math.sqrt(max(0.0, (1.0 + dot) / 2.0))
    if sin_half >= 0.9999:
        return INF
    accel = min(prev.accel, nxt.accel)
    return math.sqrt(
        max(0.0, accel * cfg.junction_deviation * sin_half / (1.0 - sin_half))
    )


def _block_time(
    length: float, v0: float, v1: float, vmax: float, accel: float
) -> float:
    """Time for a block with entry/exit speeds under a trapezoidal profile."""
    if length <= 0.0 or vmax <= 0.0:
        return 0.0
    v0 = min(v0, vmax)
    v1 = min(v1, vmax)
    accel = max(accel, 1e-6)

    # Peak speed the block can reach: v^2 = (2aL + v0^2 + v1^2) / 2
    peak_sq = (2.0 * accel * length + v0 * v0 + v1 * v1) / 2.0
    if peak_sq < vmax * vmax:
        peak = math.sqrt(max(peak_sq, 0.0))
        return max(0.0, (peak - v0) / accel) + max(0.0, (peak - v1) / accel)

    dist_accel = (vmax * vmax - v0 * v0) / (2.0 * accel)
    dist_decel = (vmax * vmax - v1 * v1) / (2.0 * accel)
    cruise = max(0.0, length - dist_accel - dist_decel)
    return (
        max(0.0, (vmax - v0) / accel)
        + cruise / vmax
        + max(0.0, (vmax - v1) / accel)
    )
