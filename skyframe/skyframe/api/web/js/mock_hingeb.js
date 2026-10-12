/* SkyFrame mock backend — Wave B hinges (?mock=1): POST /api/model
   validation mirror for wall hinges, POST /api/hinges/auto-preview and
   /surface-preview mirrors, and plausible result histories for auto,
   parametric PMM / M2M3 and wall fiber hinges in pushover / nonlinear
   static / nonlinear TH results.  The generator is an APPROXIMATION of
   skyframe/design/hinge_tables.py (meta.mock = true); the engine is the
   reference. */

import { hbMockGenerate as mhbGen, hbSpecBackbone as mhbBB, hbEllipseAt as mhbEllipse, hbValidateWall as mhbValidateWall,
  hbNormalizeWall as mhbNormWall, hbPierLabels as mhbPiers, hbNeedsAxial as mhbNeedsAxial } from "./hingeb_core.js";

const LEGACY = { "A-B": "elastic", "B-IO": "IO", "IO-LS": "LS", "LS-CP": "CP", ">CP": "collapse", "C-D": "collapse", "D-E": "collapse", ">E": "collapse" };
const hsh = s => { let h = 2166136261; for (const c of String(s)) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return ((h >>> 0) % 1000) / 1000; };

/** POST /api/model mirror → message | null. */
export function hbMockValidate(model) {
  if (!model || model.wall_hinges == null) return null;
  if (typeof model.wall_hinges !== "object" || Array.isArray(model.wall_hinges)) return "wall_hinges must be an object";
  for (const [lab, d] of Object.entries(model.wall_hinges)) {
    const e = mhbValidateWall(lab, d, model);
    if (e) return e;
  }
  return null;
}

/** Mock P_G of a member: tributary share of the gravity (rough). */
function mockPG(model, mm) {
  if (mm.kind !== "column") return 0;
  const H = Math.max(1e-6, ...(model.members || []).flatMap(x => [x.pi[2], x.pj[2]]));
  const z = Math.min(mm.pi[2], mm.pj[2]);
  return 150 * (1 + 3 * (1 - z / H));
}

/** POST /api/hinges/auto-preview mirror. */
export function hbMockPreview(model, body) {
  const prop = body.property || {};
  const out = [];
  for (const uid of body.members || []) {
    const mm = (model.members || []).find(x => x.uid === uid);
    if (!mm) throw Object.assign(new Error(`unknown member '${uid}'`), { status: 400 });
    const specs = mhbGen(model, mm, prop.auto, body.P_G || 0);
    out.push({ uid, P_G: body.P_G || 0, reason: specs.length ? "" : "not sized",
      hinges: specs.map(s => ({ type: s.type, thy: s.thy, meta: s.meta, acceptance_negative: s.neg ? { IO: s.neg.IO, LS: s.neg.LS, CP: s.neg.CP } : null,
        property: { type: s.type, backbone: s.neg ? { positive: mhbBB(s), negative: mhbBB(s.neg) } : mhbBB(s),
          scale: { yield_value: 1.0, yield_deformation: null }, acceptance: { IO: s.IO, LS: s.LS, CP: s.CP },
          hysteresis: s.neg ? "takeda" : (prop.hysteresis || "kinematic"), drop_strength: "drops", k_elastic: null, hysteresis_params: null } })) });
  }
  return { previews: out };
}
export const hbMockSurface = (prop, P) => mhbEllipse(prop.surface, P || 0);

/* ---- histories: bilinear-with-hardening response to a drift demand ---- */
function history(My, ke, kh, demand, classify) {
  const rot = [], moment = [], rot_plastic = [], hinge_state = [], state = [];
  for (const d of demand) {
    const dy = My / ke;
    const F = Math.abs(d) <= dy ? ke * d : Math.sign(d) * (My + kh * (Math.abs(d) - dy));
    const dp = Math.max(0, Math.abs(d) - Math.abs(F) / ke);
    const st = classify(dp);
    rot.push(d); moment.push(F); rot_plastic.push(dp); hinge_state.push(st); state.push(LEGACY[st]);
  }
  return { rot, moment, rot_plastic, hinge_state, state };
}
const bands = (IO, LS, CP, a, b) => dp => dp <= 1e-12 ? "A-B" : dp <= IO ? "B-IO" : dp <= LS ? "IO-LS" : dp <= CP ? "LS-CP"
  : (a != null && dp <= a) ? ">CP" : (b != null && dp <= b) ? "C-D" : ">E";

function entries(model, thetas) {
  const out = [];
  const props = model.hinge_properties || {};
  for (const mm of model.members || []) {
    if (!Array.isArray(mm.hinges)) continue;
    const L = Math.max(1e-6, Math.hypot(mm.pj[0] - mm.pi[0], mm.pj[1] - mm.pi[1], mm.pj[2] - mm.pi[2]));
    for (const h of mm.hinges) {
      const p = props[h.property];
      if (!p || !["auto", "PMM", "M2M3"].includes(p.type)) continue;
      const d = h.relative_distance;
      const end = d === 0 ? "i" : d === 1 ? "j" : String(d);
      const amp = (0.85 + 0.3 * hsh(mm.uid + "@" + d)) * (d <= 0.5 ? 1 : -1);
      if (p.type === "auto") {
        const PG = p.auto.P != null ? p.auto.P : (mhbNeedsAxial(p.auto.component) ? mockPG(model, mm) : 0);
        for (const s of mhbGen(model, mm, p.auto, PG)) {
          const ke = 10 * s.My / Math.max(s.thy, 1e-12);          // 10 x member stiffness (My / thy)
          const dy = s.My / ke;
          const peak = Math.max(1e-12, ...thetas.map(Math.abs));
          const f = Math.max(1.5 * (dy + 0.5 * s.a) / peak, 1e-9);
          const hist = history(s.My, ke, 0.03 * s.My / Math.max(s.a, 1e-9), thetas.map(t => amp * f * t), bands(s.IO, s.LS, s.CP, s.a, s.b));
          out.push({ uid: mm.uid, end, relative_distance: d, property: h.property, type: s.type, user: true, kind: "auto",
            My: s.My, thy: dy, a: s.a, b: s.b, c: s.c, IO: s.IO, LS: s.LS, CP: s.CP, k_elastic: ke, hysteresis: p.hysteresis || "kinematic",
            drop_strength: "drops", material: s.neg ? "HystereticSM" : "MultiLinear",
            envelope: { positive: [[dy, s.My], [dy + s.a, s.My * 1.03]], negative: [[dy, s.My], [dy + s.a, s.My * 1.03]] },
            auto: { ...s.meta, P_G: PG, thy: s.thy, dof: p.auto.dof }, preliminary: s.meta.preliminary, ...hist });
        }
        continue;
      }
      const P = p.type === "PMM" ? (p.axial && p.axial.source === "value" ? p.axial.value : -mockPG(model, mm)) : 0;
      const e = mhbEllipse(p.surface, P);
      const sec = (model.sections || {})[mm.section] || {};
      const mat = (model.materials || {})[sec.material] || {};
      const E = mat.E || 25e6;
      const ke3 = p.k_elastic || 10 * 6 * E * (sec.I33 || 1e-3) / L, ke2 = p.k_elastic || 10 * 6 * E * (sec.I22 || 1e-3) / L;
      const bb = p.backbone || [[0, 0], [0, 1]];
      const H = bb.length > 2 ? (bb[2][1] / bb[1][1] - 1) * e.M3y / Math.max(bb[2][0] - bb[1][0], 1e-9) : 0;
      const kt = ke3 * H / (ke3 + H || 1);
      const peak = Math.max(1e-12, ...thetas.map(Math.abs));
      const f = 2.5 * (e.M3y / ke3) / peak;
      const acc = p.acceptance || {};
      const hist = history(e.M3y, ke3, kt, thetas.map(t => amp * f * t), bands(acc.IO ?? Infinity, acc.LS ?? Infinity, acc.CP ?? Infinity));
      out.push({ uid: mm.uid, end, relative_distance: d, property: h.property, type: p.type, user: true, kind: "user_interaction",
        My: e.M3y, My2: e.M2y, My3: e.M3y, P, axial_source: p.type === "PMM" ? (p.axial || {}).source || "gravity" : "n/a",
        thy: e.M3y / ke3, a: null, b: null, c: null, IO: acc.IO ?? null, LS: acc.LS ?? null, CP: acc.CP ?? null,
        k_elastic: [ke2, ke3], hysteresis: "kinematic", drop_strength: "holds", material: "Elliptical", surface_fit: e.fit,
        rot2: hist.rot.map(() => 0), rot3: [...hist.rot], m2: hist.rot.map(() => 0), m3: [...hist.moment], ...hist });
    }
  }
  return out;
}

function wallEntries(model, thetas) {
  const wh = model.wall_hinges || {};
  const out = [];
  const labels = mhbPiers(model);
  for (const [lab, raw] of Object.entries(wh)) {
    if (!labels.includes(lab)) continue;
    const d = mhbNormWall(raw);
    const walls = (model.shells || []).filter(r => r.kind === "wall" && (r.pier || (model.auto_pier_walls ? r.uid : "")) === lab);
    for (const r of walls) {
      const zs = r.corners.map(c => c[2]), z0 = Math.min(...zs), z1 = Math.max(...zs);
      const xs = r.corners.filter(c => Math.abs(c[2] - z0) < 1e-6);
      const lw = xs.length >= 2 ? Math.hypot(xs[1][0] - xs[0][0], xs[1][1] - xs[0][1]) : 3;
      const ssec = (model.shell_sections || {})[r.section] || {};
      const t = ssec.thickness || 0.25;
      const fc = d.fc || ((model.materials || {})[ssec.material] || {}).fc || 30000;
      for (const s of model.stories || []) {
        if (!(s.elevation > z0 + 1e-6 && s.elevation <= z1 + 1e-6)) continue;
        if (d.stories && !d.stories.includes(s.name)) continue;
        const P = 300 * (1 + (z1 - s.elevation) / Math.max(z1 - z0, 1));
        const My = (d.rho_v * t * lw * d.fy * 0.4 + d.rho_b * t * d.boundary_ratio * lw * d.fy * 0.9 + P * 0.4) * lw;
        const ky = 2 * (d.fy / 2e8) / lw, lp = Math.min(d.lp_ratio * lw, 0.5 * (s.height || 3));
        const ke = My / (ky * lp) * 3;
        const peak = Math.max(1e-12, ...thetas.map(Math.abs));
        const row = d.confined ? { IO: 0.005, LS: 0.015, CP: 0.02, a: 0.015, b: 0.02, c: 0.75 } : { IO: 0.002, LS: 0.008, CP: 0.015, a: 0.008, b: 0.015, c: 0.6 };
        const hist = history(My, ke, 0.02 * ke, thetas.map(th => 3 * (My / ke) / peak * th), bands(row.IO, row.LS, row.CP, row.a, row.b));
        out.push({ uid: `${lab}@${s.name}`, end: "i", relative_distance: 0, property: lab, type: "P-M3", user: true, kind: "wall",
          pier: lab, story: s.name, region: r.uid, fiber: true, lp, kappa_y: ky, My, thy: null, ...row, axial_ratio: P / (t * lw * fc),
          shear_stress_psi: 2, table: "ASCE 41-17 Table 10-19", preliminary: true, ...hist,
          curvature: hist.rot.map(x => x / lp), axial: hist.rot.map(() => P), shear: hist.moment.map(Mv => Math.abs(Mv) / (s.height || 3)) });
      }
    }
  }
  return out;
}

/** Append the Wave B hinge entries to the mock results (in place). */
export function hbMockAugment(model, r) {
  if (!model || !r) return r;
  const props = model.hinge_properties || {};
  const special = new Set(Object.entries(props).filter(([, p]) => ["auto", "PMM", "M2M3"].includes(p.type)).map(([n]) => n));
  const hasWall = model.wall_hinges && Object.keys(model.wall_hinges).length;
  if (!special.size && !hasWall) return r;
  const keep = e => !(e && e.property && special.has(e.property));
  const H = Math.max(1e-6, ...(model.members || []).flatMap(mm => [mm.pi[2], mm.pj[2]]), ...(model.stories || []).map(s => s.elevation || 0));
  const add = (holder, th) => {
    if (!holder || !th.length) return;
    const list = [...entries(model, th), ...wallEntries(model, th)];
    holder.hinges = (holder.hinges || []).filter(keep).concat(list);
    holder.hinge_rotations = holder.hinge_rotations || {};
    for (const e of list) if (e.type !== "P" && e.type !== "V2" && e.type !== "V3")
      holder.hinge_rotations[e.uid] = Math.max(holder.hinge_rotations[e.uid] || 0, ...e.rot.map(Math.abs));
  };
  for (const [n, pd] of Object.entries(r.pushover || {})) if ((model.pushover_cases || {})[n]) add(pd, (pd.roof_drift || []).map(Math.abs));
  for (const [n, cd] of Object.entries(r.nonlinear_static || {}))
    if (cd && cd.nonlinear && (model.nonlinear_static_cases || {})[n]) add(cd.nonlinear, (((cd.nonlinear.history || {}).disp) || []).map(x => x / H));
  return r;
}
