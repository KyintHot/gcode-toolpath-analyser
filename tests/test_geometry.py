import math

import pytest

from gcode_analyzer.geometry import (
    ArcError,
    arc_length_from_ijk,
    build_arc,
    center_from_radius,
)


def _max_chord_error(points, center, axis_a=0, axis_b=1, radius=None):
    """Largest distance between a chord midpoint and the true circle."""
    worst = 0.0
    for p0, p1 in zip(points, points[1:]):
        mid = ((p0[axis_a] + p1[axis_a]) / 2.0, (p0[axis_b] + p1[axis_b]) / 2.0)
        r = radius
        if r is None:
            r = math.hypot(p0[axis_a] - center[axis_a], p0[axis_b] - center[axis_b])
        dist = math.hypot(mid[0] - center[axis_a], mid[1] - center[axis_b])
        worst = max(worst, abs(r - dist))
    return worst


def test_semicircle_length_and_endpoints():
    start = (0.0, 0.0, 0.0)
    end = (10.0, 0.0, 0.0)
    center = (5.0, 0.0, 0.0)
    geom = build_arc(start, end, center, 17, cw=False, tolerance=0.01)
    assert geom.points[0] == pytest.approx(start)
    assert geom.points[-1] == pytest.approx(end)
    assert abs(geom.sweep) == pytest.approx(math.pi)
    assert geom.length == pytest.approx(math.pi * 5.0, rel=1e-3)
    assert _max_chord_error(geom.points, center) <= 0.011


def test_full_circle_when_endpoint_coincides():
    start = (0.0, 0.0, 0.0)
    center = (5.0, 0.0, 0.0)
    geom = build_arc(start, start, center, 17, cw=True, tolerance=0.01)
    assert geom.full_circle
    assert geom.length == pytest.approx(2 * math.pi * 5.0, rel=1e-3)


def test_clockwise_goes_the_other_way():
    start = (10.0, 0.0, 0.0)
    end = (0.0, 0.0, 0.0)
    center = (5.0, 0.0, 0.0)
    cw = build_arc(start, end, center, 17, cw=True, tolerance=0.01)
    ccw = build_arc(start, end, center, 17, cw=False, tolerance=0.01)
    assert cw.sweep < 0 < ccw.sweep
    # The two half circles must pass on opposite sides of the chord.
    assert cw.points[len(cw.points) // 2][1] < 0 < ccw.points[len(ccw.points) // 2][1]


def test_radius_form_picks_minor_and_major():
    start, end = (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)
    minor = build_arc(start, end, None, 17, cw=False, tolerance=0.01, radius=8.0)
    major = build_arc(start, end, None, 17, cw=False, tolerance=0.01, radius=-8.0)
    assert abs(minor.sweep) < math.pi
    assert abs(major.sweep) > math.pi
    assert minor.length + major.length == pytest.approx(2 * math.pi * 8.0, rel=1e-3)


def test_radius_smaller_than_half_chord_is_enlarged():
    center = center_from_radius((0, 0, 0), (10, 0, 0), 1.0, 17, cw=False)
    # Clamped to a half circle of radius 5 -> centre on the midpoint.
    assert center[0] == pytest.approx(5.0, abs=1e-6)


def test_helix_includes_z_travel():
    start, end = (0.0, 0.0, 0.0), (10.0, 0.0, 1.0)
    center = (5.0, 0.0, 0.0)
    geom = build_arc(start, end, center, 17, cw=False, tolerance=0.005)
    planar = math.pi * 5.0
    assert geom.length == pytest.approx(math.hypot(planar, 1.0), rel=1e-3)
    assert geom.points[-1][2] == pytest.approx(1.0)


def test_zx_plane_arc():
    start, end = (0.0, 0.0, 0.0), (0.0, 0.0, 10.0)
    center = (0.0, 0.0, 5.0)
    geom = build_arc(start, end, center, 18, cw=True, tolerance=0.01)
    assert geom.length == pytest.approx(math.pi * 5.0, rel=1e-3)
    assert max(p[0] for p in geom.points) == pytest.approx(5.0, abs=0.01)


def test_arc_analytic_length_matches_interpolation():
    start, end = (3.0, 4.0, 0.0), (0.0, 5.0, 0.5)
    center = (0.0, 0.0, 0.0)
    analytic = arc_length_from_ijk(start, end, center, 17, cw=True)
    geom = build_arc(start, end, center, 17, cw=True, tolerance=0.001)
    assert geom.length == pytest.approx(analytic, rel=1e-4)


def test_zero_radius_is_rejected():
    with pytest.raises(ArcError):
        build_arc((1, 1, 0), (2, 2, 0), (1, 1, 0), 17, True, 0.01)
