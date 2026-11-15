/* G-code Toolpath Analyser - front end */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const STORE_KEY = "gcode-analyser.config.v1";

  const CONFIG_KEYS = [
    "filament_diameter", "density", "accel", "travel_accel",
    "junction_deviation", "jerk", "junction_model",
    "max_vx", "max_vy", "max_vz", "arc_tolerance",
    "build_x", "build_y", "build_z",
  ];

  const PRESETS = {
    ender3: {
      label: "Ender 3 (bed slinger)",
      accel: 1000, travel_accel: 1500, junction_model: "deviation",
      junction_deviation: 0.013, jerk: 8,
      max_vx: 300, max_vy: 300, max_vz: 10, build_x: 220, build_y: 220, build_z: 250,
    },
    prusa: {
      label: "Prusa MK3S+",
      accel: 1250, travel_accel: 3000, junction_model: "deviation",
      junction_deviation: 0.013, jerk: 8,
      max_vx: 380, max_vy: 380, max_vz: 12, build_x: 250, build_y: 210, build_z: 210,
    },
    voron: {
      label: "Voron 2.4 (CoreXY)",
      accel: 5000, travel_accel: 8000, junction_model: "deviation",
      junction_deviation: 0.025, jerk: 10,
      max_vx: 500, max_vy: 500, max_vz: 15, build_x: 350, build_y: 350, build_z: 330,
    },
    bambu: {
      label: "Bambu X1C (fast)",
      accel: 10000, travel_accel: 10000, junction_model: "deviation",
      junction_deviation: 0.020, jerk: 12,
      max_vx: 500, max_vy: 500, max_vz: 20, build_x: 256, build_y: 256, build_z: 256,
    },
    cnc: {
      label: "CNC router / mill",
      accel: 500, travel_accel: 800, junction_model: "jerk",
      junction_deviation: 0.01, jerk: 5,
      max_vx: 200, max_vy: 200, max_vz: 60, build_x: 600, build_y: 400, build_z: 120,
    },
    custom: { label: "Custom" },
  };

  const state = {
    data: null,
    config: loadConfig(),
    preset: "ender3",
    colorRange: [0, 1],
    plotReady: false,
    layerZ: [],
    view: {
      tab: "path",
      layerMode: false,
      layer: 0,
      showTravel: true,
      speedColor: true,
      playing: false,
      timer: null,
    },
  };

  /* ------------------------------------------------------------- config */

  function defaults() {
    const cfg = {
      filament_diameter: 1.75,
      density: 1.24,
      arc_tolerance: 0.01,
    };
    Object.assign(cfg, pickPreset("ender3"));
    return cfg;
  }

  function pickPreset(name) {
    const p = PRESETS[name] || {};
    const out = {};
    for (const key of CONFIG_KEYS) {
      if (p[key] !== undefined) out[key] = p[key];
    }
    return out;
  }

  function loadConfig() {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object") {
          return Object.assign(defaults(), parsed.config || {});
        }
      }
    } catch (err) { /* ignore corrupt storage */ }
    return defaults();
  }

  function saveConfig() {
    try {
      localStorage.setItem(
        STORE_KEY,
        JSON.stringify({ config: state.config, preset: state.preset }),
      );
    } catch (err) { /* storage unavailable */ }
  }

  function configQuery() {
    const params = new URLSearchParams();
    for (const key of CONFIG_KEYS) {
      const value = state.config[key];
      if (typeof value === "number" && Number.isFinite(value)) {
        params.set(key, String(value));
      } else if (typeof value === "string" && value) {
        params.set(key, value);
      }
    }
    return params.toString();
  }

  function syncInputs() {
    for (const key of CONFIG_KEYS) {
      const el = $(key);
      if (!el) continue;
      el.value = state.config[key];
    }
    $("jdField").hidden = state.config.junction_model !== "deviation";
    $("jerkField").hidden = state.config.junction_model === "deviation";
    const select = $("preset");
    select.value = state.preset;
    select.querySelector("option[value='custom']").textContent =
      state.preset === "custom" ? "Custom" : "Custom (modified)";
  }

  function readInputs() {
    for (const key of CONFIG_KEYS) {
      const el = $(key);
      if (!el) continue;
      if (key === "junction_model") {
        state.config[key] = el.value;
        continue;
      }
      const value = parseFloat(el.value);
      if (Number.isFinite(value)) state.config[key] = value;
    }
    saveConfig();
  }

  /* ------------------------------------------------------------ formatting */

  function fmtTime(seconds) {
    if (!Number.isFinite(seconds)) return "--";
    if (seconds < 60) return seconds.toFixed(1) + " s";
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.round(seconds % 60);
    if (h > 0) return h + " h " + String(m).padStart(2, "0") + " m";
    return m + " m " + String(s).padStart(2, "0") + " s";
  }

  function fmtTimeLong(seconds) {
    if (!Number.isFinite(seconds)) return "--";
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m ${s.toFixed(0)}s`;
    if (m > 0) return `${m}m ${s.toFixed(1)}s`;
    return `${s.toFixed(2)}s`;
  }

  function fmtNum(value, digits = 0) {
    if (!Number.isFinite(value)) return "--";
    return value.toLocaleString("en-US", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  /* ---------------------------------------------------------------- upload */

  function setLoading(text) {
    $("loadingText").textContent = text;
    $("loading").hidden = false;
  }
  function clearLoading() {
    $("loading").hidden = true;
  }

  function showError(message) {
    $("errorText").textContent = message;
    $("errorBanner").hidden = false;
  }
  function clearError() {
    $("errorBanner").hidden = true;
  }

  async function analyzeFile(file) {
    if (!file) return;
    if (!/\.(gcode|gco|g|nc|ngc|tap|txt)$/i.test(file.name)) {
      showError("That does not look like a G-code file (" + file.name + ").");
      return;
    }
    clearError();
    setLoading("Analysing " + file.name + " ...");
    try {
      const buffer = await file.arrayBuffer();
      const response = await fetch("/api/analyze?" + configQuery(), {
        method: "POST",
        headers: { "x-filename": encodeURIComponent(file.name) },
        body: buffer,
      });
      await handleResponse(response, file.name);
    } catch (err) {
      showError(err.message || String(err));
    } finally {
      clearLoading();
    }
  }

  async function loadSample(name) {
    clearError();
    setLoading("Analysing sample " + name + " ...");
    try {
      const response = await fetch(
        "/api/samples/" + encodeURIComponent(name) + "?" + configQuery(),
        { method: "POST" },
      );
      await handleResponse(response, name);
    } catch (err) {
      showError(err.message || String(err));
    } finally {
      clearLoading();
    }
  }

  async function handleResponse(response, name) {
    let payload = null;
    try {
      payload = await response.json();
    } catch (err) {
      throw new Error("Server error (" + response.status + ")");
    }
    if (!response.ok) {
      throw new Error(payload && payload.error ? payload.error : "Server error");
    }
    if (!payload.metrics || !payload.metrics.motion.segments) {
      throw new Error("No motion commands (G0/G1/G2/G3) were found in this file.");
    }
    adopt(payload, name);
  }

  function adopt(payload, name) {
    state.data = payload;
    state.layerZ = (payload.metrics.layers || []).map((row) => row.z);
    state.colorRange = speedRange(payload.path);
    const layers = payload.metrics.motion.layers;
    if (state.view.layerMode) {
      state.view.layer = Math.min(state.view.layer, Math.max(0, layers - 1));
    }
    $("fileChip").hidden = false;
    $("fileChip").textContent = name;
    $("fileChip").title = name;
    $("emptyState").hidden = true;
    $("layerBar").hidden = false;
    $("recompute").disabled = false;
    $("loadHint").hidden = true;

    const p = payload.path;
    $("pointChip").textContent =
      fmtNum(p.count) + " / " + fmtNum(p.total_points) + " points";
    $("pointChip").title =
      "Rendered points / interpolated motion blocks (decimated for display)";

    renderKpis();
    renderNotes();
    renderTables();
    renderCharts();
    updateLayerUi();
    renderPlot();
  }

  /* ------------------------------------------------------------------ KPIs */

  function renderKpis() {
    const m = state.data.metrics;
    const t = m.time;
    const f = m.filament;
    const b = m.bbox;
    const mo = m.motion;
    const over = t.accel_overhead_pct;

    const fit = [];
    const build = [state.config.build_x, state.config.build_y, state.config.build_z];
    const axes = ["X", "Y", "Z"];
    let fits = true;
    for (let i = 0; i < 3; i++) {
      const delta = b.size[i] - build[i];
      if (delta > 0.001) {
        fits = false;
        fit.push(axes[i] + " +" + delta.toFixed(1));
      }
    }
    const fitText = fits
      ? "fits " + build[0] + " \u00d7 " + build[1] + " \u00d7 " + build[2] + " mm"
      : "exceeds build volume: " + fit.join(", ") + " mm";

    const cards = [
      {
        label: "Print time",
        value: fmtTime(t.total_s),
        sub: "naive " + fmtTime(t.naive_s) + " \u00b7 +" + over.toFixed(1) + "% accel",
      },
      {
        label: "Material",
        value: (f.length_mm / 1000).toFixed(2) + ' <small>m</small>',
        sub: f.volume_cm3.toFixed(2) + " cm\u00b3 \u00b7 " + f.mass_g.toFixed(2) + " g",
      },
      {
        label: "Bounding box",
        value:
          b.size[0].toFixed(1) + " \u00d7 " + b.size[1].toFixed(1) +
          ' <small>\u00d7 ' + b.size[2].toFixed(1) + " mm</small>",
        sub: fitText,
      },
      {
        label: "Motion",
        value: (mo.path_mm / 1000).toFixed(1) + ' <small>m path</small>',
        sub:
          fmtNum(mo.layers) + " layers \u00b7 " + fmtNum(mo.segments) + " blocks \u00b7 peak " +
          mo.max_speed_mm_s.toFixed(0) + " mm/s",
      },
    ];

    $("kpis").innerHTML = cards
      .map(
        (card) =>
          '<article class="kpi"><span>' + card.label + "</span><strong>" +
          card.value + "</strong><small>" + card.sub + "</small></article>",
      )
      .join("");
  }

  function renderNotes() {
    const warnings = state.data.metrics.warnings || [];
    $("notesPanel").hidden = warnings.length === 0;
    $("noteCount").textContent = warnings.length ? warnings.length + " item(s)" : "";
    $("notes").innerHTML = warnings.map((w) => "<li>" + escapeHtml(w) + "</li>").join("");
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  /* ---------------------------------------------------------------- tables */

  function renderTables() {
    const m = state.data.metrics;
    const t = m.time;
    const mo = m.motion;
    const info = m.file;

    const timeRows = [
      ["Total (planned)", fmtTimeLong(t.total_s)],
      ["Motion only", fmtTimeLong(t.motion_s)],
      ["Printing", fmtTimeLong(t.print_s)],
      ["Travelling", fmtTimeLong(t.travel_s)],
      ["Dwells", fmtTimeLong(t.dwell_s)],
      ["Retractions", fmtTimeLong(t.retract_s)],
      ["Distance / feedrate", fmtTimeLong(t.naive_s)],
      ["Acceleration cost", "+" + t.accel_overhead_pct.toFixed(1) + " %"],
    ];

    const diagRows = [
      ["File", info.name],
      ["Lines", fmtNum(info.lines)],
      ["Slicer", info.slicer || "unknown"],
      ["Interpolated blocks", fmtNum(mo.segments)],
      ["Arcs (G2/G3)", fmtNum(mo.arcs) + " \u00b7 " + (mo.arc_length_mm / 1000).toFixed(2) + " m"],
      ["Rendered points", fmtNum(state.data.path.count) + " / " + fmtNum(state.data.path.total_points)],
      ["Peak speed", mo.max_speed_mm_s.toFixed(1) + " mm/s"],
      ["Mean print speed", mo.mean_print_speed_mm_s.toFixed(1) + " mm/s"],
      ["Peak flow", mo.max_flow_mm3_s.toFixed(2) + " mm\u00b3/s"],
      ["Retractions", fmtNum(mo.retractions) + " \u00b7 " + m.filament.retracted_mm.toFixed(1) + " mm"],
      ["Temperatures", (info.nozzle_temp ?? "-") + " \u00b0C noz / " + (info.bed_temp ?? "-") + " \u00b0C bed"],
      ["Analysed in", (state.data.elapsed_ms / 1000).toFixed(2) + " s"],
    ];

    $("timeTable").innerHTML = timeRows
      .map(([k, v]) => "<tr><td>" + k + "</td><td>" + v + "</td></tr>")
      .join("");
    $("diagTable").innerHTML = diagRows
      .map(([k, v]) => "<tr><td>" + k + '</td><td class="wrap">' + escapeHtml(v) + "</td></tr>")
      .join("");
  }

  /* ---------------------------------------------------------------- charts */

  const CHART_FONT = { family: "ui-monospace, Consolas, monospace", size: 10, color: "#7c8a9e" };

  function baseChartLayout(extra) {
    return Object.assign(
      {
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "rgba(0,0,0,0)",
        font: CHART_FONT,
        margin: { l: 48, r: 14, t: 8, b: 34 },
        showlegend: false,
        bargap: 0.12,
        hoverlabel: {
          bgcolor: "#121820", bordercolor: "#2a3441",
          font: { family: "ui-monospace, monospace", size: 11, color: "#dde5f2" },
        },
        xaxis: { gridcolor: "#161d27", zeroline: false, linecolor: "#1e2632", tickfont: CHART_FONT },
        yaxis: { gridcolor: "#161d27", zeroline: false, linecolor: "#1e2632", tickfont: CHART_FONT },
      },
      extra || {},
    );
  }

  function renderCharts() {
    const layers = state.data.metrics.layers || [];
    const feeds = state.data.metrics.feeds || [];
    const plotConfig = { displayModeBar: false, responsive: true };

    if (layers.length) {
      const xs = layers.map((row) => row.i + 1);
      const ys = layers.map((row) => row.time_s);
      Plotly.react(
        "layerChart",
        [{
          type: "bar",
          x: xs,
          y: ys,
          customdata: layers.map((row) => row.z),
          marker: { color: "#ffb454", line: { width: 0 } },
          hovertemplate: "Layer %{x}<br>%{y:.1f} s<br>Z %{customdata:.2f} mm<extra></extra>",
        }],
        baseChartLayout({
          xaxis: Object.assign(baseChartLayout().xaxis, { title: { text: "layer", font: CHART_FONT } }),
          yaxis: Object.assign(baseChartLayout().yaxis, { title: { text: "seconds", font: CHART_FONT } }),
          margin: { l: 46, r: 12, t: 8, b: 34 },
        }),
        plotConfig,
      );
    }

    if (feeds.length) {
      const feedsSorted = feeds.slice().sort((a, b) => a.time_s - b.time_s);
      Plotly.react(
        "feedChart",
        [{
          type: "bar",
          orientation: "h",
          x: feedsSorted.map((row) => row.time_s),
          y: feedsSorted.map((row) => row.feed_mm_s.toFixed(0) + " mm/s"),
          marker: { color: "#3ad2c0", line: { width: 0 } },
          hovertemplate: "%{y}<br>%{x:.1f} s<extra></extra>",
        }],
        baseChartLayout({
          margin: { l: 78, r: 14, t: 8, b: 34 },
          xaxis: Object.assign(baseChartLayout().xaxis, { title: { text: "seconds", font: CHART_FONT } }),
        }),
        plotConfig,
      );
    }
  }

  /* ------------------------------------------------------------- 3D canvas */

  function speedRange(path) {
    const speeds = [];
    for (let i = 0; i < path.count; i++) {
      if (path.extrude[i] === 1 && path.speed[i] > 0.01) speeds.push(path.speed[i]);
    }
    if (!speeds.length) return [0, 1];
    speeds.sort((a, b) => a - b);
    const lo = speeds[Math.floor(speeds.length * 0.03)] || 0;
    const hi = speeds[Math.floor(speeds.length * 0.97)] || speeds[speeds.length - 1];
    return [Math.max(0, lo), Math.max(hi, lo + 0.1)];
  }

  function visibleSegment(path, index, opts) {
    if (!opts.showTravel && path.extrude[index] === 0) return false;
    if (opts.layerMode && path.layer[index] !== opts.layer) return false;
    return true;
  }

  function collectRuns(path, opts) {
    const runs = [];
    let start = -1;
    let end = -1;
    let type = -1;
    for (let i = 0; i < path.count - 1; i++) {
      if (!visibleSegment(path, i, opts)) {
        if (start >= 0) { runs.push({ a: start, b: end, type }); start = -1; }
        continue;
      }
      const t = path.extrude[i] === 1 ? 1 : 0;
      if (start >= 0 && i === end + 1 && t === type) {
        end = i;
      } else {
        if (start >= 0) runs.push({ a: start, b: end, type });
        start = i;
        end = i;
        type = t;
      }
    }
    if (start >= 0) runs.push({ a: start, b: end, type });
    return runs;
  }

  function buildCoords(runs, path, type) {
    const xs = [];
    const ys = [];
    const zs = [];
    const cs = [];
    let last = 0;
    const speedScale = state.view.speedColor;
    for (const run of runs) {
      if (run.type !== type) continue;
      for (let i = run.a; i <= run.b + 1; i++) {
        xs.push(path.x[i]);
        ys.push(path.y[i]);
        zs.push(path.z[i]);
        last = path.speed[i];
        cs.push(speedScale ? last : 0);
      }
      // gap: null coordinates break the polyline, the colour stays valid
      xs.push(null);
      ys.push(null);
      zs.push(null);
      cs.push(last);
    }
    return { xs, ys, zs, cs };
  }

  function renderPlot() {
    if (!state.data) return;
    const path = state.data.path;
    const opts = {
      layerMode: state.view.layerMode,
      layer: state.view.layer,
      showTravel: state.view.showTravel,
    };
    const runs = collectRuns(path, opts);
    const ext = buildCoords(runs, path, 1);
    const trav = buildCoords(runs, path, 0);
    const traces = [];

    if (ext.xs.length) {
      const line = state.view.speedColor
        ? {
            color: ext.cs,
            colorscale: [
              [0.0, "#2b4c7e"], [0.25, "#2f8fa3"], [0.5, "#47c48a"],
              [0.75, "#e8c547"], [1.0, "#ff6b4a"],
            ],
            cmin: state.colorRange[0],
            cmax: state.colorRange[1],
            width: 4,
            colorbar: {
              title: { text: "mm/s", side: "top", font: { size: 9, color: "#7c8a9e" } },
              thickness: 8, len: 0.45, x: 1.015, xpad: 4,
              tickfont: { size: 9, color: "#7c8a9e" },
              outlinewidth: 0, bgcolor: "rgba(0,0,0,0)",
            },
          }
        : { color: "#ffb454", width: 4 };
      traces.push(
        ToolpathPlot.extrusionTrace(
          { x: ext.xs, y: ext.ys, z: ext.zs },
          line,
          "X %{x:.1f}  Y %{y:.1f}  Z %{z:.1f}<extra>extrusion</extra>",
        ),
      );
    }

    if (trav.xs.length) {
      traces.push({
        type: "scatter3d",
        mode: "lines",
        name: "travel",
        x: trav.xs, y: trav.ys, z: trav.zs,
        line: { color: "#39465a", width: 2 },
        hovertemplate: "X %{x:.1f}  Y %{y:.1f}  Z %{z:.1f}<extra>travel</extra>",
      });
    }

    traces.push(ToolpathPlot.boxTrace(state.data.metrics.bbox));

    const axis = (title) => ({
      title: { text: title, font: { size: 10, color: "#5f6b7d" } },
      color: "#5f6b7d",
      gridcolor: "#151d27",
      zeroline: false,
      showbackground: false,
      tickfont: { size: 9, color: "#5f6b7d" },
    });

    const layout = {
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      margin: { l: 0, r: 0, t: 0, b: 0 },
      showlegend: false,
      hovermode: "closest",
      hoverlabel: {
        bgcolor: "#121820", bordercolor: "#2a3441",
        font: { family: "ui-monospace, monospace", size: 11, color: "#dde5f2" },
      },
      scene: {
        bgcolor: "rgba(0,0,0,0)",
        aspectmode: "data",
        xaxis: axis("X"),
        yaxis: axis("Y"),
        zaxis: axis("Z"),
        camera: { eye: { x: 1.55, y: -1.65, z: 0.85 }, up: { x: 0, y: 0, z: 1 } },
      },
    };

    const config = {
      responsive: true,
      displaylogo: false,
      modeBarButtonsToRemove: ["sendDataToCloud", "resetCameraLastSave3d"],
      displayModeBar: "hover",
    };

    if (!state.plotReady) {
      Plotly.newPlot("plot3d", traces, layout, config);
      state.plotReady = true;
    } else {
      Plotly.react("plot3d", traces, layout, config);
    }
  }

  /* ---------------------------------------------------------------- layers */

  function updateLayerUi() {
    if (!state.data) return;
    const total = state.data.metrics.motion.layers;
    const slider = $("layerSlider");
    const mode = $("layerMode");
    mode.checked = state.view.layerMode;
    slider.max = String(Math.max(0, total - 1));
    slider.value = String(state.view.layer);
    slider.disabled = !state.view.layerMode || total <= 1;

    if (!state.view.layerMode) {
      $("layerLabel").textContent = "all layers \u00b7 " + fmtNum(total);
      $("playBtn").classList.remove("playing");
      return;
    }
    const z = state.layerZ[state.view.layer] ?? 0;
    $("layerLabel").textContent =
      "layer " + (state.view.layer + 1) + "/" + fmtNum(total) + " \u00b7 Z " + z.toFixed(2) + " mm";
    $("playBtn").classList.toggle("playing", state.view.playing);
  }

  function stopPlayback() {
    if (state.view.timer) clearInterval(state.view.timer);
    state.view.timer = null;
    state.view.playing = false;
    $("playBtn").classList.remove("playing");
    $("playBtn").innerHTML =
      '<svg viewBox="0 0 16 16" width="12" height="12"><path d="M4 2.5 L13 8 L4 13.5 Z" fill="currentColor"/></svg>';
  }

  function togglePlayback() {
    if (!state.data) return;
    const total = state.data.metrics.motion.layers;
    if (state.view.playing) {
      stopPlayback();
      return;
    }
    state.view.layerMode = true;
    state.view.playing = true;
    if (state.view.layer >= total - 1) state.view.layer = 0;
    $("playBtn").classList.add("playing");
    $("playBtn").innerHTML =
      '<svg viewBox="0 0 16 16" width="12" height="12"><rect x="3.5" y="2.5" width="3.5" height="11"/><rect x="9" y="2.5" width="3.5" height="11"/></svg>';
    updateLayerUi();
    renderPlot();
    state.view.timer = setInterval(() => {
      if (state.view.layer >= total - 1) {
        stopPlayback();
        return;
      }
      state.view.layer += 1;
      updateLayerUi();
      renderPlot();
    }, 150);
  }

  function setTab(tab) {
    state.view.tab = tab;
    document.querySelectorAll(".tab").forEach((el) => {
      el.classList.toggle("active", el.dataset.tab === tab);
    });
    $("viewPath").hidden = tab !== "path";
    $("viewAnalysis").hidden = tab !== "analysis";
    if (tab === "path" && state.plotReady) {
      Plotly.Plots.resize($("plot3d"));
    }
    if (tab === "analysis") renderCharts();
  }

  /* ------------------------------------------------------------------- init */

  function populatePresets() {
    const select = $("preset");
    select.innerHTML = Object.entries(PRESETS)
      .map(([key, value]) => '<option value="' + key + '">' + value.label + "</option>")
      .join("");
  }

  async function populateSamples() {
    try {
      const response = await fetch("/api/samples");
      const data = await response.json();
      const samples = (data.samples || []).filter((s) => s.name !== "");
      if (!samples.length) return;
      $("sampleBtn").hidden = false;
      $("sampleList").innerHTML = samples
        .map(
          (s) =>
            "<button type='button' data-sample='" + escapeHtml(s.name) + "'>" +
            "<b>" + escapeHtml(s.title) + "</b>" +
            "<span>" + escapeHtml(s.name) + " \u00b7 " + (s.bytes / 1024).toFixed(1) + " kB</span></button>",
        )
        .join("");
      $("emptySamples").innerHTML = samples
        .map(
          (s) =>
            "<button type='button' data-sample='" + escapeHtml(s.name) + "'>" +
            escapeHtml(s.title) + "<small>" + escapeHtml(s.name) + "</small></button>",
        )
        .join("");
    } catch (err) { /* samples are optional */ }
  }

  function scheduleReplan(delay) {
    if (!state.data) return;
    clearTimeout(scheduleReplan._timer);
    $("recompute").disabled = false;
    scheduleReplan._timer = setTimeout(() => replan(), delay ?? 600);
  }

  async function replan() {
    if (!state.data) return;
    clearError();
    setLoading("Re-planning motion\u2026");
    try {
      const response = await fetch("/api/replan/" + state.data.id, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ config: state.config }),
      });
      await handleResponse(response, state.data.file);
    } catch (err) {
      showError(err.message || String(err));
    } finally {
      clearLoading();
    }
  }

  function bind() {
    $("fileInput").addEventListener("change", (event) => {
      const file = event.target.files && event.target.files[0];
      analyzeFile(file);
      event.target.value = "";
    });

    const dropzone = $("dropzone");
    dropzone.addEventListener("click", () => $("fileInput").click());
    dropzone.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        $("fileInput").click();
      }
    });

    ["dragenter", "dragover"].forEach((type) => {
      window.addEventListener(type, (event) => {
        event.preventDefault();
        dropzone.classList.add("drag");
      });
    });
    ["dragleave", "drop"].forEach((type) => {
      window.addEventListener(type, (event) => {
        event.preventDefault();
        if (type === "dragleave" && event.relatedTarget) return;
        dropzone.classList.remove("drag");
      });
    });
    window.addEventListener("drop", (event) => {
      const files = event.dataTransfer && event.dataTransfer.files;
      if (files && files.length) analyzeFile(files[0]);
    });

    document.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", () => setTab(tab.dataset.tab));
    });

    $("showTravel").addEventListener("change", (event) => {
      state.view.showTravel = event.target.checked;
      renderPlot();
    });
    $("speedColor").addEventListener("change", (event) => {
      state.view.speedColor = event.target.checked;
      renderPlot();
    });
    $("layerMode").addEventListener("change", (event) => {
      state.view.layerMode = event.target.checked;
      if (!state.view.layerMode) stopPlayback();
      updateLayerUi();
      renderPlot();
    });
    $("layerSlider").addEventListener("input", (event) => {
      state.view.layer = parseInt(event.target.value, 10) || 0;
      stopPlayback();
      updateLayerUi();
      renderPlot();
    });
    $("playBtn").addEventListener("click", togglePlayback);

    $("preset").addEventListener("change", (event) => {
      state.preset = event.target.value;
      if (state.preset !== "custom") {
        Object.assign(state.config, pickPreset(state.preset));
        syncInputs();
        saveConfig();
        scheduleReplan(0);
      } else {
        syncInputs();
      }
    });

    $("resetMachine").addEventListener("click", () => {
      state.preset = "ender3";
      Object.assign(state.config, pickPreset("ender3"));
      syncInputs();
      saveConfig();
      scheduleReplan(0);
    });

    for (const key of CONFIG_KEYS) {
      const el = $(key);
      if (!el) continue;
      el.addEventListener("change", () => {
        readInputs();
        syncInputs();
        scheduleReplan(400);
      });
    }

    $("recompute").addEventListener("click", () => replan());
    $("dismissError").addEventListener("click", clearError);

    $("sampleBtn").addEventListener("click", (event) => {
      event.stopPropagation();
      $("sampleList").classList.toggle("open");
    });
    document.addEventListener("click", (event) => {
      if (!event.target.closest(".sample-menu")) $("sampleList").classList.remove("open");
    });
    document.addEventListener("click", (event) => {
      const button = event.target.closest("[data-sample]");
      if (!button) return;
      $("sampleList").classList.remove("open");
      loadSample(button.dataset.sample);
    });

    window.addEventListener("resize", () => {
      if (state.view.tab === "path" && state.plotReady) {
        Plotly.Plots.resize($("plot3d"));
      }
    });
  }

  function boot() {
    populatePresets();
    state.preset = (() => {
      try {
        const raw = localStorage.getItem(STORE_KEY);
        if (raw) return JSON.parse(raw).preset || "ender3";
      } catch (err) { /* ignore */ }
      return "ender3";
    })();
    syncInputs();
    bind();
    populateSamples();
    setTab("path");
    $("sampleBtn").hidden = true;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
