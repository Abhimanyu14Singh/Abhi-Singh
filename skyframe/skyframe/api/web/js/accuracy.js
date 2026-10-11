/* SkyFrame — model accuracy tools (CONTRACT "Mesh sensitivity study and
   accuracy advisor").

   * Analyze > Mesh Sensitivity Study…  dialog: refinement levels, cases,
     quantities, tolerance → POST /api/analyze/mesh_study with the CURRENT
     working model → results table (values per level, relative change,
     Richardson estimate, verdict) + convergence chart (value / Richardson
     estimate vs refinement level, tolerance band).
   * Model > Accuracy Advisor (Model Explorer · also Analyze menu)  dockable
     panel → POST /api/accuracy_advice → warnings with explanation and
     suggested fix; clicking a warning selects the objects involved.

   Neither tool edits the model (no markDirty, no POST /api/model). Every
   dimensional output goes through js/units.js. ?mock=1 → js/mock_accuracy.js.
   Additive: exposes __sky.openMeshStudy / openAccuracyAdvisor / accuracy. */

import AccU from "./units.js";
import { dialog as accDialog, btn as accBtn, footBar as accFootBar, errorLine as accErrorLine,
  showError as accShowError, closeDialog as accCloseDialog } from "./analysisdlg.js";
import { mockMeshStudy as accMockMeshStudy, mockAccuracyAdvice as accMockAdvice,
  accDefaultCases, ACC_QUANTITIES } from "./mock_accuracy.js";

const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function el(tag, attrs = {}, kids = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
    else if (v != null && v !== false) n.setAttribute(k, v === true ? "" : v);
  }
  (Array.isArray(kids) ? kids : [kids]).forEach(c =>
    c != null && n.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
  return n;
}
const SVGNS = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs = {}) => {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};

/* backend dim → js/units.js kind */
const DIM_KIND = { length: "disp", time: "period", force: "force", moment: "moment", moment_per_length: "line_moment" };
const Q_LABEL = {
  roof_disp: "Roof displacement", periods: "Modal periods", base_reactions: "Base reactions",
  max_shell_moment: "Max shell moment", max_frame_moment: "Max frame moment",
};
const SERIES = 8;     // --fp-c0..7 palette (styles.css)

const fmtSig = (kind, si) => {
  if (si == null || !isFinite(si)) return "—";
  const v = AccU.toDisplay(kind, si);
  if (v === 0) return "0";
  const a = Math.abs(v);
  return (a >= 1e6 || a < 1e-4) ? v.toExponential(4) : (+v.toPrecision(5)).toLocaleString("en-US", { maximumFractionDigits: 8 });
};
const fmtPct = v => v == null || !isFinite(v) ? "—" : (Math.abs(v) < 0.005 ? "0.00" : v.toFixed(2)) + "%";

export function initAccuracy(sky) {
  const S = sky.store;
  const toast = (t, m, k, ms) => sky.toast && sky.toast(t, m, k || "info", ms || 4500);
  if (!document.querySelector("link[data-acc-css]")) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/static/accuracy.css"; l.setAttribute("data-acc-css", "1");
    document.head.appendChild(l);
  }
  const modelBody = () => JSON.parse(JSON.stringify(S.model, (k, v) => k === "_mock_params" ? undefined : v));

  async function post(path, body) {
    const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
    return data;
  }

  const st = { study: null, studyOpts: null, hlRow: null, advice: null, adviceTh: {}, activeCode: null };

  /* =========================================================== mesh study */
  function runStudy(body) {
    if (S.mock) return Promise.resolve().then(() => accMockMeshStudy(modelBody(), body));
    return post("/api/analyze/mesh_study", { ...body, model: modelBody() });
  }

  function openMeshStudy() {
    const m = S.model;
    if (!m) return null;
    const cases = [...Object.keys(m.cases || {}).filter(n => { const c = m.cases[n] || {}; return !(c.pdelta || (c.geometric && c.geometric !== "linear")); })];
    if ((m.num_modes ?? 6) > 0 && !cases.includes("MODAL")) cases.push("MODAL");
    const defaults = new Set(accDefaultCases(m));
    const prev = st.studyOpts || {};
    const body = el("div", { class: "acc-study" });
    body.appendChild(el("p", { class: "muted dlg-intro", html:
      "Re-runs the selected <b>linear static</b> cases and the <b>modal</b> case with the shell mesh and the frame auto-mesh " +
      "refined by each factor (level 1 = the model as defined). The model itself is not changed." }));

    const form = el("div", { class: "acc-form" });
    const lvIn = el("input", { id: "accLevels", type: "text", value: (prev.levels || [1, 2, 4]).join(", "), spellcheck: "false",
      title: "Integer refinement factors, 2–6 distinct values in 1..16" });
    const presets = el("span", { class: "acc-presets" });
    for (const p of [[1, 2], [1, 2, 4], [1, 2, 3], [1, 2, 4, 8]]) {
      presets.appendChild(accBtn(p.join("·"), "btn-small", () => { lvIn.value = p.join(", "); }, `Levels ${p.join(", ")}`));
    }
    const tolIn = el("input", { id: "accTol", type: "number", min: "0.01", max: "100", step: "0.5", value: String(prev.tolerance_pct ?? 5) });
    form.append(
      el("label", { class: "acc-lbl", for: "accLevels", text: "Refinement levels" }), el("div", { class: "acc-ctl" }, [lvIn, presets]),
      el("label", { class: "acc-lbl", for: "accTol", text: "Converged within" }), el("div", { class: "acc-ctl" }, [tolIn, el("span", { class: "muted", text: " %" })]),
    );
    body.appendChild(form);

    const pickers = el("div", { class: "acc-pickers" });
    const caseBox = el("fieldset", { class: "acc-fs", id: "accCases" }, [el("legend", { text: "Cases" })]);
    if (!cases.length) caseBox.appendChild(el("p", { class: "muted", text: "No linear static or modal case." }));
    for (const c of cases) {
      const on = prev.cases ? prev.cases.includes(c) : defaults.has(c);
      caseBox.appendChild(el("label", { class: "acc-chk" }, [el("input", { type: "checkbox", value: c, checked: on }), " " + c]));
    }
    const qBox = el("fieldset", { class: "acc-fs", id: "accQuantities" }, [el("legend", { text: "Quantities" })]);
    for (const q of ACC_QUANTITIES) {
      const on = prev.quantities ? prev.quantities.includes(q) : true;
      qBox.appendChild(el("label", { class: "acc-chk" }, [el("input", { type: "checkbox", value: q, checked: on }), " " + Q_LABEL[q]]));
    }
    pickers.append(caseBox, qBox);
    body.appendChild(pickers);
    const err = accErrorLine();
    body.appendChild(err);
    const out = el("div", { class: "acc-out", id: "accStudyOut" });
    body.appendChild(out);

    const runB = accBtn("Run Study", "btn-primary", () => doRun(), "Run every level (one analysis per level)");
    runB.id = "accRunStudy";
    const closeB = accBtn("Close", "", () => accCloseDialog("accMeshDlg"));
    const foot = accFootBar("Results are not stored in the model.", [runB, closeB]);
    accDialog("accMeshDlg", { title: "Mesh Sensitivity Study", iconId: "menu-analyze", wide: true, body, foot: foot.wrap });
    st.out = out;
    if (st.study) renderStudy(out);

    async function doRun() {
      accShowError(err, "");
      const levels = lvIn.value.split(/[\s,;]+/).filter(Boolean).map(Number);
      if (!levels.length || levels.some(v => !Number.isInteger(v) || v < 1 || v > 16)) {
        accShowError(err, "Levels must be integers between 1 and 16, e.g. 1, 2, 4."); return;
      }
      if (new Set(levels).size < 2 || new Set(levels).size > 6) {
        accShowError(err, "'levels' needs at least 2 (at most 6) distinct refinement factors, e.g. 1, 2, 4."); return;
      }
      const tol = parseFloat(tolIn.value);
      if (!(tol > 0 && tol <= 100)) { accShowError(err, "Tolerance must be in (0, 100] %."); return; }
      const sel = box => [...box.querySelectorAll("input:checked")].map(i => i.value);
      const req = { levels, cases: sel(caseBox), quantities: sel(qBox), tolerance_pct: tol };
      if (!req.cases.length) { accShowError(err, "Pick at least one case."); return; }
      if (!req.quantities.length) { accShowError(err, "Pick at least one quantity."); return; }
      st.studyOpts = req;
      runB.disabled = true;
      foot.note.textContent = "Running " + levels.length + " levels…";
      out.classList.add("is-busy");
      try {
        st.study = await runStudy(req);
        st.hlRow = null;
        renderStudy(out);
        foot.note.textContent = `${st.study.summary.n_converged}/${st.study.summary.n_rows} quantities converged within ${st.study.tolerance_pct}%.`;
      } catch (e) {
        accShowError(err, e.message);
        foot.note.textContent = "Study failed.";
      } finally {
        runB.disabled = false;
        out.classList.remove("is-busy");
      }
    }
    return true;
  }

  function renderStudy(out) {
    const r = st.study;
    out.textContent = "";
    if (!r) return;
    const s = r.summary || {};
    const chips = el("div", { class: "acc-sum" }, [
      el("span", { class: "acc-chip " + (s.all_converged ? "is-ok" : "is-warn"), id: "accStudySummary",
        text: s.all_converged ? `All ${s.n_rows} converged` : `${s.n_converged} / ${s.n_rows} converged` }),
      el("span", { class: "muted", text: `tolerance ${r.tolerance_pct}% · levels ${r.levels.join(", ")}` +
        (s.max_est_error_pct != null ? ` · worst est. error ${fmtPct(s.max_est_error_pct)}` : "") }),
    ]);
    out.appendChild(chips);
    // mesh sizes per level
    const mt = el("table", { class: "data-table acc-mesh" });
    mt.innerHTML = `<thead><tr><th class="txt">Level</th>${r.mesh.map(x => `<th>×${x.level}</th>`).join("")}</tr></thead>
      <tbody><tr><td class="txt">Shell elements</td>${r.mesh.map(x => `<td>${x.n_shell_elements}</td>`).join("")}</tr>
      <tr><td class="txt">Frame segments</td>${r.mesh.map(x => `<td>${x.n_frame_segments}</td>`).join("")}</tr>
      <tr><td class="txt">Joints</td>${r.mesh.map(x => `<td>${x.n_nodes}</td>`).join("")}</tr></tbody>`;
    out.appendChild(el("div", { class: "table-scroll" }, mt));

    const grid = el("div", { class: "acc-res" });
    const tw = el("div", { class: "table-scroll acc-tablewrap" });
    const t = el("table", { class: "data-table acc-table", id: "accStudyTable" });
    const head = `<thead><tr><th class="txt">Case</th><th class="txt">Quantity</th><th class="txt">Unit</th>
      ${r.levels.map(l => `<th>×${l}</th>`).join("")}<th title="relative change of the finest level">Δ last</th>
      <th title="Richardson-extrapolated estimate">Richardson</th><th title="observed (or assumed*) convergence order">p</th>
      <th class="txt">Verdict</th></tr></thead>`;
    t.innerHTML = head;
    const tb = el("tbody");
    r.rows.forEach((row, i) => {
      const kind = DIM_KIND[row.dim] || "none";
      const tr = el("tr", { class: "acc-row" + (row.converged ? "" : " is-bad") + (st.hlRow === i ? " is-hl" : ""), "data-i": String(i),
        title: row.point ? `Tracked point (${row.point.map(v => AccU.fmt("length", v, 2)).join(", ")}) ${AccU.label("length")}` : "" });
      const last = row.rel_change_pct[row.rel_change_pct.length - 1];
      tr.innerHTML = `<td class="txt"><span class="acc-sw" style="background:var(--fp-c${i % SERIES})"></span>${esc(row.case)}</td>
        <td class="txt">${esc(row.label)}</td><td class="txt dim">${esc(AccU.label(kind))}</td>
        ${row.values.map(v => `<td>${fmtSig(kind, v)}</td>`).join("")}
        <td>${fmtPct(last == null ? null : Math.abs(last))}</td>
        <td><b>${fmtSig(kind, row.richardson)}</b></td>
        <td>${row.order == null ? "—" : row.order.toFixed(2) + (row.order_assumed ? "*" : "")}</td>
        <td class="txt"><span class="acc-verdict ${row.converged ? "is-ok" : "is-warn"}">${esc(row.verdict)}</span></td>`;
      tr.addEventListener("click", () => { st.hlRow = st.hlRow === i ? null : i; renderStudy(out); });
      tb.appendChild(tr);
    });
    if (!r.rows.length) tb.innerHTML = `<tr><td class="txt muted" colspan="${r.levels.length + 7}">No quantity applies to the chosen cases.</td></tr>`;
    t.appendChild(tb);
    tw.appendChild(t);
    grid.append(tw, chart(r));
    out.appendChild(grid);
    out.appendChild(el("p", { class: "muted acc-foot", text: "Richardson: h = 1/level; p observed from the last three levels (* = assumed 2). " +
      "Chart: value ÷ Richardson estimate; the band is ± tolerance. Click a row to highlight its curve." }));
    if ((r.warnings || []).length)
      out.appendChild(el("p", { class: "muted acc-warn", text: "Notes: " + r.warnings.slice(0, 5).join(" · ") }));
  }

  function chart(r) {
    const W = 340, H = 230, pl = 46, pr = 10, pt = 14, pb = 30;
    const box = el("div", { class: "acc-chart", id: "accChart" });
    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Convergence chart" });
    const xs = r.levels.map(l => Math.log2(l));
    const x0 = Math.min(...xs), x1 = Math.max(...xs) || 1;
    const X = v => pl + (W - pl - pr) * (x1 === x0 ? 0.5 : (Math.log2(v) - x0) / (x1 - x0));
    const series = r.rows.map((row, i) => ({ i, pts: row.richardson ? row.values.map(v => v / row.richardson) : null }))
      .filter(s => s.pts && s.pts.every(isFinite));
    const tol = r.tolerance_pct / 100;
    let lo = 1 - tol * 1.6, hi = 1 + tol * 1.6;
    for (const s of series) for (const v of s.pts) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    const pad = (hi - lo) * 0.06; lo -= pad; hi += pad;
    const Y = v => pt + (H - pt - pb) * (1 - (v - lo) / (hi - lo));
    svg.appendChild(svgEl("rect", { class: "acc-band", x: pl, y: Y(1 + tol), width: W - pl - pr, height: Math.max(0, Y(1 - tol) - Y(1 + tol)) }));
    // axes + ticks
    svg.appendChild(svgEl("line", { class: "acc-axis", x1: pl, y1: H - pb, x2: W - pr, y2: H - pb }));
    svg.appendChild(svgEl("line", { class: "acc-axis", x1: pl, y1: pt, x2: pl, y2: H - pb }));
    for (const l of r.levels) {
      const tx = svgEl("text", { class: "acc-tick", x: X(l), y: H - pb + 13, "text-anchor": "middle" }); tx.textContent = "×" + l; svg.appendChild(tx);
    }
    const lab = svgEl("text", { class: "acc-tick", x: (pl + W - pr) / 2, y: H - 3, "text-anchor": "middle" }); lab.textContent = "refinement level"; svg.appendChild(lab);
    for (let k = 0; k <= 4; k++) {
      const v = lo + (hi - lo) * k / 4;
      svg.appendChild(svgEl("line", { class: "acc-grid", x1: pl, y1: Y(v), x2: W - pr, y2: Y(v) }));
      const tx = svgEl("text", { class: "acc-tick", x: pl - 4, y: Y(v) + 3, "text-anchor": "end" }); tx.textContent = v.toFixed(3); svg.appendChild(tx);
    }
    svg.appendChild(svgEl("line", { class: "acc-one", x1: pl, y1: Y(1), x2: W - pr, y2: Y(1) }));
    for (const s of series) {
      const hl = st.hlRow === s.i, dim = st.hlRow != null && !hl;
      const d = s.pts.map((v, k) => `${k ? "L" : "M"}${X(r.levels[k]).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
      svg.appendChild(svgEl("path", { d, class: "acc-line" + (hl ? " is-hl" : "") + (dim ? " is-dim" : ""), style: `stroke:var(--fp-c${s.i % SERIES})` }));
      s.pts.forEach((v, k) => svg.appendChild(svgEl("circle", { cx: X(r.levels[k]), cy: Y(v), r: hl ? 3.2 : 2.2,
        class: "acc-dot" + (dim ? " is-dim" : ""), style: `fill:var(--fp-c${s.i % SERIES})` })));
    }
    if (!series.length) {
      const tx = svgEl("text", { class: "acc-tick", x: W / 2, y: H / 2, "text-anchor": "middle" }); tx.textContent = "no curve to plot"; svg.appendChild(tx);
    }
    box.appendChild(svg);
    return box;
  }

  /* ======================================================== advisor panel */
  let panel = null, pbody = null;
  function buildPanel() {
    panel = el("aside", { class: "acc-panel hidden", id: "accPanel", role: "complementary", "aria-label": "Accuracy Advisor" });
    const head = el("div", { class: "acc-head" }, [el("span", { class: "acc-title", text: "Accuracy Advisor" }), el("span", { class: "acc-spacer" })]);
    const re = el("button", { class: "acc-hbtn", id: "accRefresh", title: "Re-check the current model", text: "↻" });
    re.addEventListener("click", () => refreshAdvice());
    const x = el("button", { class: "acc-hbtn", id: "accClose", title: "Close", "aria-label": "Close", text: "×" });
    x.addEventListener("click", () => panel.classList.add("hidden"));
    head.append(re, x);
    pbody = el("div", { class: "acc-body" });
    panel.append(head, pbody);
    (document.getElementById("app") || document.body).appendChild(panel);
  }
  async function refreshAdvice() {
    if (!S.model) return;
    pbody.classList.add("is-busy");
    try {
      const th = st.adviceTh;
      st.advice = S.mock ? accMockAdvice(modelBody(), th) : await post("/api/accuracy_advice", { model: modelBody(), thresholds: th });
      renderAdvice();
    } catch (e) {
      pbody.textContent = "";
      pbody.appendChild(el("p", { class: "field-error", text: "Accuracy Advisor failed: " + e.message }));
    } finally { pbody.classList.remove("is-busy"); }
  }
  function openAccuracyAdvisor() {
    if (!panel) buildPanel();
    panel.classList.remove("hidden");
    const ws = document.querySelector(".workspace");
    if (ws) { const r = ws.getBoundingClientRect(); panel.style.top = Math.round(r.top) + "px"; panel.style.height = Math.round(r.bottom - r.top) + "px"; }
    return refreshAdvice();
  }

  function selectAdvice(a) {
    st.activeCode = a.code;
    const refs = (a.refs || []).filter(r => r.type === "member" || r.type === "shell");
    if (refs.length && sky.handleSelect) {
      sky.handleSelect(refs, false);
      sky.etabs && sky.etabs.refresh && sky.etabs.refresh();
      toast("Accuracy Advisor", `Selected ${refs.length} object${refs.length > 1 ? "s" : ""}: ${a.title}.`, "info", 3000);
    } else if (a.code === "MASS_SOURCE_MISSING" && sky.openMassSource) {
      sky.openMassSource();
    }
    renderAdvice();
  }

  function renderAdvice() {
    const r = st.advice;
    pbody.textContent = "";
    if (!r) return;
    const s = r.summary;
    pbody.appendChild(el("div", { class: "acc-sum" }, [
      el("span", { class: "acc-chip " + (s.ok ? "is-ok" : "is-warn"), id: "accAdviceSummary",
        text: s.ok ? "No accuracy concerns" : `${s.warnings} warning${s.warnings === 1 ? "" : "s"} · ${s.info} info` }),
      el("span", { class: "muted", text: `${s.n_members} frames · ${s.n_shells} shells` }),
    ]));
    // thresholds (unitless ratios)
    const th = r.thresholds || {};
    const thr = el("details", { class: "acc-th" }, [el("summary", { text: "Thresholds" })]);
    const mk = (key, label, scale = 1) => {
      const inp = el("input", { type: "number", step: "any", min: "0", value: String(+(th[key] * scale).toPrecision(6)), "data-th": key });
      inp.addEventListener("change", () => {
        const v = parseFloat(inp.value) / scale;
        if (v > 0 && isFinite(v)) { st.adviceTh = { ...st.adviceTh, [key]: v }; refreshAdvice(); }
      });
      return el("label", { class: "acc-thl" }, [el("span", { text: label }), inp]);
    };
    thr.append(mk("aspect_max", "Max aspect ratio"), mk("min_elements_per_span", "Min elements / span"),
      mk("opening_ratio", "Opening area %", 100), mk("thick_ratio", "Thick plate t/L >"), mk("thin_ratio", "Thin plate t/L <"));
    pbody.appendChild(thr);
    if (s.ok) {
      pbody.appendChild(el("p", { class: "muted acc-okmsg", text: "No modelling choice that commonly degrades accuracy was found. Confirm mesh convergence with Analyze › Mesh Sensitivity Study." }));
      return;
    }
    const list = el("div", { class: "acc-list", id: "accAdviceList" });
    for (const a of r.advice) {
      const item = el("div", { class: `acc-item acc-${a.severity}` + (st.activeCode === a.code ? " is-active" : ""), tabindex: "0",
        "data-code": a.code, title: a.refs && a.refs.length ? "Click to select the objects involved" : "" });
      const objs = a.objects || [];
      item.append(
        el("div", { class: "acc-ihead" }, [el("span", { class: "acc-sev", text: a.severity === "warning" ? "!" : "i" }),
          el("b", { text: a.title }), el("span", { class: "acc-count", text: String(a.count) })]),
        el("div", { class: "acc-msg", text: a.message }),
        el("div", { class: "acc-why" }, [el("span", { class: "acc-k", text: "Why: " }), a.explanation]),
        el("div", { class: "acc-fix" }, [el("span", { class: "acc-k", text: "Fix: " }), a.fix]),
        objs.length ? el("div", { class: "acc-objs muted", text: objs.slice(0, 8).join(", ") + (objs.length > 8 ? ` +${objs.length - 8} more` : "") }) : null,
      );
      item.addEventListener("click", () => selectAdvice(a));
      item.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectAdvice(a); } });
      list.appendChild(item);
    }
    pbody.appendChild(list);
  }

  // live unit switch: re-render the open study table / chart
  AccU.onUnitsChange(() => { if (st.out && st.out.isConnected && st.study) renderStudy(st.out); });

  sky.openMeshStudy = openMeshStudy;
  sky.openAccuracyAdvisor = openAccuracyAdvisor;
  sky.accuracy = { state: st, runStudy, refreshAdvice };
}
