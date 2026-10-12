/* SkyFrame Wave C (C9) — Section Designer moment-curvature (plot + table).

   CONTRACT "Anisotropic shells, material angle, Section Designer
   moment-curvature, layered shells with any material, results CSV folder"
   (skyframe/core/moment_curvature.py).

     Tools › Section Moment-Curvature…   [tool-wc9-mcurv]
     Section Designer modal: "M-φ" button next to PMM (opens this dialog
     on the section being edited)

   Live: POST /api/sections/designer/mc {name, P (kN, compression +),
   angle (deg), concrete, steel, params} → {points, yield, ultimate,
   idealized, limit, rows}.  ?mock=1: the same fibre integration in JS
   (Hognestad / bilinear / EPP; other models fall back with a note).
   Never edits the model.  Units: P force, M moment, φ 1/length (js/units.js). */

import mcU from "./units.js";
import { el as mcEl, esc as mcEsc, dkForm as mcForm, dkGroup as mcGroup, dkAdvanced as mcAdvanced, dkNote as mcNote,
  dkDialog as mcDialog } from "./dlgkit.js";

export const MC_CONCRETE = [["simple", "Hognestad parabola (simple)"], ["mander", "Mander (unconfined)"], ["mander_confined", "Mander (confined)"], ["park", "Kent-Park"], ["material", "Material's own law"]];
export const MC_STEEL = [["simple", "Bilinear (hardening b)"], ["epp", "Elastic-perfectly plastic"], ["park", "Park strain hardening"], ["material", "Material's own law"]];
const MC_DEF = { eps_c0: 0.002, eps_cu: 0.0035, fcc_ratio: 1.3, eps_cu_conf: 0.012, eps_su: 0.09, eps_sh: 0.01, b: 0.01, ft: 0 };
const MC = { last: null };

/* ------------------------------------------------------------ mock */
function clip(vs, v0, above) {
  const out = [], n = vs.length, sg = above ? 1 : -1;
  for (let i = 0; i < n; i++) {
    const [u1, w1] = vs[i], [u2, w2] = vs[(i + 1) % n];
    const in1 = sg * (w1 - v0) >= 0, in2 = sg * (w2 - v0) >= 0;
    if (in1) out.push([u1, w1]);
    if (in1 !== in2) { const t = (v0 - w1) / (w2 - w1); out.push([u1 + t * (u2 - u1), v0]); }
  }
  return out.length >= 3 ? out : [];
}
function integ(vs) {
  let A = 0, Su = 0, Sv = 0;
  for (let i = 0; i < vs.length; i++) {
    const [y1, z1] = vs[i], [y2, z2] = vs[(i + 1) % vs.length], cr = y1 * z2 - y2 * z1;
    A += cr; Su += (y1 + y2) * cr; Sv += (z1 + z2) * cr;
  }
  return [A / 2, Su / 6, Sv / 6];
}
const KIND = t => (["concrete", "masonry"].includes(t) ? "concrete" : ["steel", "rebar", "tendon", "coldformed", "aluminum"].includes(t) ? "steel" : "other");
function law(mat, cm, sm, p) {
  const k = KIND(mat.material_type || "concrete");
  if (k === "steel") {
    const fy = mat.fy || (["steel", "coldformed", "aluminum"].includes(mat.material_type) ? 345000 : 420000), E = mat.E, b = sm === "epp" ? 0 : p.b;
    return { kind: "steel", fy, E, lim: p.eps_su, f: e => { const a = Math.abs(e), ey = fy / E; const s = a <= ey ? E * a : fy + b * E * (a - ey); return Math.sign(e) * s; } };
  }
  const fc0 = mat.fc || 0.0045 * mat.E;   // rough fallback (mock only)
  const conf = cm === "mander_confined";
  const fc = conf ? fc0 * p.fcc_ratio : fc0, e0 = conf ? p.eps_c0 * (1 + 5 * (p.fcc_ratio - 1)) : p.eps_c0, ecu = conf ? p.eps_cu_conf : p.eps_cu;
  return { kind: "concrete", lim: ecu, f: e => {
    if (e >= 0) return 0;
    const x = -e;
    if (x <= e0) { const r = x / e0; return -fc * (2 * r - r * r); }
    return -(fc - 0.15 * fc * Math.min(x - e0, ecu - e0) / (ecu - e0));
  } };
}
/** ?mock=1 moment-curvature (same algorithm as the backend, simplified laws). */
export function mockMomentCurvature(model, body) {
  const ds = body.section || (model.designer_sections || {})[body.name];
  if (!ds) throw new Error(`unknown designer section '${body.name}'`);
  const p = { ...MC_DEF, ...(body.params || {}) }, mats = model.materials || {};
  const a = (body.angle || 0) * Math.PI / 180, c = Math.cos(a), s = Math.sin(a), P = +body.P || 0;
  const rot = (y, z) => [y * c - z * s, z * c + y * s];
  const laws = {};
  const L = n => laws[n] || (laws[n] = law(mats[n] || { E: 25e6, material_type: "concrete" }, body.concrete || "simple", body.steel || "simple", p));
  const fib = [], rng = [];
  let A = 0, Su = 0, Sv = 0;
  for (const pg of ds.polygons || []) {
    let vs = pg.vertices.map(([y, z]) => rot(y, z));
    if (integ(vs)[0] < 0) vs = vs.reverse();
    const sg = pg.hole ? -1 : 1, m = pg.hole ? ds.material : (pg.material || ds.material);
    const [ar, su, sv] = integ(vs); A += sg * ar; Su += sg * su; Sv += sg * sv;
    const lo = Math.min(...vs.map(q => q[1])), hi = Math.max(...vs.map(q => q[1]));
    if (sg > 0) rng.push([lo, hi, m]);
    for (let k = 0; k < 100; k++) {
      let part = clip(vs, lo + (hi - lo) * k / 100, true);
      if (part.length) part = clip(part, lo + (hi - lo) * (k + 1) / 100, false);
      if (!part.length) continue;
      const [pa, pu, pv] = integ(part);
      if (Math.abs(pa) > 1e-16) fib.push([sg * pa, pu / pa, pv / pa, m, false]);
    }
    L(m);
  }
  for (const r of ds.rebar || []) { const [u, v] = rot(r.y, r.z); fib.push([r.area, u, v, r.material, true]); L(r.material); }
  if (!(A > 0)) throw new Error("the section has no area");
  const uc = Su / A, vc = Sv / A, vmin = Math.min(...rng.map(r => r[0])), vmax = Math.max(...rng.map(r => r[1]));
  const forces = (e0, ph) => { let N = 0, M = 0, Mp = 0; for (const [ar, u, v, m] of fib) { const f = laws[m].f(e0 - ph * (v - vc)) * ar; N += f; M -= f * (v - vc); Mp += f * (u - uc); } return [N, M, Mp]; };
  const solve = (ph, g) => {
    const fn = e => forces(e, ph)[0] + P;
    let lo = g - 1e-5, hi = g + 1e-5, st = 1e-5;
    while (fn(lo) > 0 || fn(hi) < 0) { st *= 1.6; lo = g - st; hi = g + st; if (st > 0.5) throw new Error("moment-curvature: no axial equilibrium (the axial load exceeds the section capacity at this curvature)"); }
    for (let i = 0; i < 80; i++) { const m = (lo + hi) / 2; if (fn(m) > 0) hi = m; else lo = m; }
    return (lo + hi) / 2;
  };
  const meas = (e0, ph) => {
    let crush = 0, rup = 0, yl = -Infinity;
    for (const [v0, v1, m] of rng) if (laws[m].kind === "concrete") crush = Math.max(crush, -Math.min(e0 - ph * (v0 - vc), e0 - ph * (v1 - vc)) / laws[m].lim);
    for (const [, , v, m, bar] of fib) if (bar && laws[m].kind === "steel") { const e = e0 - ph * (v - vc); rup = Math.max(rup, Math.abs(e) / laws[m].lim); yl = Math.max(yl, e / (laws[m].fy / laws[m].E)); }
    return { crush, rup, yl };
  };
  const depth = vmax - vmin, phiEnd = (body.concrete === "mander_confined" ? p.eps_cu_conf : p.eps_cu) / (0.05 * depth), n = 60;
  const row = (ph, e0) => { const [, M, Mp] = forces(e0, ph); const v0 = ph > 0 ? vc + e0 / ph : null;
    return { phi: ph, M, M_perp: Mp, eps0: e0, eps_c: Math.min(0, e0 - ph * (vmax - vc)), eps_s: null, na_depth: v0 != null && v0 > vmin && v0 < vmax ? vmax - v0 : null }; };
  const pts = [row(0, solve(0, 0))];
  let y = null, limit = "curvature limit (no crushing / rupture reached)";
  for (let k = 1; k < 20 * n; k++) {
    const ph = phiEnd * (Math.min(k, n) / n) ** 2 + Math.max(0, k - n) * phiEnd * (2 * n - 1) / n / n;
    const e0 = solve(ph, pts[pts.length - 1].eps0), ms = meas(e0, ph);
    if (!y && ms.yl >= 1) { y = row(ph, e0); }
    if (Math.max(ms.crush, ms.rup) >= 1) { pts.push(row(ph, e0)); limit = ms.crush >= ms.rup ? "concrete crushing" : "steel rupture"; break; }
    pts.push(row(ph, e0));
  }
  const ult = pts[pts.length - 1];
  let ideal = null;
  if (y && ult.phi > y.phi) {
    let area = 0; for (let i = 1; i < pts.length; i++) area += 0.5 * (pts[i].M + pts[i - 1].M) * (pts[i].phi - pts[i - 1].phi);
    const ke = y.M / y.phi, d = ult.phi ** 2 - 2 * area / ke;
    if (ke > 0 && d >= 0) { const Mp = ke * (ult.phi - Math.sqrt(d)); ideal = { Mp, phi_y: Mp / ke, phi_u: ult.phi, k_e: ke, ductility: ult.phi / (Mp / ke), area }; }
  }
  const rows = pts.map((r, i) => ({ ...r, step: i, event: i === pts.length - 1 ? limit : (y && r.phi === y.phi ? "first yield" : "") }));
  return { name: ds.name, P, angle: body.angle || 0, concrete: body.concrete || "simple", steel: body.steel || "simple", params: p,
    points: pts, yield: y, ultimate: ult, idealized: ideal, limit, rows, depth, mock: true,
    notes: ["?mock=1: Hognestad / bilinear laws only (other models are approximated)"] };
}

/* ------------------------------------------------------------ api */
async function mcFetch(sky, body) {
  if (sky.store && sky.store.mock) { await new Promise(r => setTimeout(r, 30)); return mockMomentCurvature(sky.store.model, body); }
  // sync the working model first (materials / designer sections), as the
  // other analysis endpoints do; the model itself is not changed
  const payload = JSON.parse(JSON.stringify(sky.store.model));
  delete payload._mock_params;
  const sync = await fetch("/api/model", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  if (!sync.ok) { let d = null; try { d = await sync.json(); } catch { /* */ } throw new Error((d && d.error) || "model sync failed"); }
  const res = await fetch("/api/sections/designer/mc", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let d = null; try { d = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) throw new Error((d && d.error) || `${res.status} ${res.statusText}`);
  return d;
}

/* ------------------------------------------------------------ view */
const phiF = () => 1 / mcU.toDisplay("length", 1);          // SI 1/m → display 1/length
const phiLab = () => `1/${mcU.label("length")}`;
function plotSvg(out) {
  const W = 520, H = 260, L = 64, R = 14, T = 14, B = 40;
  const pts = out.points || [];
  const xs = pts.map(p => p.phi * phiF()), ys = pts.map(p => mcU.toDisplay("moment", p.M));
  const xm = Math.max(...xs, 1e-12), ymin = Math.min(0, ...ys), ymax = Math.max(...ys, 1e-12);
  const X = x => L + (W - L - R) * x / xm, Y = v => T + (H - T - B) * (1 - (v - ymin) / (ymax - ymin || 1));
  const line = xs.map((x, i) => `${i ? "L" : "M"}${X(x).toFixed(1)},${Y(ys[i]).toFixed(1)}`).join("");
  let extra = "";
  const mark = (p, cls, lab) => { if (!p) return; const x = X(p.phi * phiF()), y = Y(mcU.toDisplay("moment", p.M)); extra += `<circle class="${cls}" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="4" fill="var(--accent,#2f6fd6)"/><text x="${(x + 6).toFixed(1)}" y="${(y - 6).toFixed(1)}" font-size="11" fill="var(--text-2,#555)">${mcEsc(lab)}</text>`; };
  if (out.idealized) {
    const id = out.idealized, Mp = mcU.toDisplay("moment", id.Mp);
    extra += `<path class="wc9-mc-ideal" d="M${X(0)},${Y(0)}L${X(id.phi_y * phiF()).toFixed(1)},${Y(Mp).toFixed(1)}L${X(id.phi_u * phiF()).toFixed(1)},${Y(Mp).toFixed(1)}" fill="none" stroke="var(--text-2,#888)" stroke-dasharray="5 4"/>`;
  }
  mark(out.yield, "wc9-mc-y", "first yield");
  mark(out.ultimate, "wc9-mc-u", "ultimate");
  return `<svg id="wc9McSvg" viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Moment-curvature curve">` +
    `<line x1="${L}" y1="${Y(0)}" x2="${W - R}" y2="${Y(0)}" stroke="var(--line,#ccc)"/><line x1="${L}" y1="${T}" x2="${L}" y2="${H - B}" stroke="var(--line,#ccc)"/>` +
    `<path d="${line}" fill="none" stroke="var(--accent,#2f6fd6)" stroke-width="2"/>${extra}` +
    `<text x="${(L + W - R) / 2}" y="${H - 8}" font-size="11" text-anchor="middle" fill="var(--text-2,#555)">φ (${mcEsc(phiLab())})</text>` +
    `<text x="14" y="${(T + H - B) / 2}" font-size="11" text-anchor="middle" transform="rotate(-90 14 ${(T + H - B) / 2})" fill="var(--text-2,#555)">M (${mcEsc(mcU.label("moment"))})</text>` +
    `<text x="${L - 4}" y="${T + 10}" font-size="10" text-anchor="end" fill="var(--text-2,#555)">${mcEsc(mcU.fmt("moment", ymax / mcU.toDisplay("moment", 1), 1))}</text>` +
    `<text x="${W - R}" y="${H - B + 14}" font-size="10" text-anchor="end" fill="var(--text-2,#555)">${(xm).toPrecision(3)}</text></svg>`;
}
function tableHtml(out) {
  const f = (k, v, d) => v == null ? "—" : mcEsc(mcU.fmt(k, v, d));
  const head = ["Step", `φ (${phiLab()})`, `M (${mcU.label("moment")})`, `M⊥ (${mcU.label("moment")})`, "ε conc.", "ε steel", `NA depth (${mcU.label("dim")})`, "Event"];
  const rows = (out.rows || out.points || []).map((r, i) => `<tr><td>${r.step ?? i}</td><td class="num">${(r.phi * phiF()).toPrecision(4)}</td><td class="num">${f("moment", r.M, 2)}</td><td class="num">${f("moment", r.M_perp, 2)}</td>` +
    `<td class="num">${r.eps_c == null ? "—" : (+r.eps_c).toFixed(5)}</td><td class="num">${r.eps_s == null ? "—" : (+r.eps_s).toFixed(5)}</td><td class="num">${f("dim", r.na_depth, 3)}</td><td>${mcEsc(r.event || "")}</td></tr>`).join("");
  return `<div class="dk-table-scroll" style="max-height:220px;overflow:auto"><table class="dk-table" id="wc9McTable"><thead><tr>${head.map(h => `<th>${mcEsc(h)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`;
}
function csvText(out) {
  const lines = [["step", `phi [${phiLab()}]`, `M [${mcU.label("moment")}]`, `M_perp [${mcU.label("moment")}]`, "eps_c", "eps_s", `na_depth [${mcU.label("dim")}]`, "event"].join(",")];
  for (const [i, r] of (out.rows || out.points || []).entries()) lines.push([r.step ?? i, r.phi * phiF(), mcU.toDisplay("moment", r.M), mcU.toDisplay("moment", r.M_perp), r.eps_c ?? "", r.eps_s ?? "", r.na_depth == null ? "" : mcU.toDisplay("dim", r.na_depth), `"${r.event || ""}"`].join(","));
  return lines.join("\r\n");
}

/** Open the dialog (designer section name optional). */
export function openMomentCurvature(sky, name = null) {
  const m = sky.store.model;
  const names = Object.keys(m.designer_sections || {});
  if (!names.length) { sky.toast && sky.toast("Moment-Curvature", "Draw a section in Define › Section Properties › Section Designer first.", "info"); return null; }
  const V = { name: names.includes(name) ? name : names[0], P: 0, angle: 0, concrete: "simple", steel: "simple" };
  const prm = { ...MC_DEF };
  const F = mcForm();
  const out = mcEl("div", { id: "wc9McOut", class: "wc9-mc-out" }, [mcEl("p", { class: "muted", text: "Press Compute to integrate the section." })]);
  const summary = mcEl("p", { class: "dk-note", id: "wc9McSummary" });
  const csvB = mcEl("button", { type: "button", class: "btn btn-small", id: "wc9McCsv", disabled: true }, ["⬇ CSV"]);
  csvB.addEventListener("click", () => {
    if (!MC.last) return;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csvText(MC.last)], { type: "text/csv" }));
    a.download = `${String(MC.last.name).replace(/[^\w.-]+/g, "_")}_moment_curvature.csv`;
    document.body.appendChild(a); a.click(); a.remove();
  });
  const body = [
    mcGroup("Section and Load", [
      F.select(V, "name", names.map(n => [n, n]), { label: "Designer Section", id: "wc9McSec" }),
      F.num(V, "P", { label: "Axial Load P (compression +)", kind: "force", id: "wc9McP" }),
      F.num(V, "angle", { label: "Neutral-Axis Angle", unit: "deg", id: "wc9McAngle", hint: "0 = bending about axis 3 (+z face in compression); 90 = about axis 2" }),
    ]),
    mcGroup("Stress-Strain Models", [
      F.select(V, "concrete", MC_CONCRETE, { label: "Concrete", id: "wc9McConc" }),
      F.select(V, "steel", MC_STEEL, { label: "Steel / Rebar", id: "wc9McSteel" }),
    ]),
    mcAdvanced([
      F.num(prm, "eps_c0", { label: "Strain at f'c, εc0", gt: 0, def: MC_DEF.eps_c0, id: "wc9McEc0" }),
      F.num(prm, "eps_cu", { label: "Crushing strain, εcu", gt: 0, def: MC_DEF.eps_cu, id: "wc9McEcu" }),
      F.num(prm, "fcc_ratio", { label: "Confined f'cc / f'c", min: 1, def: MC_DEF.fcc_ratio, id: "wc9McK" }),
      F.num(prm, "eps_cu_conf", { label: "Confined crushing strain", gt: 0, def: MC_DEF.eps_cu_conf, id: "wc9McEcuc" }),
      F.num(prm, "eps_su", { label: "Steel rupture strain, εsu", gt: 0, def: MC_DEF.eps_su, id: "wc9McEsu" }),
      F.num(prm, "eps_sh", { label: "Hardening onset, εsh (Park)", gt: 0, def: MC_DEF.eps_sh, id: "wc9McEsh" }),
      F.num(prm, "b", { label: "Hardening ratio b", min: 0, lt: 1, def: MC_DEF.b, id: "wc9McB" }),
      F.num(prm, "ft", { label: "Concrete tension, ft", kind: "stress", min: 0, def: 0, id: "wc9McFt" }),
    ], { summary: "Advanced — strains and parameters", countFn: () => Object.keys(MC_DEF).filter(k => prm[k] !== MC_DEF[k]).length }),
    summary, out,
  ];
  const compute = async () => {
    const params = Object.fromEntries(Object.entries(prm).filter(([k, v]) => v !== MC_DEF[k]));
    summary.textContent = "Computing…";
    try {
      const r = await mcFetch(sky, { name: V.name, P: V.P, angle: V.angle, concrete: V.concrete, steel: V.steel, params });
      MC.last = r;
      const y = r.yield, u = r.ultimate, id = r.idealized;
      const M = v => `${mcU.fmt("moment", v, 2)} ${mcU.label("moment")}`, ph = v => `${(v * phiF()).toPrecision(4)} ${phiLab()}`;
      summary.innerHTML = `<b>${mcEsc(r.limit || "")}</b> · ` + (y ? `first yield M<sub>y</sub> ${mcEsc(M(y.M))} at φ<sub>y</sub> ${mcEsc(ph(y.phi))} · ` : "no first yield before the limit · ") +
        `ultimate M<sub>u</sub> ${mcEsc(M(u.M))} at φ<sub>u</sub> ${mcEsc(ph(u.phi))}` + (id ? ` · idealised M<sub>p</sub> ${mcEsc(M(id.Mp))}, ductility φu/φy ${id.ductility.toFixed(2)}` : "") +
        (r.mock ? " · <i>mock</i>" : "");
      out.innerHTML = plotSvg(r) + tableHtml(r);
      csvB.disabled = false;
      return "";
    } catch (e) { summary.textContent = ""; out.textContent = ""; return "Moment-curvature failed: " + e.message; }
  };
  const dlg = mcDialog({
    id: "wc9McDlg", title: "Section Moment-Curvature", iconId: "show-tables", size: "lg", form: F, body,
    intro: "Fibre integration of a Section Designer section at a constant axial load: curvature increases until the concrete crushes or the steel ruptures. The model is not changed.",
    okLabel: "Compute", cancelLabel: "Close", extra: [csvB],
    onOk: async () => { const e = await compute(); return e || false; },
    onUnits: () => { if (MC.last) out.innerHTML = plotSvg(MC.last) + tableHtml(MC.last); },
  });
  return dlg;
}

/** Tools › Section Moment-Curvature… + the "M-φ" button in Section Designer. */
export function initWc9MomentCurvature(sky) {
  if (!sky) return;
  sky.wc9 = sky.wc9 || {};
  sky.wc9.openMomentCurvature = n => openMomentCurvature(sky, n);
  sky.wc9.mcLast = () => MC.last;
  const pmm = document.getElementById("dsPmmBtn");
  if (pmm && !document.getElementById("wc9McBtn")) {
    const b = mcEl("button", { class: "btn", id: "wc9McBtn", title: "Moment-curvature of the saved section (axial load, angle, concrete / steel models)" }, ["M-φ"]);
    b.addEventListener("click", () => { const nm = (document.getElementById("dsName") || {}).value; openMomentCurvature(sky, nm); });
    pmm.insertAdjacentElement("afterend", b);
  }
  if (sky.menu && sky.menu.add)
    sky.menu.add("Tools", { label: "Section Moment-Curvature…", act: "tool-wc9-mcurv", hint: "Section Designer · M-φ plot + table", fn: () => openMomentCurvature(sky) });
}

export default { openMomentCurvature, initWc9MomentCurvature, mockMomentCurvature };
