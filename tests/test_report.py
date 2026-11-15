#!/usr/bin/env python
"""Report and CLI smoke tests."""

from __future__ import annotations

from pathlib import Path

from gcode_analyzer import Config, analyze
from gcode_analyzer.cli import build_html

ROOT = Path(__file__).resolve().parents[1]


def _result():
    text = (ROOT / "samples" / "arc_torture.gcode").read_text(encoding="utf-8")
    return analyze(text, Config(), "arc_torture.gcode")


def test_html_report_inlines_shared_renderer():
    res = _result()
    html = build_html(res.metrics, res.path, "test report")
    assert html.startswith("<!doctype html>")
    assert "toolpath-plot" not in html  # inlined, never linked
    assert "__SHARED__" not in html
    assert "ToolpathPlot.extrusionTrace" in html
    assert "ToolpathPlot.boxTrace" in html
    # The shared module itself must be present, not just its call sites.
    assert "window.ToolpathPlot" in html


def test_html_report_embeds_metrics():
    res = _result()
    html = build_html(res.metrics, res.path, "test report")
    assert "test report" in html
    assert f"{res.metrics['filament']['mass_g']:.1f} g" in html
