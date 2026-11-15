"""Exact circular-arc geometry: G2/G3 in every plane, IJK and R forms.

Arcs are subdivided into chords with a bounded chordal deviation (the same
thing firmware does when it plans an arc), so the very same point set drives
both the 3D render and the acceleration-limited time estimate.
"""

from __future__ import annotations

import math

from .models import ArcGeom

TAU = 2.0 * math.pi

# For each G17/G18/G19 plane: (in-plane axis a, in-plane axis b, helical axis).
# (a, b, helical) is right-handed, helical being the arc's rotation axis.
PLANES: dict[int, tuple[int, int, int]] = {
    17: (0, 1, 2),  # XY, normal +Z
    18: (2, 0, 1),  # ZX, normal +Y
    19: (1, 2, 0),  # YZ, normal +X
}

# Which I/J/K word carries the centre offset for in-plane axes a and b.
CENTER_WORDS: dict[int, tuple[str, str]] = {
    17: ("I", "J"),
    18: ("K", "I"),  # G18 uses I for X and K for Z
    19: ("J", "K"),  # G19 uses J for Y and K for Z
}


class ArcError(ValueError):
    """The arc description is geometrically invalid."""


def _sweep(a0: float, a1: float, cw: bool, allow_full: bool = True) -> float:
    """Signed sweep angle from a0 to a1 following the commanded direction."""
    if cw:
        delta = (a0 - a1) % TAU
        if delta <= 1e-12:
            if not allow_full:
                return 0.0
            delta = TAU
        return -delta
    delta = (a1 - a0) % TAU
    if delta <= 1e-12:
        if not allow_full:
            return 0.0
        delta = TAU
    return delta


def center_from_radius(
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    radius: float,
    plane: int,
    cw: bool,
) -> tuple[float, float, float]:
    """Recover the arc centre from an R word.

    Positive R selects the minor arc (<= 180 deg), negative R the major arc,
    per RS274/NGC. If the endpoints are further apart than the diameter, R is
    enlarged to a half circle (Marlin behaves the same way).
    """
    ia, ib, _ = PLANES[plane]
    a0, b0 = start[ia], start[ib]
    a1, b1 = end[ia], end[ib]
    dx, dy = a1 - a0, b1 - b0
    chord = math.hypot(dx, dy)
    if chord <= 1e-9:
        raise ArcError("R-form arc with coincident endpoints is underdetermined")

    r = abs(radius)
    if chord > 2.0 * r:
        r = chord / 2.0
    h = math.sqrt(max(0.0, r * r - (chord * chord) / 4.0))
    mx, my = (a0 + a1) / 2.0, (b0 + b1) / 2.0
    # Two candidate centres, offset perpendicular to the chord.
    perp_x, perp_y = -dy / chord, dx / chord
    candidates = [
        (mx + perp_x * h, my + perp_y * h),
        (mx - perp_x * h, my - perp_y * h),
    ]

    best = None
    for ca, cb in candidates:
        a_start = math.atan2(b0 - cb, a0 - ca)
        a_end = math.atan2(b1 - cb, a1 - ca)
        sweep = _sweep(a_start, a_end, cw, allow_full=False)
        if sweep == 0.0:  # degenerate half circle: treat as +-pi
            sweep = -math.pi if cw else math.pi
        minor = abs(sweep) <= math.pi + 1e-9
        score = (minor == (radius > 0), abs(sweep))
        if best is None or score > best[0]:
            best = (score, (ca, cb), sweep)

    _, (ca, cb), _ = best
    center = [0.0, 0.0, 0.0]
    center[ia] = ca
    center[ib] = cb
    return (center[0], center[1], center[2])


def build_arc(
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    center: tuple[float, float, float] | None,
    plane: int,
    cw: bool,
    tolerance: float,
    radius: float | None = None,
) -> ArcGeom:
    """Interpolate a circular (optionally helical) arc into 3D chord points.

    ``center`` is in machine coordinates and is ignored when ``radius`` is
    given. The returned points include the exact endpoints.
    """
    ia, ib, ic = PLANES[plane]
    if radius is not None:
        center = center_from_radius(start, end, radius, plane, cw)
    elif center is None:
        raise ArcError("arc needs either a centre or a radius")

    a0, b0 = start[ia], start[ib]
    a1, b1 = end[ia], end[ib]
    ca, cb = center[ia], center[ib]

    r0 = math.hypot(a0 - ca, b0 - cb)
    r1 = math.hypot(a1 - ca, b1 - cb)
    if r0 <= 1e-9:
        raise ArcError("arc radius is zero")
    if abs(r0 - r1) > 1e-3:
        # Start/end radius mismatch: azimuthal error in hand-written files.
        # Use the mean radius so the path stays continuous in angle.
        r = (r0 + r1) / 2.0
    else:
        r = r0

    a_start = math.atan2(b0 - cb, a0 - ca)
    a_end = math.atan2(b1 - cb, a1 - ca)
    sweep = _sweep(a_start, a_end, cw, allow_full=True)

    # Chord tolerance -> angular step. deviation = R*(1 - cos(step/2))
    ratio = 1.0 - min(max(tolerance, 1e-6) / r, 1.0)
    step = 2.0 * math.acos(max(-1.0, min(1.0, ratio)))
    step = max(min(step, math.pi / 2.0), 1e-4)
    segments = int(math.ceil(abs(sweep) / step))
    segments = max(1, min(segments, 8192))

    c0, c1 = start[ic], end[ic]
    points: list[tuple[float, float, float]] = [tuple(start)]  # type: ignore[list-item]
    length = 0.0
    prev = points[0]
    for i in range(1, segments + 1):
        frac = i / segments
        if i == segments:
            point = tuple(end)  # land exactly on the commanded endpoint
        else:
            ang = a_start + sweep * frac
            p = [0.0, 0.0, 0.0]
            p[ia] = ca + r * math.cos(ang)
            p[ib] = cb + r * math.sin(ang)
            p[ic] = c0 + (c1 - c0) * frac
            point = (p[0], p[1], p[2])
        length += math.dist(prev, point)
        points.append(point)  # type: ignore[arg-type]
        prev = point

    return ArcGeom(
        points=points,
        length=length,
        sweep=sweep,
        radius=r,
        full_circle=abs(abs(sweep) - TAU) < 1e-9,
    )


def arc_length_from_ijk(
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    center: tuple[float, float, float],
    plane: int,
    cw: bool,
) -> float:
    """Analytic arc length (helix included), without subdivision."""
    ia, ib, ic = PLANES[plane]
    r = math.hypot(start[ia] - center[ia], start[ib] - center[ib])
    a_start = math.atan2(start[ib] - center[ib], start[ia] - center[ia])
    a_end = math.atan2(end[ib] - center[ib], end[ia] - center[ia])
    sweep = _sweep(a_start, a_end, cw, allow_full=True)
    planar = r * abs(sweep)
    dz = end[ic] - start[ic]
    return math.hypot(planar, dz)
