/* SkyFrame — pushover performance methods: ATC-40 capacity spectrum,
   FEMA 440 equivalent linearization (MADRS), EN 1998-1 Annex B (N2) and
   the ASCE 41 coefficient method, with an ADRS plot (Sa vs Sd).
   CONTRACT "Pushover performance methods (ATC-40, FEMA 440 EL, EN 1998
   N2) and the ADRS plot".

     Define › Load Cases › Pushover Performance Methods…   (def-po-methods)
        → pushover_cases[name].performance  (written only when edited;
          "Clear" removes the key → legacy byte-identical)
     Display › Nonlinear Results › Pushover Performance (ADRS)  (dis-po-adrs)
        → the "Performance methods" card in the pushover tab: ADRS plot
          with toggles + one results table (ASCE 41 / ATC-40 / FEMA 440 /
          N2). Data: results.pushover[case].performance (run hook) or
          POST /api/results/pushover-performance (mock: js/mock_pomethods.js).

   The model stays SI; displacements / forces go through js/units.js,
   accelerations are in g and periods in s. */

import POXU from "./units.js";
import {
  dkForm as poxForm, dkGroup as poxGroup, dkAdvanced as poxAdvanced, dkDialog as poxDialog,
  dkDraft as poxDraft, dkCommit as poxCommit, dkNote as poxNote, el as poxEl, esc as poxEsc,
} from "./dlgkit.js";
import {
  POX_DEFAULTS, POX_METHODS, POX_LABELS, EC8_TABLE as POX_EC8, poxNormalize,
  mockPushoverPerformance as poxMockPerformance,
} from "./mock_pomethods.js";

const POX_SHORT = { asce41: "ASCE 41", atc40: "ATC-40", fema440: "FEMA 440 EL", n2: "EC8 N2", fema440_dm: "FEMA 440 DM" };
const POX_COLORS = { asce41: "#e05d5d", atc40: "#e5a50a", fema440: "#5ccf8a", n2: "#c58af9", fema440_dm: "#4fa3e0" };
const POX_TOGGLES = [
  ["capacity", "Capacity spectrum", true], ["bilinear", "Bilinear", true], ["demand", "Demand 5 %", true],
  ["atc40", "ATC-40 reduced", true], ["fema440", "FEMA 440 MADRS", true], ["n2", "N2 EPP", true],
  ["points", "Performance points", true],
];
const clonePox = o => (o === undefined ? undefined : JSON.parse(JSON.stringify(o)));

function poxCss() {
  if (document.getElementById("poxCss")) return;
  const st = document.createElement("style");
  st.id = "poxCss";
  st.textContent = `
.pox-card .pox-bar { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin:6px 0; }
.pox-card .pox-toggles { display:flex; gap:10px; flex-wrap:wrap; font-size:11px; margin:4px 0 6px; }
.pox-card .pox-toggles label { display:inline-flex; gap:4px; align-items:center; cursor:pointer; }
.pox-card .pox-sw { display:inline-block; width:14px; height:3px; border-radius:2px; }
.pox-card .pox-plot svg { width:100%; max-width:720px; height:auto; display:block; }
.pox-card .pox-opts { font-size:11px; }
.pox-card .pox-warn { font-size:11px; color:#e5a50a; margin-top:4px; }
.pox-card table.data-table td.num, .pox-card table.data-table th.num { text-align:right; }
`;
  document.head.appendChild(st);
}

/** Canonical stored option object (key order = backend PERF_DEFAULTS). */
function poxCanonical(w) {
  const sp = w.spectrum || {};
  let spec;
  if (sp.code === "ec8") {
    spec = { code: "ec8", ag: sp.ag, ground_type: sp.ground_type ?? "C", spectrum_type: sp.spectrum_type ?? 1 };
    for (const k of ["S", "TB", "TC", "TD"]) if (sp[k] != null) spec[k] = sp[k];
  } else {
    spec = { code: "asce7", SDS: sp.SDS, SD1: sp.SD1 };
    if (sp.TL != null) spec.TL = sp.TL;
  }
  const out = { methods: POX_METHODS.filter(m => (w.methods || []).includes(m)), spectrum: spec,
    site_class: w.site_class, behavior: w.behavior, atc40_procedure: w.atc40_procedure };
  for (const k of ["cm_system", "Cm", "near_field", "alpha_pdelta"]) if (w[k] != null && w[k] !== "") out[k] = w[k];   // Wave C optional keys
  return out;
}

export function initPoMethods(sky) {
  poxCss();
  const S = sky.store;
  const U = sky.U || POXU;
  const toast = (t, m, k = "info", ms = 4500) => sky.toast && sky.toast(t, m, k, ms);
  const cache = {};                                     // case -> computed block
  const show = Object.fromEntries(POX_TOGGLES.map(([k, , on]) => [k, on]));
  let busy = false;

  const poCases = () => Object.keys((S.model && S.model.pushover_cases) || {});
  const blockFor = name => {
    const pd = ((S.results && S.results.pushover) || {})[name];
    if (cache[name] && cache[name].resultsRef === S.results) return cache[name].data;
    return pd && pd.performance && !pd.performance.error ? pd.performance : null;
  };

  /* ================================================================
     Define › Load Cases › Pushover Performance Methods…
     ================================================================ */
  function openMethods(caseName) {
    const names = poCases();
    if (!names.length) { toast("No pushover cases", "Define a pushover case first (Define › Load Cases › Pushover…).", "warn"); return; }
    const D = poxDraft(sky);
    const work = {}, touched = {};
    const state = { name: names.includes(caseName) ? caseName : (S.poCase && names.includes(S.poCase) ? S.poCase : names[0]) };
    const workOf = n => {
      if (!(n in work)) {
        const cur = D.pushover_cases[n].performance;
        let w;
        try { w = poxNormalize(cur); } catch (e) { w = clonePox(POX_DEFAULTS); }
        work[n] = clonePox(w);
      }
      return work[n];
    };
    const F = poxForm();
    F.onTouch = () => { touched[state.name] = true; };
    const host = poxEl("div", { class: "pox-dlg-body" });
    const caseRow = F.select(state, "name", () => names.map(n => [n, n]), {
      label: "Pushover case", id: "poxCase",
      onChange: () => { render(); },
    });
    function render() {
      F.forget(host);
      host.textContent = "";
      const w = workOf(state.name);
      const sp = w.spectrum;
      const methodRows = POX_METHODS.map(m => F.check(null, m, {
        label: POX_LABELS[m], id: "poxM_" + m,
        read: () => w.methods.includes(m),
        write: on => { w.methods = on ? POX_METHODS.filter(x => x === m || w.methods.includes(x)) : w.methods.filter(x => x !== m); },
      }));
      const specRows = [
        F.select(sp, "code", [["asce7", "ASCE 7 / ASCE 41 design spectrum (SDS, SD1)"], ["ec8", "EN 1998-1 elastic spectrum (ag, ground type)"]], {
          label: "Spectrum", id: "poxSpecCode",
          write: v => {
            if (v === sp.code) return;
            for (const k of Object.keys(sp)) delete sp[k];
            if (v === "ec8") Object.assign(sp, { code: "ec8", ag: 0.25, ground_type: "C", spectrum_type: 1 });
            else Object.assign(sp, { code: "asce7", SDS: 1.0, SD1: 0.6 });
          },
          onChange: () => { touched[state.name] = true; render(); },
        }),
      ];
      if (sp.code === "ec8") {
        const tab = () => POX_EC8[sp.spectrum_type ?? 1][sp.ground_type ?? "C"];
        specRows.push(
          F.num(sp, "ag", { label: "Design ground acceleration ag", unit: "g", gt: 0, id: "poxAg" }),
          F.select(sp, "ground_type", ["A", "B", "C", "D", "E"].map(g => [g, "Ground type " + g]), { label: "Ground type", id: "poxGround", onChange: () => F.refresh() }),
          F.select(sp, "spectrum_type", [[1, "Type 1 (Ms > 5.5)"], [2, "Type 2 (Ms ≤ 5.5)"]], { label: "Spectrum type", id: "poxSpType", onChange: () => F.refresh() }),
          poxAdvanced([
            F.num(sp, "S", { label: "Soil factor S", allowNull: true, nullText: "table", gt: 0, id: "poxS", hint: "Table 3.2 / 3.3 when empty" }),
            F.num(sp, "TB", { label: "TB", kind: "period", allowNull: true, nullText: "table", gt: 0, id: "poxTB" }),
            F.num(sp, "TC", { label: "TC", kind: "period", allowNull: true, nullText: "table", gt: 0, id: "poxTC" }),
            F.num(sp, "TD", { label: "TD", kind: "period", allowNull: true, nullText: "table", gt: 0, id: "poxTD" }),
          ], { summary: "Advanced — spectrum corner periods", note: `Empty = EN 1998-1 table value (now S ${tab()[0]}, TB ${tab()[1]} s, TC ${tab()[2]} s, TD ${tab()[3]} s).`,
            countFn: () => ["S", "TB", "TC", "TD"].filter(k => sp[k] != null).length }),
        );
      } else {
        specRows.push(
          F.num(sp, "SDS", { label: "SDS (short-period)", unit: "g", gt: 0, id: "poxSds" }),
          F.num(sp, "SD1", { label: "SD1 (1-second)", unit: "g", gt: 0, id: "poxSd1" }),
          F.num(sp, "TL", { label: "Long-period transition TL", kind: "period", allowNull: true, nullText: "none", gt: 0, id: "poxTL", hint: "Empty = no TL branch (as the ASCE 41 coefficient method)" }),
        );
      }
      host.append(
        poxGroup("Methods", methodRows, { note: "Evaluated on every run once saved; the Performance methods card also computes them on demand." }),
        poxGroup("Demand Spectrum (5 % damped)", specRows),
        poxGroup("ATC-40 Capacity Spectrum", [
          F.select(w, "behavior", [["A", "Type A — stable, full loops (κ = 1.0 → 1.13 − 0.51x)"], ["B", "Type B — moderately pinched (κ = 0.67 → 0.845 − 0.446x)"], ["C", "Type C — severely pinched (κ = 0.33)"]], { label: "Structural behaviour", id: "poxBehavior" }),
          F.select(w, "atc40_procedure", [["A", "Procedure A — iterative"], ["B", "Procedure B — fixed bilinear, locus"]], { label: "Procedure", id: "poxProc" }),
        ]),
        poxGroup("ASCE 41 Coefficient Method", [
          F.select(w, "site_class", ["A", "B", "C", "D", "E", "F"].map(c => [c, "Site class " + c]), { label: "Site class (C1)", id: "poxSite" }),
          /* Wave C: ASCE 41-17 Table 7-4 Cm + FEMA 440 DM near field (core/pushover_coeff.py) */
          F.select(w, "cm_system", [["", "Cm = 1.0 (not set)"], ["concrete_moment_frame", "Concrete moment frame (0.9)"], ["concrete_shear_wall", "Concrete shear wall (0.8)"],
            ["concrete_pier_spandrel", "Concrete pier-spandrel (0.8)"], ["steel_moment_frame", "Steel moment frame (0.9)"], ["steel_cbf", "Steel CBF (0.9)"],
            ["steel_ebf", "Steel EBF (0.9)"], ["other", "Other (1.0)"]], { label: "System for Cm (Table 7-4)", id: "poxCmSys",
            read: () => w.cm_system || "", write: v => { if (v) w.cm_system = v; else delete w.cm_system; }, hint: "3+ stories and Te ≤ 1 s; also used by FEMA 440 DM" }),
          F.check(w, "near_field", { label: "Near-field site (FEMA 440 DM λ = 0.2)", id: "poxNear", read: () => !!w.near_field,
            write: on => { if (on) w.near_field = true; else delete w.near_field; } }),
        ]),
      );
    }
    render();
    const clearBtn = poxEl("button", { type: "button", class: "btn btn-small", id: "poxClear", title: "Remove the saved options from this case (defaults on demand, not evaluated on run)" }, ["Clear"]);
    clearBtn.addEventListener("click", () => { work[state.name] = null; touched[state.name] = true; dlg.setNote(`Options of <b>${poxEsc(state.name)}</b> will be removed on OK.`); });
    const dlg = poxDialog({
      id: "poxMethodsDlg", title: "Pushover Performance Methods", iconId: "exleaf-cases", size: "md",
      intro: "Performance point methods of a pushover case: ASCE 41-17 coefficient method, ATC-40 capacity spectrum, FEMA 440 equivalent linearization (MADRS) and EN 1998-1 Annex B (N2). Analysis results only — no acceptance checks.",
      form: F, body: [poxGroup(null, [caseRow]), host], extra: [clearBtn],
      onOk: () => {
        for (const n of Object.keys(touched)) {
          const pc = D.pushover_cases[n];
          if (!pc) continue;
          if (work[n] === null) { delete pc.performance; continue; }
          const canon = poxCanonical(work[n]);
          if (!canon.methods.length) return "Select at least one method.";
          try { poxNormalize(canon); } catch (e) { return e.message; }
          pc.performance = canon;
        }
        const changed = poxCommit(sky, D);
        if (changed.length) for (const n of Object.keys(touched)) delete cache[n];
        renderCard();
        return true;
      },
    });
    return dlg;
  }

  /* ================================================================
     Pushover tab card
     ================================================================ */
  function ensureCard() {
    let card = document.getElementById("poxCard");
    if (card) return card;
    const anchor = document.getElementById("perfCard");
    if (!anchor) return null;
    card = poxEl("div", { class: "perf-card pox-card", id: "poxCard" });
    card.innerHTML = `
      <div class="perf-head"><b>Performance methods</b>
        <span class="muted">ADRS (S<sub>a</sub> vs S<sub>d</sub>) — ATC-40 · FEMA 440 EL · EN 1998 N2 · ASCE 41</span></div>
      <div class="pox-bar">
        <button class="btn btn-small" id="poxOptsBtn" type="button" title="Methods, demand spectrum, ATC-40 behaviour type">Methods…</button>
        <button class="btn btn-primary" id="poxRunBtn" type="button">Compute</button>
        <span class="spinner hidden" id="poxSpinner"></span>
        <span class="muted pox-opts" id="poxOptsNote"></span>
      </div>
      <div class="pox-toggles" id="poxToggles"></div>
      <div class="pox-plot" id="poxPlot"></div>
      <div class="table-scroll"><table class="data-table" id="poxTable"></table></div>
      <div class="pox-warn" id="poxWarn"></div>`;
    anchor.insertAdjacentElement("afterend", card);
    card.querySelector("#poxOptsBtn").addEventListener("click", () => openMethods(S.poCase));
    card.querySelector("#poxRunBtn").addEventListener("click", () => compute());
    const tg = card.querySelector("#poxToggles");
    const swatch = { capacity: "#3fb6e8", bilinear: "#dce3ea", demand: "#9aa7b4", atc40: POX_COLORS.atc40, fema440: POX_COLORS.fema440, n2: POX_COLORS.n2, points: "#e05d5d" };
    for (const [k, lbl] of POX_TOGGLES) {
      const cb = poxEl("input", { type: "checkbox", id: "poxT_" + k });
      cb.checked = show[k];
      cb.addEventListener("change", () => { show[k] = cb.checked; renderCard(); });
      tg.appendChild(poxEl("label", {}, [cb, poxEl("span", { class: "pox-sw", style: `background:${swatch[k]}` }), poxEl("span", { text: lbl })]));
    }
    return card;
  }

  async function compute() {
    const name = S.poCase;
    if (!name || busy) return;
    busy = true;
    const btn = document.getElementById("poxRunBtn"), sp = document.getElementById("poxSpinner");
    if (btn) btn.disabled = true;
    if (sp) sp.classList.remove("hidden");
    const pc = (S.model.pushover_cases || {})[name] || {};
    const body = { case: name, performance: pc.performance ?? null };
    try {
      let out = null;
      if (!S.mock) {
        try {
          const res = await fetch("/api/results/pushover-performance", { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...body, model: S.model }) });
          const js = await res.json();
          if (!res.ok) throw new Error(js.error || res.statusText);
          out = js;
        } catch (e) {
          if (!/fetch|network/i.test(e.message)) throw e;
          console.warn("pushover-performance endpoint unavailable, mock:", e.message);
        }
      }
      if (!out) out = poxMockPerformance(S.model, S.results, body);
      cache[name] = { data: out, resultsRef: S.results };
      renderCard();
    } catch (e) {
      toast("Performance methods failed", e.message, "error", 8000);
    } finally {
      busy = false;
      if (btn) btn.disabled = false;
      if (sp) sp.classList.add("hidden");
    }
  }

  const fmtN = (v, d = 3) => (v == null || !isFinite(v) ? "—" : (+v).toFixed(d));
  function optsNote(o) {
    if (!o) return "defaults";
    const sp = o.spectrum || {};
    const spec = sp.code === "ec8" ? `EC8 ag ${sp.ag} g, ground ${sp.ground_type ?? "C"} (type ${sp.spectrum_type ?? 1})`
      : `SDS ${sp.SDS} g, SD1 ${sp.SD1} g${sp.TL != null ? `, TL ${sp.TL} s` : ""}`;
    return `${spec} · ATC-40 type ${o.behavior}, proc. ${o.atc40_procedure} · site ${o.site_class}`;
  }

  function renderCard() {
    const card = ensureCard();
    if (!card) return;
    const name = S.poCase;
    const pc = ((S.model && S.model.pushover_cases) || {})[name] || {};
    let saved = null;
    try { saved = pc.performance ? poxNormalize(pc.performance) : null; } catch (e) { saved = null; }
    const data = name ? blockFor(name) : null;
    card.querySelector("#poxOptsNote").textContent = (pc.performance ? "Saved: " : "Defaults: ") + optsNote(saved || poxNormalize(null));
    const plot = card.querySelector("#poxPlot"), tbl = card.querySelector("#poxTable"), warn = card.querySelector("#poxWarn");
    plot.textContent = ""; tbl.textContent = ""; warn.textContent = "";
    if (!data) {
      plot.appendChild(poxEl("p", { class: "muted", style: "font-size:11px", text: "Press Compute (or save methods for this case and re-run) to evaluate the performance point methods." }));
      return;
    }
    plot.appendChild(adrsSvg(data));
    tbl.innerHTML = tableHtml(data);
    warn.textContent = (data.warnings || []).join(" · ");
  }

  function tableHtml(data) {
    const dU = U.label("disp"), fU = U.label("force");
    const head = `<thead><tr><th class="txt">Method</th><th class="num">Roof disp ${poxEsc(dU)}</th><th class="num">Base shear ${poxEsc(fU)}</th>
      <th class="num">S<sub>d</sub> ${poxEsc(dU)}</th><th class="num">S<sub>a</sub> g</th><th class="num">T<sub>eff</sub> s</th>
      <th class="num">β<sub>eff</sub> %</th><th class="num">Step</th><th class="txt">Details</th></tr></thead>`;
    const rows = (data.summary || []).map(r => {
      const m = (data.methods || {})[r.method] || {};
      let det = "";
      if (r.method === "asce41") det = `C0 ${fmtN(m.C0, 2)} · C1 ${fmtN(m.C1)} · C2 ${fmtN(m.C2)} · μ ${fmtN(m.mu, 2)}`;
      else if (r.method === "atc40") det = `proc. ${m.procedure} · type ${m.behavior} · κ ${fmtN(m.kappa, 3)} · SRA ${fmtN(m.SRA)} · SRV ${fmtN(m.SRV)}`;
      else if (r.method === "fema440") det = `μ ${fmtN(m.mu, 2)} · α ${fmtN(m.alpha, 3)} · B ${fmtN(m.B)} · M ${fmtN(m.M)}`;
      else if (r.method === "n2") det = `m* ${fmtN(m.m_star, 1)} t · Γ ${fmtN(m.gamma)} · ${(m.rule || "").replace("_", " ")}${m.rule === "short_period" ? ` · qu ${fmtN(m.qu, 2)}` : ""}`;
      if (m.error) det = m.error;
      const status = r.roof_disp == null ? `<span class="hstate hstate-collapse">demand &gt; capacity</span>`
        : r.beyond_curve ? `<span class="hstate hstate-CP">beyond curve</span>` : "";
      return `<tr data-method="${r.method}"><td class="txt"><span class="pox-sw" style="background:${POX_COLORS[r.method]}"></span> ${poxEsc(r.label || POX_LABELS[r.method])}</td>
        <td class="num">${r.roof_disp == null ? "—" : U.fmt("disp", r.roof_disp, 1)}</td>
        <td class="num">${r.base_shear == null ? "—" : U.fmt("force", r.base_shear, 0)}</td>
        <td class="num">${r.Sd == null ? "—" : U.fmt("disp", r.Sd, 1)}</td>
        <td class="num">${fmtN(r.Sa)}</td><td class="num">${fmtN(r.Teff)}</td>
        <td class="num">${r.beta_eff == null ? "—" : fmtN(r.beta_eff, 1)}</td>
        <td class="num">${r.step == null ? "—" : r.step}</td>
        <td class="txt dim">${poxEsc(det)} ${status}</td></tr>`;
    }).join("");
    const md = data.modal || {};
    const foot = `<tfoot><tr><td class="txt dim" colspan="9">Mode ${md.mode ?? "—"} · T ${fmtN(md.T)} s · PF1·φroof ${fmtN(md.pf_phi)} · α1 ${fmtN(md.alpha)} · W ${U.fmt("force", md.W, 0)} ${poxEsc(fU)} — S<sub>a</sub> = V/(α1·W), S<sub>d</sub> = Δroof/(PF1·φroof)</td></tr></tfoot>`;
    return head + `<tbody>${rows}</tbody>` + foot;
  }

  /* ---- ADRS plot ---- */
  function adrsSvg(data) {
    const NS = "http://www.w3.org/2000/svg";
    const mk = (tag, attrs = {}, text) => {
      const n = document.createElementNS(NS, tag);
      for (const [k, v] of Object.entries(attrs)) if (v != null) n.setAttribute(k, v);
      if (text != null) n.textContent = text;
      return n;
    };
    const W = 640, H = 300, M = { l: 52, r: 16, t: 16, b: 36 };
    const pw = W - M.l - M.r, ph = H - M.t - M.b;
    const cap = data.capacity || { Sd: [], Sa: [] };
    const meth = data.methods || {};
    const pts = (data.summary || []).filter(r => r.Sd != null);
    let xMax = Math.max(...cap.Sd, ...pts.map(r => r.Sd), 1e-6) * 1.25;
    const inX = (c) => c.Sd.map((x, i) => [x, c.Sa[i]]).filter(([x]) => x <= xMax);
    const dem = data.demand ? inX(data.demand) : [];
    let yMax = Math.max(...cap.Sa, ...dem.map(p => p[1]), 1e-6) * 1.08;
    const xOf = x => M.l + x / xMax * pw, yOf = y => M.t + ph - Math.min(y, yMax) / yMax * ph;
    const dFac = U.factor("disp");
    const svg = mk("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "ADRS plot: spectral acceleration vs spectral displacement", id: "poxSvg" });
    const ticks = (mx, n = 6) => {
      const raw = mx / n, p = 10 ** Math.floor(Math.log10(raw)), s = raw / p;
      const st = (s < 1.5 ? 1 : s < 3 ? 2 : s < 7 ? 5 : 10) * p;
      const out = []; for (let t = 0; t <= mx + 1e-12; t += st) out.push(t); return out;
    };
    for (const t of ticks(yMax)) {
      svg.appendChild(mk("line", { x1: M.l, x2: M.l + pw, y1: yOf(t), y2: yOf(t), stroke: "#202836" }));
      svg.appendChild(mk("text", { x: M.l - 5, y: yOf(t) + 3, fill: "#9aa7b4", "font-size": 9, "text-anchor": "end" }, (+t).toFixed(yMax < 1 ? 2 : 1)));
    }
    for (const t of ticks(xMax * dFac)) {
      const x = xOf(t / dFac);
      svg.appendChild(mk("line", { x1: x, x2: x, y1: M.t, y2: M.t + ph, stroke: "#202836", "stroke-opacity": 0.6 }));
      svg.appendChild(mk("text", { x, y: M.t + ph + 13, fill: "#9aa7b4", "font-size": 9, "text-anchor": "middle" }, (+t).toPrecision(3).replace(/\.?0+$/, "")));
    }
    svg.appendChild(mk("line", { x1: M.l, x2: M.l, y1: M.t, y2: M.t + ph, stroke: "#66727f" }));
    svg.appendChild(mk("line", { x1: M.l, x2: M.l + pw, y1: M.t + ph, y2: M.t + ph, stroke: "#66727f" }));
    svg.appendChild(mk("text", { x: M.l + pw, y: H - 6, fill: "#66727f", "font-size": 9, "text-anchor": "end" }, `Sd  ${U.label("disp")}`));
    svg.appendChild(mk("text", { x: M.l + 4, y: M.t + 8, fill: "#66727f", "font-size": 9 }, "Sa  g"));
    const path = (arr, attrs) => {
      const p = arr.filter(([x, y]) => x <= xMax * 1.0001 && isFinite(y) && y >= 0);
      if (p.length < 2) return;
      svg.appendChild(mk("path", { d: p.map(([x, y], i) => `${i ? "L" : "M"}${xOf(x).toFixed(1)},${yOf(y).toFixed(1)}`).join(""), fill: "none", ...attrs }));
    };
    const curve = c => (c && c.Sd ? c.Sd.map((x, i) => [x, c.Sa[i]]) : []);
    if (show.demand) path(dem, { stroke: "#9aa7b4", "stroke-width": 1.5, "data-series": "demand" });
    if (show.atc40 && meth.atc40 && meth.atc40.reduced_demand) path(curve(meth.atc40.reduced_demand), { stroke: POX_COLORS.atc40, "stroke-width": 1.3, "stroke-dasharray": "5 3", "data-series": "atc40" });
    if (show.atc40 && meth.atc40 && meth.atc40.locus) path(curve(meth.atc40.locus), { stroke: POX_COLORS.atc40, "stroke-width": 1, "stroke-dasharray": "1 3", "data-series": "atc40-locus" });
    if (show.fema440 && meth.fema440 && meth.fema440.madrs) {
      path(curve(meth.fema440.reduced_demand), { stroke: POX_COLORS.fema440, "stroke-width": 1, "stroke-dasharray": "2 3", "data-series": "fema440-reduced" });
      path(curve(meth.fema440.madrs), { stroke: POX_COLORS.fema440, "stroke-width": 1.5, "data-series": "fema440" });
    }
    if (show.capacity) path([[0, 0], ...curve(cap)], { stroke: "#3fb6e8", "stroke-width": 2.2, "data-series": "capacity" });
    if (show.bilinear) {
      const bl = (meth.fema440 && meth.fema440.bilinear) || (meth.atc40 && (meth.atc40.bilinear_fixed || meth.atc40.bilinear));
      if (bl) path([[0, 0], [bl.dy, bl.ay], [bl.dpi, bl.api]], { stroke: "#dce3ea", "stroke-width": 1.2, "stroke-dasharray": "6 3", "data-series": "bilinear" });
      const a = meth.asce41, md = data.modal || {};
      if (a && a.Vy && md.pf_phi && md.alpha && md.W) {
        const s = 1 / (md.alpha * md.W), k = 1 / md.pf_phi;
        path([[0, 0], [a.dy * k, a.Vy * s], [a.du * k, a.Vu * s]], { stroke: POX_COLORS.asce41, "stroke-width": 1, "stroke-dasharray": "3 2", "data-series": "asce41-bilinear" });
      }
    }
    if (show.n2 && meth.n2 && meth.n2.epp && meth.n2.m_star) {
      const e = meth.n2.epp, m = meth.n2.m_star;
      path(e.d_star.map((d, i) => [d, e.F_star[i] / (m * 9.81)]), { stroke: POX_COLORS.n2, "stroke-width": 1.2, "stroke-dasharray": "4 2", "data-series": "n2" });
    }
    if (show.points) {
      pts.forEach((r, i) => {
        if (r.Sa == null) return;
        const cx = xOf(r.Sd), cy = yOf(r.Sa);
        svg.appendChild(mk("circle", { cx, cy, r: 4.5, fill: POX_COLORS[r.method], stroke: "#0d1117", "stroke-width": 1, "data-point": r.method },));
        svg.lastChild.appendChild(mk("title", {}, `${POX_SHORT[r.method]}: Sd ${U.fmtU("disp", r.Sd, 1)}, Sa ${fmtN(r.Sa)} g, roof ${U.fmtU("disp", r.roof_disp, 1)}`));
        svg.appendChild(mk("text", { x: cx + 6, y: cy + (i % 2 ? 14 : -7) + (i >= 2 ? (i % 2 ? 10 : -10) : 0), fill: POX_COLORS[r.method], "font-size": 9 }, POX_SHORT[r.method]));
      });
    }
    return svg;
  }

  /* ---- menus + __sky ---- */
  sky.poMethods = { openMethods, compute, render: renderCard, data: blockFor, cache };
  sky.openPoMethods = name => openMethods(name);
  sky.poxRender = () => { try { renderCard(); } catch (e) { console.warn("pushover methods card", e); } };
  if (sky.menu && sky.menu.add) {
    sky.menu.add("Define/Load Cases", { label: "Pushover Performance Methods…", act: "def-po-methods", hint: "ATC-40 · FEMA 440 · N2",
      before: "Staged Construction…", fn: () => openMethods(S.poCase) });
    sky.menu.add("Display/Nonlinear Results", { label: "Pushover Performance (ADRS)", act: "dis-po-adrs", hint: "ATC-40 · FEMA 440 · N2", needs: "results",
      before: "Nonlinear Static Results…",
      fn: () => {
        const po = (S.results && S.results.pushover) || {};
        if (!Object.keys(po).length) { toast("No pushover results", "Run a model with a pushover case first (F5).", "warn"); return; }
        sky.setMode && sky.setMode("analyze");
        sky.switchTab && sky.switchTab("pushover");
        requestAnimationFrame(() => { renderCard(); const c = document.getElementById("poxCard"); if (c) c.scrollIntoView({ block: "start", behavior: "smooth" }); });
      } });
  }
  if (POXU.onUnitsChange) POXU.onUnitsChange(() => { if (document.getElementById("poxCard")) renderCard(); });
}
