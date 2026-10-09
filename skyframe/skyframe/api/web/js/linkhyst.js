/* SkyFrame — link hysteresis types (CONTRACT "Named spring properties and
   link hysteresis types", B11) + the Link Properties dialog.

   Adds the 5 isolator-layout hysteresis types to ME.LINK_TYPES (so the
   Properties-panel link editor, the plan / elevation glyph letters and
   normalizeLinkDevice know them):

     multilinear_kinematic     {points, kv?}
     multilinear_takeda        {points, points_neg?, kv?}
     multilinear_pivot         {points, points_neg?, pinch_x?, pinch_y?, unload_beta?, kv?}
     friction_spring           {mu, k_init?, kv?}
     rubber_isolator_bouc_wen  {k_init, qd, alpha1?, alpha2?, mu?, eta?, beta?, gamma?, kv?}

   Optional keys are kept only when present (never back-filled), so loading
   and saving a model is byte-identical. Assign > Link Properties… edits every
   parameter of the selected links with a backbone / hysteresis preview
   (illustrative cycle for kinematic / Takeda / pivot / friction / Bouc-Wen). */

import { dialog as lhDialog, btn as lhBtn, footBar as lhFootBar, errorLine as lhErrorLine,
  showError as lhShowError } from "./analysisdlg.js";
import LHU from "./units.js";
import { LINK_TYPES as LH_LINK_TYPES, linkTypeOf as lhLinkTypeOf } from "./modeledit.js";
import { b9h as lhH, b9group as lhGroup, b9num as lhNum, b9after as lhAfter, b9css as lhCss,
  b9esc as lhEsc, b9clone as lhClone, b9same as lhSame, b9curveSvg as lhCurveSvg } from "./b9common.js";

const isNum = v => typeof v === "number" && isFinite(v);
const KV = ["kv", "kN/m", 1e7, "Vertical (axial) stiffness kv"];
/** Unit strings → units.js kinds (same mapping as the Properties panel). */
export const LH_UNIT_KIND = { "kN·s/m": "damping_coeff", "kN/m": "stiffness", "m": "length", "kN": "force", "–": "none" };

/** New type defs: params = required scalars [key, unit, default, label];
    opt = optional scalars (written only when set); points / neg flags. */
export const HYST_TYPES = {
  multilinear_kinematic: {
    label: "Multilinear plastic · kinematic", letter: "K",
    note: "Multilinear backbone (any number of points, mirrored) with kinematic hardening: unloads at the initial stiffness over twice the first force, then follows the translated backbone. Isolator layout (vertical / zero-length link): both shear directions; vertical elastic kv. Nonlinear static + direct-integration TH (not FNA).",
    params: [], opt: [KV], points: true, maxPts: null, neg: false,
    defaultPoints: [[0.01, 100], [0.05, 140]],
  },
  multilinear_takeda: {
    label: "Multilinear plastic · Takeda", letter: "A",
    note: "Takeda degrading hysteresis on a 2–3 point backbone: unloads parallel to the initial stiffness, then aims at the opposite peak (or the opposite first point if that side never yielded). Isolator layout; vertical elastic kv.",
    params: [], opt: [KV], points: true, maxPts: 3, neg: true,
    defaultPoints: [[0.01, 100], [0.05, 140]],
  },
  multilinear_pivot: {
    label: "Multilinear plastic · pivot", letter: "P",
    note: "Pinching pivot-type hysteresis (approximation of Dowell's pivot model): unloads at k0·μ^-β to zero force, reloads through the pinch point (pinch_x, pinch_y), then on to the peak. 2–3 backbone points. Isolator layout; vertical elastic kv.",
    params: [],
    opt: [["pinch_x", "–", 0.5, "Pinch point — deformation fraction"], ["pinch_y", "–", 0.25, "Pinch point — force fraction"],
      ["unload_beta", "–", 0, "Unloading-stiffness exponent β"], KV],
    points: true, maxPts: 3, neg: true,
    defaultPoints: [[0.01, 100], [0.05, 140]],
  },
  friction_spring: {
    label: "Friction spring (Coulomb slider)", letter: "S",
    note: "Coulomb friction slider: slip force μ·N with N the instantaneous compressive axial load, stick stiffness k_init, no restoring stiffness. Isolator layout; vertical elastic kv.",
    params: [["mu", "–", 0.06, "Friction coefficient μ"]],
    opt: [["k_init", "kN/m", 1e5, "Stick (pre-slip) stiffness"], KV],
  },
  rubber_isolator_bouc_wen: {
    label: "Rubber isolator · Bouc-Wen", letter: "B",
    note: "Elastomeric bearing with Bouc-Wen smooth hysteresis: characteristic strength qd, initial stiffness k_init; the large-displacement force tends to qd + α1·k_init·u. Isolator layout; vertical elastic kv.",
    params: [["k_init", "kN/m", 20000, "Initial elastic stiffness"], ["qd", "kN", 100, "Characteristic strength qd"]],
    opt: [["alpha1", "–", 0, "Post-yield stiffness ratio α1"], ["alpha2", "–", 0, "Nonlinear hardening ratio α2"],
      ["mu", "–", 2, "Hardening exponent μ"], ["eta", "–", 1, "Yield exponent η"],
      ["beta", "–", 0.5, "Loop shape β"], ["gamma", "–", 0.5, "Loop shape γ"], KV],
  },
};

/** Keys kept when switching a link to a hysteresis type (same meaning across types). */
const CARRY = ["points", "points_neg", "kv", "k_init"];
const cleanPts = a => (Array.isArray(a) ? a : []).filter(p => Array.isArray(p) && p.length === 2 && p.every(isNum)).map(p => [+p[0], +p[1]]);
function makeNormalize(t, def) {
  return src => {
    src = src && typeof src === "object" ? src : {};
    const allowed = new Set([...def.params.map(p => p[0]), ...def.opt.map(p => p[0]), ...(def.points ? ["points"] : []), ...(def.neg ? ["points_neg"] : [])]);
    const out = {};
    for (const k of Object.keys(src)) {
      if (!allowed.has(k)) continue;
      if (k === "points" || k === "points_neg") { const p = cleanPts(src[k]); if (p.length) out[k] = p; }
      else if (isNum(src[k])) out[k] = +src[k];
    }
    for (const [k, , dv] of def.params) if (!(k in out)) out[k] = dv;
    if (def.points && !out.points) out.points = def.defaultPoints.map(p => [...p]);
    return out;
  };
}
for (const [t, def] of Object.entries(HYST_TYPES)) {
  if (LH_LINK_TYPES[t]) continue;
  LH_LINK_TYPES[t] = { ...def, params: def.params.map(([k, u, dv]) => [k, u, dv]), normalize: makeNormalize(t, def), hysteresis: true, carry: CARRY };
}

/* ================================================================
   backbone + illustrative hysteresis
   ================================================================ */
const interp = (pts, u) => {             // pts from (0,0), |u| beyond last → last slope
  const a = [[0, 0], ...pts];
  const s = Math.sign(pts[0][0]);
  for (let i = 1; i < a.length; i++) if (s * u <= s * a[i][0]) {
    const [d0, f0] = a[i - 1], [d1, f1] = a[i];
    return f0 + (f1 - f0) * (u - d0) / (d1 - d0);
  }
  const n = a.length;
  const [d0, f0] = a[n - 2], [d1, f1] = a[n - 1];
  return f1 + (f1 - f0) / (d1 - d0) * (u - d1);
};
const negOf = p => (p.points_neg && p.points_neg.length ? p.points_neg : p.points.map(([d, f]) => [-d, -f]));

/** Monotonic backbone polyline (both sides). */
export function backbone(type, p) {
  if (HYST_TYPES[type] && HYST_TYPES[type].points || type === "multilinear") {
    const pos = p.points || [], neg = type === "multilinear" ? pos.map(([d, f]) => [-d, -f]) : negOf(p);
    return [...neg.slice().reverse(), [0, 0], ...pos];
  }
  return null;
}

function masing(B, amps) {                    // Masing (kinematic) cycle: F = Fr ± 2B(|u-ur|/2)
  const out = [[0, 0]];
  let ur = 0, Fr = 0, first = true;
  for (const a of amps) {
    const n = 40, sgn = Math.sign(a - ur);
    for (let i = 1; i <= n; i++) {
      const u = ur + (a - ur) * i / n;
      out.push([u, first ? B(u) : Fr + sgn * 2 * B(Math.abs(u - ur) / 2)]);
    }
    Fr = out[out.length - 1][1]; ur = a; first = false;
  }
  return out;
}
function peakOriented(type, p, amps) {        // Takeda / pivot
  const pos = p.points, neg = negOf(p);
  const k0p = pos[0][1] / pos[0][0], k0n = neg[0][1] / neg[0][0];
  const B = u => u >= 0 ? interp(pos, u) : interp(neg, u);
  const px = isNum(p.pinch_x) ? p.pinch_x : 0.5, py = isNum(p.pinch_y) ? p.pinch_y : 0.25, beta = isNum(p.unload_beta) ? p.unload_beta : 0;
  let peakP = [pos[0][0], pos[0][1]], peakN = [neg[0][0], neg[0][1]];
  const out = [[0, 0]];
  let ur = 0, Fr = 0;
  const seg = (path, uEnd) => {               // walk a polyline path (monotone in u) up to uEnd
    const s = Math.sign(uEnd - path[0][0]);
    for (let i = 1; i < path.length; i++) {
      const [a0, f0] = path[i - 1], [a1, f1] = path[i];
      if (s * (uEnd - a1) <= 0) { const F = f0 + (f1 - f0) * (uEnd - a0) / ((a1 - a0) || 1); out.push([uEnd, F]); return F; }
      out.push([a1, f1]);
    }
    const F = B(uEnd); out.push([uEnd, F]); return F;
  };
  amps.forEach((a, idx) => {
    if (idx === 0) {
      const n = 30; for (let i = 1; i <= n; i++) { const u = a * i / n; out.push([u, B(u)]); }
      Fr = B(a); ur = a;
    } else {
      const down = a < ur;
      const k0 = down ? k0p : k0n;
      const dy = down ? pos[0][0] : neg[0][0];
      const mu = Math.max(1, Math.abs(ur / dy));
      const ku = type === "multilinear_pivot" ? k0 * Math.pow(mu, -beta) : k0;
      const u0 = ur - Fr / ku;
      const tgt = down ? peakN : peakP;
      const path = [[ur, Fr], [u0, 0]];
      if (type === "multilinear_pivot") {
        const ds = tgt[0] - (1 - py) * tgt[1] / (down ? k0n : k0p);
        path.push([u0 + px * (ds - u0), py * tgt[1]]);
      }
      path.push(tgt);
      const far = down ? Math.min(a, tgt[0]) : Math.max(a, tgt[0]);
      path.push([far, B(far)]);
      Fr = seg(path, a); ur = a;
    }
    if (a > 0 && a > peakP[0]) peakP = [a, B(a)];
    if (a < 0 && a < peakN[0]) peakN = [a, B(a)];
  });
  return out;
}
function boucWen(p, amps) {
  const k0 = p.k_init, qd = p.qd;
  const a1 = isNum(p.alpha1) ? p.alpha1 : 0, a2 = isNum(p.alpha2) ? p.alpha2 : 0, mu = isNum(p.mu) ? p.mu : 2;
  const eta = isNum(p.eta) ? p.eta : 1, be = isNum(p.beta) ? p.beta : 0.5, ga = isNum(p.gamma) ? p.gamma : 0.5;
  let u = 0, z = 0;
  const out = [[0, 0]];
  const F = () => qd * z + a1 * k0 * u + a2 * k0 * Math.sign(u) * Math.pow(Math.abs(u), mu);
  for (const a of amps) {
    const n = 400, du = (a - u) / n;
    for (let i = 0; i < n; i++) {
      const dz = (k0 / qd) * (1 - Math.pow(Math.abs(z), eta) * (ga * Math.sign(du * z) + be)) * du;
      z += dz; u += du;
      if (i % 4 === 3) out.push([u, F()]);
    }
  }
  return out;
}
/** {series, marks, xKind, yKind, note} for a type + params. */
export function hysteresisPreview(type, p) {
  try {
    const def = HYST_TYPES[type];
    if (type === "friction_spring") {
      const N = 1000, Fy = (p.mu || 0.06) * N, k = isNum(p.k_init) && p.k_init > 0 ? p.k_init : 1e5;
      const D = Math.max(10 * Fy / k, 0.05);
      const B = x => Math.sign(x) * Math.min(k * Math.abs(x), Fy);
      const cyc = masing(B, [D, -D, D]);
      return { series: [{ pts: cyc, cls: "b9-s-cyc" }], marks: [], note: `Illustrative cycle at N = ${LHU.fmtU("force", N, 0)} (slip force μ·N = ${LHU.fmtU("force", Fy, 1)}).` };
    }
    if (type === "rubber_isolator_bouc_wen") {
      if (!(p.k_init > 0 && p.qd > 0)) return null;
      const D = Math.max(8 * p.qd / p.k_init, 0.05);
      const cyc = boucWen(p, [D, -D, D]);
      return { series: [{ pts: cyc, cls: "b9-s-cyc" }], marks: [], note: `Illustrative ±${LHU.fmtU("disp", D, 1)} cycle; large-displacement force → qd + α1·k_init·u.` };
    }
    const bb = backbone(type, p);
    if (!bb) return null;
    const pos = p.points || [];
    if (!pos.length || !pos.every(q => q[0] > 0 && q[1] > 0)) return { series: [{ pts: bb, cls: "b9-s-bb" }], marks: pos, note: "Positive points must have d > 0 and F > 0." };
    const neg = type === "multilinear" ? pos.map(([d, f]) => [-d, -f]) : negOf(p);
    const D = pos[pos.length - 1][0], Dn = neg[neg.length - 1][0];
    let cyc = null;
    if (type === "multilinear_kinematic") cyc = masing(u => Math.sign(u) * interp(pos, Math.abs(u)), [D, -D, D]);
    else if (def && (type === "multilinear_takeda" || type === "multilinear_pivot")) cyc = peakOriented(type, p, [0.5 * D, 0.5 * Dn, D, Dn, D]);
    const series = [{ pts: bb, cls: "b9-s-bb", dash: "4 3" }];
    if (cyc) series.push({ pts: cyc, cls: "b9-s-cyc" });
    return { series, marks: [...pos, ...(type === "multilinear" ? [] : neg)], note: cyc ? "Dashed: backbone · solid: illustrative cycle." : "Elastic multilinear: loads and unloads on the curve." };
  } catch (e) { return null; }
}

/** Link axis vertical or zero length (isolator layout rule). */
export const isolatorLayoutOk = l => {
  const d = [0, 1, 2].map(k => l.pj[k] - l.pi[k]);
  const L = Math.hypot(...d);
  return L < 1e-9 || Math.hypot(d[0], d[1]) <= 1e-6 * L;
};

/** Client checks mirroring springprops.validate_link_params → message | null. */
export function validateHyst(uid, type, p) {
  const def = HYST_TYPES[type];
  if (!def) return null;
  const w = `Link ${uid} (${type})`;
  for (const [k] of def.params) if (!isNum(p[k])) return `${w}: ${k} is required.`;
  if (def.points) {
    const chk = (pts, s, key) => {
      if (!pts.length) return `${w}: ${key} needs at least one point.`;
      if (def.maxPts && !(pts.length >= 2 && pts.length <= def.maxPts)) return `${w}: ${key} needs 2..${def.maxPts} points.`;
      let prev = 0;
      for (const [d, f] of pts) {
        if (!(s * d > s * prev)) return `${w}: ${key} displacements must move strictly away from 0.`;
        if (!(s * f > 0)) return `${w}: ${key} forces must be ${s > 0 ? "> 0" : "< 0"}.`;
        prev = d;
      }
      return null;
    };
    const e1 = chk(p.points || [], 1, "points"); if (e1) return e1;
    if (p.points_neg) {
      const e2 = chk(p.points_neg, -1, "points_neg"); if (e2) return e2;
      if (p.points_neg.length !== p.points.length) return `${w}: points_neg must have as many points as points.`;
    }
  }
  if (type === "multilinear_pivot") {
    for (const k of ["pinch_x", "pinch_y"]) if (k in p && !(p[k] > 0 && p[k] <= 1)) return `${w}: ${k} must be in (0, 1].`;
    if ("unload_beta" in p && !(p.unload_beta >= 0)) return `${w}: unload_beta must be ≥ 0.`;
  }
  if (type === "friction_spring") {
    if (!(p.mu > 0)) return `${w}: mu must be > 0.`;
    if ("k_init" in p && !(p.k_init > 0)) return `${w}: k_init must be > 0.`;
  }
  if (type === "rubber_isolator_bouc_wen") {
    if (!(p.k_init > 0)) return `${w}: k_init must be > 0.`;
    if (!(p.qd > 0)) return `${w}: qd must be > 0.`;
    if ("alpha1" in p && !(p.alpha1 >= 0 && p.alpha1 < 1)) return `${w}: alpha1 must be in [0, 1).`;
    if ("alpha2" in p && !(p.alpha2 >= 0)) return `${w}: alpha2 must be ≥ 0.`;
    for (const k of ["mu", "eta"]) if (k in p && !(p[k] > 0)) return `${w}: ${k} must be > 0.`;
    if (!((isNum(p.beta) ? p.beta : 0.5) + (isNum(p.gamma) ? p.gamma : 0.5) > 0)) return `${w}: beta + gamma must be > 0.`;
  }
  if ("kv" in p && !(p.kv > 0)) return `${w}: kv must be > 0.`;
  return null;
}

export function initLinkHyst(sky) {
  lhCss();
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 4500) => sky.toast && sky.toast(t, m, k, ms);
  const selLinks = () => {
    const m = S.model;
    if (!m) return [];
    return (S.selection || []).filter(r => r.type === "link").map(r => (m.links || []).find(x => x.uid === r.uid)).filter(Boolean);
  };

  /* ================= Assign > Link Properties ================= */
  function openLinkProps() {
    const m = S.model;
    if (!m) return null;
    const links = selLinks();
    if (!links.length) { toast("Link Properties", "Select one or more links first (Select tool).", "error"); return null; }
    const l0 = links[0];
    let type = lhLinkTypeOf(l0);
    let params = lhClone(l0.params || {});
    let stiff = (l0.stiffness || [1e5, 1e5, 1e5, 1e4, 1e4, 1e4]).slice();
    let touched = false;
    const mixed = !links.every(l => lhLinkTypeOf(l) === type && lhSame(l.params || {}, l0.params || {}));
    const body = lhH("div", { class: "b9-lh" });
    body.appendChild(lhH("p", { class: "muted dlg-intro", html:
      `${links.length} link${links.length > 1 ? "s" : ""} selected (${lhEsc(links.slice(0, 5).map(l => l.uid).join(", "))}${links.length > 5 ? ", …" : ""}).` +
      (mixed ? ` <b>Values differ — showing ${lhEsc(l0.uid)}; nothing is written unless you change something.</b>` : "") }));
    const top = lhH("div", { class: "b9-top" });
    const typeSel = lhH("select", { id: "lhType" });
    typeSel.innerHTML = Object.entries(LH_LINK_TYPES).map(([k, d]) => `<option value="${k}"${k === type ? " selected" : ""}>${lhEsc(d.label)}${d.hysteresis ? " ✱" : ""}</option>`).join("");
    top.appendChild(lhH("label", { class: "asn-field" }, [lhH("span", { text: "Link type (✱ hysteresis types)" }), typeSel]));
    body.appendChild(top);
    const grid = lhH("div", { class: "b9-lh-grid" });
    const left = lhH("div", { class: "b9-lh-left", id: "lhParams" });
    const right = lhH("div", { class: "b9-lh-right" });
    const gp = lhGroup("Backbone / hysteresis", "b9-prev");
    const prevBox = lhH("div", { class: "b9-prev-box", id: "lhPreview" });
    const prevNote = lhH("p", { class: "muted b9-small", id: "lhPreviewNote" });
    gp.append(prevBox, prevNote);
    const note = lhH("p", { class: "muted b9-small b9-lh-note" });
    const layout = lhH("p", { class: "field-error b9-small hidden", id: "lhLayoutWarn" });
    right.append(gp, note, layout);
    grid.append(left, right);
    body.appendChild(grid);
    const err = lhErrorLine();
    body.appendChild(err);

    typeSel.addEventListener("change", () => {
      touched = true;
      type = typeSel.value;
      const def = LH_LINK_TYPES[type];
      if (def.normalize) params = def.normalize(Object.fromEntries(Object.entries(params).filter(([k]) => CARRY.includes(k))));
      else {
        const np = {};
        for (const [k, , dv] of def.params) np[k] = isNum(params[k]) ? params[k] : dv;
        if (def.points) np.points = cleanPts(params.points).length ? cleanPts(params.points) : def.defaultPoints.map(p => [...p]);
        params = np;
      }
      render();
    });

    const pointsTable = (key, kindD, kindF) => {
      const pts = params[key];
      const g = lhGroup(key === "points" ? "Backbone points (+ side)" : "Backbone points (− side)");
      const t = lhH("div", { class: "b9-pts", id: key === "points" ? "lhPoints" : "lhPointsNeg" });
      t.appendChild(lhH("div", { class: "b9-pts-row head" }, [lhH("span", { text: `d (${LHU.label(kindD)})` }), lhH("span", { text: `F (${LHU.label(kindF)})` }), lhH("span")]));
      pts.forEach((q, j) => {
        const fd = lhNum(kindD, q[0], { "data-j": j, "data-c": 0 });
        const ff = lhNum(kindF, q[1], { "data-j": j, "data-c": 1 });
        for (const [f, c] of [[fd, 0], [ff, 1]]) f.el.addEventListener("change", () => {
          const v = f.get();
          if (!isNum(v)) { f.set(q[c]); return; }
          q[c] = v; touched = true; preview();
        });
        const x = lhH("button", { type: "button", class: "chip-x", text: "✕", title: "Remove point" });
        x.addEventListener("click", () => { if (pts.length <= 1) return; pts.splice(j, 1); touched = true; render(); });
        t.appendChild(lhH("div", { class: "b9-pts-row" }, [fd.el, ff.el, x]));
      });
      const add = lhBtn("+ Point", "btn-small", () => {
        const last = pts[pts.length - 1];
        pts.push([+(last[0] * 1.6).toPrecision(6), +(last[1] * 1.15).toPrecision(6)]);
        touched = true; render();
      });
      add.id = key === "points" ? "lhPtAdd" : "lhPtNegAdd";
      g.append(t, add);
      return g;
    };

    function render() {
      left.textContent = "";
      const def = LH_LINK_TYPES[type];
      if (type === "elastic") {
        const g = lhGroup("Stiffness (6 DOF)");
        const gr = lhH("div", { class: "asn-grid3" });
        ["kx", "ky", "kz", "krx", "kry", "krz"].forEach((lab, i) => {
          const kind = i < 3 ? "stiffness" : "rot_stiffness";
          const f = lhNum(kind, stiff[i], { min: "0", class: "lhK" });
          f.el.addEventListener("change", () => { const v = f.get(); if (isNum(v) && v >= 0) { stiff[i] = v; touched = true; } else f.set(stiff[i]); });
          gr.appendChild(lhH("label", { class: "asn-num-row" }, [lhH("span", { text: lab }), f.el, lhH("span", { class: "asn-unit", text: LHU.label(kind) })]));
        });
        g.appendChild(gr); left.appendChild(g);
      }
      const hd = HYST_TYPES[type];
      const scal = (hd ? hd.params : def.params.map(([k, u, dv]) => [k, u, dv, k]));
      const opt = hd ? hd.opt : [];
      if (scal.length || opt.length) {
        const g = lhGroup("Parameters");
        const row = ([k, u, dv, lbl], optional) => {
          const kind = LH_UNIT_KIND[u] || "none";
          const has = k in params;
          const f = lhNum(kind, has ? params[k] : dv, { "data-pk": k, class: "lhP" + (optional && !has ? " is-default" : "") });
          f.el.addEventListener("change", () => {
            const v = f.get();
            if (v == null && optional) { delete params[k]; f.set(dv); f.el.classList.add("is-default"); touched = true; preview(); return; }
            if (!isNum(v)) { f.set(has ? params[k] : dv); return; }
            params[k] = v; f.el.classList.remove("is-default"); touched = true; preview();
          });
          g.appendChild(lhH("label", { class: "asn-num-row b9-num-row" }, [
            lhH("span", { html: `<b>${lhEsc(k)}</b> <span class="muted">${lhEsc(lbl || "")}${optional ? " · optional" : ""}</span>` }), f.el,
            lhH("span", { class: "asn-unit", text: kind === "none" ? (u === "–" ? "" : u) : LHU.label(kind) })]));
        };
        scal.forEach(p => row(p, false));
        opt.forEach(p => row(p, true));
        if (opt.length) g.appendChild(lhH("p", { class: "muted b9-small", text: "Optional values left at their default are not written to the model (blank = default)." }));
        left.appendChild(g);
      }
      if (def.points) {
        const kd = "disp", kf = "force";
        left.appendChild(pointsTable("points", kd, kf));
        if (hd && hd.neg) {
          const c = lhH("input", { type: "checkbox", id: "lhAsym" });
          c.checked = !!params.points_neg;
          c.addEventListener("change", () => {
            if (c.checked) params.points_neg = params.points.map(([d, f]) => [-d, -f]);
            else delete params.points_neg;
            touched = true; render();
          });
          left.appendChild(lhH("label", { class: "dlg-chk b9-asym" }, [c, lhH("span", { text: "Asymmetric — define the negative backbone (points_neg)" })]));
          if (params.points_neg) left.appendChild(pointsTable("points_neg", kd, kf));
        }
      }
      note.textContent = def.note || "";
      preview();
    }
    function preview() {
      const pv = hysteresisPreview(type, params);
      if (pv) {
        prevBox.innerHTML = lhCurveSvg(pv.series, { xKind: "disp", yKind: "force", marks: pv.marks, title: LH_LINK_TYPES[type].label, w: 340, hgt: 230 });
        prevNote.textContent = pv.note || "";
      } else {
        prevBox.innerHTML = `<p class="muted b9-small">${type === "elastic" ? "Linear spring — same stiffness in every analysis." : "No backbone preview for this type."}</p>`;
        prevNote.textContent = "";
      }
      const bad = HYST_TYPES[type] ? links.filter(l => !isolatorLayoutOk(l)) : [];
      layout.textContent = bad.length ? `${bad.map(l => l.uid).join(", ")}: hysteresis types need a vertical or zero-length link axis (isolator layout).` : "";
      layout.classList.toggle("hidden", !bad.length);
      lhShowError(err, validateHyst(l0.uid, type, params) || "");
    }

    const apply = () => {
      if (!touched) return true;
      for (const l of links) {
        const msg = validateHyst(l.uid, type, params);
        if (msg) { lhShowError(err, msg); return false; }
      }
      for (const l of links) {
        l.link_type = type;
        l.params = lhClone(params);
        if (type === "elastic") l.stiffness = stiff.slice();
      }
      lhAfter(sky, "link-properties");
      return true;
    };
    const fb = lhFootBar("Hysteresis types act in nonlinear static and direct-integration time history (FNA rejects them).", [
      lhBtn("Cancel", "", () => dlg.close()),
      lhBtn("OK", "btn-primary", () => { if (apply()) dlg.close(); }),
    ]);
    fb.wrap.querySelector(".btn-primary").id = "lhOk";
    const dlg = lhDialog("lhLinkDlg", { title: "Link Properties", iconId: "tool-link", wide: true, body, foot: fb.wrap });
    render();
    return dlg;
  }

  /* ================= properties panel decoration ================= */
  function decorateProps(box, sel) {
    const links = (sel && sel.links) || [];
    if (!links.length) return;
    const wrap = lhH("div", { class: "b9-props", id: "lhPropsBlock" });
    if (links.length === 1) {
      const l = links[0], t = lhLinkTypeOf(l);
      const pv = hysteresisPreview(t, l.params || {});
      if (pv) wrap.appendChild(lhH("div", { class: "b9-props-prev", id: "lhPropsPreview", html: lhCurveSvg(pv.series, { xKind: "disp", yKind: "force", marks: pv.marks, w: 260, hgt: 170 }) }));
      if (HYST_TYPES[t] && !isolatorLayoutOk(l))
        wrap.appendChild(lhH("p", { class: "field-error b9-small", text: "Hysteresis types need a vertical or zero-length link axis." }));
    }
    wrap.appendChild(lhH("div", { class: "b9-props-btns" }, [lhBtn("Link Properties…", "btn-small", () => openLinkProps(), "All parameters + hysteresis preview")]));
    const n = box.querySelector(".link-note") || box.querySelector("#mlAdd") || box.querySelector(".link-params") || box.querySelector("#propLinkType");
    const anchor = n ? (n.closest(".field") || n) : null;
    if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(wrap, anchor.nextSibling); else box.appendChild(wrap);
  }

  sky.openLinkProperties = openLinkProps;
  sky.linkHyst = { openLinkProps, decorateProps, hysteresisPreview, validateHyst, HYST_TYPES };
  return sky.linkHyst;
}
