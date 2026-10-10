/* SkyFrame — Display > Hinge Results… (ETABS hinge state display) for
   pushover, nonlinear static and nonlinear time-history results
   (CONTRACT "User-defined hinges and hinge overwrites" → Results).

   * per hinge: deformation vs force history plotted over its backbone
     envelope (user hinges carry `envelope`), with IO / LS / CP markers and
     the current point coloured by its ETABS state;
   * a step slider (+ play) that colours the hinge dots on the deformed
     shape by state at that step — in the dialog's own deformed-shape view
     and, with "Show on model", on the plan / elevation / 3D views;
   * the hinge-state table (and state counts) at the current step.

   A-B grey · B-IO blue · IO-LS green · LS-CP yellow · >CP red ·
   C-D / D-E / >E dark red.  Forces / deformations go through units.js. */

import { dialog as hrDialog, btn as hrBtn, footBar as hrFootBar } from "./analysisdlg.js";
import HRU from "./units.js";
import { b9h as hrH, b9esc as hrEsc } from "./b9common.js";
import { UH_STATES, UH_STATE_COLORS, uhStateAt, uhCaseList, uhSteps, uhDeformer, uhEntryDistance,
  uhPointAt, uhKinds, uhEnvAtPlastic, uhNice, isNum } from "./uhcore.js";

const STATE_TEXT = { "A-B": "A-B (elastic)", "B-IO": "B-IO", "IO-LS": "IO-LS", "LS-CP": "LS-CP", ">CP": ">CP",
  "C-D": "C-D", "D-E": "D-E", ">E": ">E" };

export function initHingeResults(sky, { redraw } = {}) {
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 4500) => sky.toast && sky.toast(t, m, k, ms);
  const st = { active: false, key: null, step: 0, sel: 0, view: "auto", timer: null };
  let cache = null;                                  // {key, c, def, scale, n}

  const cases = () => uhCaseList(S.results);
  const curCase = () => cases().find(c => c.key === st.key) || null;
  function prep() {
    const c = curCase();
    if (!c) { cache = null; return null; }
    if (cache && cache.key === c.key && cache.c.data === c.data && cache.model === S.model) return cache;
    const def = uhDeformer(S.model, S.results, c);
    const { n } = uhSteps(c);
    // auto deformation scale: peak displacement over the members ≈ 8 % of the model size
    const pts = [];
    for (const mm of S.model.members || []) pts.push(mm.pi, mm.pj);
    let lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (const p of pts) for (let i = 0; i < 3; i++) { lo[i] = Math.min(lo[i], p[i]); hi[i] = Math.max(hi[i], p[i]); }
    const size = Math.max(1e-6, ...[0, 1, 2].map(i => isFinite(hi[i] - lo[i]) ? hi[i] - lo[i] : 0));
    let umax = 0;
    const every = Math.max(1, Math.floor(n / 40));
    for (let k = 0; k < n; k += every) for (const p of pts) {
      const u = def.at(p, k);
      if (u) umax = Math.max(umax, Math.abs(u[0]), Math.abs(u[1]), Math.abs(u[2]));
    }
    const u = def.at(pts[0] || [0, 0, 0], n - 1);
    if (u) umax = Math.max(umax, Math.abs(u[0]), Math.abs(u[1]));
    cache = { key: c.key, c, def, n, size, umax, model: S.model, scale: umax > 0 ? 0.08 * size / umax : 1 };
    return cache;
  }

  /** displaced point (exaggerated by `scale`) on member mm at relative d. */
  function dispPoint(def, mm, d, k, scale) {
    const p = uhPointAt(mm, d);
    let u = def.at(p, k);
    if (!u) {
      const a = def.at(mm.pi, k) || [0, 0, 0], b = def.at(mm.pj, k) || [0, 0, 0];
      u = [0, 1, 2].map(i => a[i] + d * (b[i] - a[i]));
    }
    return [p[0] + scale * u[0], p[1] + scale * u[1], p[2] + scale * u[2]];
  }

  /* ---- shared state for the plan / elevation / 3D overlays ---- */
  function stateOf(uid, d) {
    const c = curCase();
    if (!c) return null;
    const h = c.hinges.find(x => x.uid === uid && Math.abs(uhEntryDistance(x) - d) < 1e-6);
    return h ? uhStateAt(h, st.step) : null;
  }
  function draw3d(ctx, P, viewer) {
    const pc = prep();
    if (!pc) return;
    const m = S.model;
    const scale = pc.umax > 0 ? (viewer.radius || pc.size) * 0.08 / pc.umax : 0;
    const proj = p => { const q = P.toCam(p); return q[2] < P.near ? null : P.proj(q); };
    const k = Math.min(st.step, pc.n - 1);
    const byMember = new Map();
    for (const h of pc.c.hinges) {
      const arr = byMember.get(h.uid) || [];
      arr.push(uhEntryDistance(h));
      byMember.set(h.uid, arr);
    }
    ctx.save();
    ctx.lineWidth = 1.8; ctx.strokeStyle = "rgba(53,181,229,0.9)";
    ctx.beginPath();
    for (const mm of m.members || []) {
      const ds = [0, ...(byMember.get(mm.uid) || []).filter(d => d > 0 && d < 1), 1].sort((a, b) => a - b);
      let prev = null;
      for (const d of ds) {
        const q = proj(dispPoint(pc.def, mm, d, k, scale));
        if (q && prev) { ctx.moveTo(prev.x, prev.y); ctx.lineTo(q.x, q.y); }
        prev = q;
      }
    }
    ctx.stroke();
    for (const h of pc.c.hinges) {
      const mm = (m.members || []).find(x => x.uid === h.uid);
      if (!mm) continue;
      const q = proj(dispPoint(pc.def, mm, uhEntryDistance(h), k, scale));
      if (!q) continue;
      ctx.beginPath(); ctx.arc(q.x, q.y, 5, 0, Math.PI * 2);
      ctx.fillStyle = UH_STATE_COLORS[uhStateAt(h, k)]; ctx.fill();
      ctx.lineWidth = 1.2; ctx.strokeStyle = "#0b0f14"; ctx.stroke();
    }
    // legend chip
    ctx.font = "11px system-ui, sans-serif"; ctx.textBaseline = "middle"; ctx.textAlign = "left";
    let x = 12;
    const y = (viewer.canvas ? viewer.canvas.clientHeight : 300) - 18;
    ctx.fillStyle = "rgba(11,15,20,0.72)";
    ctx.fillRect(6, y - 11, 8 + UH_STATES.length * 52, 22);
    for (const s of UH_STATES) {
      ctx.fillStyle = UH_STATE_COLORS[s]; ctx.beginPath(); ctx.arc(x + 5, y, 4.5, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = "#e8edf3"; ctx.fillText(s, x + 12, y);
      x += 52;
    }
    ctx.restore();
  }
  sky.uhResultsState = () => ({ active: st.active && !!curCase(), stateOf, draw3d, step: st.step, key: st.key });
  const refreshViews = () => { if (redraw) redraw(); };

  /* ---- deformed-shape SVG ---- */
  function shapeSvg(pc, k) {
    const m = S.model;
    const c = pc.c;
    let view = st.view;
    if (view === "auto") {
      let dir = "X";
      if (c.kind === "pushover") dir = ((m.pushover_cases || {})[c.name] || {}).direction || "X";
      else if (c.kind === "th") dir = ((m.th_cases || {})[c.name] || {}).direction || "X";
      else dir = (((m.nonlinear_static_cases || {})[c.name] || {}).control_dof || "UX").slice(1);
      const ys = new Set((m.members || []).flatMap(mm => [mm.pi[1], mm.pj[1]]).map(v => v.toFixed(3)));
      const xs = new Set((m.members || []).flatMap(mm => [mm.pi[0], mm.pj[0]]).map(v => v.toFixed(3)));
      view = ys.size === 1 ? "XZ" : xs.size === 1 ? "YZ" : dir === "Y" ? "YZ" : "XZ";
      if (ys.size > 1 && xs.size > 1) view = "ISO";
    }
    const pr = view === "XZ" ? p => [p[0], p[2]] : view === "YZ" ? p => [p[1], p[2]] : view === "XY" ? p => [p[0], p[1]]
      : p => [(p[0] - p[1]) * 0.866, p[2] + (p[0] + p[1]) * 0.25];
    const byMember = new Map();
    for (const h of c.hinges) { const a = byMember.get(h.uid) || []; a.push(uhEntryDistance(h)); byMember.set(h.uid, a); }
    const segs = [], ghost = [];
    for (const mm of m.members || []) {
      ghost.push([pr(mm.pi), pr(mm.pj)]);
      const ds = [0, ...(byMember.get(mm.uid) || []).filter(d => d > 0 && d < 1), 1].sort((a, b) => a - b);
      segs.push(ds.map(d => pr(dispPoint(pc.def, mm, d, k, pc.scale))));
    }
    const dots = c.hinges.map((h, i) => {
      const mm = (m.members || []).find(x => x.uid === h.uid);
      return mm ? { i, h, q: pr(dispPoint(pc.def, mm, uhEntryDistance(h), k, pc.scale)) } : null;
    }).filter(Boolean);
    const all = [...ghost.flat(), ...segs.flat(), ...dots.map(d => d.q)];
    let x0 = Math.min(...all.map(q => q[0])), x1 = Math.max(...all.map(q => q[0]));
    let y0 = Math.min(...all.map(q => q[1])), y1 = Math.max(...all.map(q => q[1]));
    if (!isFinite(x0)) { x0 = 0; x1 = 1; y0 = 0; y1 = 1; }
    const W = 420, H = 300, pad = 22;
    const sc = Math.min((W - 2 * pad) / Math.max(x1 - x0, 1e-6), (H - 2 * pad) / Math.max(y1 - y0, 1e-6));
    const ox = (W - sc * (x1 - x0)) / 2, oy = (H - sc * (y1 - y0)) / 2;
    const X = q => (ox + (q[0] - x0) * sc).toFixed(1), Y = q => (H - oy - (q[1] - y0) * sc).toFixed(1);
    let s = `<svg viewBox="0 0 ${W} ${H}" class="uh-shape" id="uhResShape" role="img" aria-label="Deformed shape with hinge states">`;
    for (const [a, b] of ghost) s += `<line x1="${X(a)}" y1="${Y(a)}" x2="${X(b)}" y2="${Y(b)}" class="uh-ghost"/>`;
    for (const sg of segs) s += `<path d="${sg.map((q, i) => `${i ? "L" : "M"}${X(q)},${Y(q)}`).join(" ")}" class="uh-defl"/>`;
    for (const d of dots) {
      const state = uhStateAt(d.h, k);
      s += `<circle cx="${X(d.q)}" cy="${Y(d.q)}" r="${d.i === st.sel ? 7 : 5.2}" fill="${UH_STATE_COLORS[state]}" class="uh-sdot${d.i === st.sel ? " is-sel" : ""}" data-i="${d.i}" data-state="${hrEsc(state)}"><title>${hrEsc(d.h.uid)} @ ${hrEsc(String(d.h.end))} — ${hrEsc(state)}</title></circle>`;
    }
    s += `<text x="8" y="${H - 6}" class="uh-tick">${hrEsc(view === "ISO" ? "isometric" : view)} · deformation ×${(+pc.scale.toPrecision(3)).toLocaleString("en-US")}</text>`;
    return s + "</svg>";
  }

  /* ---- hinge F–d plot over the backbone ---- */
  function hingeSvg(h, k) {
    const kd = uhKinds(h.type || "M3");
    const rot = h.rot || [], mom = h.moment || [];
    const kk = Math.min(k, rot.length - 1);
    const env = h.envelope;
    let xm = Math.max(1e-12, ...rot.map(Math.abs)), ym = Math.max(1e-12, ...mom.map(Math.abs), Math.abs(h.My || 0));
    // envelope markers (B..last user point) and acceptance points
    const marks = [];
    if (env) {
      for (const [side, sg] of [["positive", 1], ["negative", -1]]) {
        const pts = env[side] || [];
        const ke = h.k_elastic || (pts[0] ? pts[0][1] / pts[0][0] : 1);
        pts.slice(0, Math.max(1, pts.length - (h.drop_strength === "holds" ? 1 : 2))).forEach((q, i) => marks.push({ x: sg * q[0], y: sg * q[1], t: "BCDEF"[i], cls: "uh-mk-pt" }));
        for (const key of ["IO", "LS", "CP"]) {
          if (!isNum(h[key])) continue;
          const q = uhEnvAtPlastic({ points: pts }, ke, h[key]);
          if (q) marks.push({ x: sg * q[0], y: sg * q[1], t: key, cls: "uh-mk-acc uh-acc-" + key });
        }
      }
      xm = Math.max(xm, ...marks.map(q => Math.abs(q.x)));
      ym = Math.max(ym, ...marks.map(q => Math.abs(q.y)));
    }
    xm = uhNice(xm * 1.1); ym = uhNice(ym * 1.1);
    const W = 400, Hh = 280, M = { l: 56, r: 12, t: 14, b: 30 };
    const pw = W - M.l - M.r, ph = Hh - M.t - M.b;
    const X = d => (M.l + (d + xm) / (2 * xm) * pw).toFixed(1), Y = f => (M.t + (ym - f) / (2 * ym) * ph).toFixed(1);
    let s = `<svg viewBox="0 0 ${W} ${Hh}" class="uh-plot" id="uhResPlot" role="img" aria-label="Hinge force-deformation history">`;
    s += `<defs><clipPath id="uhResClip"><rect x="${M.l}" y="${M.t}" width="${pw}" height="${ph}"/></clipPath></defs>`;
    s += `<rect x="${M.l}" y="${M.t}" width="${pw}" height="${ph}" class="uh-plot-bg"/>`;
    for (const t of [-1, -0.5, 0.5, 1]) {
      s += `<line x1="${X(t * xm)}" y1="${M.t}" x2="${X(t * xm)}" y2="${M.t + ph}" class="uh-grid"/>`;
      s += `<line x1="${M.l}" y1="${Y(t * ym)}" x2="${M.l + pw}" y2="${Y(t * ym)}" class="uh-grid"/>`;
    }
    s += `<line x1="${M.l}" y1="${Y(0)}" x2="${M.l + pw}" y2="${Y(0)}" class="uh-axis"/><line x1="${X(0)}" y1="${M.t}" x2="${X(0)}" y2="${M.t + ph}" class="uh-axis"/>`;
    s += `<g clip-path="url(#uhResClip)">`;
    if (env) {
      const neg = (env.negative || []).map(q => [-q[0], -q[1]]).reverse();
      const pts = [...neg, [0, 0], ...(env.positive || [])];
      s += `<path d="${pts.map((q, i) => `${i ? "L" : "M"}${X(q[0])},${Y(q[1])}`).join(" ")}" class="uh-bb"/>`;
    } else if (isNum(h.My)) {
      s += `<line x1="${M.l}" y1="${Y(h.My)}" x2="${M.l + pw}" y2="${Y(h.My)}" class="uh-bb-tail"/><line x1="${M.l}" y1="${Y(-h.My)}" x2="${M.l + pw}" y2="${Y(-h.My)}" class="uh-bb-tail"/>`;
    }
    if (rot.length) s += `<path d="${rot.slice(0, kk + 1).map((d, i) => `${i ? "L" : "M"}${X(d)},${Y(mom[i] || 0)}`).join(" ")}" class="uh-hist"/>`;
    for (const q of marks) s += `<g class="${q.cls}"><circle cx="${X(q.x)}" cy="${Y(q.y)}" r="3"/><text x="${X(q.x)}" y="${(+Y(q.y) - 6).toFixed(1)}" text-anchor="middle" class="uh-tick">${q.t}</text></g>`;
    if (rot.length) {
      const state = uhStateAt(h, kk);
      s += `<circle cx="${X(rot[kk])}" cy="${Y(mom[kk] || 0)}" r="6" fill="${UH_STATE_COLORS[state]}" class="uh-cur" id="uhResCur" data-state="${hrEsc(state)}"/>`;
    }
    s += `</g>`;
    const fx = v => HRU.fmt(kd.d, v, kd.d === "rotation" ? 4 : 2), fy = v => HRU.fmt(kd.f, v, 1);
    s += `<text x="${M.l + pw}" y="${Hh - 8}" class="uh-tick" text-anchor="end">${hrEsc(fx(xm))} ${hrEsc(HRU.label(kd.d))}</text>`;
    s += `<text x="${M.l}" y="${Hh - 8}" class="uh-tick">${hrEsc(fx(-xm))}</text>`;
    s += `<text x="${(M.l + pw / 2 + 28).toFixed(0)}" y="${Hh - 8}" class="uh-tick" text-anchor="middle">hinge deformation</text>`;
    s += `<text x="${M.l - 4}" y="${M.t + 8}" class="uh-tick" text-anchor="end">${hrEsc(fy(ym))}</text>`;
    s += `<text x="${M.l - 4}" y="${M.t + ph}" class="uh-tick" text-anchor="end">${hrEsc(fy(-ym))}</text>`;
    s += `<text x="${M.l - 4}" y="${(+Y(0) + 3).toFixed(1)}" class="uh-tick" text-anchor="end">${hrEsc(HRU.label(kd.f))}</text>`;
    return s + "</svg>";
  }

  /* ================= the dialog ================= */
  function openHingeResults(key) {
    const list = cases();
    if (!S.results) { toast("Hinge Results", "Run the analysis first (Analyze > Run Analysis).", "error"); return null; }
    if (!list.length) { toast("Hinge Results", "No hinge results — run a pushover, nonlinear static or nonlinear time-history case with hinges.", "error"); return null; }
    if (key && list.some(c => c.key === key)) st.key = key;
    if (!list.some(c => c.key === st.key)) { st.key = list[0].key; st.step = 0; st.sel = 0; }
    const body = hrH("div", { class: "uh-res" });
    const caseSel = hrH("select", { id: "uhResCase" });
    caseSel.innerHTML = list.map(c => `<option value="${hrEsc(c.key)}"${c.key === st.key ? " selected" : ""}>${hrEsc(c.label)}</option>`).join("");
    const slider = hrH("input", { type: "range", id: "uhResStep", min: "0", step: "1" });
    const stepNum = hrH("span", { class: "uh-stepnum", id: "uhResStepTxt" });
    const playBtn = hrBtn("▶ Play", "btn-small", () => togglePlay(), "Animate the steps");
    playBtn.id = "uhResPlay";
    const viewSel = hrH("select", { id: "uhResView", title: "Deformed-shape view" });
    viewSel.innerHTML = [["auto", "Auto view"], ["XZ", "Elevation X-Z"], ["YZ", "Elevation Y-Z"], ["XY", "Plan X-Y"], ["ISO", "Isometric"]]
      .map(([v, l]) => `<option value="${v}"${v === st.view ? " selected" : ""}>${l}</option>`).join("");
    const showChk = hrH("input", { type: "checkbox", id: "uhResShow" });
    showChk.checked = st.active;
    body.appendChild(hrH("div", { class: "uh-res-top" }, [
      hrH("label", { class: "uh-fld" }, [hrH("span", { text: "Case" }), caseSel]),
      hrH("label", { class: "uh-fld uh-res-slider" }, [hrH("span", { text: "Step" }), slider]),
      stepNum, playBtn, viewSel,
      hrH("label", { class: "dlg-chk" }, [showChk, hrH("span", { text: "Show on model (plan · elevation · 3D)" })]),
    ]));
    const legend = hrH("div", { class: "uh-legend uh-res-legend", id: "uhResLegend" });
    body.appendChild(legend);
    const grid = hrH("div", { class: "uh-res-grid" });
    const shapeBox = hrH("div", { class: "uh-res-shape" });
    const plotWrap = hrH("div", { class: "uh-res-plot" });
    const info = hrH("p", { class: "muted b9-small", id: "uhResInfo" });
    const hingeSel = hrH("select", { id: "uhResHinge" });
    plotWrap.append(hrH("label", { class: "uh-fld" }, [hrH("span", { text: "Hinge" }), hingeSel]), hrH("div", { id: "uhResPlotBox" }), info);
    grid.append(shapeBox, plotWrap);
    body.appendChild(grid);
    const note = hrH("p", { class: "muted b9-small", id: "uhResNote" });
    body.appendChild(note);
    const tblBox = hrH("div", { class: "table-scroll uh-res-tbl" });
    body.appendChild(tblBox);

    function rebuildHingeSel() {
      const c = curCase();
      hingeSel.innerHTML = c ? c.hinges.map((h, i) => `<option value="${i}"${i === st.sel ? " selected" : ""}>${hrEsc(h.uid)} @ ${hrEsc(String(h.end))}${h.property ? " · " + hrEsc(h.property) : h.kind ? " · " + hrEsc(h.kind) : ""}</option>`).join("") : "";
    }
    function render() {
      const pc = prep();
      if (!pc) return;
      const c = pc.c;
      const { n, axis, label, kind } = uhSteps(c);
      slider.max = String(Math.max(0, n - 1));
      st.step = Math.min(st.step, Math.max(0, n - 1));
      slider.value = String(st.step);
      const k = st.step;
      stepNum.textContent = `${k} / ${Math.max(0, n - 1)}` + (axis && axis[k] != null ? ` · ${label} ${kind === "none" ? axis[k] : HRU.fmtU(kind, axis[k], kind === "period" ? 2 : 1)}` : "");
      // counts
      const counts = Object.fromEntries(UH_STATES.map(s => [s, 0]));
      for (const h of c.hinges) counts[uhStateAt(h, k)]++;
      legend.innerHTML = UH_STATES.map(s => `<span class="uh-chip" data-state="${hrEsc(s)}"><i class="uh-sw" style="background:${UH_STATE_COLORS[s]}"></i>${hrEsc(s)} <b id="uhResCnt_${UH_STATES.indexOf(s)}">${counts[s]}</b></span>`).join("");
      shapeBox.innerHTML = shapeSvg(pc, k);
      for (const dot of shapeBox.querySelectorAll(".uh-sdot")) dot.addEventListener("click", () => { st.sel = +dot.getAttribute("data-i"); rebuildHingeSel(); render(); });
      st.sel = Math.min(st.sel, c.hinges.length - 1);
      const h = c.hinges[st.sel];
      document.getElementById("uhResPlotBox").innerHTML = h ? hingeSvg(h, k) : "";
      if (h) {
        const kd = uhKinds(h.type || "M3");
        const kk = Math.min(k, (h.rot || []).length - 1);
        info.innerHTML = `<b>${hrEsc(h.uid)}</b> @ ${hrEsc(String(h.end))}` + (h.property ? ` · ${hrEsc(h.property)} (${hrEsc(h.type)}, ${hrEsc(h.hysteresis || "")}${h.material ? " → " + hrEsc(h.material) : ""})` : h.kind ? ` · ${hrEsc(h.kind)}` : "") +
          ` · F<sub>B</sub> = ${hrEsc(HRU.fmtU(kd.f, h.My, 1))}` + (isNum(h.k_elastic) ? ` · k<sub>e</sub> = ${hrEsc(HRU.fmtU(kd.k, h.k_elastic, 0))}` : "") +
          ` · state <b style="color:${UH_STATE_COLORS[uhStateAt(h, kk)]}">${hrEsc(uhStateAt(h, kk))}</b>`;
      }
      note.textContent = `Deformed shape: ${pc.def.note}. ${c.hinges.length} hinge${c.hinges.length === 1 ? "" : "s"}; click a dot or a table row to plot that hinge.`;
      // table
      const rows = c.hinges.map((x, i) => {
        const kd = uhKinds(x.type || "M3");
        const kk = Math.min(k, (x.rot || []).length - 1);
        const s = uhStateAt(x, kk);
        const pl = x.rot_plastic ? x.rot_plastic[kk] : null;
        return `<tr data-i="${i}" class="${i === st.sel ? "is-sel" : ""}"><td class="txt">${hrEsc(x.uid)}</td><td class="txt">${hrEsc(String(x.end))}</td>` +
          `<td class="txt">${hrEsc(x.property || x.kind || "")}</td><td class="txt">${hrEsc(x.type || "M3")}</td>` +
          `<td>${hrEsc(HRU.fmt(kd.d, (x.rot || [])[kk], kd.d === "rotation" ? 5 : 2))}</td><td>${hrEsc(HRU.fmt(kd.f, (x.moment || [])[kk], 1))}</td>` +
          `<td>${pl == null ? "—" : hrEsc(HRU.fmt(kd.d, pl, kd.d === "rotation" ? 5 : 2))}</td>` +
          `<td class="txt"><span class="uh-state" style="background:${UH_STATE_COLORS[s]}" data-state="${hrEsc(s)}">${hrEsc(STATE_TEXT[s])}</span></td></tr>`;
      }).join("");
      tblBox.innerHTML = `<table class="data-table uh-state-tbl" id="uhResTable"><thead><tr><th class="txt">Member</th><th class="txt">Location</th><th class="txt">Property</th><th class="txt">DOF</th>` +
        `<th>Deformation</th><th>Force</th><th>Plastic def.</th><th class="txt">State @ step ${k}</th></tr></thead><tbody>${rows}</tbody></table>`;
      for (const tr of tblBox.querySelectorAll("tr[data-i]")) tr.addEventListener("click", () => { st.sel = +tr.getAttribute("data-i"); rebuildHingeSel(); render(); });
      if (st.active) refreshViews();
    }
    function togglePlay() {
      if (st.timer) { clearInterval(st.timer); st.timer = null; playBtn.textContent = "▶ Play"; return; }
      const pc = prep();
      if (!pc) return;
      if (st.step >= pc.n - 1) st.step = 0;
      playBtn.textContent = "■ Stop";
      const every = Math.max(1, Math.round(pc.n / 60));
      st.timer = setInterval(() => {
        const p = prep();
        if (!p || st.step >= p.n - 1) { clearInterval(st.timer); st.timer = null; playBtn.textContent = "▶ Play"; return; }
        st.step = Math.min(p.n - 1, st.step + every);
        render();
      }, 90);
    }
    caseSel.addEventListener("change", () => { st.key = caseSel.value; st.step = 0; st.sel = 0; rebuildHingeSel(); render(); });
    slider.addEventListener("input", () => { st.step = +slider.value; render(); });
    hingeSel.addEventListener("change", () => { st.sel = +hingeSel.value; render(); });
    viewSel.addEventListener("change", () => { st.view = viewSel.value; render(); });
    showChk.addEventListener("change", () => { st.active = showChk.checked; refreshViews(); });

    const fb = hrFootBar("ETABS hinge states: A-B grey · B-IO blue · IO-LS green · LS-CP yellow · >CP red · beyond C dark red.", [
      hrBtn("Close", "btn-primary", () => dlg.close()),
    ]);
    fb.wrap.querySelector(".btn-primary").id = "uhResClose";
    const dlg = hrDialog("uhResultsDlg", { title: "Hinge Results", iconId: "tool-beam", wide: true, body, foot: fb.wrap,
      onClose: () => { if (st.timer) { clearInterval(st.timer); st.timer = null; } refreshViews(); } });
    rebuildHingeSel(); render();
    return dlg;
  }

  document.addEventListener("sky:results-changed", () => {
    cache = null;
    if (st.active && !curCase()) st.active = false;
    refreshViews();
  });
  sky.openHingeResults = openHingeResults;
  sky.hingeResults = { open: openHingeResults, state: st, setStep: k => { st.step = k; refreshViews(); }, setActive: on => { st.active = !!on; refreshViews(); } };
}
