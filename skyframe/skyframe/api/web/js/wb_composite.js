/* SkyFrame Wave B — composite column frame sections (SRC / CFT).

   CONTRACT "Orthotropic materials, slab / deck property types, cold-formed
   and composite sections, named line / area springs" (skyframe/core/
   composite_col.py — this file is its line-by-line port, so a section
   saved here reloads byte-identically).

     SRC_I     steel I (d, bf, tf, tw) encased in concrete b × h, optional
               bars (bar_d, n_bars_3, n_bars_2, cover)
     CFT_rect  concrete-filled rectangular tube d × b × t
     CFT_round concrete-filled round tube D × t
   dims also carry "concrete" (material name), optional "rebar" and "code"
   ("AISC360" default | "EC4").  The section material is the STEEL and the
   stored A / I33 / I22 / J are transformed to E_s with EI_eff of AISC
   360-16 I2.1b / I2.2b (C1 / C3) or EN 1994-1-1 6.7.3.3 (Ke = 0.6);
   mod_weight / mod_mass make the self-weight / mass the real steel +
   concrete.

   UI: Define › Section Properties › Composite Column Sections… and the
   Frame Properties list ("Add Composite Column…"; Modify/Show of a
   composite section opens this dialog). Units through js/units.js. */

import wbcU from "./units.js";
import { el as wbcEl, esc as wbcEsc, clone as wbcClone, isNum as wbcIsNum, dkForm as wbcForm, dkGroup as wbcGroup, dkNote as wbcNote,
  dkDialog as wbcDialog, dkDraft as wbcDraft, dkCommit as wbcCommit, uniqueName as wbcUnique } from "./dlgkit.js";
import { spvFramePanel as wbcSpvFrame, spvSplit as wbcSpvSplit } from "./secpreview.js";

export const CC_TYPES = ["SRC_I", "CFT_rect", "CFT_round"];
export const CC_LABEL = { SRC_I: "Steel-encased concrete (SRC)", CFT_rect: "Concrete-filled rectangular tube (CFT)", CFT_round: "Concrete-filled round tube (CFT)" };
const DIMS = {
  SRC_I: [["b", "Concrete Width, b (along 3)"], ["h", "Concrete Depth, h (along 2)"], ["d", "Steel Depth, d"], ["bf", "Flange Width, bf"],
    ["tf", "Flange Thickness, tf"], ["tw", "Web Thickness, tw"], ["bar_d", "Bar Diameter", true], ["cover", "Cover to Bar Centre", true]],
  CFT_rect: [["d", "Tube Depth, d (along 2)"], ["b", "Tube Width, b (along 3)"], ["t", "Wall Thickness, t"]],
  CFT_round: [["D", "Outside Diameter, D"], ["t", "Wall Thickness, t"]],
};
const DEF = {
  SRC_I: { b: 0.6, h: 0.6, d: 0.4, bf: 0.2, tf: 0.016, tw: 0.01, bar_d: 0.025, n_bars_3: 4, n_bars_2: 4, cover: 0.06 },
  CFT_rect: { d: 0.4, b: 0.4, t: 0.012 },
  CFT_round: { D: 0.4, t: 0.012 },
};
const G0 = 9.80665;
export const ccIs = s => !!(s && CC_TYPES.includes(s.shape) && s.dims && typeof s.dims === "object");

/* ---- material helpers (mirror of Material.G / mass_per_volume) */
const matG = m => (m.symmetry === "orthotropic" && m.ortho ? +m.ortho.G12 : m.E / (2 * (1 + (m.nu ?? 0.2))));
const matRho = m => (m.mass_density != null ? m.mass_density : (m.unit_weight ?? 24) / G0);
const roarkJ = (b, h) => { const a = Math.max(b, h) / 2, c = Math.min(b, h) / 2; return a * c ** 3 * (16 / 3 - 3.36 * (c / a) * (1 - c ** 4 / (12 * a ** 4))); };

export function ccBars(dims) {
  const n3 = Math.trunc(dims.n_bars_3 || 0), n2 = Math.trunc(dims.n_bars_2 || 0);
  if (!n3 || !n2) return [];
  const c = dims.cover, x0 = -dims.b / 2 + c, x1 = dims.b / 2 - c, y0 = -dims.h / 2 + c, y1 = dims.h / 2 - c;
  const key = new Map(), r12 = v => Math.round(v * 1e12) / 1e12;
  const add = (x, y) => key.set(`${r12(x)},${r12(y)}`, [r12(x), r12(y)]);
  for (let i = 0; i < n3; i++) { const x = x0 + (x1 - x0) * i / (n3 - 1); add(x, y0); add(x, y1); }
  for (let j = 0; j < n2; j++) { const y = y0 + (y1 - y0) * j / (n2 - 1); add(x0, y); add(x1, y); }
  return [...key.values()].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
}

/** Gross parts (mirror of composite_col.parts). */
export function ccParts(typ, g) {
  if (typ === "SRC_I") {
    const { b, h, d, bf, tf, tw } = g;
    const As = 2 * bf * tf + (d - 2 * tf) * tw;
    const Is33 = (bf * d ** 3 - (bf - tw) * (d - 2 * tf) ** 3) / 12, Is22 = (2 * tf * bf ** 3 + (d - 2 * tf) * tw ** 3) / 12;
    const Js = (2 * bf * tf ** 3 + (d - tf) * tw ** 3) / 3;
    const bars = ccBars(g), bd = g.bar_d || 0, ab = Math.PI * bd ** 2 / 4;
    const Asr = ab * bars.length;
    const Isr33 = bars.reduce((s, [, y]) => s + (ab * y * y + ab * bd ** 2 / 16), 0);
    const Isr22 = bars.reduce((s, [x]) => s + (ab * x * x + ab * bd ** 2 / 16), 0);
    const Ag = b * h;
    return { Ag, As, Asr, Ac: Ag - As - Asr, Is33, Is22, Isr33, Isr22, Ic33: b * h ** 3 / 12 - Is33 - Isr33, Ic22: h * b ** 3 / 12 - Is22 - Isr22,
      Js, Jc: roarkJ(b, h), b, h, n_bars: bars.length, bars };
  }
  if (typ === "CFT_rect") {
    const { d, b, t } = g, bi = b - 2 * t, di = d - 2 * t;
    return { Ag: b * d, As: b * d - bi * di, Asr: 0, Ac: bi * di, Is33: (b * d ** 3 - bi * di ** 3) / 12, Is22: (d * b ** 3 - di * bi ** 3) / 12,
      Isr33: 0, Isr22: 0, Ic33: bi * di ** 3 / 12, Ic22: di * bi ** 3 / 12, Js: 4 * ((b - t) * (d - t)) ** 2 * t / (2 * ((b - t) + (d - t))),
      Jc: roarkJ(bi, di), b, h: d, n_bars: 0, bars: [] };
  }
  const { D, t } = g, Di = D - 2 * t, Is = Math.PI * (D ** 4 - Di ** 4) / 64, Ic = Math.PI * Di ** 4 / 64;
  return { Ag: Math.PI * D * D / 4, As: Math.PI * (D * D - Di * Di) / 4, Asr: 0, Ac: Math.PI * Di * Di / 4, Is33: Is, Is22: Is, Isr33: 0, Isr22: 0,
    Ic33: Ic, Ic22: Ic, Js: 2 * Is, Jc: 2 * Ic, b: D, h: D, n_bars: 0, bars: [] };
}

export function ccCoefficient(typ, code, p) {
  if (code === "EC4") return 0.6;
  const ratio = (p.As + p.Asr) / p.Ag;
  return typ === "SRC_I" ? Math.min(0.25 + 3 * ratio, 0.7) : Math.min(0.45 + 3 * ratio, 0.9);
}

/** Validation message ("" = ok) — mirror of composite_col.validate_dims. */
export function ccCheck(typ, g, mats) {
  for (const [k, , opt] of DIMS[typ]) if (!opt && !(g[k] > 0)) return `Enter ${k} (> 0).`;
  if (!g.concrete || !(mats || {})[g.concrete]) return "Pick the concrete material.";
  if (typ === "SRC_I") {
    if (g.d >= g.h || g.bf >= g.b) return "The steel shape must fit inside the concrete.";
    if (2 * g.tf >= g.d || g.tw >= g.bf) return "Inconsistent steel I dimensions.";
    const nb = [g.n_bars_3 || 0, g.n_bars_2 || 0];
    if ((nb[0] || nb[1]) && !(g.bar_d > 0 && g.cover > 0)) return "Rebar needs a bar diameter and cover.";
    if ((nb[0] || nb[1]) && Math.min(...nb) < 2) return "Bars per face must be ≥ 2 (corner bars).";
    if ((nb[0] || nb[1]) && 2 * g.cover >= Math.min(g.b, g.h)) return "Cover too large.";
  } else if (typ === "CFT_rect") { if (2 * g.t >= Math.min(g.d, g.b)) return "Wall thicker than half the tube."; }
  else if (2 * g.t >= g.D) return "Wall thicker than the radius.";
  return "";
}

/** Transformed properties (mirror of composite_col.properties). */
export function ccProps(typ, dims, steel, conc, rebar) {
  rebar = rebar || steel;
  const code = dims.code || "AISC360", p = ccParts(typ, dims);
  const Es = +steel.E, Ec = +conc.E, Er = +rebar.E, C = ccCoefficient(typ, code, p);
  const rb = typ === "SRC_I" && code === "AISC360" ? 0.5 : 1;
  const EI33 = Es * p.Is33 + rb * Er * p.Isr33 + C * Ec * p.Ic33, EI22 = Es * p.Is22 + rb * Er * p.Isr22 + C * Ec * p.Ic22;
  const EA = Es * p.As + Ec * p.Ac + Er * p.Asr, A = EA / Es;
  const wReal = steel.unit_weight * p.As + conc.unit_weight * p.Ac + rebar.unit_weight * p.Asr;
  const mReal = matRho(steel) * p.As + matRho(conc) * p.Ac + matRho(rebar) * p.Asr;
  return { A, I33: EI33 / Es, I22: EI22 / Es, J: p.Js + matG(conc) / matG(steel) * p.Jc, b: p.b, h: p.h, EA, EI33, EI22, C, code,
    mod_weight: steel.unit_weight > 0 ? wReal / (steel.unit_weight * A) : 1, mod_mass: matRho(steel) > 0 ? mReal / (matRho(steel) * A) : 1, parts: p };
}

/** Section dict (FrameSection.to_dict key order) or { error }. */
export function ccSection(name, steelName, typ, dims, mats) {
  const msg = ccCheck(typ, dims, mats);
  if (msg) return { error: msg };
  const pr = ccProps(typ, dims, mats[steelName], mats[dims.concrete], mats[dims.rebar || steelName]);
  return { name, material: steelName, A: pr.A, I33: pr.I33, I22: pr.I22, J: pr.J, b: pr.b, h: pr.h, mod_A: 1, mod_I33: 1, mod_I22: 1, mod_J: 1,
    As2: null, As3: null, shear_deformation: false, mod_As2: 1, mod_As3: 1, mod_mass: pr.mod_mass, mod_weight: pr.mod_weight, shape: typ, dims: { ...dims } };
}

/* ---- secpreview geometry (concrete outline + steel + bars) */
const rect = (x0, x1, y0, y1) => [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
const iPoly = (d, bf, tf, tw) => [[-bf / 2, -d / 2], [bf / 2, -d / 2], [bf / 2, -d / 2 + tf], [tw / 2, -d / 2 + tf], [tw / 2, d / 2 - tf], [bf / 2, d / 2 - tf],
  [bf / 2, d / 2], [-bf / 2, d / 2], [-bf / 2, d / 2 - tf], [-tw / 2, d / 2 - tf], [-tw / 2, -d / 2 + tf], [-bf / 2, -d / 2 + tf]];
const circ = (r, n = 48) => Array.from({ length: n }, (_, k) => [r * Math.cos(2 * Math.PI * k / n), r * Math.sin(2 * Math.PI * k / n)]);
/** {polys, rebar, dims, label, shape} or null — read by js/secpreview.js spvGeometry. */
export function ccGeometry(sec) {
  if (!ccIs(sec)) return null;
  const g = sec.dims, typ = sec.shape;
  const ok = (DIMS[typ] || []).every(([k, , opt]) => opt || g[k] > 0);
  if (!ok) return null;
  const polys = [], rebar = [], dims = [];
  if (typ === "SRC_I") {
    polys.push({ pts: rect(-g.b / 2, g.b / 2, -g.h / 2, g.h / 2), hole: false }, { pts: iPoly(g.d, g.bf, g.tf, g.tw), hole: true });
    for (const [x, y] of ccBars(g)) rebar.push({ x, y, r: (g.bar_d || 0) / 2 });
    dims.push({ dir: "h", a: -g.b / 2, b: g.b / 2, at: g.h / 2, side: 1, sym: "b", v: g.b }, { dir: "v", a: -g.h / 2, b: g.h / 2, at: -g.b / 2, side: -1, sym: "h", v: g.h },
      { dir: "v", a: -g.d / 2, b: g.d / 2, at: g.b / 2, side: 1, sym: "d", v: g.d });
  } else if (typ === "CFT_rect") {
    const e = Math.min(g.b, g.d) * 0.006;
    polys.push({ pts: rect(-g.b / 2, g.b / 2, -g.d / 2, g.d / 2), hole: false }, { pts: rect(-g.b / 2 + g.t, g.b / 2 - g.t, -g.d / 2 + g.t, g.d / 2 - g.t), hole: true },
      { pts: rect(-g.b / 2 + g.t + e, g.b / 2 - g.t - e, -g.d / 2 + g.t + e, g.d / 2 - g.t - e), hole: false });
    dims.push({ dir: "h", a: -g.b / 2, b: g.b / 2, at: g.d / 2, side: 1, sym: "b", v: g.b }, { dir: "v", a: -g.d / 2, b: g.d / 2, at: -g.b / 2, side: -1, sym: "d", v: g.d });
  } else {
    const e = g.D * 0.006;
    polys.push({ pts: circ(g.D / 2), hole: false }, { pts: circ(g.D / 2 - g.t), hole: true }, { pts: circ(g.D / 2 - g.t - e), hole: false });
    dims.push({ dir: "h", a: -g.D / 2, b: g.D / 2, at: g.D / 2, side: 1, sym: "D", v: g.D });
  }
  return { shape: typ, label: CC_LABEL[typ], polys, rebar, dims, sc: [0, 0] };
}

/* ================================================================
   Composite Column Section Data dialog (edits draft D; done(name))
   ================================================================ */
export function editCompositeSection(sky, D, name, { done, typ0 } = {}) {
  const src = name ? D.sections[name] : null;
  const steelMats = () => Object.entries(D.materials || {}).filter(([, m]) => ["steel", "coldformed", "other"].includes(m.material_type || "concrete")).map(([n]) => [n, n]);
  const concMats = () => Object.entries(D.materials || {}).filter(([, m]) => (m.material_type || "concrete") === "concrete").map(([n]) => [n, n]);
  const allMats = () => Object.keys(D.materials || {}).map(n => [n, n]);
  const typ = { v: src ? src.shape : (typ0 || "CFT_rect") };
  const nm0 = name || wbcUnique(D.sections || {}, typ.v === "SRC_I" ? "SRC1" : "CFT1");
  const st = { name: nm0, steel: src ? src.material : ((steelMats()[0] || allMats()[0] || [""])[0]) };
  const dims = src ? wbcClone(src.dims) : { ...DEF[typ.v], concrete: (concMats()[0] || allMats()[0] || [""])[0] };
  const F = wbcForm();
  const box = wbcEl("div", { class: "wbc-dims", id: "wbcDims" });
  const out = wbcEl("div", { class: "wbc-out", id: "wbcOut" });
  let pv = null;
  const current = () => { const s = ccSection(st.name, st.steel, typ.v, dims, D.materials || {}); return s.error ? null : s; };
  const paint = () => {
    out.textContent = "";
    const s = ccSection(st.name, st.steel, typ.v, dims, D.materials || {});
    if (s.error) { out.appendChild(wbcEl("p", { class: "dk-err", role: "alert", id: "wbcErr", text: s.error })); pv && pv.update(); return; }
    const m = D.materials, pr = ccProps(typ.v, dims, m[st.steel], m[dims.concrete], m[dims.rebar || st.steel]);
    const tb = wbcEl("table", { class: "spv-props", id: "wbcProps" });
    const row = (k, v, key) => tb.appendChild(wbcEl("tr", { "data-prop": key || "" }, [wbcEl("th", { text: k }), wbcEl("td", { text: v })]));
    const coefLbl = pr.code === "EC4" ? "Ke (EN 1994-1-1 6.7.3.3)" : (typ.v === "SRC_I" ? "C1 (AISC I2.1b)" : "C3 (AISC I2.2b)");
    row(coefLbl, String(+pr.C.toPrecision(5)), "C");
    row(`EI_eff,33 (${wbcU.label("force")}·${wbcU.label("dim")}²)`, (pr.EI33 * wbcU.factor("force") * wbcU.factor("dim") ** 2).toExponential(4), "EI33");
    row(`EI_eff,22 (${wbcU.label("force")}·${wbcU.label("dim")}²)`, (pr.EI22 * wbcU.factor("force") * wbcU.factor("dim") ** 2).toExponential(4), "EI22");
    row(`Transformed A (${wbcU.label("area")})`, wbcU.sci("area", pr.A, 4), "A");
    row(`I33 = EI/Es (${wbcU.label("inertia")})`, wbcU.sci("inertia", pr.I33, 4), "I33");
    row(`I22 = EI/Es (${wbcU.label("inertia")})`, wbcU.sci("inertia", pr.I22, 4), "I22");
    row(`J (${wbcU.label("inertia")})`, wbcU.sci("inertia", pr.J, 4), "J");
    row("Steel / bars / concrete area", `${wbcU.sci("area", pr.parts.As, 3)} / ${wbcU.sci("area", pr.parts.Asr, 3)} / ${wbcU.sci("area", pr.parts.Ac, 3)} ${wbcU.label("area")}`);
    row("Weight / mass multipliers", `${pr.mod_weight.toFixed(4)} / ${pr.mod_mass.toFixed(4)}`, "mod_weight");
    out.appendChild(tb);
    pv && pv.update();
  };
  const rebuild = () => {
    F.forget(box);
    box.textContent = "";
    const rows = DIMS[typ.v].map(([k, lbl, opt]) => F.num(dims, k, { label: lbl, kind: "dim", id: "wbcDim_" + k, ...(opt ? { min: 0, allowNull: true, nullText: "none" } : { gt: 0 }),
      write: v => { if (v == null) delete dims[k]; else dims[k] = v; }, onChange: paint }));
    if (typ.v === "SRC_I") rows.push(
      F.num(dims, "n_bars_3", { label: "Bars along faces ∥ 3", int: true, min: 0, id: "wbcN3", write: v => { if (!v) delete dims.n_bars_3; else dims.n_bars_3 = v; }, onChange: paint }),
      F.num(dims, "n_bars_2", { label: "Bars along faces ∥ 2", int: true, min: 0, id: "wbcN2", write: v => { if (!v) delete dims.n_bars_2; else dims.n_bars_2 = v; }, onChange: paint }));
    box.appendChild(wbcGroup("Dimensions", rows));
    paint();
  };
  const body = [
    wbcGroup("General", [
      F.text(st, "name", { label: "Section Name", id: "wbcName", validate: v => (v !== name && (D.sections || {})[v]) ? `“${v}” is already used — pick another name.` : "" }),
      F.select(typ, "v", CC_TYPES.map(t => [t, CC_LABEL[t]]), { label: "Section Type", id: "wbcType",
        write: v => { typ.v = v; for (const k of Object.keys(dims)) if (!["concrete", "rebar", "code"].includes(k)) delete dims[k]; Object.assign(dims, DEF[v]); }, onChange: rebuild }),
    ]),
    wbcGroup("Materials and Code", [
      F.select(st, "steel", () => steelMats().length ? steelMats() : allMats(), { label: "Steel (section material)", id: "wbcSteel", onChange: paint }),
      F.select(dims, "concrete", () => concMats().length ? concMats() : allMats(), { label: "Concrete", id: "wbcConc", onChange: paint }),
      F.select(dims, "rebar", () => [["", "same as steel"], ...allMats()], { label: "Rebar", id: "wbcRebar", read: () => dims.rebar || "",
        write: v => { if (v) dims.rebar = v; else delete dims.rebar; }, onChange: paint }),
      F.select(dims, "code", [["AISC360", "AISC 360-16 (C1 / C3)"], ["EC4", "EN 1994-1-1 (Ke = 0.6)"]], { label: "Effective Stiffness", id: "wbcCode",
        read: () => dims.code || "AISC360", write: v => { if (v === "AISC360") delete dims.code; else dims.code = v; }, onChange: paint }),
    ]),
    box,
    wbcGroup("Transformed Properties (to E_s)", [out], { note: "A = ΣEA/E_s, I = EI_eff/E_s, J = J_s + (G_c/G_s)·J_c; self-weight and mass use the real steel + concrete." }),
  ];
  pv = wbcSpvFrame({ model: D, name: () => st.name, section: () => current() });
  rebuild();
  return wbcDialog({
    id: "wbCompositeData", title: "Composite Column Section Data", iconId: "exleaf-frames", size: "lg", form: F, body: wbcSpvSplit(body, pv),
    intro: name ? null : "New composite column section (steel + concrete, transformed effective stiffness).",
    onOk: () => {
      const s = ccSection(st.name.trim(), st.steel, typ.v, dims, D.materials || {});
      if (s.error) return s.error;
      if (name && !F.touched) { done && done(name); return; }
      D.sections = D.sections || {};
      if (name && st.name.trim() !== name) {
        delete D.sections[name];
        for (const m of D.members || []) if (m.section === name) m.section = st.name.trim();
      }
      D.sections[st.name.trim()] = s;
      done && done(st.name.trim());
    },
  });
}

/** Standalone entry (own draft; commits on OK). */
export function openComposite(sky, name) {
  const D = wbcDraft(sky);
  if (!Object.keys(D.materials || {}).length) { sky.toast && sky.toast("Composite Column", "Define a steel and a concrete material first.", "warn"); return null; }
  return editCompositeSection(sky, D, name && ccIs((D.sections || {})[name]) ? name : null, { done: () => wbcCommit(sky, D) });
}

export function initComposite(sky) {
  if (!sky.menu) return;
  sky.menu.add("Define/Section Properties", { label: "Composite Column Sections…", act: "def-wb-composite", hint: "SRC / CFT, AISC I2 / EC4 EI_eff",
    before: "Section Designer…", fn: () => openComposite(sky) });
  sky.wbComposite = { open: n => openComposite(sky, n), edit: (D, n, o) => editCompositeSection(sky, D, n, o), ccSection, ccProps, ccIs };
}

export default { CC_TYPES, ccIs, ccProps, ccSection, ccGeometry, editCompositeSection, openComposite, initComposite };
