"""Command line front end: analyse a .gcode file and print engineering metrics.

Also writes self-contained Plotly HTML reports, so an analysis can be shared
without running the web app.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from .analyzer import analyze
from .models import Config, ConfigError

BANNER = "=" * 66


def fmt_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, rest = divmod(seconds, 3600.0)
    minutes, secs = divmod(rest, 60.0)
    if hours >= 1:
        return f"{int(hours)}h {int(minutes):02d}m {secs:04.1f}s"
    if minutes >= 1:
        return f"{int(minutes)}m {secs:04.1f}s"
    return f"{secs:.2f}s"


def _config_from_args(args: argparse.Namespace) -> Config:
    data = {}
    for field in (
        "filament_diameter", "density", "accel", "travel_accel",
        "junction_deviation", "jerk", "junction_model",
        "max_vx", "max_vy", "max_vz", "arc_tolerance", "max_points",
        "build_x", "build_y", "build_z", "tool_change_time",
    ):
        value = getattr(args, field, None)
        if value is not None:
            data[field] = value
    data["model_label"] = "CLI"
    return Config.from_mapping(data)


def print_report(metrics: dict, filename: str, elapsed_ms: float) -> None:
    time = metrics["time"]
    fil = metrics["filament"]
    bbox = metrics["bbox"]
    motion = metrics["motion"]
    info = metrics["file"]

    print(BANNER)
    print(f" G-CODE ANALYSIS  {filename}")
    print(BANNER)
    slicer = info["slicer"] or "unknown"
    print(f" lines: {info['lines']:,}   slicer: {slicer}   "
          f"segments: {motion['segments']:,}   analysed in {elapsed_ms:.0f} ms")
    if info["nozzle_temp"] or info["bed_temp"]:
        print(f" nozzle: {info['nozzle_temp'] or '-'} C   "
              f"bed: {info['bed_temp'] or '-'} C")
    print()

    print(" TIME  (acceleration-limited motion planning)")
    print(f"   total            {fmt_duration(time['total_s']):>18}"
          f"   naive distance/speed {fmt_duration(time['naive_s'])}")
    print(f"   printing         {fmt_duration(time['print_s']):>18}")
    print(f"   travelling       {fmt_duration(time['travel_s']):>18}")
    print(f"   dwells           {fmt_duration(time['dwell_s']):>18}")
    print(f"   retractions      {fmt_duration(time['retract_s']):>18}")
    print(f"   accel overhead   {time['accel_overhead_pct']:>17.1f}%")
    print()

    print(" MATERIAL")
    print(f"   filament         {fil['length_mm'] / 1000:>15.3f} m"
          f"   (retracted {fil['retracted_mm']:.1f} mm)")
    print(f"   volume           {fil['volume_cm3']:>15.3f} cm3")
    print(f"   mass             {fil['mass_g']:>15.2f} g"
          f"   @ {fil['density_g_cm3']} g/cm3, {fil['diameter_mm']} mm filament")
    print()

    print(" BOUNDING BOX")
    size = bbox["size"]
    print(f"   size             {size[0]:>8.2f} x {size[1]:.2f} x {size[2]:.2f} mm")
    print(f"   min              {bbox['min'][0]:>8.2f} {bbox['min'][1]:>7.2f} "
          f"{bbox['min'][2]:>7.2f}")
    print(f"   max              {bbox['max'][0]:>8.2f} {bbox['max'][1]:>7.2f} "
          f"{bbox['max'][2]:>7.2f}")
    print()

    print(" MOTION")
    print(f"   path             {motion['path_mm'] / 1000:>15.3f} m"
          f"   (print {motion['print_mm'] / 1000:.3f} m / "
          f"travel {motion['travel_mm'] / 1000:.3f} m)")
    print(f"   layers           {motion['layers']:>15,}"
          f"   layer height {motion['layer_height_mm']} mm")
    print(f"   arcs             {motion['arcs']:>15,}"
          f"   arc length {motion['arc_length_mm'] / 1000:.3f} m")
    print(f"   peak speed       {motion['max_speed_mm_s']:>15.1f} mm/s"
          f"   mean print {motion['mean_print_speed_mm_s']:.1f} mm/s")
    print(f"   peak flow        {motion['max_flow_mm3_s']:>15.2f} mm3/s"
          f"   mean flow {motion['mean_flow_mm3_s']:.2f} mm3/s")
    print(f"   retractions      {motion['retractions']:>15,}"
          f"   tool changes {motion['tool_changes']}")
    print(BANNER)

    warnings = metrics.get("warnings") or []
    if warnings:
        print(" NOTES")
        for warning in warnings:
            print(f"   - {warning}")
        print(BANNER)


def build_html(metrics: dict, path: dict, title: str) -> str:
    """Self-contained Plotly report (vendor bundle inlined when available)."""
    vendor = Path(__file__).resolve().parent.parent / "web" / "vendor" / "plotly.min.js"
    if vendor.is_file():
        plotly = vendor.read_text(encoding="utf-8")
    else:
        plotly = ""
    # Shared with the web app, so the report's 3D geometry can never drift.
    shared = (
        Path(__file__).resolve().parent.parent / "web" / "toolpath-plot.js"
    ).read_text(encoding="utf-8")
    cdn = '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>'
    data = json.dumps({"metrics": metrics, "path": path}, separators=(",", ":"))
    time = metrics["time"]
    fil = metrics["filament"]
    bbox = metrics["bbox"]
    subtitle = (
        f"{fmt_duration(time['total_s'])} &middot; "
        f"{fil['length_mm'] / 1000:.2f} m filament &middot; "
        f"{fil['mass_g']:.1f} g &middot; "
        f"{bbox['size'][0]:.1f} x {bbox['size'][1]:.1f} x {bbox['size'][2]:.1f} mm"
    )
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{title}</title>
<style>
  html,body{{margin:0;height:100%;background:#0b0e14;color:#dfe6f3;
    font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}}
  header{{padding:14px 22px;border-bottom:1px solid #1f2733;display:flex;
    justify-content:space-between;align-items:baseline;gap:16px}}
  h1{{font-size:15px;margin:0;letter-spacing:.14em;text-transform:uppercase;font-weight:600}}
  .sub{{font-size:12px;color:#8a97ab;font-variant-numeric:tabular-nums}}
  #plot{{position:absolute;inset:64px 0 0 0}}
</style></head>
<body>
<header><h1>{title}</h1><div class="sub">{subtitle}</div></header>
<div id="plot"></div>
<script>{plotly}</script>{cdn if not plotly else ""}
<script>
/*__SHARED__*/
const D = {data};
const m = D.metrics, p = D.path;
const traces = [
  ToolpathPlot.extrusionTrace(
    {{x:p.x, y:p.y, z:p.z}},
    {{color:p.speed, colorscale:'Turbo', width:4,
      colorbar:{{title:{{text:'mm/s'}}, thickness:10, len:.55, x:1.0}}}}),
  ToolpathPlot.boxTrace(m.bbox, {{name:'bounding box', color:'#2f3a4a', width:2}})
];
const layout = {{
  paper_bgcolor:'#0b0e14', plot_bgcolor:'#0b0e14',
  margin:{{l:0,r:0,t:0,b:0}},
  scene:{{bgcolor:'#0b0e14', aspectmode:'data',
    xaxis:{{color:'#6b7a8f', gridcolor:'#1a2230', title:{{text:'X mm'}}}},
    yaxis:{{color:'#6b7a8f', gridcolor:'#1a2230', title:{{text:'Y mm'}}}},
    zaxis:{{color:'#6b7a8f', gridcolor:'#1a2230', title:{{text:'Z mm'}}}}}},
  showlegend:false
}};
Plotly.newPlot('plot', traces, layout, {{responsive:true,
  displaylogo:false, modeBarButtonsToRemove:['sendDataToCloud']}});
</script>
</body></html>
"""
    return html.replace("/*__SHARED__*/", shared)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gcode-analyze",
        description="Interpolate a .gcode toolpath and compute real engineering metrics.",
    )
    parser.add_argument("file", help="path to a .gcode / .nc file")
    parser.add_argument("--filament", dest="filament_diameter", type=float,
                        help="filament diameter in mm (default 1.75)")
    parser.add_argument("--density", type=float, help="material density in g/cm3")
    parser.add_argument("--accel", type=float, help="print acceleration in mm/s^2")
    parser.add_argument("--travel-accel", dest="travel_accel", type=float)
    parser.add_argument("--junction-deviation", dest="junction_deviation", type=float)
    parser.add_argument("--jerk", type=float, help="per-axis jerk limit in mm/s")
    parser.add_argument("--junction-model", dest="junction_model",
                        choices=("deviation", "jerk"))
    parser.add_argument("--max-vx", dest="max_vx", type=float)
    parser.add_argument("--max-vy", dest="max_vy", type=float)
    parser.add_argument("--max-vz", dest="max_vz", type=float)
    parser.add_argument("--arc-tolerance", dest="arc_tolerance", type=float,
                        help="max chord deviation for arc interpolation (mm)")
    parser.add_argument("--json", metavar="PATH", help="write metrics + path as JSON")
    parser.add_argument("--html", metavar="PATH", help="write a Plotly HTML report")
    parser.add_argument("--quiet", action="store_true", help="only print warnings")
    args = parser.parse_args(argv)

    path = Path(args.file)
    if not path.is_file():
        print(f"error: {path} does not exist", file=sys.stderr)
        return 2

    try:
        cfg = _config_from_args(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="latin-1", errors="replace")

    import time as _time

    started = _time.perf_counter()
    result = analyze(text, cfg, path.name)
    elapsed_ms = (_time.perf_counter() - started) * 1000.0

    if not args.quiet:
        print_report(result.metrics, path.name, elapsed_ms)

    if args.json:
        payload = {"metrics": result.metrics, "path": result.path}
        Path(args.json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")

    if args.html:
        Path(args.html).write_text(
            build_html(result.metrics, result.path, f"G-code analysis / {path.name}"),
            encoding="utf-8",
        )
        print(f"wrote {args.html}")

    if not math.isfinite(result.metrics["time"]["total_s"]):
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
