/* Shared Plotly trace builders.
 *
 * Used by the web app (web/app.js) and inlined into the standalone HTML report
 * written by `python -m gcode_analyzer.cli --html`. Keeping the 3D geometry in
 * one place means the report and the app can never drift apart.
 */
(function (window) {
  "use strict";

  // Corner pairs of an axis-aligned box, indexed 0-7 (low face, then high face).
  const EDGES = [
    [0, 1], [1, 2], [2, 3], [3, 0],
    [4, 5], [5, 6], [6, 7], [7, 4],
    [0, 4], [1, 5], [2, 6], [3, 7],
  ];

  function boxTrace(bbox, options) {
    const opts = options || {};
    const lo = bbox.min;
    const hi = bbox.max;
    const corners = [
      [lo[0], lo[1], lo[2]], [hi[0], lo[1], lo[2]],
      [hi[0], hi[1], lo[2]], [lo[0], hi[1], lo[2]],
      [lo[0], lo[1], hi[2]], [hi[0], lo[1], hi[2]],
      [hi[0], hi[1], hi[2]], [lo[0], hi[1], hi[2]],
    ];
    const x = [];
    const y = [];
    const z = [];
    for (const [a, b] of EDGES) {
      // null coordinates break the polyline between edges
      x.push(corners[a][0], corners[b][0], null);
      y.push(corners[a][1], corners[b][1], null);
      z.push(corners[a][2], corners[b][2], null);
    }
    return {
      type: "scatter3d",
      mode: "lines",
      name: opts.name || "envelope",
      x, y, z,
      line: { color: opts.color || "#2a3444", width: opts.width || 2 },
      hoverinfo: "skip",
      showlegend: false,
    };
  }

  function extrusionTrace(coords, line, hovertemplate) {
    const trace = {
      type: "scatter3d",
      mode: "lines",
      name: "extrusion",
      x: coords.x,
      y: coords.y,
      z: coords.z,
      line: line,
    };
    if (hovertemplate) trace.hovertemplate = hovertemplate;
    return trace;
  }

  window.ToolpathPlot = { boxTrace: boxTrace, extrusionTrace: extrusionTrace };
})(window);
