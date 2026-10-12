/* SkyFrame — Wave C: pier / spandrel / link force diagrams on the 3D model
   (ETABS Display › Force/Stress Diagrams › Pier / Spandrel / Link Forces).

     Display › Force Diagrams › Pier / Spandrel / Link Diagrams (3D)…
                                                    act dis-wc-diag3d

   Drawn by the Viewer3D `wcOverlay` hook (one line in js/viewer3d.js), like
   the frame diagrams of js/plotfn.js:

     Piers      results.piers[case][label][story] = {P, V, M[, min]} — one
                block per story along the pier centroid line, offset in the
                wall plane (V, M) or along the wall normal (P);
     Spandrels  results.spandrels[case][label][story] = {Left, Right[, min]}
                — a beam-like diagram along the spandrel span (Left → Right
                values, linear between), offset vertically;
     Links      elastic links, F = k (u_j − u_i) on the global axes from the
                case's node_disp (static cases and single-valued combos —
                RS / TH link forces are not in the results); a diagram along
                the link (or an arrow for a zero-length link).

   Every case type with pier / spandrel results is offered (static, RS,
   time history and max/min combos — engine/cut_results.py); max/min entries
   draw the Max outline solid and the Min outline dashed. The dialog edits
   VIEW settings only (the model is never touched). Values go through
   js/units.js. */

import wcU from "./units.js";
import { el as wcEl, dkForm as wcForm, dkGroup as wcGroup, dkDialog as wcDialog, dkNote as wcNote } from "./dlgkit.js";

const KINDS = {
  pier: { label: "Piers", comps: [["V", "Shear V2 (in-plane)", "force"], ["M", "Moment M3 (in-plane)", "moment"], ["P", "Axial P (compression +)", "force"]] },
  spandrel: { label: "Spandrels", comps: [["V2", "Shear V2", "force"], ["M3", "Moment M3", "moment"], ["P", "Axial P", "force"],
    ["V3", "Shear V3", "force"], ["T", "Torsion T", "moment"], ["M2", "Moment M2", "moment"]] },
  link: { label: "Links", comps: [["0", "U1 force (global X)", "force"], ["1", "U2 force (global Y)", "force"], ["2", "U3 force (global Z)", "force"],
    ["3", "R1 moment (about X)", "moment"], ["4", "R2 moment (about Y)", "moment"], ["5", "R3 moment (about Z)", "moment"]] },
};
export const wcDiag = { on: false, kind: "pier", case: null, comp: "V", scale: 1, labels: true, fill: true, drawn: null };

const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const mul = (a, k) => [a[0] * k, a[1] * k, a[2] * k];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = a => { const l = Math.hypot(a[0], a[1], a[2]); return l > 1e-12 ? mul(a, 1 / l) : null; };
function canon(e) {           // engine/spandrels._canon: point toward +X (or +Y)
  if (e[0] < -1e-9 || (Math.abs(e[0]) <= 1e-9 && e[1] < 0)) return mul(e, -1);
  return e;
}
function wallAxes(sh) {
  const c = sh.corners || [];
  if (c.length < 3) return null;
  let n = c.length === 4 ? cross(sub(c[1], c[0]), sub(c[3], c[0])) : [0, 0, 0];
  if (c.length !== 4) for (let i = 0; i < c.length; i++) {           // Newell
    const a = c[i], b = c[(i + 1) % c.length];
    n = add(n, [(a[1] - b[1]) * (a[2] + b[2]), (a[2] - b[2]) * (a[0] + b[0]), (a[0] - b[0]) * (a[1] + b[1])]);
  }
  n = norm(n);
  if (!n || Math.abs(n[2]) > 1e-3) return null;
  const h = norm(cross([0, 0, 1], n));
  return h ? { h: canon(h), n } : null;
}

/* ---------------------------------------------------------------- data */
function storyBands(model, r) {
  const out = {};
  const sts = (model && model.stories) || [];
  for (const s of sts) {
    const top = (r && r.story_elev && r.story_elev[s.name] != null) ? +r.story_elev[s.name] : +s.elevation;
    out[s.name] = [top - (+s.height || 0), top];
  }
  return out;
}

/** Pier geometry: label → {c: plan centroid, h: in-plane axis, n, walls}. */
export function wcPierGeometry(model) {
  const out = {};
  for (const sh of (model && model.shells) || []) {
    if (sh.kind !== "wall") continue;
    const lab = sh.pier || (model.auto_pier_walls ? sh.uid : "");
    if (!lab) continue;
    const ax = wallAxes(sh);
    if (!ax) continue;
    const g = out[lab] = out[lab] || { pts: [], h: ax.h, n: ax.n };
    g.pts.push(...sh.corners);
  }
  for (const g of Object.values(out)) {
    const hs = g.pts.map(p => dot(p, g.h));
    const mid = (Math.min(...hs) + Math.max(...hs)) / 2;
    const ns = g.pts.map(p => dot(p, g.n));
    const nbar = ns.reduce((a, b) => a + b, 0) / ns.length;
    g.c = add(mul(g.h, mid), mul(g.n, nbar));       // plan point (z from story)
    g.c[2] = 0;
    g.width = Math.max(...hs) - Math.min(...hs);
  }
  return out;
}

/** Spandrel geometry: "label|story" → {e1, e3, sLo, sHi, z, nbar}. */
export function wcSpandrelGeometry(model, bands) {
  const groups = {};
  const storyAt = (obj, z) => {
    if (obj.story && bands[obj.story]) return obj.story;
    for (const [s, [lo, hi]] of Object.entries(bands)) if (z > lo - 1e-6 && z <= hi + 1e-6) return s;
    return Object.keys(bands).slice(-1)[0] || "";
  };
  for (const sh of (model && model.shells) || []) {
    if (!sh.spandrel || sh.kind !== "wall") continue;
    const ax = wallAxes(sh);
    if (!ax) continue;
    const zc = sh.corners.reduce((a, p) => a + p[2], 0) / sh.corners.length;
    const key = sh.spandrel + "|" + storyAt(sh, zc);
    const g = groups[key] = groups[key] || { label: sh.spandrel, story: storyAt(sh, zc), e1: ax.h, pts: [] };
    g.pts.push(...sh.corners);
  }
  for (const m of (model && model.members) || []) {
    if (!m.spandrel) continue;
    const d = [m.pj[0] - m.pi[0], m.pj[1] - m.pi[1], 0];
    const e = norm(d);
    if (!e) continue;
    const zc = (m.pi[2] + m.pj[2]) / 2;
    const key = m.spandrel + "|" + storyAt(m, zc);
    const g = groups[key] = groups[key] || { label: m.spandrel, story: storyAt(m, zc), e1: canon(e), pts: [] };
    g.pts.push(m.pi, m.pj);
  }
  for (const g of Object.values(groups)) {
    g.e3 = cross(g.e1, [0, 0, 1]);
    const s = g.pts.map(p => dot(p, g.e1));
    g.sLo = Math.min(...s); g.sHi = Math.max(...s);
    g.z = g.pts.reduce((a, p) => a + p[2], 0) / g.pts.length;
    g.nbar = g.pts.reduce((a, p) => a + dot(p, g.e3), 0) / g.pts.length;
  }
  return groups;
}

function nodeAt(r, p, cache) {
  const key = p.map(v => (+v).toFixed(5)).join(",");
  if (cache.has(key)) return cache.get(key);
  let best = null, bd = 1e-4;
  for (const [t, xyz] of Object.entries(r.nodes || {})) {
    const d = Math.hypot(xyz[0] - p[0], xyz[1] - p[1], xyz[2] - p[2]);
    if (d < bd) { bd = d; best = t; }
  }
  cache.set(key, best);
  return best;
}

/** Elastic link forces F = k (u_j − u_i) (global) of one signed case. */
export function wcLinkForces(model, r, caseName) {
  const cd = (r.cases || {})[caseName] || (r.combos || {})[caseName];
  if (!cd || cd.min || !cd.node_disp) return {};
  const cache = new Map();
  const out = {};
  for (const lk of (model.links || [])) {
    if ((lk.link_type || "elastic") !== "elastic") continue;
    const ti = nodeAt(r, lk.pi, cache), tj = nodeAt(r, lk.pj, cache);
    if (ti == null || tj == null) continue;
    const di = cd.node_disp[ti] || [0, 0, 0, 0, 0, 0], dj = cd.node_disp[tj] || [0, 0, 0, 0, 0, 0];
    out[lk.uid] = (lk.stiffness || []).map((k, i) => (+k || 0) * ((dj[i] || 0) - (di[i] || 0)));
  }
  return out;
}

export function wcCaseOptions(kind, r) {
  if (!r) return [];
  if (kind === "pier") return Object.keys(r.piers || {});
  if (kind === "spandrel") return Object.keys(r.spandrels || {});
  const out = [...Object.keys(r.cases || {})];
  for (const [n, c] of Object.entries(r.combos || {})) if (!c.min) out.push(n);
  return out;
}

/* ---------------------------------------------------------------- drawing */
function drawBlock(ctx, P, base, tip, value, kind, st, dashed) {
  const pb = base.map(p => { const c = P.toCam(p); return c[2] > P.near ? P.proj(c) : null; });
  const pt = tip.map(p => { const c = P.toCam(p); return c[2] > P.near ? P.proj(c) : null; });
  if (pb.some(p => !p) || pt.some(p => !p)) return false;
  const pos = value.every(v => v >= 0) ? true : value.every(v => v <= 0) ? false : (value[0] + value[value.length - 1]) >= 0;
  ctx.fillStyle = pos ? "rgba(53,181,229,0.26)" : "rgba(229,103,103,0.26)";
  ctx.strokeStyle = pos ? "rgba(53,181,229,0.95)" : "rgba(229,103,103,0.95)";
  ctx.setLineDash(dashed ? [5, 4] : []);
  if (st.fill && !dashed) {
    ctx.beginPath(); ctx.moveTo(pb[0].x, pb[0].y);
    pt.forEach(p => ctx.lineTo(p.x, p.y));
    ctx.lineTo(pb[pb.length - 1].x, pb[pb.length - 1].y); ctx.closePath(); ctx.fill();
  }
  ctx.beginPath(); pt.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
  ctx.lineTo(pb[pb.length - 1].x, pb[pb.length - 1].y);
  ctx.moveTo(pb[0].x, pb[0].y); ctx.lineTo(pt[0].x, pt[0].y);
  ctx.stroke();
  ctx.setLineDash([]);
  if (st.labels) {
    ctx.fillStyle = "rgba(232,237,243,0.92)";
    ctx.textAlign = "center"; ctx.textBaseline = "bottom";
    const seen = new Set();
    value.forEach((v, i) => {
      const txt = (dashed ? "min " : "") + wcU.fmt(kind, v, 1);
      if (seen.has(txt)) return;
      seen.add(txt);
      ctx.fillText(txt, pt[i].x, pt[i].y - 2);
    });
  }
  return true;
}

export function wcOverlay(ctx, P, viewer, sky) {
  const st = wcDiag;
  if (!st.on) return;
  const S = sky.store, r = S.results, m = S.model;
  if (!r || !m || !st.case) return;
  const def = KINDS[st.kind];
  const comp = def.comps.find(c => c[0] === st.comp) || def.comps[0];
  const qk = comp[2];
  const R = viewer.radius || 10;
  const drawn = { items: 0, kind: st.kind, case: st.case, comp: comp[0] };
  ctx.save();
  ctx.lineWidth = 1.4;
  ctx.font = "600 10px -apple-system, 'Segoe UI', sans-serif";
  let vmax = 0;
  const items = [];             // {base:[p..], dir, vals:[..], min:[..]|null}
  if (st.kind === "pier") {
    const blk = (r.piers || {})[st.case] || {};
    const geo = wcPierGeometry(m), bands = storyBands(m, r);
    for (const [lab, byStory] of Object.entries(blk)) {
      const g = geo[lab];
      if (!g) continue;
      const dir = comp[0] === "P" ? g.n : g.h;
      for (const [s, f] of Object.entries(byStory)) {
        const b = bands[s];
        if (!b) continue;
        const v = +f[comp[0]] || 0, vmin = f.min ? (+f.min[comp[0]] || 0) : null;
        items.push({ base: [[g.c[0], g.c[1], b[0]], [g.c[0], g.c[1], b[1]]], dir, vals: [v, v], min: vmin == null ? null : [vmin, vmin] });
      }
    }
  } else if (st.kind === "spandrel") {
    const blk = (r.spandrels || {})[st.case] || {};
    const geo = wcSpandrelGeometry(m, storyBands(m, r));
    for (const [lab, byStory] of Object.entries(blk)) for (const [s, e] of Object.entries(byStory)) {
      const g = geo[lab + "|" + s];
      if (!g || !e.Left || !e.Right) continue;
      const at = sv => { const p = add(mul(g.e1, sv), mul(g.e3, g.nbar)); return [p[0], p[1], g.z]; };
      const k = comp[0];
      items.push({ base: [at(g.sLo), at(g.sHi)], dir: [0, 0, 1], vals: [+e.Left[k] || 0, +e.Right[k] || 0],
        min: e.min && e.min.Left ? [+e.min.Left[k] || 0, +e.min.Right[k] || 0] : null });
    }
  } else {
    const F = wcLinkForces(m, r, st.case);
    const i = +comp[0];
    for (const lk of (m.links || [])) {
      const f = F[lk.uid];
      if (!f) continue;
      const v = f[i] || 0;
      const ax = sub(lk.pj, lk.pi);
      const L = Math.hypot(ax[0], ax[1], ax[2]);
      if (L > 1e-6) {
        const e = mul(ax, 1 / L);
        const dir = norm(Math.abs(e[2]) > 0.9 ? cross(e, [1, 0, 0]) : cross(e, [0, 0, 1])) || [0, 0, 1];
        items.push({ base: [lk.pi, lk.pj], dir, vals: [v, v], min: null });
      } else {
        const d = [0, 0, 0]; d[i % 3] = 1;
        items.push({ base: [lk.pi, add(lk.pi, mul(d, R * 0.02))], dir: d, vals: [v, v], min: null, arrow: true });
      }
    }
  }
  for (const it of items) for (const v of [...it.vals, ...(it.min || [])]) vmax = Math.max(vmax, Math.abs(v));
  const k = vmax > 1e-12 ? R * 0.08 * (st.scale || 1) / vmax : 0;
  if (k > 0) for (const it of items) {
    const tip = it.base.map((b, j) => add(b, mul(it.dir, it.vals[j] * k)));
    if (drawBlock(ctx, P, it.base, tip, it.vals, qk, st, false)) drawn.items++;
    if (it.min) drawBlock(ctx, P, it.base, it.base.map((b, j) => add(b, mul(it.dir, it.min[j] * k))), it.min, qk, st, true);
  }
  ctx.textAlign = "left"; ctx.textBaseline = "top";
  ctx.fillStyle = "rgba(154,167,180,0.95)";
  ctx.fillText(`${def.label} · ${comp[1]} · ${st.case}${vmax ? ` · max ${wcU.fmtU(qk, vmax, 1)}` : ""}`, 12, 26);
  ctx.restore();
  st.drawn = drawn;
}

/* ---------------------------------------------------------------- dialog */
export function wcOpenDiagrams(sky) {
  const S = sky.store, r = S.results;
  const st = { ...wcDiag };
  const F = wcForm();
  const caseOpts = () => wcCaseOptions(st.kind, r).map(n => [n, n]);
  const compOpts = () => KINDS[st.kind].comps.map(([v, t, q]) => [v, `${t} (${wcU.label(q)})`]);
  const fixCase = () => {
    const opts = wcCaseOptions(st.kind, r);
    if (!opts.includes(st.case)) st.case = opts[0] || null;
    if (!KINDS[st.kind].comps.some(c => c[0] === st.comp)) st.comp = KINDS[st.kind].comps[0][0];
  };
  fixCase();
  const note = wcNote("");
  const syncNote = () => {
    const n = wcCaseOptions(st.kind, r).length;
    note.innerHTML = !r ? "Run the analysis first." :
      n ? (st.kind === "link" ? "Elastic links, F = k (u<sub>j</sub> − u<sub>i</sub>) on the global axes; static cases and single-valued combos." :
        "Static, response-spectrum (envelope), time-history and max/min combo results; Min drawn dashed.") :
        `No ${KINDS[st.kind].label.toLowerCase()} results in this run.`;
  };
  const body = [
    wcGroup("Diagram", [
      F.select(st, "kind", Object.entries(KINDS).map(([k, d]) => [k, d.label]), { label: "Object type", id: "wcDiagKind",
        onChange: () => { fixCase(); F.refresh(); syncNote(); } }),
      F.select(st, "case", caseOpts, { label: "Case / combo", id: "wcDiagCase" }),
      F.select(st, "comp", compOpts, { label: "Component", id: "wcDiagComp" }),
    ]),
    wcGroup("Display", [
      F.num(st, "scale", { label: "Scale factor", gt: 0, id: "wcDiagScale" }),
      F.check(st, "fill", { label: "Fill diagram", id: "wcDiagFill" }),
      F.check(st, "labels", { label: "Show values", id: "wcDiagLabels" }),
    ]),
    note,
  ];
  syncNote();
  const off = wcEl("button", { type: "button", class: "btn", id: "wcDiagOff" }, ["Hide diagrams"]);
  const d = wcDialog({
    id: "wcDiagDlg", title: "Pier / Spandrel / Link Force Diagrams", size: "sm", form: F, body,
    intro: "Force diagrams of wall piers, spandrels and links drawn on the 3D model.",
    extra: [off],
    onOk: () => {
      if (!r) return true;
      if (!st.case) return "No results of this object type — pick another type.";
      Object.assign(wcDiag, st, { on: true });
      sky.setMode && sky.setMode("analyze");
      sky.switchTab && sky.switchTab("view3d");
      refresh(sky);
      return true;
    },
  });
  off.addEventListener("click", () => { wcDiag.on = false; refresh(sky); d.close(); });
  return d;
}

function refresh(sky) {
  install(sky);
  if (sky.viewer) sky.viewer._dirty = true;
}
function install(sky) {
  const v = sky.viewer;
  if (v && !v.wcOverlay) v.wcOverlay = (ctx, P, viewer) => { wcOverlay(ctx, P, viewer, sky); if (sky.wcVideo && sky.wcVideo.caption) sky.wcVideo.caption(ctx, viewer); };
}

export function initWcDiagrams(sky) {
  install(sky);
  sky.wcDiagrams = { state: wcDiag, open: () => wcOpenDiagrams(sky), refresh: () => refresh(sky),
    pierGeometry: wcPierGeometry, spandrelGeometry: wcSpandrelGeometry, linkForces: wcLinkForces, caseOptions: wcCaseOptions };
  document.addEventListener("sky:results-changed", () => { if (wcDiag.on && !wcCaseOptions(wcDiag.kind, sky.store.results).includes(wcDiag.case)) wcDiag.on = false; refresh(sky); });
}
