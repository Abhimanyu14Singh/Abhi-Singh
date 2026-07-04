/* SkyFrame story-results charts — hand-rolled SVG (no deps).
   Three linked charts: story displacement, drift ratio (with limit line),
   story shear. Vertical axis = story elevation; series X / Y direction. */

const S = {
  x: "var(--series-x)", y: "var(--series-y)",
  xRaw: "#1e9ad4", yRaw: "#d55181",
  grid: "#202836", axis: "#66727f", text: "#9aa7b4",
  amber: "#e5a50a", surface: "#151b23",
};

/* v0.4 — theme swap so the same chart builders render on the report's
   LIGHT, print-friendly page. Mutates S in place; callers restore "dark". */
const THEMES = {
  dark: { ...S },
  light: {
    x: "#1274ab", y: "#b93a67",
    xRaw: "#1274ab", yRaw: "#b93a67",
    grid: "#e3e8ee", axis: "#8a94a0", text: "#5c6672",
    amber: "#b57e00", surface: "#ffffff",
  },
};
export function setChartTheme(mode) {
  Object.assign(S, THEMES[mode] || THEMES.dark);
}

const NS = "http://www.w3.org/2000/svg";
function el(tag, attrs = {}, children = []) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  for (const c of children) e.appendChild(c);
  return e;
}
function txt(tag, attrs, text) { const e = el(tag, attrs); e.textContent = text; return e; }

/** Nice tick values covering [0, max] (data starts at 0 in all 3 charts). */
function niceTicks(max, n = 4) {
  if (max <= 0) max = 1;
  const raw = max / n;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
  const ticks = [];
  for (let v = 0; v <= max * 1.0001 + step * 0.5; v += step) {
    ticks.push(v);
    if (ticks.length > 12) break;
  }
  return ticks;
}

let tooltipDiv = null;
function tooltip() {
  if (!tooltipDiv) {
    tooltipDiv = document.createElement("div");
    tooltipDiv.className = "chart-tooltip hidden";
    document.body.appendChild(tooltipDiv);
  }
  return tooltipDiv;
}
function showTip(html, cx, cy) {
  const t = tooltip();
  t.innerHTML = html;
  t.classList.remove("hidden");
  const r = t.getBoundingClientRect();
  let x = cx + 14, y = cy - r.height / 2;
  if (x + r.width > window.innerWidth - 8) x = cx - r.width - 14;
  y = Math.max(8, Math.min(y, window.innerHeight - r.height - 8));
  t.style.left = x + "px"; t.style.top = y + "px";
}
function hideTip() { tooltip().classList.add("hidden"); }

const fmt = (v, d = 1) => (v == null || !isFinite(v)) ? "—" :
  v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });

/**
 * rows: bottom→top [{story, elev, h, vx, vy}] — vx/vy already in display units.
 * opts: {title, unit, kind:"line"|"step", limit (display units), zero:"include"}
 */
function storyChart(rows, opts) {
  const W = 320, H = 300;
  const M = { l: 58, r: 14, t: 10, b: 34 };
  const pw = W - M.l - M.r, ph = H - M.t - M.b;

  const elevs = [0, ...rows.map(r => r.elev)];
  const maxE = elevs[elevs.length - 1] || 1;
  const yOf = e => M.t + ph - (e / maxE) * ph;

  let maxV = 0;
  for (const r of rows) maxV = Math.max(maxV, Math.abs(r.vx), Math.abs(r.vy));
  if (opts.limit != null) maxV = Math.max(maxV, opts.limit);
  if (maxV <= 0) maxV = 1;
  const ticks = niceTicks(maxV * 1.06);
  const maxT = ticks[ticks.length - 1];
  const xOf = v => M.l + (Math.abs(v) / maxT) * pw;

  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });

  // gridlines (vertical, at value ticks) + x labels
  for (const t of ticks) {
    svg.appendChild(el("line", {
      x1: xOf(t), x2: xOf(t), y1: M.t, y2: M.t + ph,
      stroke: S.grid, "stroke-width": 1,
    }));
    svg.appendChild(txt("text", {
      x: xOf(t), y: M.t + ph + 16, fill: S.text, "font-size": 10,
      "text-anchor": "middle", style: "font-variant-numeric:tabular-nums",
    }, fmt(t, maxT < 10 ? 1 : 0)));
  }
  // horizontal story lines + labels
  svg.appendChild(txt("text", {
    x: M.l - 8, y: yOf(0) + 3, fill: S.axis, "font-size": 10, "text-anchor": "end",
  }, "Base"));
  for (const r of rows) {
    svg.appendChild(el("line", {
      x1: M.l, x2: M.l + pw, y1: yOf(r.elev), y2: yOf(r.elev),
      stroke: S.grid, "stroke-width": 1, "stroke-opacity": 0.6,
    }));
    svg.appendChild(txt("text", {
      x: M.l - 8, y: yOf(r.elev) + 3, fill: S.text, "font-size": 10, "text-anchor": "end",
    }, r.story.replace(/^Story/, "S")));
  }
  // axes
  svg.appendChild(el("line", { x1: M.l, x2: M.l, y1: M.t, y2: M.t + ph, stroke: S.axis, "stroke-width": 1 }));
  svg.appendChild(el("line", { x1: M.l, x2: M.l + pw, y1: M.t + ph, y2: M.t + ph, stroke: S.axis, "stroke-width": 1 }));
  // x axis unit
  svg.appendChild(txt("text", {
    x: M.l + pw, y: M.t + ph + 28, fill: S.axis, "font-size": 10, "text-anchor": "end",
  }, opts.unit));

  // limit line (drift chart)
  if (opts.limit != null && opts.limit <= maxT) {
    const lx = xOf(opts.limit);
    svg.appendChild(el("line", {
      x1: lx, x2: lx, y1: M.t, y2: M.t + ph,
      stroke: S.amber, "stroke-width": 1.5, "stroke-dasharray": "5 4",
    }));
    svg.appendChild(txt("text", {
      x: lx, y: M.t + 2, fill: S.amber, "font-size": 9, "text-anchor": "middle", dy: "-0", transform: `translate(0,-2)`,
    }, "limit"));
  }

  // series
  const series = [
    { key: "vx", color: S.xRaw, label: "X" },
    { key: "vy", color: S.yRaw, label: "Y" },
  ];
  for (const ser of series) {
    const pts = [];
    if (opts.kind === "step") {
      // shear constant over story height: stairs from base up
      let prevX = null;
      for (let i = rows.length - 1; i >= 0; i--) {} // noop, keep order bottom->top
      for (let i = 0; i < rows.length; i++) {
        const r = rows[i];
        const zBot = i === 0 ? 0 : rows[i - 1].elev;
        const x = xOf(r[ser.key]);
        pts.push([x, yOf(zBot)]);
        pts.push([x, yOf(r.elev)]);
        prevX = x;
      }
    } else {
      pts.push([xOf(0), yOf(0)]);
      for (const r of rows) pts.push([xOf(r[ser.key]), yOf(r.elev)]);
    }
    const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
    svg.appendChild(el("path", {
      d, fill: "none", stroke: ser.color, "stroke-width": 2,
      "stroke-linejoin": "round", "stroke-linecap": "round",
    }));
    // markers at story levels (with surface ring)
    if (opts.kind !== "step") {
      for (const r of rows) {
        const exceeded = opts.limit != null && Math.abs(r[ser.key]) > opts.limit;
        svg.appendChild(el("circle", {
          cx: xOf(r[ser.key]), cy: yOf(r.elev), r: exceeded ? 4.5 : 3.5,
          fill: exceeded ? S.amber : ser.color, stroke: S.surface, "stroke-width": 2,
        }));
      }
    }
  }

  // hover: nearest story row
  const hot = el("rect", {
    x: M.l, y: M.t, width: pw, height: ph, fill: "transparent",
  });
  const cross = el("line", {
    x1: M.l, x2: M.l + pw, y1: 0, y2: 0, stroke: S.axis,
    "stroke-width": 1, "stroke-dasharray": "3 3", visibility: "hidden",
  });
  svg.appendChild(cross);
  hot.addEventListener("mousemove", e => {
    const rect = svg.getBoundingClientRect();
    const sy = (e.clientY - rect.top) * (H / rect.height);
    let best = null, bd = 1e9;
    for (const r of rows) {
      const d = Math.abs(yOf(r.elev) - sy);
      if (d < bd) { bd = d; best = r; }
    }
    if (!best) return;
    cross.setAttribute("y1", yOf(best.elev));
    cross.setAttribute("y2", yOf(best.elev));
    cross.setAttribute("visibility", "visible");
    showTip(
      `<b>${best.story}</b> · ${fmt(best.elev, 1)} m<br>` +
      `<span style="color:${S.xRaw}">●</span> X ${fmt(best.vx, opts.dec ?? 2)} ${opts.unit}<br>` +
      `<span style="color:${S.yRaw}">●</span> Y ${fmt(best.vy, opts.dec ?? 2)} ${opts.unit}`,
      e.clientX, e.clientY);
  });
  hot.addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); hideTip(); });
  svg.appendChild(hot);

  const card = document.createElement("div");
  card.className = "chart-card";
  const title = document.createElement("div");
  title.className = "chart-title";
  title.innerHTML = `${opts.title} <span class="unit">${opts.unit}</span>`;
  card.appendChild(title);
  card.appendChild(svg);
  return card;
}

/**
 * Small member-station diagram (N / V / M along the member).
 * xs: station positions (m), vs: values. Filled-area style with zero line
 * and min/max labels (value @ position).
 */
export function stationDiagram(xs, vs, opts = {}) {
  const W = 268, H = 104;
  const M = { l: 10, r: 10, t: 8, b: 16 };
  const pw = W - M.l - M.r, ph = H - M.t - M.b;
  const L = xs[xs.length - 1] || 1;
  const color = opts.color || "#1e9ad4";

  let lo = Math.min(0, ...vs), hi = Math.max(0, ...vs);
  if (hi - lo < 1e-9) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.12;
  hi += pad; lo -= pad;
  const xOf = x => M.l + (x / L) * pw;
  const yOf = v => M.t + (hi - v) / (hi - lo) * ph;

  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });

  // zero line + member baseline ticks
  svg.appendChild(el("line", {
    x1: M.l, x2: M.l + pw, y1: yOf(0), y2: yOf(0),
    stroke: S.axis, "stroke-width": 1,
  }));
  for (const t of [0, 0.25, 0.5, 0.75, 1]) {
    svg.appendChild(el("line", {
      x1: xOf(t * L), x2: xOf(t * L), y1: yOf(0) - 2.5, y2: yOf(0) + 2.5,
      stroke: S.axis, "stroke-width": 1, "stroke-opacity": 0.55,
    }));
  }
  svg.appendChild(txt("text", {
    x: M.l, y: H - 4, fill: S.axis, "font-size": 9, "text-anchor": "start",
    style: "font-variant-numeric:tabular-nums",
  }, "0"));
  svg.appendChild(txt("text", {
    x: M.l + pw, y: H - 4, fill: S.axis, "font-size": 9, "text-anchor": "end",
    style: "font-variant-numeric:tabular-nums",
  }, `${fmt(L, 1)} m`));

  // filled area + stroke
  let dArea = `M${xOf(xs[0]).toFixed(1)},${yOf(0).toFixed(1)}`;
  let dLine = "";
  xs.forEach((x, i) => {
    const px = xOf(x).toFixed(1), py = yOf(vs[i]).toFixed(1);
    dArea += ` L${px},${py}`;
    dLine += `${i ? " L" : "M"}${px},${py}`;
  });
  dArea += ` L${xOf(xs[xs.length - 1]).toFixed(1)},${yOf(0).toFixed(1)} Z`;
  svg.appendChild(el("path", { d: dArea, fill: color, "fill-opacity": 0.18 }));
  svg.appendChild(el("path", {
    d: dLine, fill: "none", stroke: color, "stroke-width": 1.8,
    "stroke-linejoin": "round", "stroke-linecap": "round",
  }));

  // min / max annotations (value @ x)
  let iMax = 0, iMin = 0;
  vs.forEach((v, i) => { if (v > vs[iMax]) iMax = i; if (v < vs[iMin]) iMin = i; });
  const dec = opts.dec != null ? opts.dec : 1;
  const label = (i, above) => {
    const v = vs[i];
    if (Math.abs(v) < 1e-9) return;
    const px = Math.max(M.l + 26, Math.min(xOf(xs[i]), M.l + pw - 26));
    const py = above ? Math.max(yOf(v) - 5, 9) : Math.min(yOf(v) + 11, H - 6);
    svg.appendChild(el("circle", { cx: xOf(xs[i]), cy: yOf(v), r: 2.2, fill: color }));
    svg.appendChild(txt("text", {
      x: px, y: py, fill: S.text, "font-size": 9, "text-anchor": "middle",
      style: "font-variant-numeric:tabular-nums",
    }, `${fmt(v, dec)} @ ${fmt(xs[i], 1)}`));
  };
  label(iMax, true);
  if (iMin !== iMax) label(iMin, false);

  const card = document.createElement("div");
  card.className = "diagram-card";
  const title = document.createElement("div");
  title.className = "diagram-title";
  title.innerHTML = `<b>${opts.title || ""}</b> <span class="unit">${opts.unit || ""}</span>`;
  card.appendChild(title);
  card.appendChild(svg);
  return card;
}

/**
 * Response-spectrum preview — Sa(g) vs T with a log-ish (sqrt-compressed)
 * period axis from 0 to max T. Returns a bare SVG element (the caller owns
 * the surrounding card). points: [[T, Sa], …] in any order.
 */
export function spectrumChart(points, opts = {}) {
  const W = opts.width || 320, H = opts.height || 176;
  const M = { l: 40, r: 12, t: 18, b: 26 };
  const pw = W - M.l - M.r, ph = H - M.t - M.b;
  const color = opts.color || S.xRaw;

  const pts = (points || [])
    .filter(p => Array.isArray(p) && isFinite(p[0]) && isFinite(p[1]) && p[0] >= 0)
    .slice().sort((a, b) => a[0] - b[0]);

  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });
  if (pts.length < 2) {
    svg.appendChild(txt("text", {
      x: W / 2, y: H / 2, fill: S.axis, "font-size": 11, "text-anchor": "middle",
    }, "Add at least 2 spectrum points"));
    return svg;
  }

  const maxT = pts[pts.length - 1][0] || 1;
  const maxSa = Math.max(...pts.map(p => p[1]), 1e-6);
  const sTicks = niceTicks(maxSa * 1.08, 3);
  const maxS_ = sTicks[sTicks.length - 1];
  // sqrt compression: keeps 0 on the axis, spreads the short-period range
  const xOf = T => M.l + Math.sqrt(T / maxT) * pw;
  const yOf = v => M.t + ph - (v / maxS_) * ph;

  // Sa gridlines + labels
  for (const t of sTicks) {
    svg.appendChild(el("line", {
      x1: M.l, x2: M.l + pw, y1: yOf(t), y2: yOf(t), stroke: S.grid, "stroke-width": 1,
    }));
    svg.appendChild(txt("text", {
      x: M.l - 6, y: yOf(t) + 3, fill: S.text, "font-size": 9, "text-anchor": "end",
      style: "font-variant-numeric:tabular-nums",
    }, fmt(t, maxS_ < 2 ? 1 : 0)));
  }
  // period gridlines at nice T values that exist inside the range
  const tTicks = [0.1, 0.2, 0.5, 1, 2, 3, 4, 6, 8, 10].filter(t => t <= maxT * 1.001);
  for (const t of [0, ...tTicks, maxT]) {
    svg.appendChild(el("line", {
      x1: xOf(t), x2: xOf(t), y1: M.t, y2: M.t + ph,
      stroke: S.grid, "stroke-width": 1, "stroke-opacity": 0.55,
    }));
    svg.appendChild(txt("text", {
      x: xOf(t), y: M.t + ph + 14, fill: S.text, "font-size": 9, "text-anchor": "middle",
      style: "font-variant-numeric:tabular-nums",
    }, fmt(t, t < 1 && t > 0 ? 1 : 0)));
  }
  // axes + units
  svg.appendChild(el("line", { x1: M.l, x2: M.l, y1: M.t, y2: M.t + ph, stroke: S.axis, "stroke-width": 1 }));
  svg.appendChild(el("line", { x1: M.l, x2: M.l + pw, y1: M.t + ph, y2: M.t + ph, stroke: S.axis, "stroke-width": 1 }));
  svg.appendChild(txt("text", {
    x: M.l + pw, y: M.t + ph + 24, fill: S.axis, "font-size": 9, "text-anchor": "end",
  }, "T  s"));
  svg.appendChild(txt("text", {
    x: M.l - 6, y: 9, fill: S.axis, "font-size": 9, "text-anchor": "end",
  }, "Sa g"));

  // filled area + line + point markers
  let dArea = `M${xOf(pts[0][0]).toFixed(1)},${yOf(0).toFixed(1)}`;
  let dLine = "";
  pts.forEach((p, i) => {
    const px = xOf(p[0]).toFixed(1), py = yOf(p[1]).toFixed(1);
    dArea += ` L${px},${py}`;
    dLine += `${i ? " L" : "M"}${px},${py}`;
  });
  dArea += ` L${xOf(maxT).toFixed(1)},${yOf(0).toFixed(1)} Z`;
  svg.appendChild(el("path", { d: dArea, fill: color, "fill-opacity": 0.14 }));
  svg.appendChild(el("path", {
    d: dLine, fill: "none", stroke: color, "stroke-width": 2,
    "stroke-linejoin": "round", "stroke-linecap": "round", class: "spectrum-line",
  }));
  for (const p of pts) {
    svg.appendChild(el("circle", {
      cx: xOf(p[0]), cy: yOf(p[1]), r: 3, fill: p[1] === maxSa ? color : S.surface,
      stroke: color, "stroke-width": 1.5,
    }));
  }
  // peak annotation
  const peak = pts.find(p => p[1] === maxSa);
  if (peak) {
    svg.appendChild(txt("text", {
      x: Math.min(xOf(peak[0]) + 6, M.l + pw - 30), y: Math.max(yOf(peak[1]) - 6, 9),
      fill: S.text, "font-size": 9, style: "font-variant-numeric:tabular-nums",
    }, `${fmt(maxSa, 2)} g`));
  }
  return svg;
}

/**
 * Render the three story charts into `container`.
 * caseData.story: {story: {ux,uy,drift_x,drift_y,shear_x,shear_y}}
 * driftLimitPct: e.g. 0.5 (%)
 */
export function renderStoryCharts(container, results, caseData, driftLimitPct) {
  container.textContent = "";
  const rows = results.story_order.map(s => {
    const st = caseData.story[s] || {};
    return { story: s, elev: results.story_elev[s] };
  });
  const dispRows = results.story_order.map((s, i) => {
    const st = caseData.story[s] || {};
    return {
      story: s, elev: results.story_elev[s],
      vx: (st.ux || 0) * 1000, vy: (st.uy || 0) * 1000,   // m → mm
    };
  });
  const driftRows = results.story_order.map(s => {
    const st = caseData.story[s] || {};
    return {
      story: s, elev: results.story_elev[s],
      vx: Math.abs(st.drift_x || 0) * 100, vy: Math.abs(st.drift_y || 0) * 100,  // ratio → %
    };
  });
  const shearRows = results.story_order.map(s => {
    const st = caseData.story[s] || {};
    return {
      story: s, elev: results.story_elev[s],
      vx: Math.abs(st.shear_x || 0), vy: Math.abs(st.shear_y || 0),
    };
  });

  container.appendChild(storyChart(dispRows, {
    title: "Story displacement", unit: "mm", kind: "line", dec: 1,
  }));
  container.appendChild(storyChart(driftRows, {
    title: "Story drift ratio", unit: "%", kind: "line", limit: driftLimitPct, dec: 3,
  }));
  container.appendChild(storyChart(shearRows, {
    title: "Story shear", unit: "kN", kind: "step", dec: 1,
  }));
}

/* ================================================================
   v0.4 — time-history charts
   ================================================================ */

/** Symmetric-capable value ticks for a [lo, hi] domain that includes 0. */
function spanTicks(lo, hi, n = 4) {
  const span = Math.max(hi - lo, 1e-9);
  const raw = span / n;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
  const ticks = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-6; v += step) {
    ticks.push(+v.toFixed(9));
    if (ticks.length > 12) break;
  }
  return ticks;
}

/**
 * Time-series line chart (full trace over t) with crosshair + tooltip.
 * t: seconds; series: [{label, values, color}] (≤ 2); opts: {title, unit, dec}.
 * Returns a .chart-card div.
 */
export function timeSeriesChart(t, series, opts = {}) {
  const W = 640, H = 220;
  const M = { l: 56, r: 12, t: 10, b: 30 };
  const pw = W - M.l - M.r, ph = H - M.t - M.b;
  const dec = opts.dec != null ? opts.dec : 2;

  const tMax = t.length ? t[t.length - 1] : 1;
  let lo = 0, hi = 0;
  for (const s of series) for (const v of s.values) {
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  if (hi - lo < 1e-12) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.08;
  hi += pad; lo -= pad;
  const xOf = tv => M.l + (tv / (tMax || 1)) * pw;
  const yOf = v => M.t + (hi - v) / (hi - lo) * ph;

  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });

  // value gridlines + labels
  for (const v of spanTicks(lo, hi)) {
    svg.appendChild(el("line", {
      x1: M.l, x2: M.l + pw, y1: yOf(v), y2: yOf(v),
      stroke: v === 0 ? S.axis : S.grid, "stroke-width": 1,
    }));
    svg.appendChild(txt("text", {
      x: M.l - 6, y: yOf(v) + 3, fill: S.text, "font-size": 9, "text-anchor": "end",
      style: "font-variant-numeric:tabular-nums",
    }, fmt(v, Math.abs(hi) < 10 ? dec : 0)));
  }
  // time gridlines
  for (const tv of spanTicks(0, tMax, 6)) {
    if (tv < 0) continue;
    svg.appendChild(el("line", {
      x1: xOf(tv), x2: xOf(tv), y1: M.t, y2: M.t + ph,
      stroke: S.grid, "stroke-width": 1, "stroke-opacity": 0.6,
    }));
    svg.appendChild(txt("text", {
      x: xOf(tv), y: M.t + ph + 14, fill: S.text, "font-size": 9, "text-anchor": "middle",
      style: "font-variant-numeric:tabular-nums",
    }, fmt(tv, tMax < 10 ? 1 : 0)));
  }
  // axes + units
  svg.appendChild(el("line", { x1: M.l, x2: M.l, y1: M.t, y2: M.t + ph, stroke: S.axis, "stroke-width": 1 }));
  svg.appendChild(txt("text", {
    x: M.l + pw, y: M.t + ph + 26, fill: S.axis, "font-size": 9, "text-anchor": "end",
  }, "t  s"));

  // series lines (thin, 2px)
  series.forEach(ser => {
    let d = "";
    ser.values.forEach((v, i) => {
      d += `${i ? "L" : "M"}${xOf(t[i]).toFixed(1)},${yOf(v).toFixed(1)}`;
    });
    svg.appendChild(el("path", {
      d, fill: "none", stroke: ser.color || S.xRaw, "stroke-width": 1.6,
      "stroke-linejoin": "round", "stroke-linecap": "round",
    }));
  });

  // peak markers with direct labels (selective: one per series)
  series.forEach(ser => {
    let ip = 0;
    ser.values.forEach((v, i) => { if (Math.abs(v) > Math.abs(ser.values[ip])) ip = i; });
    const v = ser.values[ip];
    if (!isFinite(v) || Math.abs(v) < 1e-12) return;
    svg.appendChild(el("circle", {
      cx: xOf(t[ip]), cy: yOf(v), r: 3.2,
      fill: ser.color || S.xRaw, stroke: S.surface, "stroke-width": 1.5,
    }));
    const px = Math.max(M.l + 30, Math.min(xOf(t[ip]), M.l + pw - 46));
    svg.appendChild(txt("text", {
      x: px, y: v >= 0 ? Math.max(yOf(v) - 7, 9) : Math.min(yOf(v) + 13, H - 18),
      fill: S.text, "font-size": 9, "text-anchor": "middle",
      style: "font-variant-numeric:tabular-nums",
    }, `${fmt(v, dec)} @ ${fmt(t[ip], 2)} s`));
  });

  // crosshair + tooltip
  const cross = el("line", {
    x1: 0, x2: 0, y1: M.t, y2: M.t + ph, stroke: S.axis,
    "stroke-width": 1, "stroke-dasharray": "3 3", visibility: "hidden",
  });
  svg.appendChild(cross);
  const hot = el("rect", { x: M.l, y: M.t, width: pw, height: ph, fill: "transparent" });
  hot.addEventListener("mousemove", e => {
    const rect = svg.getBoundingClientRect();
    const sx = (e.clientX - rect.left) * (W / rect.width);
    const tv = (sx - M.l) / pw * tMax;
    let i = 0, bd = Infinity;
    for (let k = 0; k < t.length; k++) {
      const d = Math.abs(t[k] - tv);
      if (d < bd) { bd = d; i = k; }
    }
    cross.setAttribute("x1", xOf(t[i]));
    cross.setAttribute("x2", xOf(t[i]));
    cross.setAttribute("visibility", "visible");
    showTip(
      `<b>t = ${fmt(t[i], 3)} s</b><br>` +
      series.map(ser =>
        `<span style="color:${ser.color || S.xRaw}">●</span> ${ser.label} ` +
        `${fmt(ser.values[i], dec)} ${opts.unit || ""}`).join("<br>"),
      e.clientX, e.clientY);
  });
  hot.addEventListener("mouseleave", () => {
    cross.setAttribute("visibility", "hidden");
    hideTip();
  });
  svg.appendChild(hot);

  const card = document.createElement("div");
  card.className = "chart-card";
  const title = document.createElement("div");
  title.className = "chart-title";
  title.innerHTML = `${opts.title || ""} <span class="unit">${opts.unit || ""}</span>`;
  card.appendChild(title);
  card.appendChild(svg);
  return card;
}

/**
 * Compact acceleration-record sparkline (TH case card preview).
 * Bare SVG: zero line, trace, peak annotation. accel in m/s², dt in s.
 */
export function thSparkline(accel, dt, opts = {}) {
  const W = opts.width || 300, H = opts.height || 64;
  const M = { l: 6, r: 6, t: 8, b: 14 };
  const pw = W - M.l - M.r, ph = H - M.t - M.b;
  const color = opts.color || S.xRaw;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", class: "th-spark" });
  const vals = Array.isArray(accel) ? accel.filter(v => isFinite(v)) : [];
  if (vals.length < 2) {
    svg.appendChild(txt("text", {
      x: W / 2, y: H / 2 + 3, fill: S.axis, "font-size": 10, "text-anchor": "middle",
    }, "No record — paste values or seed the sine demo"));
    return svg;
  }
  const peak = vals.reduce((a, b) => Math.max(a, Math.abs(b)), 0) || 1;
  const dur = (vals.length - 1) * (dt || 0.02);
  const xOf = i => M.l + (i / (vals.length - 1)) * pw;
  const yOf = v => M.t + (1 - v / peak) * ph / 2;
  svg.appendChild(el("line", {
    x1: M.l, x2: M.l + pw, y1: yOf(0), y2: yOf(0), stroke: S.grid, "stroke-width": 1,
  }));
  let d = "";
  vals.forEach((v, i) => { d += `${i ? "L" : "M"}${xOf(i).toFixed(1)},${yOf(v).toFixed(1)}`; });
  svg.appendChild(el("path", {
    d, fill: "none", stroke: color, "stroke-width": 1.2, "stroke-linejoin": "round",
  }));
  svg.appendChild(txt("text", {
    x: M.l, y: H - 3, fill: S.text, "font-size": 9, "text-anchor": "start",
    style: "font-variant-numeric:tabular-nums",
  }, `${vals.length} pts · ${fmt(dur, 1)} s`));
  svg.appendChild(txt("text", {
    x: M.l + pw, y: H - 3, fill: S.text, "font-size": 9, "text-anchor": "end",
    style: "font-variant-numeric:tabular-nums",
  }, `peak ${fmt(peak, 2)} m/s²`));
  return svg;
}
