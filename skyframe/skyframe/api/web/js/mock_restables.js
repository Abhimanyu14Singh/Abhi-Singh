/* SkyFrame — offline (?mock=1) support for the element / wall / nonlinear
   Show-Tables tables, spandrel forces, results export and the Word report
   (mirrors skyframe/core/tables_results.py, skyframe/engine/spandrels.py,
   skyframe/io/results_export.py and skyframe/io/docx.py).

     registers 11 tables into js/mock_tables.js (same keys / columns as the
       backend catalogue) — computed from the mock results;
     mrtMockAugment(model, r)     results.spandrels (frame spandrels: the
                                  member stations at the end cuts, exactly
                                  as the engine; wall spandrels: a plausible
                                  share of the story shear);
     mrtZip(files)                stored zip of {name: string | Uint8Array};
     mrtXlsx(tables, units)       minimal .xlsx (one sheet per table);
     mrtSqlDump(tables, units)    SQL text dump (mock stand-in for .sqlite —
                                  SQLite files need the live backend);
     mrtDocx(doc)                 minimal .docx (same block format as
                                  POST /api/report/docx).
   All values SI; conversions go through js/units.js. */

import { registerMockTable as mrtRegister } from "./mock_tables.js";
import mrtU from "./units.js";

const C = (key, label, quantity) => ({ key, label, quantity });
const FORCE6 = [C("P", "P", "force"), C("V2", "V2", "force"), C("V3", "V3", "force"),
  C("T", "T", "moment"), C("M2", "M2", "moment"), C("M3", "M3", "moment")];
const CASE2 = [C("case", "Output Case", "text"), C("case_type", "Case Type", "text")];

function sources(model, r, f) {
  const out = [];
  for (const [n, cd] of Object.entries(r.cases || {})) {
    if (f && !f.has(n)) continue;
    const lc = (model.cases || {})[n] || {};
    out.push([n, (lc.pdelta || (lc.geometric && lc.geometric !== "linear")) ? "NonStatic" : "LinStatic", cd]);
  }
  for (const [n, cd] of Object.entries(r.combos || {})) {
    if (f && !f.has(n)) continue;
    const cb = (model.combos || {})[n] || {};
    if ((cb.combo_type || "add") !== "add" || cd.min) continue;
    out.push([n, "Combination", cd]);
  }
  return out;
}
const caseType = (model, r, n) => (r.combos || {})[n] ? "Combination" : "LinStatic";
const storyRank = model => Object.fromEntries((model.stories || []).map((s, i) => [s.name, i]));
const nlKeep = (f, name, names) => !f || !names.some(n => f.has(n)) || f.has(name);

/* ---------------------------------------------------------------- tables */
function tFrames(model, r, f) {
  const story = Object.fromEntries((r.members || []).map(m => [m.uid, m.story || ""]));
  const rows = [];
  for (const [name, ct, cd] of sources(model, r, f))
    for (const [uid, st] of Object.entries(cd.member_stations || {}))
      (st.x || []).forEach((x, i) => rows.push({ story: story[uid] || "", frame: uid, case: name, case_type: ct, station: x,
        P: st.N[i], V2: st.V2[i], V3: st.V3[i], T: st.T[i], M2: st.M2[i], M3: st.M3[i] }));
  return rows;
}
function findTag(r, p) {
  for (const [t, c] of Object.entries(r.nodes || {}))
    if (Math.abs(c[0] - p[0]) < 1e-6 && Math.abs(c[1] - p[1]) < 1e-6 && Math.abs(c[2] - p[2]) < 1e-6) return t;
  return null;
}
function tLinks(model, r, f, warn) {
  const rows = [], links = model.links || [];
  const dev = links.filter(l => (l.link_type || "elastic") !== "elastic").map(l => l.uid);
  if (dev.length) warn.push(`device links (${dev.join(", ")}) are nonlinear / velocity-dependent: their forces are reported by the time-history plot functions, not by this static table`);
  for (const [name, ct, cd] of sources(model, r, f))
    for (const lk of links) {
      if ((lk.link_type || "elastic") !== "elastic") continue;
      const ti = findTag(r, lk.pi), tj = findTag(r, lk.pj);
      const di = (cd.node_disp || {})[ti] || [0, 0, 0, 0, 0, 0], dj = (cd.node_disp || {})[tj] || [0, 0, 0, 0, 0, 0];
      const F = [0, 1, 2, 3, 4, 5].map(k => (lk.stiffness || [])[k] * (dj[k] - di[k]) || 0);
      const st = (model.stories || []).find(s => Math.abs(s.elevation - 0.5 * (lk.pi[2] + lk.pj[2])) < 1e-6);
      for (const loc of ["I-End", "J-End"])
        rows.push({ story: st ? st.name : "", link: lk.uid, case: name, case_type: ct, location: loc,
          P: F[0], V2: F[1], V3: F[2], T: F[3], M2: F[4], M3: F[5] });
    }
  return rows;
}
function tPiers(model, r, f) {
  const rank = storyRank(model), rows = [];
  for (const [name, by] of Object.entries(r.piers || {})) {
    if (f && !f.has(name)) continue;
    for (const pier of Object.keys(by).sort())
      for (const [st, v] of Object.entries(by[pier]).sort((a, b) => (rank[b[0]] ?? -1) - (rank[a[0]] ?? -1)))
        rows.push({ story: st, pier, case: name, case_type: caseType(model, r, name), location: "Bottom", P: -v.P, V2: v.V, M3: v.M });
  }
  return rows;
}
function tSpandrels(model, r, f) {
  const rank = storyRank(model), rows = [];
  for (const [name, by] of Object.entries(r.spandrels || {})) {
    if (f && !f.has(name)) continue;
    for (const lab of Object.keys(by).sort())
      for (const [st, e] of Object.entries(by[lab]).sort((a, b) => (rank[b[0]] ?? -1) - (rank[a[0]] ?? -1)))
        for (const loc of ["Left", "Right"]) if (e[loc])
          rows.push({ story: st, spandrel: lab, case: name, case_type: caseType(model, r, name), location: loc, ...e[loc], length: e.length });
  }
  return rows;
}
function tCuts(model, r, f, warn) {
  const rows = [];
  for (const [name, cuts] of Object.entries(r.section_cuts || {})) {
    if (f && !f.has(name)) continue;
    for (const [cut, v] of Object.entries(cuts)) {
      for (const w of v.warnings || []) warn.push(`${cut}: ${w}`);
      rows.push({ cut, case: name, case_type: caseType(model, r, name), F1: v.FX, F2: v.FY, F3: v.FZ, M1: v.MX, M2: v.MY, M3: v.MZ,
        n_members: v.n_members || 0, n_shells: v.n_shells || 0 });
    }
  }
  return rows;
}
const USER_STATE = { "A-B": ["A to B", "<=IO"], "B-IO": ["B to C", "<=IO"], "IO-LS": ["B to C", "IO to LS"], "LS-CP": ["B to C", "LS to CP"],
  ">CP": ["B to C", ">CP"], "C-D": ["C to D", ">CP"], "D-E": ["D to E", ">CP"], ">E": [">E", ">CP"] };
const LEG_STATUS = { elastic: "<=IO", IO: "<=IO", LS: "IO to LS", CP: "LS to CP", collapse: ">CP" };
function elasticK(h) {
  let best = 0, ke = 0;
  (h.rot || []).forEach((rot, i) => {
    if ((h.state || [])[i] === "elastic" && Math.abs(rot) > best && Math.abs(rot) > 1e-15) { best = Math.abs(rot); ke = Math.abs((h.moment || [])[i] || 0) / Math.abs(rot); }
  });
  return ke;
}
/** [ETABS state, status, plastic deformation] (= tables_results.hinge_state). */
export function mrtHingeState(h, k) {
  const r = (h.rot || [])[k] || 0;
  if (h.hinge_state && k < h.hinge_state.length) {
    const [st, status] = USER_STATE[h.hinge_state[k]] || [h.hinge_state[k], ""];
    return [st, status, (h.rot_plastic || [])[k] || 0];
  }
  const lg = (h.state || [])[k] || "elastic";
  let p;
  if (h.rot_plastic && k < h.rot_plastic.length) p = h.rot_plastic[k];
  else { const ke = elasticK(h); p = ke > 0 ? Math.max(0, Math.abs(r) - (h.My || 0) / ke) : 0; }
  if (lg === "elastic") p = 0;
  const st = (lg === "elastic" || p <= 1e-12) ? "A to B" : (h.a == null || p <= h.a) ? "B to C" : (h.b == null || p <= h.b) ? "D to E" : ">E";
  return [st, LEG_STATUS[lg] || lg, p];
}
function hingeSources(r, f) {
  const out = [];
  for (const [n, po] of Object.entries(r.pushover || {})) out.push([n, "NonStatic", po.hinges || []]);
  for (const [n, ns] of Object.entries(r.nonlinear_static || {})) out.push([n, "NonStatic", ((ns.nonlinear || {}).hinges) || []]);
  for (const [n, th] of Object.entries(r.th_cases || {})) out.push([n, "NonModHist", th.hinges || []]);
  const names = out.map(o => o[0]);
  return out.filter(([n, , h]) => h.length && nlKeep(f, n, names));
}
function tHinges(model, r, f) {
  const story = Object.fromEntries((r.members || []).map(m => [m.uid, m.story || ""]));
  const rows = [];
  for (const [name, ct, hs] of hingeSources(r, f)) {
    const n = Math.max(0, ...hs.map(h => (h.rot || []).length));
    for (let k = 0; k < n; k++) for (const h of hs) {
      if (k >= (h.rot || []).length) continue;
      const [st, status, p] = mrtHingeState(h, k);
      const rd = h.relative_distance != null ? h.relative_distance : h.end === "i" ? 0 : h.end === "j" ? 1 : (+h.end || 0);
      rows.push({ story: story[h.uid] || "", frame: h.uid, case: name, case_type: ct, step: k + 1, hinge: `${h.uid}-${h.end}`,
        rel_dist: rd, dof: h.type || "M3", force: (h.moment || [])[k] || 0, deform: h.rot[k], plastic: p, state: st, status });
    }
  }
  return rows;
}
function tTendons(model, r) {
  const rows = [];
  for (const [uid, td] of Object.entries(r.tendons || {})) {
    const st = td.stations || {}, P0 = td.P0 || 0, pts = st.points || [];
    (st.s || []).forEach((s, i) => {
      const p = pts[i] || [null, null, null];
      rows.push({ tendon: uid, pattern: td.pattern || "", station: s, alpha: (st.alpha || [])[i] || 0, P: st.P[i],
        loss: P0 ? 100 * (1 - st.P[i] / P0) : 0, x: p[0], y: p[1], z: p[2] });
    });
  }
  return rows;
}
const SK = { "A to B": "AtoB", "B to C": "BtoC", "C to D": "CtoD", "D to E": "DtoE", ">E": "beyondE" };
const SS = { "<=IO": "AtoIO", "IO to LS": "IOtoLS", "LS to CP": "LStoCP", ">CP": "beyondCP" };
function tPushover(model, r, f) {
  const rows = [], all = Object.keys(r.pushover || {});
  for (const [name, po] of Object.entries(r.pushover || {})) {
    if (!nlKeep(f, name, all)) continue;
    const hs = po.hinges || [];
    (po.roof_disp || []).forEach((d, k) => {
      const row = { case: name, step: k + 1, disp: d, base_force: (po.base_shear || [])[k], drift: (po.roof_drift || [])[k] };
      for (const v of [...Object.values(SK), ...Object.values(SS)]) row[v] = 0;
      for (const h of hs) {
        if (k >= (h.rot || []).length) continue;
        const [st, status] = mrtHingeState(h, k);
        if (SK[st]) row[SK[st]]++;
        if (SS[status]) row[SS[status]]++;
      }
      row.total = Object.values(SK).reduce((a, v) => a + row[v], 0);
      rows.push(row);
    });
  }
  return rows;
}
function tNls(model, r, f) {
  const rows = [], all = Object.keys(r.nonlinear_static || {});
  for (const [name, ns] of Object.entries(r.nonlinear_static || {})) {
    if (!nlKeep(f, name, all)) continue;
    const h = (ns.nonlinear || {}).history || {}, b = h.base || {};
    (h.step || []).forEach((s, i) => rows.push({ case: name, step: s, load_factor: (h.load_factor || [])[i], disp: (h.disp || [])[i],
      ...Object.fromEntries(["FX", "FY", "FZ", "MX", "MY", "MZ"].map(k => [k, (b[k] || [])[i] ?? null])) }));
  }
  return rows;
}
function thItems(r, f) {
  const all = Object.keys(r.th_cases || {});
  return Object.entries(r.th_cases || {}).filter(([n, th]) => nlKeep(f, n, all) && (th.t || []).length);
}
function tThBase(model, r, f) {
  const rows = [];
  for (const [name, th] of thItems(r, f))
    th.t.forEach((t, i) => rows.push({ case: name, step: i + 1, time: t, FX: (th.base_FX || [])[i] ?? null, FY: (th.base_FY || [])[i] ?? null }));
  return rows;
}
function tThStory(model, r, f) {
  const rows = [], order = [...(model.stories || [])].reverse().map(s => s.name);
  for (const [name, th] of thItems(r, f)) {
    const ux = th.story_ux || {}, uy = th.story_uy || {};
    const names = order.filter(s => ux[s] || uy[s]);
    th.t.forEach((t, i) => { for (const s of names) rows.push({ case: name, step: i + 1, time: t, story: s, UX: (ux[s] || [])[i] ?? null, UY: (uy[s] || [])[i] ?? null }); });
  }
  return rows;
}

const G_FRAME = "Element Output > Frame Output", G_WALL = "Element Output > Wall Output";
export const MRT_TABLES = [
  ["element_forces_frames", "Element Forces - Frames", G_FRAME,
    [C("story", "Story", "text"), C("frame", "Frame", "text"), ...CASE2, C("station", "Station", "length"), ...FORCE6], tFrames],
  ["frame_hinge_states", "Frame Hinge States", G_FRAME,
    [C("story", "Story", "text"), C("frame", "Frame", "text"), ...CASE2, C("step", "Step", "id"), C("hinge", "Hinge ID", "text"),
      C("rel_dist", "Rel Dist", "ratio"), C("dof", "Hinge DOF", "text"), C("force", "Force/Moment", "moment"),
      C("deform", "Deformation", "angle"), C("plastic", "Plastic Deformation", "angle"), C("state", "Hinge State", "text"),
      C("status", "Hinge Status", "text")], tHinges],
  ["element_forces_links", "Element Forces - Links", "Element Output > Link Output",
    [C("story", "Story", "text"), C("link", "Link", "text"), ...CASE2, C("location", "Location", "text"), ...FORCE6], tLinks],
  ["pier_forces", "Pier Forces", G_WALL,
    [C("story", "Story", "text"), C("pier", "Pier", "text"), ...CASE2, C("location", "Location", "text"),
      C("P", "P", "force"), C("V2", "V2", "force"), C("M3", "M3", "moment")], tPiers],
  ["spandrel_forces", "Spandrel Forces", G_WALL,
    [C("story", "Story", "text"), C("spandrel", "Spandrel", "text"), ...CASE2, C("location", "Location", "text"), ...FORCE6,
      C("length", "Length", "length")], tSpandrels],
  ["tendon_forces", "Tendon Forces", "Element Output > Tendon Output",
    [C("tendon", "Tendon", "text"), C("pattern", "Load Pattern", "text"), C("station", "Station", "length"),
      C("alpha", "Angle Change", "angle"), C("P", "P", "force"), C("loss", "Loss", "percent"),
      C("x", "X", "length"), C("y", "Y", "length"), C("z", "Z", "length")], tTendons],
  ["section_cut_forces", "Section Cut Forces - Analysis", "Structure Output > Other Output Items",
    [C("cut", "SectionCut", "text"), ...CASE2, C("F1", "F1", "force"), C("F2", "F2", "force"), C("F3", "F3", "force"),
      C("M1", "M1", "moment"), C("M2", "M2", "moment"), C("M3", "M3", "moment"), C("n_members", "Frames", "id"),
      C("n_shells", "Shells Excl.", "id")], tCuts],
  ["pushover_curve", "Pushover Curve", "Structure Output > Nonlinear Output",
    [C("case", "Output Case", "text"), C("step", "Step", "id"), C("disp", "Monitored Displ", "length"),
      C("base_force", "Base Force", "force"), C("drift", "Roof Drift", "ratio"),
      C("AtoB", "A-B", "id"), C("BtoC", "B-C", "id"), C("CtoD", "C-D", "id"), C("DtoE", "D-E", "id"), C("beyondE", ">E", "id"),
      C("AtoIO", "A-IO", "id"), C("IOtoLS", "IO-LS", "id"), C("LStoCP", "LS-CP", "id"), C("beyondCP", ">CP", "id"),
      C("total", "Total", "id")], tPushover],
  ["nonlinear_static_steps", "Nonlinear Static Steps", "Structure Output > Nonlinear Output",
    [C("case", "Output Case", "text"), C("step", "Step", "id"), C("load_factor", "Load Factor", "factor"),
      C("disp", "Monitored Displ", "length"), C("FX", "FX", "force"), C("FY", "FY", "force"), C("FZ", "FZ", "force"),
      C("MX", "MX", "moment"), C("MY", "MY", "moment"), C("MZ", "MZ", "moment")], tNls],
  ["th_base_reactions_steps", "Base Reactions - Time History Steps", "Structure Output > Time History Output",
    [C("case", "Output Case", "text"), C("step", "Step", "id"), C("time", "Time", "time"), C("FX", "FX", "force"),
      C("FY", "FY", "force")], tThBase],
  ["th_story_displacements_steps", "Story Displacements - Time History Steps", "Structure Output > Time History Output",
    [C("case", "Output Case", "text"), C("step", "Step", "id"), C("time", "Time", "time"), C("story", "Story", "text"),
      C("UX", "UX", "length"), C("UY", "UY", "length")], tThStory],
];
for (const [k, t, g, cols, fn] of MRT_TABLES) mrtRegister(k, t, g, cols, fn);

/* ---------------------------------------------------------------- spandrels */
/** results.spandrels for the mock solve (engine/spandrels.py semantics). */
export function mrtMockAugment(model, r) {
  if (!model || !r) return r;
  const frames = (model.members || []).filter(m => m.spandrel);
  const walls = (model.shells || []).filter(s => s.spandrel && s.kind === "wall");
  if (!frames.length && !walls.length) return r;
  const elev = Object.fromEntries((model.stories || []).map(s => [s.name, s]));
  const storyOf = (o, zc) => (o.story && elev[o.story]) ? o.story
    : ((model.stories || []).find(s => s.elevation - s.height - 1e-6 < zc && zc <= s.elevation + 1e-6) || {}).name || "";
  const groups = {};
  const axis = d => { let x = d[0], y = d[1]; const n = Math.hypot(x, y) || 1; x /= n; y /= n; if (x < -1e-9 || (Math.abs(x) <= 1e-9 && y < 0)) { x = -x; y = -y; } return [x, y]; };
  for (const m of frames) {
    const e1 = axis([m.pj[0] - m.pi[0], m.pj[1] - m.pi[1]]);
    const k = m.spandrel + "\u0001" + storyOf(m, 0.5 * (m.pi[2] + m.pj[2]));
    (groups[k] = groups[k] || { e1, frames: [], walls: [] }).frames.push(m);
  }
  for (const w of walls) {
    const c = w.corners || [];
    const e1 = axis([c[1][0] - c[0][0], c[1][1] - c[0][1]]);
    const zc = c.reduce((a, p) => a + p[2], 0) / Math.max(1, c.length);
    const k = w.spandrel + "\u0001" + storyOf(w, zc);
    (groups[k] = groups[k] || { e1, frames: [], walls: [] }).walls.push(w);
  }
  const out = {};
  const src = [...Object.entries(r.cases || {}), ...Object.entries(r.combos || {}).filter(([, cd]) => !cd.min)];
  for (const [name, cd] of src) {
    const by = {};
    for (const [k, g] of Object.entries(groups)) {
      const [lab, story] = k.split("\u0001");
      const s = p => p[0] * g.e1[0] + p[1] * g.e1[1];
      const sv = [...g.frames.flatMap(m => [s(m.pi), s(m.pj)]), ...g.walls.flatMap(w => (w.corners || []).map(s))];
      const lo = Math.min(...sv), hi = Math.max(...sv), L = hi - lo;
      if (!(L > 1e-6)) continue;
      const e = { length: L, n_shells: g.walls.length, n_frames: g.frames.length,
        Left: { P: 0, V2: 0, V3: 0, T: 0, M2: 0, M3: 0 }, Right: { P: 0, V2: 0, V3: 0, T: 0, M2: 0, M3: 0 } };
      for (const m of g.frames) {           // exact: member stations at the cuts
        const st = (cd.member_stations || {})[m.uid];
        if (!st) continue;
        const fwd = s(m.pj) > s(m.pi), sg = fwd ? 1 : -1;
        const at = (side, i) => { for (const q of ["N", "V2", "M3"]) e[side][q === "N" ? "P" : q] += sg * st[q][i]; };
        at("Left", fwd ? 0 : st.x.length - 1);
        at("Right", fwd ? st.x.length - 1 : 0);
      }
      if (g.walls.length) {                 // plausible: coupling share of the story shear
        const sh = (cd.story || {})[story] || {};
        const V = 0.12 * Math.hypot(sh.shear_x || 0, sh.shear_y || 0);
        e.Left.V2 += V; e.Right.V2 += V;
        e.Left.M3 += V * L / 2; e.Right.M3 += -V * L / 2;
      }
      (by[lab] = by[lab] || {})[story] = e;
    }
    if (Object.keys(by).length) out[name] = by;
  }
  if (Object.keys(out).length) r.spandrels = out;
  return r;
}

/* ---------------------------------------------------------------- writers */
const ENC = new TextEncoder();
const CRC_T = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
const crc32 = b => { let c = 0xFFFFFFFF; for (let i = 0; i < b.length; i++) c = CRC_T[(c ^ b[i]) & 0xFF] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; };
/** Stored (method 0) zip of {name: string | Uint8Array} → Uint8Array. */
export function mrtZip(files) {
  const chunks = [], central = [];
  let off = 0;
  const hdr = (sig, fields) => { const b = new Uint8Array(fields.reduce((a, [, n]) => a + n, 4)); const dv = new DataView(b.buffer); dv.setUint32(0, sig, true); let p = 4; for (const [v, n] of fields) { if (n === 2) dv.setUint16(p, v, true); else dv.setUint32(p, v >>> 0, true); p += n; } return b; };
  for (const [name, content] of Object.entries(files)) {
    const nb = ENC.encode(name), data = typeof content === "string" ? ENC.encode(content) : content, crc = crc32(data);
    const lh = hdr(0x04034b50, [[20, 2], [0x0800, 2], [0, 2], [0, 2], [0x21, 2], [crc, 4], [data.length, 4], [data.length, 4], [nb.length, 2], [0, 2]]);
    chunks.push(lh, nb, data);
    central.push(hdr(0x02014b50, [[20, 2], [20, 2], [0x0800, 2], [0, 2], [0, 2], [0x21, 2], [crc, 4], [data.length, 4], [data.length, 4],
      [nb.length, 2], [0, 2], [0, 2], [0, 2], [0, 2], [0, 4], [off, 4]]), nb);
    off += lh.length + nb.length + data.length;
  }
  const csize = central.reduce((a, b) => a + b.length, 0), n = Object.keys(files).length;
  const end = hdr(0x06054b50, [[0, 2], [0, 2], [n, 2], [n, 2], [csize, 4], [off, 4], [0, 2]]);
  const all = [...chunks, ...central, end], out = new Uint8Array(all.reduce((a, b) => a + b.length, 0));
  let p = 0; for (const a of all) { out.set(a, p); p += a.length; }
  return out;
}
const xesc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* SI → export unit per Show-Tables quantity (= results_export.unit_for) */
const QK = { force: "force", length: "length", moment: "moment", stiffness: "stiffness", mass: "mass" };
const FIXED = { angle: "rad", time: "s", frequency: "Hz", percent: "%", circular_frequency: "rad/s", eigenvalue: "rad^2/s^2" };
export function mrtUnitFor(q, units) {
  const kind = QK[q];
  if (!kind) return [1, FIXED[q] || ""];
  const s = mrtU.unitSet(units);           // labels as the backend (ASCII)
  const lab = { force: s.fu, length: s.lu, moment: `${s.fu}*${s.lu}`, stiffness: `${s.fu}/${s.lu}`,
    mass: units === "kN-m" ? "tonne" : `${s.fu}/${s.lu}*s^2` }[kind];
  return [mrtU.toDisplay(kind, 1, units), lab];
}
function converted(tab, units) {
  const conv = tab.columns.map(c => mrtUnitFor(c.quantity, units));
  const head = tab.columns.map((c, i) => conv[i][1] ? `${c.label} [${conv[i][1]}]` : c.label);
  const body = tab.rows.map(r => tab.columns.map((c, i) => {
    const v = r[c.key];
    if (v == null || typeof v === "boolean") return v;
    if (typeof v === "number" && c.quantity !== "text") return conv[i][0] !== 1 ? v * conv[i][0] : v;
    return String(v);
  }));
  return { head, body, conv };
}
const colName = n => { let s = ""; n += 1; while (n) { const r = (n - 1) % 26; s = String.fromCharCode(65 + r) + s; n = Math.floor((n - 1) / 26); } return s; };
function sheetXml(rows) {
  const cell = (ref, v) => v == null || v === "" ? "" : typeof v === "number" ? (isFinite(v) ? `<c r="${ref}"><v>${v}</v></c>` : "")
    : typeof v === "boolean" ? `<c r="${ref}" t="b"><v>${v ? 1 : 0}</v></c>` : `<c r="${ref}" t="inlineStr"><is><t xml:space="preserve">${xesc(v)}</t></is></c>`;
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>` +
    rows.map((row, r) => `<row r="${r + 1}">${row.map((v, c) => cell(colName(c) + (r + 1), v)).join("")}</row>`).join("") + `</sheetData></worksheet>`;
}
/** Minimal .xlsx: _index + one sheet per table + _meta (= export_results_xlsx). */
export function mrtXlsx(tabs, units, modelName = "") {
  const names = [];
  const sheets = [["_index", [["key", "table", "group", "rows", "sheet"]]]];
  for (const t of tabs) {
    let nm = t.title.replace(/[[\]:*?/\\]/g, "_").slice(0, 31), base = nm, k = 1;
    while (names.includes(nm)) { k++; nm = `${base.slice(0, 28)}~${k}`; }
    names.push(nm);
    const { head, body } = converted(t, units);
    sheets.push([nm, [head, ...body]]);
    sheets[0][1].push([t.key, t.title, t.group, t.rows.length, nm]);
  }
  sheets.push(["_meta", [["key", "value"], ["format", "skyframe-results-xlsx"], ["version", 1], ["units", units], ["model", modelName]]]);
  const NS = "http://schemas.openxmlformats.org";
  const files = {
    "[Content_Types].xml": `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="${NS}/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>` +
      sheets.map((_, i) => `<Override PartName="/xl/worksheets/sheet${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join("") + `</Types>`,
    "_rels/.rels": `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="${NS}/package/2006/relationships"><Relationship Id="rId1" Type="${NS}/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`,
    "xl/workbook.xml": `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="${NS}/spreadsheetml/2006/main" xmlns:r="${NS}/officeDocument/2006/relationships"><sheets>` +
      sheets.map(([n], i) => `<sheet name="${xesc(n)}" sheetId="${i + 1}" r:id="rId${i + 1}"/>`).join("") + `</sheets></workbook>`,
    "xl/_rels/workbook.xml.rels": `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="${NS}/package/2006/relationships">` +
      sheets.map((_, i) => `<Relationship Id="rId${i + 1}" Type="${NS}/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${i + 1}.xml"/>`).join("") + `</Relationships>`,
  };
  sheets.forEach(([, rows], i) => { files[`xl/worksheets/sheet${i + 1}.xml`] = sheetXml(rows); });
  return mrtZip(files);
}
/** SQL text dump with the same tables / fields as the .sqlite export. */
export function mrtSqlDump(tabs, units, modelName = "") {
  const q = s => '"' + String(s).replace(/"/g, '""') + '"';
  const lit = v => v == null ? "NULL" : typeof v === "number" ? (isFinite(v) ? String(v) : "NULL") : `'${String(v).replace(/'/g, "''")}'`;
  const field = l => l.replace(/>/g, "Gt").replace(/<=/g, "Le").replace(/-/g, "_").replace(/[^0-9A-Za-z_]+/g, "") || "Field";
  const out = ["-- SkyFrame results (offline mock): SQL dump of the SQLite export. Run the live server for a .sqlite file.",
    "BEGIN TRANSACTION;", 'CREATE TABLE "Table Index" (TableKey TEXT PRIMARY KEY, TableName TEXT, TableGroup TEXT, NumRows INTEGER);',
    'CREATE TABLE "Field Units" (TableName TEXT, Field TEXT, Quantity TEXT, Unit TEXT);',
    'CREATE TABLE "Program Control" (Item TEXT, Value TEXT);',
    `INSERT INTO "Program Control" VALUES ('ProgramName','SkyFrame'),('Units',${lit(units)}),('Model',${lit(modelName)});`];
  for (const t of tabs) {
    const { body, conv } = converted(t, units);
    const fields = [];
    for (const c of t.columns) { let f = field(c.label), b = f, k = 1; while (fields.includes(f)) { k++; f = `${b}_${k}`; } fields.push(f); }
    const aff = t.columns.map(c => c.quantity === "text" ? "TEXT" : c.quantity === "id" ? "INTEGER" : "REAL");
    out.push(`CREATE TABLE ${q(t.title)} (${fields.map((f, i) => `${q(f)} ${aff[i]}`).join(", ")});`);
    for (const row of body) out.push(`INSERT INTO ${q(t.title)} VALUES (${row.map(lit).join(",")});`);
    out.push(`INSERT INTO "Table Index" VALUES (${lit(t.key)},${lit(t.title)},${lit(t.group)},${body.length});`);
    t.columns.forEach((c, i) => out.push(`INSERT INTO "Field Units" VALUES (${lit(t.title)},${lit(fields[i])},${lit(c.quantity)},${lit(conv[i][1])});`));
  }
  out.push("COMMIT;");
  return out.join("\n");
}

/* .docx (same block format as skyframe/io/docx.py) */
const b64ToBytes = b64 => { const bin = atob(b64); const u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i); return u; };
export function mrtDocx(doc) {
  const W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main", R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
  const PKG = "http://schemas.openxmlformats.org/package/2006/relationships";
  const pages = { A4: [11906, 16838], Letter: [12240, 15840] };
  let [pw, ph] = pages[doc.paper] || pages.A4;
  if (doc.landscape) [pw, ph] = [ph, pw];
  const M = 1020, tw = pw - 2 * M, maxCx = tw * 635;
  const t = s => `<w:t xml:space="preserve">${xesc(s)}</w:t>`;
  const run = (s, b) => `<w:r>${b ? "<w:rPr><w:b/></w:rPr>" : ""}${String(s == null ? "" : s).split("\n").map(t).join("<w:br/>")}</w:r>`;
  const para = (s, style, jc) => `<w:p>${style || jc ? `<w:pPr>${style ? `<w:pStyle w:val="${style}"/>` : ""}${jc ? `<w:jc w:val="${jc}"/>` : ""}</w:pPr>` : ""}${s !== "" ? run(s) : ""}</w:p>`;
  const table = b => {
    const cols = b.columns || [], n = cols.length, w = Math.max(400, Math.floor(tw / Math.max(1, n)));
    const first = (b.rows || [])[0] || [];
    const al = b.align && b.align.length === n ? b.align : cols.map((_, i) => typeof first[i] === "number" ? "r" : "l");
    const cell = (v, i, hd) => `<w:tc><w:tcPr><w:tcW w:w="${w}" w:type="dxa"/>${hd ? '<w:shd w:val="clear" w:color="auto" w:fill="EEF2F6"/>' : ""}</w:tcPr><w:p><w:pPr><w:pStyle w:val="TableText"/><w:jc w:val="${al[i] === "l" ? "left" : "right"}"/></w:pPr>${run(typeof v === "number" ? +v.toPrecision(6) : v, hd)}</w:p></w:tc>`;
    return (b.caption ? para(b.caption, "Caption") : "") + `<w:tbl><w:tblPr><w:tblStyle w:val="ReportTable"/><w:tblW w:w="5000" w:type="pct"/></w:tblPr><w:tblGrid>${cols.map(() => `<w:gridCol w:w="${w}"/>`).join("")}</w:tblGrid>` +
      `<w:tr><w:trPr><w:tblHeader/></w:trPr>${cols.map((c, i) => cell(c, i, true)).join("")}</w:tr>` +
      (b.rows || []).map(r => `<w:tr>${cols.map((_, i) => cell(r[i], i, false)).join("")}</w:tr>`).join("") + `</w:tbl>` + para("");
  };
  const media = [];
  const body = [para(doc.title || "Report", "Title")];
  if (doc.subtitle) body.push(para(doc.subtitle, "Subtitle"));
  if (doc.meta) body.push(table({ columns: ["Item", "Value"], rows: doc.meta, align: ["l", "l"] }));
  for (const b of doc.blocks || []) {
    if (b.type === "heading") body.push(para(b.text, "Heading" + (b.level || 1)));
    else if (b.type === "paragraph") body.push(para(b.text, b.style === "note" ? "Note" : null));
    else if (b.type === "table") body.push(table(b));
    else if (b.type === "page_break") body.push('<w:p><w:r><w:br w:type="page"/></w:r></w:p>');
    else if (b.type === "image") {
      const bytes = b64ToBytes(b.png_b64 || String(b.data_url || "").split(",")[1] || "");
      if (bytes[0] !== 0x89 || bytes[1] !== 0x50) throw new Error("image blocks must be PNG");
      const dv = new DataView(bytes.buffer);
      const wpx = dv.getUint32(16), hpx = dv.getUint32(20);
      media.push(bytes);
      const k = media.length;
      const dw = b.width_px > 0 ? +b.width_px : wpx;               // display width (aspect kept)
      let cx = Math.round(dw * 9525), cy = Math.round(hpx * dw / wpx * 9525);
      if (cx > maxCx) { cy = Math.round(cy * maxCx / cx); cx = maxCx; }
      body.push(`<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="${cx}" cy="${cy}"/><wp:docPr id="${k}" name="Picture ${k}"/><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic><pic:nvPicPr><pic:cNvPr id="${k}" name="image${k}.png"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:embed="rIdImg${k}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="${cx}" cy="${cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>`);
      if (b.caption) body.push(para(b.caption, "Caption", "center"));
    } else throw new Error(`unknown block type ${b.type}`);
  }
  const sect = `<w:sectPr><w:pgSz w:w="${pw}" w:h="${ph}"${doc.landscape ? ' w:orient="landscape"' : ""}/><w:pgMar w:top="${M}" w:right="${M}" w:bottom="${M}" w:left="${M}" w:header="567" w:footer="567" w:gutter="0"/></w:sectPr>`;
  const st = (id, name, rpr, ppr = "") => `<w:style w:type="paragraph" w:styleId="${id}"><w:name w:val="${name}"/><w:basedOn w:val="Normal"/><w:pPr>${ppr}</w:pPr><w:rPr>${rpr}</w:rPr></w:style>`;
  const files = {
    "[Content_Types].xml": `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>`,
    "_rels/.rels": `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="${PKG}"><Relationship Id="rId1" Type="${R}/officeDocument" Target="word/document.xml"/></Relationships>`,
    "word/document.xml": `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="${W}" xmlns:r="${R}" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><w:body>${body.join("")}${sect}</w:body></w:document>`,
    "word/styles.xml": `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="${W}"><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:rPr><w:sz w:val="20"/></w:rPr></w:style>` +
      st("Title", "Title", '<w:b/><w:color w:val="10344D"/><w:sz w:val="52"/>') + st("Subtitle", "Subtitle", '<w:color w:val="5C6672"/><w:sz w:val="24"/>') +
      st("Heading1", "heading 1", '<w:b/><w:color w:val="10344D"/><w:sz w:val="30"/>', '<w:keepNext/><w:outlineLvl w:val="0"/>') +
      st("Heading2", "heading 2", '<w:b/><w:sz w:val="24"/>', '<w:keepNext/><w:outlineLvl w:val="1"/>') +
      st("Heading3", "heading 3", '<w:b/><w:sz w:val="21"/>', '<w:keepNext/><w:outlineLvl w:val="2"/>') +
      st("Note", "Note", '<w:color w:val="5C6672"/><w:sz w:val="18"/>') + st("Caption", "caption", '<w:i/><w:sz w:val="18"/>') +
      st("TableText", "Table Text", '<w:sz w:val="16"/>', '<w:spacing w:after="0"/>') +
      `<w:style w:type="table" w:styleId="ReportTable"><w:name w:val="Report Table"/><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4" w:space="0" w:color="D0D8E0"/><w:left w:val="single" w:sz="4" w:space="0" w:color="D0D8E0"/><w:bottom w:val="single" w:sz="4" w:space="0" w:color="D0D8E0"/><w:right w:val="single" w:sz="4" w:space="0" w:color="D0D8E0"/><w:insideH w:val="single" w:sz="4" w:space="0" w:color="E1E6EC"/><w:insideV w:val="single" w:sz="4" w:space="0" w:color="E1E6EC"/></w:tblBorders></w:tblPr></w:style></w:styles>`,
    "word/_rels/document.xml.rels": `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="${PKG}"><Relationship Id="rIdStyles" Type="${R}/styles" Target="styles.xml"/>` +
      media.map((_, i) => `<Relationship Id="rIdImg${i + 1}" Type="${R}/image" Target="media/image${i + 1}.png"/>`).join("") + `</Relationships>`,
  };
  media.forEach((b, i) => { files[`word/media/image${i + 1}.png`] = b; });
  return mrtZip(files);
}
