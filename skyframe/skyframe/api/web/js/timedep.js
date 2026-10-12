/* SkyFrame — time-dependent materials, step-by-step staged creep and bonded
   tendon elements (UI).

   CONTRACT "Time-dependent materials, incremental creep and tendon
   elements":
     model.material_time_dependent = {material: {law, rh, h0, cure_days,
       cement, curing, fcm, creep, shrinkage, aging, phi_u, eps_shu,
       creep_curve, shrinkage_curve, modulus_curve, strength_curve,
       relaxation}}                                   (only when defined)
     staged_cases[n].time_dependent.method = "aaem" | "step",
       steps_per_stage, output_times, t_eval
     tendons[k].model_as = "elements", tendons[k].losses.es_factor
     results.staged[n].time_history = {times, stage, story_uz,
       tendon_force, outputs, tendons}

   * Define › Section Properties › Material Time-Dependent Properties…
     (def-td-matprops): materials list → "Time-Dependent Properties" dialog
     (tabs Creep & Shrinkage / User Curves / Relaxation / Preview).  Also
     opened from Material Property Data › "Time Dependent…".
   * Staged case card (loads.js hook sky.tdStagedCard): creep integration
     method (AAEM closed form / step-by-step) + Time Options… (steps per
     stage, output times, end time).  Stage durations stay in Stage Data.
   * Tendon Properties (tendons.js hook sky.tdTendonRows): Model as Loads /
     Elements and the hand ES factor; Tendon Forces gains the element card.
   * Display › Nonlinear Results › Staged Time History (Creep)…
     (dis-td-history): column shortening vs time per story + tendon force
     vs time + table.
   Every number goes through js/units.js (model stays SI); dialogs follow
   docs/DIALOG_STANDARD.md (dlgkit). */

import { el as tdEl, esc as tdEsc, clone as tdClone, isNum as tdIsNum, dkForm as tdForm, dkGroup as tdGroup,
  dkAdvanced as tdAdvanced, dkTabs as tdTabs, dkList as tdList, dkNote as tdNote, dkDialog as tdDialog,
  dkDraft as tdDraft, dkCommit as tdCommit } from "./dlgkit.js";
import tdU from "./units.js";
import { TD_LAWS as TDM_LAWS, TD_CEMENTS as TDM_CEMENTS, tdNormalize as tdmNormalize,
  tdMockCurves as tdmCurves, tdRelaxation as tdmRelax } from "./mock_timedep.js";

const DAYS = "d";
const LAW_LABEL = Object.fromEntries(TDM_LAWS);
const CEMENT_LABEL = {
  cebfip2010: { SL: "SL — slowly hardening (32.5 N)", N: "N — normal (32.5 R, 42.5 N)", R: "R — rapid (42.5 R, 52.5 N/R)" },
  aci209: { I: "Type I", III: "Type III" },
  gl2000: { I: "Type I", II: "Type II", III: "Type III" },
  user: { N: "—" },
};
const DEF = { rh: 70, h0: 0.15, cure_days: 7 };

let cssDone = false;
function tdCss() {
  if (cssDone || document.querySelector("link[data-td-css]")) { cssDone = true; return; }
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = "/static/timedep.css"; l.setAttribute("data-td-css", "1");
  document.head.appendChild(l);
  cssDone = true;
}

/* ------------------------------------------------ small SVG line chart */
function lineChart({ series, xLabel, yLabel, logX = false, w = 520, h = 210, id }) {
  const pts = series.flatMap(s => s.x.map((x, i) => [x, s.y[i]])).filter(([x, y]) => tdIsNum(x) && tdIsNum(y));
  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("class", "td-chart");
  if (id) svg.setAttribute("id", id);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `${yLabel} vs ${xLabel}`);
  if (!pts.length) {
    svg.innerHTML = `<text x="${w / 2}" y="${h / 2}" text-anchor="middle" class="td-ax">No data</text>`;
    return svg;
  }
  const fx = x => logX ? Math.log10(1 + Math.max(x, 0)) : x;
  let x0 = Math.min(...pts.map(p => fx(p[0]))), x1 = Math.max(...pts.map(p => fx(p[0])));
  let y0 = Math.min(0, ...pts.map(p => p[1])), y1 = Math.max(0, ...pts.map(p => p[1]));
  if (x1 - x0 < 1e-12) x1 = x0 + 1;
  if (y1 - y0 < 1e-15) y1 = y0 + 1;
  const L = 56, R = 26, T = 22, B = 34;
  const X = x => L + (fx(x) - x0) / (x1 - x0) * (w - L - R);
  const Y = y => T + (y1 - y) / (y1 - y0) * (h - T - B);
  let s = `<line x1="${L}" y1="${Y(0)}" x2="${w - R}" y2="${Y(0)}" class="td-zero"/>`;
  s += `<line x1="${L}" y1="${T}" x2="${L}" y2="${h - B}" class="td-axis"/><line x1="${L}" y1="${h - B}" x2="${w - R}" y2="${h - B}" class="td-axis"/>`;
  const fmtY = v => Math.abs(v) >= 1000 || (Math.abs(v) < 0.01 && v !== 0) ? v.toExponential(2) : (+v.toPrecision(4)).toString();
  for (const v of [y0, (y0 + y1) / 2, y1]) s += `<text x="${L - 4}" y="${Y(v) + 3}" text-anchor="end" class="td-ax">${tdEsc(fmtY(v))}</text>`;
  const xt = logX ? [0, 1, 10, 100, 1000, 10000, 100000].filter(v => fx(v) >= x0 - 1e-9 && fx(v) <= x1 + 1e-9)
    : [x0, (x0 + x1) / 2, x1];
  for (const v of xt) s += `<text x="${X(v)}" y="${h - B + 14}" text-anchor="middle" class="td-ax">${tdEsc((+(+v).toPrecision(4)).toString())}</text>`;
  s += `<text x="${(L + w - R) / 2}" y="${h - 4}" text-anchor="middle" class="td-ax td-ax-title">${tdEsc(xLabel)}</text>`;
  s += `<text x="${L}" y="12" class="td-ax td-ax-title">${tdEsc(yLabel)}</text>`;
  series.forEach((ser, k) => {
    const d = ser.x.map((x, i) => tdIsNum(ser.y[i]) ? `${X(x).toFixed(1)},${Y(ser.y[i]).toFixed(1)}` : null).filter(Boolean);
    if (d.length) s += `<polyline points="${d.join(" ")}" class="td-line td-c${k % 8}" fill="none"><title>${tdEsc(ser.name)}</title></polyline>`;
  });
  svg.innerHTML = s;
  return svg;
}
function legend(series) {
  return tdEl("div", { class: "td-legend" }, series.map((s, k) => tdEl("span", { class: "td-key" }, [tdEl("i", { class: `td-sw td-c${k % 8}` }), s.name])));
}

export function initTimeDep(sky) {
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 4500) => sky.toast && sky.toast(t, m, k, ms);
  const isMock = () => !!(S && S.mock);

  async function apiCurves(body) {
    if (!isMock()) {
      try {
        const res = await fetch("/api/timedep/curves", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        const data = await res.json();
        if (res.ok) return data;
        throw new Error(data && data.error || res.statusText);
      } catch (e) { console.warn("timedep curves endpoint:", e.message); }
    }
    return tdmCurves(body.props, body.E, body.fc, body.t0 ?? 28);
  }

  /* ================================================================
     Time-Dependent Properties (one material)
     ================================================================ */
  function defaultsFor(mat) {
    const t = (mat && mat.material_type) || "concrete";
    if (t === "tendon") return { law: "user", creep: false, shrinkage: false, aging: false, relaxation: { law: "ec2", class: 2, rho1000: 2.5 } };
    return { law: "cebfip2010" };
  }

  /** Open the dialog for material `name`. Dext = a parent draft (Material
      Property Data / the list): edits go into it and the parent commits. */
  function openMaterialTD(name, Dext = null, done = null) {
    tdCss();
    const D = Dext || tdDraft(sky);
    const mat = (D.materials || {})[name];
    if (!mat) { toast("Time-Dependent Properties", `Unknown material ${name}.`, "error"); return null; }
    const had = !!(D.material_time_dependent && D.material_time_dependent[name]);
    const pr = had ? tdClone(D.material_time_dependent[name]) : defaultsFor(mat);
    const st = { on: had };
    const F = tdForm();
    const law = () => pr.law || "cebfip2010";
    const rd = (k, d) => () => pr[k] ?? d;
    const panels = { gen: tdEl("div"), curves: tdEl("div"), relax: tdEl("div"), prev: tdEl("div", { class: "td-prev" }) };
    let tabs = null;

    const buildGen = () => {
      F.forget(panels.gen);
      panels.gen.textContent = "";
      const L = law();
      const cem = TDM_CEMENTS[L];
      panels.gen.append(...[
        tdGroup("Time-Dependent Behaviour", [
          F.check(st, "on", { label: "Use time-dependent properties for this material", id: "tdOn", onChange: () => { buildAll(); } }),
          F.select(pr, "law", TDM_LAWS, { label: "Code / Law", id: "tdLaw", onChange: v => { if (!(TDM_CEMENTS[v] || []).includes(pr.cement)) delete pr.cement; buildAll(); } }),
          F.check(pr, "creep", { label: "Creep", id: "tdCreep", read: rd("creep", true) }),
          F.check(pr, "shrinkage", { label: "Shrinkage", id: "tdShrink", read: rd("shrinkage", true) }),
          F.check(pr, "aging", { label: "Modulus / strength ageing", id: "tdAging", read: rd("aging", true) }),
        ]),
        L === "user" ? tdNote("User curves: enter the creep coefficient φ(t − t0), the free shrinkage ε(t − ts) and the E(t)/E28 ageing on the <b>User Curves</b> tab. J(t, t0) = (1 + φ) / E(t0).")
          : tdGroup("Environment and Member", [
            F.num(pr, "rh", { label: "Relative humidity", unit: "%", min: 40, max: 100, def: DEF.rh, read: rd("rh", DEF.rh), id: "tdRh" }),
            F.num(pr, "h0", { label: "Notional size h0 = 2Ac/u", kind: "dim", gt: 0, def: DEF.h0, read: rd("h0", DEF.h0), id: "tdH0", hint: "V/S = h0 / 2" }),
            F.num(pr, "cure_days", { label: "Curing ends (drying starts) at age", unit: DAYS, gt: 0, def: DEF.cure_days, read: rd("cure_days", DEF.cure_days), id: "tdCure" }),
            cem.length > 1 ? F.select(pr, "cement", cem.map(c => [c, (CEMENT_LABEL[L] || {})[c] || c]), { label: "Cement", id: "tdCement", read: () => pr.cement ?? cem[L === "cebfip2010" ? 1 : 0] }) : null,
            L === "aci209" ? F.select(pr, "curing", [["moist", "Moist cured"], ["steam", "Steam cured"]], { label: "Curing", id: "tdCuring", read: rd("curing", "moist") }) : null,
            L === "aci209" ? null : F.num(pr, "fcm", { label: "Mean 28-day strength fcm", kind: "stress", gt: 0, allowNull: true,
              nullText: L === "gl2000" ? "1.1 f'c + 5 MPa" : "f'c + 8 MPa", id: "tdFcm" }),
          ]),
        L === "aci209" ? tdAdvanced([
          F.num(pr, "phi_u", { label: "Ultimate creep φu (override)", min: 0, allowNull: true, nullText: "2.35 γc", def: null, id: "tdPhiU" }),
          F.num(pr, "eps_shu", { label: "Ultimate shrinkage εshu (override)", kind: "strain", min: 0, allowNull: true, nullText: "780e-6 γsh", def: null, id: "tdEpsU" }),
        ], { summary: "Advanced — ACI 209 ultimate values", countFn: () => (pr.phi_u != null) + (pr.eps_shu != null) }) : null,
      ].filter(Boolean));
      panels.gen.classList.toggle("dk-disabled", false);
    };
    const curveRows = key => (pr[key] || []).map(([t, v]) => ({ t, v }));
    const tables = {};
    const buildCurves = () => {
      F.forget(panels.curves);
      panels.curves.textContent = "";
      if (law() !== "user") { panels.curves.appendChild(tdNote(`User curves apply to the <b>User curves</b> law only (current: ${tdEsc(LAW_LABEL[law()])}).`)); return; }
      const mk = (key, title, vLabel, kind, idp) => {
        tables[key] = curveRows(key);
        const rows = tables[key];
        return tdGroup(title, [F.table(rows, [{ key: "t", label: "t (days)", type: "num" }, { key: "v", label: vLabel, type: "num", kind }], {
          id: idp, addLabel: "+ Add point", newRow: r => ({ t: r.length ? r[r.length - 1].t * 10 || 10 : 0, v: r.length ? r[r.length - 1].v : 0 }),
          onChange: () => { pr[key] = rows.map(r => [r.t, r.v]); if (!rows.length) delete pr[key]; },
          validate: r => r.length && r.length < 2 ? "Enter at least two points." : r.some((x, i) => i && !(x.t > r[i - 1].t)) ? "Times must increase." : "",
          empty: "No points — the effect is off unless defined." })]);
      };
      panels.curves.append(
        mk("creep_curve", "Creep Coefficient φ(t − t0)", "φ", "none", "tdCreepCurve"),
        mk("shrinkage_curve", "Free Shrinkage ε(t − ts)", "ε (contraction)", "strain", "tdShrinkCurve"),
        mk("modulus_curve", "Modulus Ageing E(t)/E28 (concrete age)", "E/E28", "none", "tdModCurve"));
    };
    const buildRelax = () => {
      F.forget(panels.relax);
      panels.relax.textContent = "";
      const rx = { on: !!pr.relaxation };
      const r = pr.relaxation || { law: "ec2", class: 2, rho1000: 2.5 };
      panels.relax.append(tdGroup("Prestressing Steel Relaxation", [
        F.check(rx, "on", { label: "Relaxation (tendon elements in step-by-step staged cases)", id: "tdRelOn",
          write: v => { if (v) pr.relaxation = r; else delete pr.relaxation; }, onChange: () => buildRelax() }),
        rx.on ? F.select(r, "law", [["ec2", "EN 1992-1-1 §3.3.2"], ["aci", "ACI 423 / PCI (Magura)"]], { label: "Relaxation law", id: "tdRelLaw",
          write: v => { for (const k of Object.keys(r)) delete r[k]; Object.assign(r, v === "ec2" ? { law: "ec2", class: 2, rho1000: 2.5 } : { law: "aci", K: 45 }); }, onChange: () => buildRelax() }) : null,
        rx.on && r.law === "ec2" ? F.select(r, "class", [[1, "Class 1 — wire / strand, ordinary"], [2, "Class 2 — low relaxation"], [3, "Class 3 — hot-rolled bars"]], { label: "Class", id: "tdRelClass" }) : null,
        rx.on && r.law === "ec2" ? F.num(r, "rho1000", { label: "ρ1000 (1000 h loss at 0.7 fpk)", unit: "%", min: 0, id: "tdRho" }) : null,
        rx.on && r.law === "ec2" ? F.num(r, "fpk", { label: "Characteristic strength fpk", kind: "stress", gt: 0, allowNull: true, nullText: "material Fu", id: "tdFpk" }) : null,
        rx.on && r.law === "aci" ? F.num(r, "K", { label: "K (45 low relaxation, 10 stress relieved)", gt: 0, id: "tdK" }) : null,
        rx.on && r.law === "aci" ? F.num(r, "fpy", { label: "Yield strength fpy", kind: "stress", gt: 0, allowNull: true, nullText: "material Fy", id: "tdFpy" }) : null,
      ]));
      if (rx.on) {
        const sig = 0.7 * (r.fpk || mat.fu || 1.86e6);
        const ts = [1, 10, 100, 1000, 10000];
        panels.relax.appendChild(tdNote("Loss at σpi = 0.7 fpk: " + ts.map(t => `${t} d: ${(100 * tdmRelax(r, sig, t, mat.fu, mat.fy)).toFixed(2)} %`).join(" · ")));
      }
    };
    const buildPrev = async () => {
      panels.prev.textContent = "";
      let p;
      try { p = tdmNormalize(pr); } catch (e) { panels.prev.appendChild(tdNote(tdEsc(e.message))); return; }
      const c = await apiCurves({ props: p, E: mat.E, fc: mat.fc ?? null, t0: 28 });
      const xs = c.times;
      panels.prev.append(
        tdGroup("Creep coefficient φ(28 + Δt, 28)", [lineChart({ series: [{ name: "φ", x: xs, y: c.creep }], xLabel: "Δt (days, log)", yLabel: "φ", logX: true, id: "tdPrevCreep" })]),
        tdGroup("Free shrinkage (contraction)", [lineChart({ series: [{ name: "εsh", x: xs, y: c.shrinkage }], xLabel: "age (days, log)", yLabel: "ε", logX: true, id: "tdPrevShrink" })]),
        tdGroup("Ageing", [lineChart({ series: [{ name: "E(t)/E28", x: xs, y: c.modulus_ratio }, { name: "fc(t)/fc28", x: xs, y: c.strength_ratio }], xLabel: "age (days, log)", yLabel: "ratio", logX: true, id: "tdPrevAge" }),
          legend([{ name: "E(t)/E28" }, { name: "fc(t)/fc28" }])]),
        tdNote(`J(28 + 10000, 28) = ${(c.compliance[c.compliance.length - 1] * tdU.toDisplay("modulus", 1)).toExponential(4)} 1/${tdEsc(tdU.label("modulus"))}`));
    };
    const buildAll = () => {
      buildGen(); buildCurves(); buildRelax();
      for (const k of ["curves", "relax"]) panels[k].classList.toggle("dk-disabled", !st.on);
      panels.gen.querySelectorAll(".dk-row").forEach((r, i) => { if (i > 0) r.classList.toggle("dk-disabled", !st.on); });
    };
    buildAll();
    tabs = tdTabs([
      { id: "gen", label: "Creep & Shrinkage", body: panels.gen },
      { id: "curves", label: "User Curves", body: panels.curves },
      { id: "relax", label: "Relaxation", body: panels.relax },
      { id: "prev", label: "Preview", body: panels.prev },
    ], { id: "tdTabs", onSwitch: t => { if (t === "prev") buildPrev(); } });
    return tdDialog({
      id: "tdMatDlg", title: `Time-Dependent Properties — ${name}`, iconId: "exleaf-materials", size: "md", form: F, tabs,
      intro: "Creep compliance, shrinkage, modulus / strength ageing and steel relaxation used by <b>step-by-step</b> staged construction cases.",
      body: tabs,
      onOk: () => {
        if (!F.touched) { done && done(); return; }
        if (st.on) {
          let p;
          try { p = tdmNormalize(pr); } catch (e) { return e.message; }
          void p;
          D.material_time_dependent = { ...(D.material_time_dependent || {}), [name]: pr };
        } else if (D.material_time_dependent) {
          delete D.material_time_dependent[name];
          if (!Object.keys(D.material_time_dependent).length) delete D.material_time_dependent;
        }
        if (!Dext) tdCommit(sky, D);
        done && done();
      },
    });
  }

  /* ================================================================
     Materials list (Define › Section Properties › …)
     ================================================================ */
  function openMaterialTDList() {
    tdCss();
    const D = tdDraft(sky);
    const tdOf = n => (D.material_time_dependent || {})[n];
    const list = tdList({
      id: "tdMatList",
      items: () => Object.keys(D.materials || {}).map(k => ({ key: k })),
      columns: [
        { label: "Material", get: it => it.key },
        { label: "Type", get: it => (D.materials[it.key] || {}).material_type || "concrete" },
        { label: "Time-dependent law", get: it => tdOf(it.key) ? LAW_LABEL[tdOf(it.key).law || "cebfip2010"] : "— none —" },
        { label: "RH (%)", get: it => tdOf(it.key) && tdOf(it.key).law !== "user" ? String(tdOf(it.key).rh ?? DEF.rh) : "" },
        { label: "Relaxation", get: it => tdOf(it.key) && tdOf(it.key).relaxation ? tdOf(it.key).relaxation.law.toUpperCase() : "" },
      ],
      actions: [
        { label: "Modify/Show…", id: "tdMatModify", needsSel: true, fn: sel => openMaterialTD(sel, D, () => list.render()) },
        { label: "Remove", id: "tdMatRemove", needsSel: true, disabled: sel => !tdOf(sel), fn: sel => {
          delete D.material_time_dependent[sel];
          if (!Object.keys(D.material_time_dependent).length) delete D.material_time_dependent;
          list.render();
        } },
      ],
      onOpen: sel => openMaterialTD(sel, D, () => list.render()),
      empty: "No materials yet — Define › Material Properties… first.",
    });
    return tdDialog({
      id: "tdMatListDlg", title: "Material Time-Dependent Properties", iconId: "exleaf-materials", size: "lg",
      intro: "Per-material creep, shrinkage, ageing (CEB-FIP 2010, ACI 209, GL2000 or user curves) and tendon relaxation. Used by step-by-step staged construction.",
      body: list, onOk: () => { tdCommit(sky, D); },
    });
  }

  /** Material Property Data › "Time Dependent…" (dlgstd_defs.js hook). */
  function materialButton(D, getName) {
    const b = tdEl("button", { type: "button", class: "btn btn-small dk-legacy", id: "dsMatTimeDep", title: "Creep, shrinkage, ageing and relaxation of this material" }, ["Time Dependent…"]);
    b.addEventListener("click", () => {
      const n = getName();
      if (!n || !(D.materials || {})[n]) { toast("Time Dependent", "Press OK to create the material first, then edit its time-dependent properties.", "info"); return; }
      openMaterialTD(n, D);
    });
    return b;
  }

  /* ================================================================
     Staged case card hook (loads.js)
     ================================================================ */
  function stagedCard(card, m, sc, mutated) {
    const td = sc && sc.time_dependent;
    if (!td || typeof td !== "object") return;
    const box = tdEl("div", { class: "td-staged", id: "tdStaged-" + sc.name });
    if (!Array.isArray(sc.stages)) {
      box.appendChild(tdEl("p", { class: "muted staged-note", html: "Step-by-step creep integration with per-material laws needs <b>User-defined stages</b> (Stage Data…)." }));
      card.appendChild(box);
      return;
    }
    const sel = tdEl("select", { class: "td-method", id: "tdMethod-" + sc.name, "aria-label": "Creep integration method" }, [
      tdEl("option", { value: "aaem" }, ["Age-adjusted effective modulus (closed form)"]),
      tdEl("option", { value: "step" }, ["Step-by-step (per-material laws, relaxation)"])]);
    sel.value = td.method === "step" ? "step" : "aaem";
    sel.addEventListener("change", () => {
      if (sel.value === "step") td.method = "step";
      else { delete td.method; delete td.steps_per_stage; delete td.output_times; }
      mutated && mutated();
      opt.disabled = sel.value !== "step";
      summary.textContent = summaryText();
    });
    const opt = tdEl("button", { type: "button", class: "btn btn-small", id: "tdOptions-" + sc.name, title: "Steps per stage, output times, end time" }, ["Time Options…"]);
    opt.disabled = td.method !== "step";
    opt.addEventListener("click", () => openTimeOptions(sc.name));
    const summaryText = () => td.method === "step"
      ? `${td.steps_per_stage ?? 4} steps / stage · ${(td.output_times || []).length} output time(s) · end ${td.t_eval != null ? td.t_eval + " d" : "= last stage"}`
      : "χ = 0.8, staged-case ACI 209 curves";
    const summary = tdEl("span", { class: "muted td-sum" }, [summaryText()]);
    box.append(tdEl("label", { class: "rs-field td-field" }, [tdEl("span", {}, ["Creep integration"]), sel]), opt, summary);
    card.appendChild(box);
  }

  function openTimeOptions(name) {
    tdCss();
    const D = tdDraft(sky);
    const sc = (D.staged_cases || {})[name];
    if (!sc || !sc.time_dependent) return null;
    const td = sc.time_dependent;
    const F = tdForm();
    const outs = (td.output_times || []).map(t => ({ t }));
    const tTotal = Array.isArray(sc.stages) ? sc.stages.reduce((a, s) => a + (+s.duration_days || 0), 0) : 0;
    const body = [
      tdGroup("Time Steps", [
        F.num(td, "steps_per_stage", { label: "Creep steps per stage", int: true, min: 1, max: 50, def: 4, read: () => td.steps_per_stage ?? 4, id: "tdSteps",
          hint: "doubling steps over each stage duration" }),
        F.num(td, "t_eval", { label: "End time (long term)", unit: DAYS, min: tTotal, allowNull: true, nullText: `end of last stage (${tTotal} d)`, id: "tdTEnd",
          hint: "creep / shrinkage continue after the last stage up to this time" }),
      ]),
      tdGroup("Output Times", [F.table(outs, [{ key: "t", label: "t (days)", type: "num" }], {
        id: "tdOuts", addLabel: "+ Add time", newRow: r => ({ t: r.length ? r[r.length - 1].t * 2 || 28 : 28 }),
        onChange: () => { if (outs.length) td.output_times = outs.map(o => o.t); else delete td.output_times; },
        validateRow: r => r.t >= 0 ? "" : "Times must be ≥ 0 days.",
        empty: "No output times — snapshots at stage ends only." })]),
      tdNote(`Stage durations are edited in <b>Stage Data…</b> (total ${tTotal} d).`),
    ];
    return tdDialog({
      id: "tdTimeOptDlg", title: `Time-Dependent Options — ${name}`, iconId: null, size: "sm", form: F,
      intro: "Step-by-step creep integration: each stage's operations act at its start, then its duration is integrated in creep steps.",
      body, onOk: () => { if (F.touched) { td.method = "step"; tdCommit(sky, D); } },
    });
  }

  /* ================================================================
     Tendon Properties rows (tendons.js hook)
     ================================================================ */
  function tendonRows(p, onDirty, ids, { row, select, numInput }) {
    const rows = [];
    rows.push(row("Model tendon as", select([["loads", "Loads (equivalent loads)"], ["elements", "Elements (bonded truss)"]], p.model_as === "elements" ? "elements" : "loads",
      v => { if (v === "elements") p.model_as = "elements"; else delete p.model_as; onDirty(); }, ids + "ModelAs"), null,
      "elements: stiffness + elastic shortening from the analysis"));
    rows.push(row("ES loss factor K_es", numInput("none", p.losses.es_factor ?? 0, { min: 0, id: ids + "EsFactor",
      onSet: v => { if (v) p.losses.es_factor = v; else delete p.losses.es_factor; onDirty(); } }), null, "loads model: Δfp = K_es·(Ep/Ec)·fcgp (0 = off; 0.5 post-tensioned)"));
    return rows;
  }
  function tendonElementCard(rep) {
    const e = rep.element;
    const cases = Object.entries(e.cases || {});
    const wrap = tdEl("div", { class: "td-tel", id: "tdTendonElement" });
    wrap.appendChild(tdEl("div", { class: "dlg-group-title" }, [`Bonded elements (${e.n})`]));
    if (!cases.length) { wrap.appendChild(tdNote("No static case applies this tendon's pattern.")); return wrap; }
    const fu = tdU.label("force");
    const t = tdEl("table", { class: "data-table cd-res-table", id: "tdTendonElTable" });
    t.innerHTML = `<thead><tr><th class="txt">Case</th><th>P initial (${tdEsc(fu)})</th><th>P mean (${tdEsc(fu)})</th><th>Elastic shortening loss (${tdEsc(fu)})</th><th class="txt">Loss</th></tr></thead><tbody>` +
      cases.map(([n, c]) => {
        const p0 = c.P_initial.reduce((a, b) => a + b, 0) / Math.max(1, c.P_initial.length);
        const ls = c.loss.reduce((a, b) => a + b, 0) / Math.max(1, c.loss.length);
        return `<tr><td class="txt">${tdEsc(n)}</td><td>${tdU.fmt("force", p0, 1)}</td><td>${tdU.fmt("force", c.P_mean, 1)}</td><td>${tdU.fmt("force", ls, 2)}</td><td class="txt">${p0 ? (100 * ls / p0).toFixed(2) + " %" : "—"}</td></tr>`;
      }).join("") + "</tbody>";
    wrap.appendChild(tdEl("div", { class: "table-scroll" }, [t]));
    return wrap;
  }

  /* ================================================================
     Display › Staged Time History (Creep)
     ================================================================ */
  function openStagedTimeHistory(name) {
    tdCss();
    const r = S.results;
    const avail = Object.entries((r && r.staged) || {}).filter(([, cd]) => cd && cd.time_history).map(([n]) => n);
    if (!avail.length) {
      toast("Staged Time History", r ? "No step-by-step staged case in the results — set Creep integration = Step-by-step on a staged case and run." : "Run the analysis first (F5).", "info", 6000);
      return null;
    }
    let cur = name && avail.includes(name) ? name : avail[0];
    let logX = false;
    const body = tdEl("div", { class: "td-hist" });
    const draw = () => {
      body.textContent = "";
      const cd = r.staged[cur];
      const th = cd.time_history;
      const ctl = tdEl("div", { class: "td-ctl" });
      const sel = tdEl("select", { id: "tdHistCase", "aria-label": "Staged case" }, avail.map(n => tdEl("option", { value: n }, [n])));
      sel.value = cur;
      sel.addEventListener("change", () => { cur = sel.value; draw(); });
      const lg = tdEl("input", { type: "checkbox", id: "tdHistLog" });
      lg.checked = logX;
      lg.addEventListener("change", () => { logX = lg.checked; draw(); });
      ctl.append(tdEl("label", { class: "td-field" }, ["Staged case ", sel]), tdEl("label", { class: "dk-check" }, [lg, tdEl("span", {}, ["log time axis"])]));
      body.appendChild(ctl);
      const order = ((r.story_order || []).filter(s => th.story_uz[s])).concat(Object.keys(th.story_uz).filter(s => !(r.story_order || []).includes(s)));
      const k = tdU.toDisplay("disp", 1);
      const series = order.map(s => ({ name: s, x: th.times, y: th.story_uz[s].map(v => v == null ? null : -v * k) }))
        .filter(se => se.y.some(v => v != null));          // stories never built are left out
      body.appendChild(tdGroup(`Column shortening at story tops (${tdU.label("disp")}, positive = down)`, [
        lineChart({ series, xLabel: "time (days)" + (logX ? ", log" : ""), yLabel: "shortening", logX, id: "tdHistShort" }), legend(series)]));
      const tf = Object.entries(th.tendon_force || {}).filter(([, v]) => v.some(x => x != null));
      if (tf.length) {
        const kf = tdU.toDisplay("force", 1);
        const ts = tf.map(([u, v]) => ({ name: u, x: th.times, y: v.map(x => x == null ? null : x * kf) }));
        body.appendChild(tdGroup(`Tendon force (mean over elements, ${tdU.label("force")})`, [
          lineChart({ series: ts, xLabel: "time (days)" + (logX ? ", log" : ""), yLabel: "P", logX, id: "tdHistTendon" }), legend(ts)]));
      }
      const tbl = tdEl("table", { class: "data-table cd-res-table", id: "tdHistTable" });
      const D = tdEsc(tdU.label("disp"));
      tbl.innerHTML = `<thead><tr><th>t (d)</th><th class="txt">Stage</th>${order.map(s => `<th>${tdEsc(s)} uz (${D})</th>`).join("")}</tr></thead><tbody>` +
        th.times.map((t, i) => `<tr><td>${(+t.toPrecision(5))}</td><td class="txt">${tdEsc(th.stage[i] || "")}</td>${order.map(s => `<td>${th.story_uz[s][i] == null ? "—" : tdU.fmt("disp", th.story_uz[s][i], 3)}</td>`).join("")}</tr>`).join("") + "</tbody>";
      body.appendChild(tdGroup("Time history", [tdEl("div", { class: "table-scroll td-scroll" }, [tbl])]));
      if (cd.shortening) {
        const sh = cd.shortening;
        const t2 = tdEl("table", { class: "data-table cd-res-table", id: "tdHistShortTable" });
        t2.innerHTML = `<thead><tr><th class="txt">Story</th><th>uz elastic (${D})</th><th>uz total (${D})</th><th>Δ creep + shrinkage (${D})</th></tr></thead><tbody>` +
          order.filter(s => sh[s]).map(s => `<tr><td class="txt">${tdEsc(s)}</td><td>${tdU.fmt("disp", sh[s].uz_elastic, 3)}</td><td>${tdU.fmt("disp", sh[s].uz_time_dependent, 3)}</td><td>${tdU.fmt("disp", sh[s].delta, 3)}</td></tr>`).join("") + "</tbody>";
        body.appendChild(tdGroup("Final shortening", [t2]));
      }
      if ((th.outputs || []).length) body.appendChild(tdNote(`Snapshots stored at ${th.outputs.map(o => o.t + " d").join(", ")} (results.staged[…].time_history.outputs).`));
    };
    draw();
    return tdDialog({ id: "tdHistDlg", title: "Staged Time History (Creep)", iconId: null, size: "lg", body, noCancel: true, onUnits: draw,
      intro: "Step-by-step creep, shrinkage and relaxation over the stage timeline." });
  }

  /* ------------------------------------------------ registration */
  Object.assign(sky, {
    openMaterialTD, openMaterialTDList, openStagedTimeHistory, openTimeDepOptions: openTimeOptions,
    tdMaterialButton: materialButton, tdStagedCard: stagedCard, tdTendonRows: tendonRows, tdTendonElementCard: tendonElementCard,
  });
  if (sky.menu && sky.menu.add) {
    try {
      sky.menu.add("Define/Section Properties", { label: "Material Time-Dependent Properties…", act: "def-td-matprops", before: "def-b9-springprops",
        hint: "creep · shrinkage · ageing · relaxation", fn: () => openMaterialTDList() });
      sky.menu.add("Display/Nonlinear Results", { label: "Staged Time History (Creep)…", act: "dis-td-history", before: "dis-pt-hyper",
        hint: "shortening vs time", fn: () => openStagedTimeHistory() });
    } catch (e) { console.warn("timedep menus", e); }
  }
}
