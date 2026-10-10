/* SkyFrame mock (?mock=1) — PT tendons + hyperstatic cases (CONTRACT
   "Post-tensioning tendons (as loads) and hyperstatic case").

   mockValidateTendons(model)      mirrors the backend ValueErrors of
                                   POST /api/model for "tendons" /
                                   "hyperstatic_cases" (null = accepted).
   mockAugmentTendons(model, res)  adds results["tendons"] (P(x) after
                                   friction / wobble / anchor set /
                                   long-term losses, equivalent loads) and
                                   results["hyperstatic"] (continuous-beam
                                   estimate: primary -P e, secondary linear
                                   between supports, self-equilibrating
                                   secondary reactions). */

import { canonical as tgCanonical, tendonReport as tgReport, forceProfile as tgProfile,
  materialE as tgMaterialE, frameChain as tgFrameChain, chainHyperstatic as tgHyper,
  LOSS_KEYS as TG_LOSS_KEYS, JACKING_ENDS as TG_JACKING_ENDS, LIBRARY_TENDON_E as TG_LIB } from "./tendon_geom.js";

const isNum = v => typeof v === "number" && isFinite(v);
const COMP = ["N", "V2", "V3", "T", "M2", "M3"];

export function mockValidateTendons(model) {
  if (!model) return null;
  const tds = model.tendons, hcs = model.hyperstatic_cases;
  if (tds == null && hcs == null) return null;
  if (tds != null && !Array.isArray(tds)) return "tendons must be a list";
  const mem = new Set((model.members || []).map(m => m.uid));
  const shl = new Set((model.shells || []).map(s => s.uid));
  const seen = new Set();
  for (const t of tds || []) {
    const uid = t && t.uid;
    if (typeof uid !== "string" || !uid) return "tendon: uid must be a non-empty string";
    const tag = `Tendon '${uid}'`;
    if (seen.has(uid)) return `Duplicate tendon uid '${uid}'`;
    seen.add(uid);
    const c = tgCanonical(t);
    if (c.points.length < 2) return `${tag}: points must list >= 2 [x, y, z] points`;
    if (c.points.some(p => !p.every(isNum))) return `${tag}: points must be finite`;
    if (c.sags.length !== c.points.length - 1 || !c.sags.every(isNum)) return `${tag}: sags must list one value per segment (${c.points.length - 1})`;
    for (let k = 0; k + 1 < c.points.length; k++)
      if (Math.hypot(...c.points[k + 1].map((v, i) => v - c.points[k][i])) < 1e-9) return `${tag}: segment ${k} has zero length`;
    if (!(isNum(c.area) && c.area > 0)) return `${tag}: area must be > 0`;
    if (!(isNum(c.jacking_stress) && c.jacking_stress > 0)) return `${tag}: jacking_stress must be > 0`;
    if (t.jacking_end != null && !TG_JACKING_ENDS.includes(t.jacking_end)) return `${tag}: jacking_end must be one of start, end, both`;
    const bad = Object.keys(t.losses || {}).filter(k => !TG_LOSS_KEYS.includes(k));
    if (bad.length) return `${tag}: unknown losses keys ${JSON.stringify(bad)}`;
    for (const k of TG_LOSS_KEYS) if (!(c.losses[k] >= 0)) return `${tag}: losses.${k} must be >= 0`;
    if (c.losses.long_term_fraction >= 1) return `${tag}: losses.long_term_fraction must be < 1`;
    if (!(Number.isInteger(c.n_sub) && c.n_sub >= 1 && c.n_sub <= 200)) return `${tag}: n_sub must be an integer in 1..200`;
    if (!c.pattern || !(model.patterns || {})[c.pattern]) return `${tag}: unknown load pattern '${c.pattern}'`;
    if (!(model.materials || {})[c.material] && !TG_LIB[c.material]) return `${tag}: unknown material '${c.material}'`;
    if (!c.host.length) return `${tag}: host must be a member/shell uid or a non-empty list of uids`;
    const frame = c.host.every(h => mem.has(h)), shell = c.host.every(h => shl.has(h));
    if (!frame && !shell) return `${tag}: host must be all frame-member uids or all shell-region uids (got ${JSON.stringify(c.host)})`;
    if (frame) {
      for (const m of model.members || [])
        if (c.host.includes(m.uid) && (m.axial_limit || "both") !== "both") return `${tag}: host member '${m.uid}' is axial-only`;
      const ch = tgFrameChain(model, c.host);
      if (!ch.error) {
        for (const p of c.points) {
          const ok = ch.items.some(it => {
            const ax = it.b.map((v, i) => v - it.a[i]);
            const t2 = ax.reduce((a, v, i) => a + v * (p[i] - it.a[i]), 0) / (it.L * it.L);
            return t2 >= -1e-6 && t2 <= 1 + 1e-6;
          });
          if (!ok) return `${tag}: tendon point (${p.map(v => +v.toFixed(3)).join(", ")}) projects onto no host member axis`;
        }
      }
    }
  }
  if (hcs != null && (typeof hcs !== "object" || Array.isArray(hcs))) return "hyperstatic_cases must be an object";
  const tpats = new Set((tds || []).map(t => t.pattern));
  const others = new Set([...Object.keys(model.cases || {}), ...Object.keys(model.rs_cases || {}),
    ...Object.keys(model.th_cases || {}), ...Object.keys(model.pushover_cases || {}),
    ...Object.keys(model.buckling_cases || {}), ...Object.keys(model.staged_cases || {}),
    ...Object.keys(model.nonlinear_static_cases || {}), ...Object.keys(model.steady_state_cases || {}),
    ...Object.keys(model.psd_cases || {})]);
  for (const [n, h] of Object.entries(hcs || {})) {
    if (!h || typeof h.case !== "string") return `Hyperstatic case '${n}': needs {"case": <static case name>}`;
    const extra = Object.keys(h).filter(k => k !== "case");
    if (extra.length) return `Hyperstatic case '${n}': unknown keys ${JSON.stringify(extra)}`;
    if (others.has(n)) return `Hyperstatic case '${n}': name already used by another case`;
    const sc = (model.cases || {})[h.case];
    if (!sc) return `Hyperstatic case '${n}': unknown static case '${h.case}'`;
    if (!Object.keys(sc.patterns || {}).some(p => tpats.has(p))) return `Hyperstatic case '${n}': static case '${h.case}' applies no tendon load pattern`;
  }
  return null;
}

/** Nearest results node tag to a point (null when none within tol). */
function nodeTag(nodes, p, tol = 1e-3) {
  let best = null, bd = tol;
  for (const [t, q] of Object.entries(nodes || {})) {
    const d = Math.hypot(q[0] - p[0], q[1] - p[1], q[2] - p[2]);
    if (d < bd) { bd = d; best = t; }
  }
  return best;
}
/** The support node under a chain node (same plan position, lowest support). */
function supportUnder(res, p) {
  let best = null;
  for (const t of res.supports || []) {
    const q = (res.nodes || {})[t];
    if (!q || Math.hypot(q[0] - p[0], q[1] - p[1]) > 1e-3) continue;
    if (!best || q[2] < res.nodes[best][2]) best = t;
  }
  return best || nodeTag(res.nodes, p);
}
const interpAt = (xs, vs, x) => {
  if (x <= xs[0]) return vs[0];
  for (let i = 1; i < xs.length; i++) if (x <= xs[i]) {
    const t = (x - xs[i - 1]) / ((xs[i] - xs[i - 1]) || 1);
    return vs[i - 1] + t * (vs[i] - vs[i - 1]);
  }
  return vs[vs.length - 1];
};

export function mockAugmentTendons(model, res) {
  if (!model || !res) return res;
  const tds = (Array.isArray(model.tendons) ? model.tendons : []).map(tgCanonical).filter(Boolean);
  if (tds.length) {
    res.tendons = {};
    for (const c of tds) {
      try { res.tendons[c.uid] = tgReport(c, tgMaterialE(model, c.material)); }
      catch (e) { /* invalid geometry: leave it out */ }
    }
  }
  const hcs = model.hyperstatic_cases || {};
  const status = res.case_status || {};
  for (const [name, h] of Object.entries(hcs)) {
    if (status[name] === "not_run") continue;
    const sc = (model.cases || {})[h.case];
    if (!sc) continue;
    if (status[h.case] === "not_run") status[h.case] = "run_as_dependency";
    const caseRes = (res.cases || {})[h.case] || {};
    const pats = sc.patterns || {};
    const used = tds.filter(c => pats[c.pattern] != null && pats[c.pattern] !== 0);
    const out = { case: h.case, tendon_patterns: {}, reactions: {}, base: { FX: 0, FY: 0, FZ: 0, MX: 0, MY: 0, MZ: 0 }, members: {} };
    for (const c of used) {
      const scale = +pats[c.pattern];
      out.tendon_patterns[c.pattern] = scale;
      const hostsFrame = c.host.every(u => (model.members || []).some(m => m.uid === u));
      if (!hostsFrame) continue;
      const ch = tgFrameChain(model, c.host);
      if (ch.error) continue;
      const prof = tgProfile(c, tgMaterialE(model, c.material));
      const hy = tgHyper(ch, c, prof, scale);
      // members (stations of the static case when present, else 11)
      for (const it of ch.items) {
        const mm = (model.members || []).find(m => m.uid === it.uid);
        const st = ((caseRes.member_stations || {})[it.uid]) || null;
        const xs = st && Array.isArray(st.x) ? st.x.slice() : Array.from({ length: 11 }, (_, i) => it.L * i / 10);
        const rev = mm && Math.hypot(...mm.pi.map((v, i) => v - it.a[i])) > 1e-6;   // member runs against the chain
        const cx = x => it.x0 + (rev ? it.L - x : x);
        const prim = xs.map(x => interpAt(hy.xs, hy.primary, cx(x)));
        const secd = xs.map(x => interpAt(hy.xs, hy.secondary, cx(x)));
        const prev = out.members[it.uid];
        const zero = () => xs.map(() => 0);
        const blk = prev || { x: xs, hosts_tendon: true,
          total: Object.fromEntries(COMP.map(k => [k, zero()])),
          primary: Object.fromEntries(COMP.map(k => [k, zero()])),
          secondary: Object.fromEntries(COMP.map(k => [k, zero()])) };
        // axial: primary N = -P (compression), secondary ~ 0 for a free-sliding beam
        const Pn = xs.map(x => {
          const sx = cx(x);
          let best = 0, bd = Infinity;
          prof.points.forEach((p, i) => { const d = Math.abs((p[0] - it.a[0]) * (it.b[0] - it.a[0]) + (p[1] - it.a[1]) * (it.b[1] - it.a[1]) + (p[2] - it.a[2]) * (it.b[2] - it.a[2])) / it.L - (sx - it.x0); if (Math.abs(d) < bd) { bd = Math.abs(d); best = prof.P[i]; } });
          return -scale * best;
        });
        prim.forEach((v, i) => { blk.primary.M3[i] += v; blk.secondary.M3[i] += secd[i]; blk.primary.N[i] += Pn[i]; });
        // V2 from the moment gradient
        const grad = arr => arr.map((_, i) => {
          const a = Math.max(0, i - 1), b = Math.min(arr.length - 1, i + 1);
          return (arr[b] - arr[a]) / ((xs[b] - xs[a]) || 1);
        });
        const g1 = grad(prim), g2 = grad(secd);
        g1.forEach((v, i) => { blk.primary.V2[i] += v; blk.secondary.V2[i] += g2[i]; });
        for (const k of COMP) blk.total[k] = blk.primary[k].map((v, i) => v + blk.secondary[k][i]);
        out.members[it.uid] = blk;
      }
      // secondary reactions at the supports under the chain nodes
      const nodesXYZ = [ch.items[0].a, ...ch.items.map(it => it.b)];
      nodesXYZ.forEach((p, i) => {
        const tag = supportUnder(res, p) || `n${i}`;
        const R = out.reactions[tag] || [0, 0, 0, 0, 0, 0];
        R[2] += hy.reactions[i];
        out.reactions[tag] = R;
      });
    }
    for (const R of Object.values(out.reactions)) {
      out.base.FZ += R[2];
    }
    res.hyperstatic = res.hyperstatic || {};
    res.hyperstatic[name] = out;
  }
  if (Object.keys(hcs).length) res.case_status = status;
  return res;
}
