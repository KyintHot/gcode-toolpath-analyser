import math

import pytest

from gcode_analyzer import Config, analyze

FILAMENT_AREA = math.pi * (1.75 / 2) ** 2


def test_filament_volume_and_mass():
    g = "M83\nG1 Z0.2 F600\nG1 X10 Y0 E0.5 F1200"
    res = analyze(g, Config())
    fil = res.metrics["filament"]
    assert fil["length_mm"] == pytest.approx(0.5, rel=1e-6)
    assert fil["volume_cm3"] == pytest.approx(0.5 * FILAMENT_AREA / 1000, abs=1e-5)
    assert fil["mass_g"] == pytest.approx(0.5 * FILAMENT_AREA / 1000 * 1.24, abs=1e-5)


def test_retraction_does_not_count_as_material():
    g = "M83\nG1 Z0.2\nG1 X10 E0.5\nG1 E-0.8\nG1 X0\nG1 E0.8\nG1 X10 E0.5"
    res = analyze(g, Config())
    fil = res.metrics["filament"]
    assert fil["length_mm"] == pytest.approx(1.0, rel=1e-6)
    assert fil["retracted_mm"] == pytest.approx(0.8, rel=1e-6)
    assert res.metrics["motion"]["retractions"] == 1
    assert res.metrics["time"]["retract_s"] > 0


def test_bounding_box_covers_arc_bulge():
    # Half circle bulging to +Y: the bbox must include Y = 10, not just the
    # 0 endpoints.
    g = "G17\nG1 X0 Y0\nG2 X20 Y0 I10 J0 E1 F1200"
    res = analyze(g, Config())
    bbox = res.metrics["bbox"]
    assert bbox["min"][0] == pytest.approx(0.0, abs=0.01)
    assert bbox["max"][0] == pytest.approx(20.0, abs=0.01)
    assert bbox["max"][1] == pytest.approx(10.0, abs=0.05)
    assert bbox["size"][0] == pytest.approx(20.0, abs=0.01)


def test_layer_counting():
    g = """
    M83
    G1 Z0.2 F600
    G1 X10 E0.4
    G1 Z0.4
    G1 X0 E0.4
    G1 Z0.6
    G1 X10 E0.4
    """
    res = analyze(g, Config())
    assert res.metrics["motion"]["layers"] == 3
    assert res.metrics["motion"]["layer_height_mm"] == pytest.approx(0.2, abs=1e-6)
    assert [row["z"] for row in res.metrics["layers"]] == pytest.approx([0.2, 0.4, 0.6])


def test_time_includes_dwell_and_is_above_naive():
    g = "M83\nG1 X1 F3000\nG1 X2\nG1 X3\nG4 P1000"
    res = analyze(g, Config(accel=500))
    t = res.metrics["time"]
    assert t["dwell_s"] == pytest.approx(1.0)
    assert t["total_s"] > t["naive_s"]
    assert t["accel_overhead_pct"] > 0


def test_configured_acceleration_changes_the_estimate():
    g = "M204 P1250 T2500\nM83\nG1 X10 F6000\nG1 X0\nG1 X10\nG1 X0"
    slow = analyze(g, Config(accel=500, travel_accel=500)).metrics["time"]["total_s"]
    fast = analyze(g, Config(accel=8000, travel_accel=8000)).metrics["time"]["total_s"]
    assert fast < slow
    # The file's own M204 is reported so the difference is explainable.
    assert analyze(g, Config()).metrics["file"]["file_accel"] == pytest.approx(1250.0)


def test_arc_time_and_length():
    cfg = Config(arc_tolerance=0.005)
    g = "G1 X0 Y0\nG2 X20 Y0 I10 J0 E1 F600"
    res = analyze(g, cfg)
    motion = res.metrics["motion"]
    assert motion["arcs"] == 1
    assert motion["arc_length_mm"] == pytest.approx(math.pi * 10, rel=0.01)


def test_r_form_and_all_planes_parse():
    g = """
    G18
    G2 X0 Z10 I0 K5
    G19
    G3 Y10 Z10 J0 K5
    G17
    G2 X20 Y0 R10
    """
    res = analyze(g, Config())
    assert res.metrics["motion"]["arcs"] == 3


def test_invalid_arc_falls_back_to_line_and_warns():
    g = "G1 X5 Y5\nG2 X10 Y0 I0 J0"
    res = analyze(g, Config())
    assert any("arc" in w.lower() for w in res.metrics["warnings"])


def test_path_is_decimated_but_keeps_total_point_count():
    lines = ["M83", "G1 Z0.2 F600"]
    for i in range(4000):
        lines.append(f"G1 X{(i + 1) * 0.1:.3f} Y0 E0.01")
    res = analyze("\n".join(lines), Config(max_points=1000))
    assert res.path["total_points"] > 1000
    assert res.path["count"] <= 1100
    assert len(res.path["x"]) == res.path["count"]
    assert res.path["x"][0] == 0.0


def test_empty_file_is_harmless():
    res = analyze("; nothing here\n", Config())
    assert res.metrics["motion"]["segments"] == 0
    assert res.path["count"] == 0
    assert res.metrics["bbox"]["size"] == [0.0, 0.0, 0.0]


def test_feed_buckets_and_flow():
    g = "M83\nG1 Z0.2\nG1 X20 E0.8 F1200\nG1 X0 E0.8 F2400"
    res = analyze(g, Config())
    assert res.metrics["motion"]["max_flow_mm3_s"] > 0
    feeds = res.metrics["feeds"]
    assert len(feeds) == 2
    assert feeds[0]["feed_mm_s"] == pytest.approx(20.0)
