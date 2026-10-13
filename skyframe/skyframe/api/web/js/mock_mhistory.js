/* SkyFrame — ?mock=1 stand-ins for Tools › Model History (CONTRACT "Model
   history and visual diff").

   * mhMockStore: the checkpoint store in localStorage
     ("sky.mh.mock.<key>", bounded: ≤ 20 checkpoints, ≤ 4 MB of JSON;
     oldest unpinned auto checkpoints pruned first, then manual).
   * mhDiff / mhRestore: a JavaScript port of core/model_diff.py (same
     types, uid matching with geometric / content fallback, field-level
     changes, restore of selected objects).  The live build uses the
     backend; the port keeps ?mock=1 usable and is checked against the
     backend on the same edits by the Playwright run. */

export const MH_TYPES = ["joints", "frames", "shells", "links", "supports", "sections", "materials", "loads",
  "cases", "combos", "stories", "grids", "options"];
const DERIVED = new Set(["story_masses", "grid", "_mock_params"]);
const SECTION_KEYS = ["sections", "shell_sections", "designer_sections", "builtup_sections", "np_sections"];
const HANDLED = new Set(["members", "shells", "links", "supports", "spring_supports", "line_springs", "nodal_masses",
  "materials", "patterns", "combos", "rs_combos", "stories", "grid_systems", ...SECTION_KEYS]);
const isCaseKey = k => k === "cases" || k.endsWith("_cases");
const caseKind = k => k === "cases" ? "static" : k.slice(0, -"_cases".length);
const clone = o => o === undefined ? undefined : JSON.parse(JSON.stringify(o));
const r6 = c => { const v = Math.round((+c) * 1e6) / 1e6; return v === 0 ? 0 : v; };
const fmtG = v => { const s = String(+v.toPrecision(12)); return s; };
export const ptKey = p => Array.isArray(p) ? p.map(c => fmtG(r6(c))).join(",") : "";
const sortedPts = pts => pts.map(p => ptKey(p)).sort().join("|");

/* ---------------------------------------------------------------- equality */
function numEq(a, b, tol) {
  if (a === b) return true;
  if (!isFinite(a) || !isFinite(b)) return false;
  return Math.abs(a - b) <= tol * Math.max(1, Math.abs(a), Math.abs(b));
}
function short(v) {
  if (v == null || typeof v !== "object") return v;
  const s = JSON.stringify(v);
  if (s.length <= 160) return v;
  return Array.isArray(v) ? `[${v.length} items]` : `{${Object.keys(v).length} keys}`;
}
function deepEq(a, b) { return a === b || JSON.stringify(a) === JSON.stringify(b); }
export function fieldChanges(a, b, tol = 1e-12, path = "", out = [], limit = 50) {
  if (out.length >= limit || a === b) return out;
  const na = typeof a === "number", nb = typeof b === "number";
  if (na && nb) { if (!numEq(a, b, tol)) out.push({ path, a, b }); return out; }
  const da = a && typeof a === "object" && !Array.isArray(a), db = b && typeof b === "object" && !Array.isArray(b);
  if (da && db) {
    for (const k of [...Object.keys(a), ...Object.keys(b).filter(k => !(k in a))]) {
      const p = path ? `${path}.${k}` : k;
      if (!(k in b)) out.push({ path: p, a: short(a[k]), b: null, present_b: false });
      else if (!(k in a)) out.push({ path: p, a: null, b: short(b[k]), present_a: false });
      else fieldChanges(a[k], b[k], tol, p, out, limit);
      if (out.length >= limit) break;
    }
    return out;
  }
  if (Array.isArray(a) && Array.isArray(b)) {
    if (deepEq(a, b)) return out;
    if (a.length === b.length && (a.length <= 12 || a.slice(0, 3).every(x => x && typeof x === "object"))) {
      for (let i = 0; i < a.length && out.length < limit; i++) fieldChanges(a[i], b[i], tol, `${path}[${i}]`, out, limit);
      return out;
    }
    if (a.length === b.length && a.every((x, i) => typeof x === "number" && typeof b[i] === "number" && numEq(x, b[i], tol))) return out;
    out.push({ path, a: short(a), b: short(b) });
    return out;
  }
  if (!deepEq(a, b)) out.push({ path, a: short(a), b: short(b) });
  return out;
}

/* ---------------------------------------------------------------- collect */
const obj = (key, rec, o = {}) => ({ key, rec, label: o.label || key, geo: o.geo || null, geom: o.geom || null, ref: o.ref || null, content: o.content || null });
function uidObjects(items, kind, geoFn, geomFn) {
  const out = new Map();
  (items || []).forEach((it, i) => {
    if (!it || typeof it !== "object") return;
    const uid = String(it.uid ?? `#${i}`);
    const rec = { ...it }; delete rec.length;
    out.set(uid, obj(uid, rec, { geo: geoFn(it), geom: geomFn(it), ref: { kind, uid } }));
  });
  return out;
}
const frameGeo = m => (m.pi && m.pj) ? "F|" + [ptKey(m.pi), ptKey(m.pj)].sort().join("|") : null;
const frameGeom = m => ({ pi: m.pi, pj: m.pj });
const shellGeo = s => (s.corners && s.corners.length) ? "S|" + sortedPts(s.corners) : null;
function joints(d) {
  const pts = new Map();
  const add = p => { if (!Array.isArray(p)) return; const k = ptKey(p); if (k && !pts.has(k)) pts.set(k, p.map(Number)); };
  for (const m of d.members || []) { add(m.pi); add(m.pj); }
  for (const l of d.links || []) { add(l.pi); add(l.pj); }
  for (const s of d.shells || []) for (const c of s.corners || []) add(c);
  for (const k of ["supports", "spring_supports", "nodal_masses"]) for (const s of d[k] || []) add(s.point);
  const masses = new Map();
  for (const nm of d.nodal_masses || []) {
    const k = ptKey(nm.point), cur = masses.get(k) || {};
    for (const [f, v] of Object.entries(nm)) if (f !== "point") cur[f] = v;
    masses.set(k, cur);
  }
  const out = new Map();
  for (const [k, p] of pts) {
    const rec = { point: p.map(r6) };
    if (masses.has(k)) rec.mass = masses.get(k);
    out.set(k, obj(k, rec, { label: `(${k})`, geom: { point: p }, ref: { kind: "joint", point: p } }));
  }
  return out;
}
function supports(d) {
  const out = new Map();
  for (const s of d.supports || []) { const k = "restraint@" + ptKey(s.point); out.set(k, obj(k, s, { label: `Restraint (${ptKey(s.point)})`, geom: { point: s.point }, ref: { kind: "joint", point: s.point } })); }
  for (const s of d.spring_supports || []) { const k = "spring@" + ptKey(s.point); out.set(k, obj(k, s, { label: `Point spring (${ptKey(s.point)})`, geom: { point: s.point }, ref: { kind: "joint", point: s.point } })); }
  for (const s of d.line_springs || []) { const k = `line@${ptKey(s.p1)}|${ptKey(s.p2)}`; out.set(k, obj(k, s, { label: `Line spring (${ptKey(s.p1)} – ${ptKey(s.p2)})`, geom: { pi: s.p1, pj: s.p2 }, ref: { kind: "linespring" } })); }
  return out;
}
const contentKey = v => { const c = (v && typeof v === "object" && !Array.isArray(v)) ? { ...v } : v; if (c && typeof c === "object") delete c.name; return canonical(c); };
function canonical(o) {
  if (Array.isArray(o)) return "[" + o.map(canonical).join(",") + "]";
  if (o && typeof o === "object") return "{" + Object.keys(o).sort().map(k => JSON.stringify(k) + ":" + canonical(o[k])).join(",") + "}";
  return JSON.stringify(o);
}
function named(d, keys, kindFn) {
  const out = new Map();
  for (const key of keys) {
    const coll = d[key];
    if (!coll || typeof coll !== "object" || Array.isArray(coll)) continue;
    const kind = kindFn ? kindFn(key) : key;
    for (const [name, v] of Object.entries(coll)) {
      const k = kindFn ? `${kind}/${name}` : (key === keys[0] ? name : `${key}/${name}`);
      out.set(k, obj(k, v, { label: name, ref: { kind, name }, content: contentKey(v) }));
    }
  }
  return out;
}
const isLoadList = v => Array.isArray(v) && v.every(x => x && typeof x === "object" && !Array.isArray(x));
function loadTarget(it) {
  if ("member_uid" in it) return ["frame", String(it.member_uid)];
  if ("region_uid" in it) return ["shell", String(it.region_uid)];
  if ("uid" in it) return ["object", String(it.uid)];
  if ("point" in it) return ["joint", ptKey(it.point)];
  if ("story" in it) return ["story", String(it.story)];
  return ["", ""];
}
function loads(d) {
  const out = new Map();
  for (const [pname, pat] of Object.entries(d.patterns || {})) {
    if (!pat || typeof pat !== "object") continue;
    const def = {}, groups = new Map();
    for (const [k, v] of Object.entries(pat)) {
      if (isLoadList(v)) for (const it of v) {
        const [tk, tid] = loadTarget(it), gk = `${k}|${tk}:${tid}`;
        if (!groups.has(gk)) groups.set(gk, { lk: k, tk, tid, items: [] });
        groups.get(gk).items.push(it);
      } else def[k] = v;
    }
    out.set(pname, obj(pname, def, { label: `Pattern ${pname}`, ref: { kind: "pattern", name: pname } }));
    for (const [gk, g] of groups) {
      const key = `${pname}|${gk}`;
      const ref = g.tk === "frame" ? { kind: "member", uid: g.tid } : g.tk === "shell" ? { kind: "shell", uid: g.tid } : g.tk === "joint" ? { kind: "joint" } : null;
      out.set(key, obj(key, { items: g.items }, { label: `${pname} · ${g.lk.replace(/_/g, " ")} · ${g.tid}`, ref }));
    }
  }
  return out;
}
function listNamed(arr, kind, geoFn, contentOn) {
  const out = new Map();
  (arr || []).forEach((s, i) => {
    if (!s || typeof s !== "object") return;
    const name = String(s.name ?? `#${i}`);
    out.set(name, obj(name, s, { ref: { kind, name }, geo: geoFn ? geoFn(s) : null, content: contentOn ? contentKey(s) : null }));
  });
  return out;
}
function options(d) {
  const out = new Map();
  for (const [k, v] of Object.entries(d)) {
    if (DERIVED.has(k) || HANDLED.has(k) || isCaseKey(k)) continue;
    if (v && typeof v === "object" && !Array.isArray(v) && Object.keys(v).length && Object.values(v).every(x => x && typeof x === "object" && !Array.isArray(x))) {
      for (const [n, x] of Object.entries(v)) out.set(`${k}/${n}`, obj(`${k}/${n}`, x, { label: `${k.replace(/_/g, " ")}: ${n}`, ref: { kind: "option", key: k, name: n } }));
    } else out.set(k, obj(k, v, { label: k.replace(/_/g, " "), ref: { kind: "option", key: k } }));
  }
  return out;
}
export function mhCollect(d, types = MH_TYPES) {
  const w = new Set(types), C = {};
  if (w.has("frames")) C.frames = uidObjects(d.members, "member", frameGeo, frameGeom);
  if (w.has("shells")) C.shells = uidObjects(d.shells, "shell", shellGeo, s => ({ corners: s.corners }));
  if (w.has("links")) C.links = uidObjects(d.links, "link", frameGeo, frameGeom);
  if (w.has("joints")) C.joints = joints(d);
  if (w.has("supports")) C.supports = supports(d);
  if (w.has("sections")) C.sections = named(d, SECTION_KEYS);
  if (w.has("materials")) C.materials = named(d, ["materials"]);
  if (w.has("loads")) C.loads = loads(d);
  if (w.has("cases")) C.cases = named(d, Object.keys(d).filter(isCaseKey), caseKind);
  if (w.has("combos")) C.combos = named(d, ["combos", "rs_combos"]);
  if (w.has("stories")) C.stories = listNamed(d.stories, "story", s => "Z|" + r6(s.elevation || 0));
  if (w.has("grids")) C.grids = listNamed(d.grid_systems, "grid", null, true);
  if (w.has("options")) C.options = options(d);
  return C;
}

/* ---------------------------------------------------------------- diff */
function mkItem(type, status, oa, ob, fields, match = "key") {
  const o = ob || oa;
  const it = { type, key: o.key, label: o.label, status, match };
  if (oa && ob && oa.key !== ob.key) { it.key_a = oa.key; it.key_b = ob.key; it.label = `${oa.label} → ${ob.label}`; }
  if (fields) it.fields = fields;
  const ref = (ob || oa).ref;
  if (ref) it.ref = ref;
  const geom = status === "removed" ? oa && oa.geom : ob && ob.geom;
  if (geom) it.geom = geom;
  if (status === "changed" && oa && oa.geom && ob && ob.geom && !deepEq(oa.geom, ob.geom)) it.geom_a = oa.geom;
  return it;
}
function diffType(type, A, B, tol, items) {
  const s = { added: 0, removed: 0, changed: 0, unchanged: 0 };
  const remK = [...A.keys()].filter(k => !B.has(k)), addK = [...B.keys()].filter(k => !A.has(k));
  for (const [k, oa] of A) {
    const ob = B.get(k);
    if (!ob) continue;
    const f = deepEq(oa.rec, ob.rec) ? [] : fieldChanges(oa.rec, ob.rec, tol);
    if (!f.length) { s.unchanged++; continue; }
    s.changed++;
    items.push(mkItem(type, "changed", oa, ob, f));
  }
  const pa = new Set(), pb = new Set();
  if (remK.length && addK.length) {
    for (const [attr, match] of [["geo", "geometry"], ["content", "content"]]) {
      const idx = new Map();
      for (const k of addK) { if (pb.has(k)) continue; const g = B.get(k)[attr]; if (g != null) { if (!idx.has(g)) idx.set(g, []); idx.get(g).push(k); } }
      for (const k of remK) {
        if (pa.has(k)) continue;
        const g = A.get(k)[attr], cand = g != null ? idx.get(g) : null;
        if (!cand || !cand.length) continue;
        const kb = cand.shift();
        pa.add(k); pb.add(kb);
        const oa = A.get(k), ob = B.get(kb);
        const f = fieldChanges(oa.rec, ob.rec, tol);
        if (!f.some(x => x.path === "uid" || x.path === "name"))
          f.unshift({ path: ["frames", "shells", "links"].includes(type) ? "uid" : "name", a: oa.key, b: ob.key });
        s.changed++;
        items.push(mkItem(type, "changed", oa, ob, f, match));
      }
    }
  }
  for (const k of remK) if (!pa.has(k)) { s.removed++; items.push(mkItem(type, "removed", A.get(k), null)); }
  for (const k of addK) if (!pb.has(k)) { s.added++; items.push(mkItem(type, "added", null, B.get(k))); }
  return s;
}
export function mhDiff(a, b, { types = MH_TYPES, tol = 1e-12, max_items = null } = {}) {
  const t0 = performance.now();
  types = types.filter(t => MH_TYPES.includes(t));
  const CA = mhCollect(a, types), CB = mhCollect(b, types);
  const summary = {}, items = [];
  for (const t of types) summary[t] = diffType(t, CA[t] || new Map(), CB[t] || new Map(), tol, items);
  const n = items.length;
  const out = items.slice(0, max_items || n);
  const equal = Object.values(summary).every(s => !s.added && !s.removed && !s.changed);
  return { summary, items: out, n_items: n, truncated: out.length < n, equal, types, elapsed_ms: +(performance.now() - t0).toFixed(3) };
}

/* ---------------------------------------------------------------- restore */
function restoreList(cur, ref, keyf, selA, selB) {
  const refBy = new Map(), refKeys = [];
  (ref || []).forEach((x, i) => { const k = keyf(x, i); refBy.set(k, x); refKeys.push(k); });
  const out = [];
  (cur || []).forEach((x, i) => { const k = keyf(x, i); if (!selB.has(k)) out.push([k, x]); });
  const present = new Set(out.map(([k]) => k));
  refKeys.forEach((k, j) => {
    if (!selA.has(k) || present.has(k)) return;
    let pos = 0;
    for (let p = j - 1; p >= 0; p--) { const hit = out.findIndex(([ok]) => ok === refKeys[p]); if (hit >= 0) { pos = hit + 1; break; } }
    out.splice(pos, 0, [k, clone(refBy.get(k))]);
    present.add(k);
  });
  return out.map(([, x]) => x);
}
export function mhRestore(cur, ref, items = null) {
  const dd = mhDiff(ref, cur);
  const want = items ? new Set(items.map(i => `${i.type}\u0000${i.key}`)) : null;
  const chosen = dd.items.filter(it => !want || want.has(`${it.type}\u0000${it.key}`) || (it.key_a && want.has(`${it.type}\u0000${it.key_a}`)) || (it.key_b && want.has(`${it.type}\u0000${it.key_b}`)));
  const nw = clone(cur);
  const report = { restored: chosen.map(it => ({ type: it.type, key: it.key, status: it.status })), skipped: [] };
  const by = {};
  for (const it of chosen) (by[it.type] = by[it.type] || []).push(it);
  const ab = lst => {
    const sa = new Set(), sb = new Set();
    for (const it of lst) {
      if (it.status === "added") sb.add(it.key);
      else if (it.status === "removed") sa.add(it.key);
      else { sa.add(it.key_a || it.key); sb.add(it.key_b || it.key); }
    }
    return [sa, sb];
  };
  const uidK = (x, i) => String((x && x.uid) ?? `#${i}`), nameK = (x, i) => String((x && x.name) ?? `#${i}`);
  for (const [t, coll] of [["frames", "members"], ["shells", "shells"], ["links", "links"]])
    if (by[t]) { const [sa, sb] = ab(by[t]); nw[coll] = restoreList(nw[coll], ref[coll], uidK, sa, sb); }
  if (by.stories) { const [sa, sb] = ab(by.stories); nw.stories = restoreList(nw.stories, ref.stories, nameK, sa, sb); }
  if (by.grids) { const [sa, sb] = ab(by.grids); nw.grid_systems = restoreList(nw.grid_systems, ref.grid_systems, nameK, sa, sb); if (nw.grid_systems.length) nw.grid = clone(nw.grid_systems[0]); }
  if (by.supports) {
    const [sa, sb] = ab(by.supports);
    for (const [pre, coll, kf] of [["restraint@", "supports", x => "restraint@" + ptKey(x.point)], ["spring@", "spring_supports", x => "spring@" + ptKey(x.point)],
      ["line@", "line_springs", x => `line@${ptKey(x.p1)}|${ptKey(x.p2)}`]]) {
      const a2 = new Set([...sa].filter(k => k.startsWith(pre))), b2 = new Set([...sb].filter(k => k.startsWith(pre)));
      if (a2.size || b2.size) nw[coll] = restoreList(nw[coll], ref[coll], kf, a2, b2);
    }
  }
  if (by.joints) {
    const [sa, sb] = ab(by.joints), keys = new Set([...sa, ...sb]);
    nw.nodal_masses = restoreList(nw.nodal_masses, ref.nodal_masses, x => ptKey(x.point), keys, keys);
    if (!nw.nodal_masses.length && !cur.nodal_masses) delete nw.nodal_masses;
    for (const it of by.joints) if (it.status !== "changed") report.skipped.push({ type: "joints", key: it.key, reason: "joints follow the objects that use them" });
  }
  const touched = new Set();
  const restoreNamed = (lst, keyfn) => {
    for (const it of lst) {
      const [ca, na] = keyfn(it.key_a || it.key), [cb, nb] = keyfn(it.key_b || it.key);
      if ((it.status === "added" || it.status === "changed") && nw[cb] && typeof nw[cb] === "object") { delete nw[cb][nb]; touched.add(cb); }
      if (it.status === "removed" || it.status === "changed") {
        const src = ref[ca] || {};
        if (na in src) { if (!nw[ca] || typeof nw[ca] !== "object") nw[ca] = {}; nw[ca][na] = clone(src[na]); touched.add(ca); }
      }
    }
  };
  const split = k => { const i = k.indexOf("/"); return [k.slice(0, i), k.slice(i + 1)]; };
  if (by.sections) restoreNamed(by.sections, k => (k.includes("/") && SECTION_KEYS.slice(1).includes(split(k)[0])) ? split(k) : ["sections", k]);
  if (by.materials) restoreNamed(by.materials, k => ["materials", k]);
  if (by.combos) restoreNamed(by.combos, k => k.startsWith("rs_combos/") ? split(k) : ["combos", k]);
  if (by.cases) restoreNamed(by.cases, k => { const [kind, n] = split(k); return [kind === "static" ? "cases" : kind + "_cases", n]; });
  if (by.options) for (const it of by.options) {
    if (it.ref && it.ref.name != null && it.key.includes("/")) restoreNamed([it], split);
    else if (it.key in ref) nw[it.key] = clone(ref[it.key]); else delete nw[it.key];
  }
  for (const c of touched) if (nw[c] && ref[c] && typeof ref[c] === "object") {
    const rank = new Map(Object.keys(ref[c]).map((k, i) => [k, i])), cur0 = Object.keys(nw[c]), n = rank.size;
    const o = {};
    for (const k of [...cur0].sort((x, y) => (rank.has(x) ? rank.get(x) : n + cur0.indexOf(x)) - (rank.has(y) ? rank.get(y) : n + cur0.indexOf(y)))) o[k] = nw[c][k];
    nw[c] = o;
  }
  if (by.loads) restoreLoads(nw, ref, by.loads);
  return { model: nw, report };
}
function restoreLoads(nw, ref, lst) {
  const np = nw.patterns = nw.patterns || {}, rp = ref.patterns || {};
  for (const it of lst) {
    const key = it.key;
    if (!key.includes("|")) {
      if (it.status === "added") { delete np[key]; continue; }
      const src = rp[key];
      if (!src) continue;
      if (it.status === "removed" || !np[key]) { np[key] = clone(src); continue; }
      const cur = np[key];
      for (const [k, v] of Object.entries(src)) { if (!isLoadList(v)) cur[k] = clone(v); else if (!(k in cur)) cur[k] = []; }
      for (const k of Object.keys(cur)) if (!(k in src) && !isLoadList(cur[k])) delete cur[k];
      continue;
    }
    const [pname, lk, tgt] = [key.slice(0, key.indexOf("|")), ...(() => { const rest = key.slice(key.indexOf("|") + 1); const j = rest.indexOf("|"); return [rest.slice(0, j), rest.slice(j + 1)]; })()];
    const ci = tgt.indexOf(":"), tk = tgt.slice(0, ci), tid = tgt.slice(ci + 1);
    if (!np[pname]) { if (rp[pname]) np[pname] = clone(rp[pname]); continue; }
    const cur = np[pname];
    const match = x => { const [a, b] = loadTarget(x); return a === tk && b === tid; };
    const old = cur[lk] || [], kept = old.filter(x => !match(x));
    const back = ((rp[pname] || {})[lk] || []).filter(match).map(clone);
    let first = old.findIndex(match);
    if (first < 0) {
      const rl = (rp[pname] || {})[lk] || [];
      let ri = rl.findIndex(match); if (ri < 0) ri = rl.length;
      first = Math.min(rl.slice(0, ri).filter(x => !match(x)).length, kept.length);
    } else first = old.slice(0, first).filter(x => !match(x)).length;
    cur[lk] = [...kept.slice(0, first), ...back, ...kept.slice(first)];
  }
}

export function mhCounts(d) {
  return { stories: (d.stories || []).length, frames: (d.members || []).length, shells: (d.shells || []).length,
    links: (d.links || []).length, patterns: Object.keys(d.patterns || {}).length,
    cases: Object.entries(d).filter(([k, v]) => isCaseKey(k) && v && typeof v === "object").reduce((s, [, v]) => s + Object.keys(v).length, 0),
    combos: Object.keys(d.combos || {}).length };
}

/* ---------------------------------------------------------------- store */
const MAX_N = 20, MAX_CHARS = 4e6;
const skey = k => "sky.mh.mock." + String(k || "Untitled").replace(/[^A-Za-z0-9 _.-]+/g, "_").slice(0, 80);
function load(key) { try { const o = JSON.parse(localStorage.getItem(skey(key)) || "null"); return o && Array.isArray(o.checkpoints) ? o : { checkpoints: [] }; } catch { return { checkpoints: [] }; } }
function save(key, st) { try { localStorage.setItem(skey(key), JSON.stringify(st)); return true; } catch { return false; } }
function prune(st, keep) {
  const removed = [];
  const size = () => st.checkpoints.reduce((s, c) => s + (c.chars || 0), 0);
  for (const kind of ["auto", "manual"]) {
    for (const c of [...st.checkpoints].sort((a, b) => a.created - b.created)) {
      if (!(st.checkpoints.length > MAX_N || size() > MAX_CHARS)) break;
      if (c.kind !== kind || c.pinned || c.id === keep) continue;
      st.checkpoints = st.checkpoints.filter(x => x !== c);
      removed.push(c.id);
    }
  }
  return removed;
}
async function sha(text) {
  try {
    const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");
  } catch { let h = 0; for (let i = 0; i < text.length; i++) h = (h * 31 + text.charCodeAt(i)) | 0; return "x" + (h >>> 0).toString(16); }
}
export const mhMockStore = {
  list(key) {
    const st = load(key);
    const cps = st.checkpoints.map(({ model, ...m }) => m).sort((a, b) => b.created - a.created);
    return { key: String(key), dir: "localStorage (mock)", settings: { max_checkpoints: MAX_N, max_auto: MAX_N, max_bytes: MAX_CHARS }, checkpoints: cps,
      total_bytes: cps.reduce((s, c) => s + (c.bytes || 0), 0) };
  },
  async add(key, model, { name = "", note = "", kind = "manual", reason = "", dedupe } = {}) {
    const m = clone(model); delete m._mock_params;
    const text = JSON.stringify(m), hash = await sha(canonical(m));
    const st = load(key);
    if (dedupe === undefined) dedupe = kind === "auto";
    const newest = [...st.checkpoints].sort((a, b) => b.created - a.created)[0];
    if (dedupe && newest && newest.hash === hash) { const { model: _m, ...meta } = newest; return { checkpoint: meta, pruned: [], skipped: true }; }
    let created = Date.now() / 1000;
    if (newest && created <= newest.created) created = newest.created + 1e-3;
    const cp = { id: Math.random().toString(16).slice(2, 14).padEnd(12, "0"), name: String(name).slice(0, 120), note: String(note).slice(0, 2000),
      kind, reason: String(reason).slice(0, 80), created, hash, bytes: text.length, raw_bytes: text.length, chars: text.length, pinned: false,
      model_name: m.name || "", counts: mhCounts(m), model: m };
    st.checkpoints.push(cp);
    const pruned = prune(st, cp.id);
    if (!save(key, st)) { st.checkpoints = st.checkpoints.filter(c => c.id !== cp.id); throw new Error("browser storage is full — delete old checkpoints"); }
    const { model: _m, ...meta } = cp;
    return { checkpoint: meta, pruned, skipped: false };
  },
  get(key, id) {
    const c = load(key).checkpoints.find(x => x.id === id);
    if (!c) throw new Error(`no checkpoint ${id}`);
    const { model, ...meta } = c;
    return { checkpoint: meta, model: clone(model) };
  },
  update(key, id, patch) {
    const st = load(key), c = st.checkpoints.find(x => x.id === id);
    if (!c) throw new Error(`no checkpoint ${id}`);
    if (patch.name != null) c.name = String(patch.name).slice(0, 120);
    if (patch.note != null) c.note = String(patch.note).slice(0, 2000);
    if (patch.pinned != null) c.pinned = !!patch.pinned;
    save(key, st);
    const { model, ...meta } = c;
    return { checkpoint: meta };
  },
  remove(key, id) {
    const st = load(key), n = st.checkpoints.length;
    st.checkpoints = st.checkpoints.filter(c => c.id !== id);
    save(key, st);
    return { ok: st.checkpoints.length < n };
  },
};
