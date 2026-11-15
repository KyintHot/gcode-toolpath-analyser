import io
import math

import pytest

from gcode_analyzer.models import Config
from gcode_analyzer.parser import GCodeParser, strip_comments


def moves(text, cfg=None):
    parser = GCodeParser(cfg or Config())
    return list(parser.parse(io.StringIO(text))), parser


def motion(text, cfg=None):
    return [m for m in moves(text, cfg)[0] if m.kind in (0, 1)]


def test_strip_comments():
    assert strip_comments("G1 X1 ; move (fast)")[0].strip() == "G1 X1"
    code, comment = strip_comments("G1 X1 (a (nested) note) Y2")
    assert code.strip() == "G1 X1  Y2"
    assert "nested" in comment
    assert strip_comments("N10 G1 X1*42")[0].strip() == "N10 G1 X1"


def test_compact_words_without_spaces():
    (m,) = motion("G1X10Y20E0.5")
    assert (m.x1, m.y1) == (10.0, 20.0)
    assert m.e == pytest.approx(0.5)


def test_modal_motion_is_remembered():
    parsed = motion("G1 X10 F600\nY10\nX0")
    assert [(m.x1, m.y1) for m in parsed] == [(10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]


def test_incremental_and_absolute():
    parsed = motion("G90\nG1 X10\nG91\nG1 X5")
    assert parsed[-1].x1 == pytest.approx(15.0)


def test_inches_scale_feedrate_too():
    (m,) = motion("G20\nG1 X1 Y1 F60")
    assert m.x1 == pytest.approx(25.4)
    assert m.feed == pytest.approx(25.4)  # 60 in/min -> 25.4 mm/s


def test_g92_offsets_position_without_moving():
    parsed = motion("G1 X10\nG92 X0\nG1 X10")
    assert len(parsed) == 2
    assert parsed[1].x0 == pytest.approx(0.0)
    assert parsed[1].x1 == pytest.approx(10.0)


def test_extruder_modes():
    absolute = motion("M82\nG1 X10 E2\nG1 X20 E3")[-1]
    assert absolute.e == pytest.approx(1.0)
    relative = motion("M83\nG1 X10 E2\nG1 X20 E3")[-1]
    assert relative.e == pytest.approx(3.0)


def test_arc_words_are_converted_to_centres():
    (m,) = motion("G17\nG2 X10 Y0 I5 J0")
    assert m.kind == 1
    assert m.cw
    assert (m.cx, m.cy) == pytest.approx((5.0, 0.0))


def test_radius_arc_is_flagged():
    (m,) = motion("G3 X10 Y0 R5")
    assert m.radius == pytest.approx(5.0)


def test_zx_plane_centre_letters():
    (m,) = motion("G18\nG2 X0 Z10 I0 K5")
    assert (m.cx, m.cz) == pytest.approx((0.0, 5.0))


def test_dwell_barriers_and_toolchange():
    events, parser = moves("G4 P250\nM0\nT1")
    dwells = [e for e in events if e.kind == 2]
    barriers = [e for e in events if e.kind == 3]
    assert dwells[0].dwell == pytest.approx(0.25)
    assert len(barriers) >= 2
    assert parser.info.tool_changes == 1


def test_g28_homes_and_barriers():
    events, parser = moves("G1 X50\nG28\nG1 X10")
    assert parser.x == pytest.approx(10.0)
    assert any(e.kind == 3 for e in events)


def test_volumetric_extrusion_converts_to_filament_length():
    cfg = Config(filament_diameter=1.75)
    area = math.pi * (1.75 / 2) ** 2
    (m,) = motion("M200 D1.75\nM83\nG1 X10 E2", cfg)
    assert m.e == pytest.approx(2.0 / area)


def test_m204_sets_acceleration():
    parsed = motion("M204 S1500\nG1 X10")
    assert parsed[0].accel == pytest.approx(1500.0)


def test_m204_cannot_exceed_configured_machine_acceleration():
    # The file may ask for less than the machine can do, never for more.
    parsed = motion("M204 S9000 T9000\nG1 X10", Config(accel=3000, travel_accel=3000))
    assert parsed[0].accel == pytest.approx(3000.0)
    events, parser = moves("M204 S9000 T9000\nG1 X10", Config(accel=3000, travel_accel=3000))
    assert parser.info.file_accel == pytest.approx(9000.0)
    assert any("M204" in w for w in parser.info.warnings)


def test_extra_axis_words_ignored():
    (m,) = motion("G1 X1 Y2 Z3 A9 B8 C7")
    assert (m.x1, m.y1, m.z1) == (1.0, 2.0, 3.0)
