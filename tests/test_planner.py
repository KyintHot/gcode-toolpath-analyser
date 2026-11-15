import math

import pytest

from gcode_analyzer.models import Config, Segment
from gcode_analyzer.planner import junction_speed, plan, speed_limit


def seg(p0, p1, feed=100.0, accel=1000.0, extrude=True, run=0, layer=0):
    return Segment(
        x0=p0[0], y0=p0[1], z0=p0[2],
        x1=p1[0], y1=p1[1], z1=p1[2],
        length=math.dist(p0, p1),
        feed=feed,
        accel=accel,
        e=0.05 if extrude else 0.0,
        extrude=extrude,
        arc=False,
        layer=layer,
        run=run,
        line=1,
    )


def test_single_long_move_is_trapezoidal():
    cfg = Config(accel=1000, travel_accel=1000, max_vx=500)
    s = seg((0, 0, 0), (100, 0, 0), feed=100, accel=1000)
    plan([s], cfg)
    # accel 5 mm to 100 mm/s, 90 mm cruise, decel 5 mm
    assert s.t == pytest.approx(0.1 + 0.9 + 0.1, rel=1e-6)
    assert s.v == pytest.approx(100 / 1.1)  # mean, not peak, speed


def test_short_move_is_triangular():
    cfg = Config(accel=1000)
    s = seg((0, 0, 0), (1, 0, 0), feed=100, accel=1000)
    plan([s], cfg)
    peak = math.sqrt(1000.0)
    assert s.t == pytest.approx(2 * peak / 1000.0, rel=1e-6)
    assert s.v < 100


def test_stops_at_run_boundaries():
    cfg = Config(accel=1000)
    a = seg((0, 0, 0), (10, 0, 0), run=0)
    b = seg((10, 0, 0), (20, 0, 0), run=1)
    plan([a, b], cfg)
    # Both must accelerate from a standstill: 10 mm, peak exactly 100 mm/s.
    assert a.t == pytest.approx(0.2, rel=1e-6)
    assert a.v == pytest.approx(50.0, rel=1e-6)


def test_junction_deviation_limits_corners():
    cfg = Config(junction_model="deviation", junction_deviation=0.013, accel=1000)
    a = seg((0, 0, 0), (10, 0, 0), feed=100)
    b = seg((10, 0, 0), (10, 10, 0), feed=100)
    plan([a, b], cfg)
    expected = math.sqrt(1000 * 0.013 * math.sqrt(0.5) / (1 - math.sqrt(0.5)))
    assert junction_speed(a, b, cfg) == pytest.approx(expected, rel=1e-6)
    assert b.v < 100  # the corner costs speed against the naive estimate


def test_straight_junctions_keep_full_speed():
    cfg = Config(junction_model="deviation", junction_deviation=0.013)
    a = seg((0, 0, 0), (50, 0, 0), feed=80)
    b = seg((50, 0, 0), (100, 0, 0), feed=80)
    plan([a, b], cfg)
    assert junction_speed(a, b, cfg) == math.inf
    # Entering at 80 mm/s and never slowing down: no deceleration distance.
    assert a.t == pytest.approx(0.08 + 46.8 / 80, rel=1e-6)
    assert b.t == pytest.approx(0.08 + 46.8 / 80, rel=1e-6)


def test_classic_jerk_model():
    cfg = Config(junction_model="jerk", jerk=8.0)
    a = seg((0, 0, 0), (10, 0, 0), feed=100)
    b = seg((10, 0, 0), (10, 10, 0), feed=100)
    plan([a, b], cfg)
    # 90 deg turn: each axis would jump to 8 mm/s instantly.
    assert junction_speed(a, b, cfg) == pytest.approx(8.0)
    assert b.t > 10 / 100
    reversal_cfg = Config(junction_model="jerk", jerk=8.0)
    c = seg((10, 0, 0), (0, 0, 0), feed=100)
    plan([a, c], reversal_cfg)
    assert junction_speed(a, c, reversal_cfg) == pytest.approx(8.0)


def test_axis_velocity_ceiling():
    cfg = Config(max_vx=100, max_vy=100, max_vz=5)
    assert speed_limit(seg((0, 0, 0), (10, 0, 0), feed=400), cfg) == pytest.approx(100)
    assert speed_limit(seg((0, 0, 0), (0, 0, 10), feed=400), cfg) == pytest.approx(5)
    diagonal = speed_limit(seg((0, 0, 0), (10, 10, 0), feed=400), cfg)
    assert diagonal == pytest.approx(100 * math.sqrt(2))


def test_acceleration_always_costs_against_naive():
    cfg = Config(accel=1500)
    segments = [seg((i, 0, 0), (i + 1, 0, 0), feed=100) for i in range(40)]
    plan(segments, cfg)
    total = sum(s.t for s in segments)
    naive = sum(s.length / s.feed for s in segments)
    assert total > naive * 1.2


def test_zero_feed_segments_do_not_divide_by_zero():
    cfg = Config()
    s = seg((0, 0, 0), (10, 0, 0), feed=0.0)
    plan([s], cfg)
    assert s.t == 0.0
