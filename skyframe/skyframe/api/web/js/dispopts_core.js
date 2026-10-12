/* SkyFrame — Display Options: pure data layer (no DOM).

   ETABS "View › Set Display Options" (Ctrl+W) keeps one set of display
   options PER WINDOW. SkyFrame's windows are the main Plan, Elevation and
   3D views plus the panes of View › Windows ("w1" … "w4"). This module
   owns the schema, its normalisation, the sparse (de)serialisation used
   for persistence, the mapping onto the 3D renderer's options (js/viewext.js
   "vx" options — the single 3D implementation, so no option is duplicated)
   and the colour-by rules. It is imported by js/dispopts.js (UI + hooks),
   js/viewext.js (colour-by) and the node unit tests
   (tests/test_display_options.py).

   Settings object (every key always present after dpNormalize):
     objects  joint beam column brace wall slab link tendon support spring load  (bool)
     labels   frameName frameSection frameAxes frameReleases frameInsertion
              shellName shellSection shellAxes linkName                          (bool)
     view     extrude extrudeShells fill edges shrink (bool), colorBy (DP_COLOR_BY),
              shellTransp 0 … 0.9
     other    grid bubbles storyLabels dims (bool), background (DP_BACKGROUNDS),
              jointSize 1 … 12 px, lineScale 0.5 … 4
     filter   group (name|""), selOnly (bool), storyFrom, storyTo, loadPattern
              (name|""), cut {on, axis x|y|z, coord m|null, keep below|above}

   Persistence is SPARSE: only values that differ from the defaults are
   written ({"v": 1, "s": {...}}), so the defaults can evolve and a window
   left at its defaults stores nothing. Display options are view state,
   never model state: nothing here is written to the model. */

export const DP_VERSION = 1;

export const DP_OBJECTS = [
  ["joint", "Joints"], ["beam", "Beams"], ["column", "Columns"], ["brace", "Braces"],
  ["wall", "Walls"], ["slab", "Floors (slabs)"], ["link", "Links"], ["tendon", "Tendons"],
  ["support", "Supports"], ["spring", "Springs"], ["load", "Loads"],
];
export const DP_LABELS = [
  ["frameName", "Labels", "Frames"], ["frameSection", "Sections", "Frames"], ["frameAxes", "Local axes", "Frames"],
  ["frameReleases", "Releases", "Frames"], ["frameInsertion", "Insertion points", "Frames"],
  ["shellName", "Labels", "Shells"], ["shellSection", "Sections", "Shells"], ["shellAxes", "Local axes", "Shells"],
  ["linkName", "Labels", "Links"],
];
export const DP_COLOR_BY = [
  ["type", "Object type"], ["section", "Section"], ["material", "Material"],
  ["group", "Group"], ["story", "Story"], ["property", "Property type"],
];
export const DP_BACKGROUNDS = [["theme", "Theme (default)"], ["white", "White"], ["black", "Black"], ["grey", "Grey"]];
export const DP_BACKGROUND_CSS = { theme: "", white: "#ffffff", black: "#000000", grey: "#3a4048" };
export const DP_WINDOWS = ["plan", "elev", "3d", "w1", "w2", "w3", "w4"];

export function dpDefaults() {
  return {
    objects: { joint: false, beam: true, column: true, brace: true, wall: true, slab: true, link: true,
      tendon: true, support: true, spring: true, load: false },
    labels: { frameName: false, frameSection: false, frameAxes: false, frameReleases: false, frameInsertion: false,
      shellName: false, shellSection: false, shellAxes: false, linkName: false },
    view: { extrude: false, extrudeShells: true, fill: true, edges: true, shrink: false, colorBy: "type", shellTransp: 0 },
    other: { grid: true, bubbles: true, storyLabels: true, dims: true, background: "theme", jointSize: 4, lineScale: 1 },
    filter: { group: "", selOnly: false, storyFrom: "", storyTo: "", loadPattern: "",
      cut: { on: false, axis: "x", coord: null, keep: "below" } },
  };
}

const isObj = o => !!o && typeof o === "object" && !Array.isArray(o);
const clamp = (v, lo, hi, d) => { const x = +v; return isFinite(x) ? Math.min(hi, Math.max(lo, x)) : d; };
export const dpClone = o => JSON.parse(JSON.stringify(o));

/** Full, valid settings from anything (unknown keys dropped, bad values → default). */
export function dpNormalize(o) {
  const d = dpDefaults();
  if (!isObj(o)) return d;
  for (const sec of ["objects", "labels"]) {
    const src = isObj(o[sec]) ? o[sec] : {};
    for (const k of Object.keys(d[sec])) if (k in src) d[sec][k] = !!src[k];
  }
  const v = isObj(o.view) ? o.view : {};
  for (const k of ["extrude", "extrudeShells", "fill", "edges", "shrink"]) if (k in v) d.view[k] = !!v[k];
  if (DP_COLOR_BY.some(([k]) => k === v.colorBy)) d.view.colorBy = v.colorBy;
  if ("shellTransp" in v) d.view.shellTransp = clamp(v.shellTransp, 0, 0.9, 0);
  const t = isObj(o.other) ? o.other : {};
  for (const k of ["grid", "bubbles", "storyLabels", "dims"]) if (k in t) d.other[k] = !!t[k];
  if (DP_BACKGROUNDS.some(([k]) => k === t.background)) d.other.background = t.background;
  if ("jointSize" in t) d.other.jointSize = clamp(t.jointSize, 1, 12, 4);
  if ("lineScale" in t) d.other.lineScale = clamp(t.lineScale, 0.5, 4, 1);
  const f = isObj(o.filter) ? o.filter : {};
  for (const k of ["group", "storyFrom", "storyTo", "loadPattern"]) if (typeof f[k] === "string") d.filter[k] = f[k];
  if ("selOnly" in f) d.filter.selOnly = !!f.selOnly;
  const c = isObj(f.cut) ? f.cut : {};
  if ("on" in c) d.filter.cut.on = !!c.on;
  if (["x", "y", "z"].includes(c.axis)) d.filter.cut.axis = c.axis;
  if (["below", "above"].includes(c.keep)) d.filter.cut.keep = c.keep;
  if (c.coord != null && c.coord !== "" && isFinite(+c.coord)) d.filter.cut.coord = +c.coord;
  return d;
}

/** Sparse diff of `o` against `base` (default: the defaults) — only the leaves that differ. */
export function dpDiff(o, base = dpDefaults()) {
  const walk = (a, b) => {
    const out = {};
    for (const k of Object.keys(a)) {
      if (isObj(a[k]) && isObj(b[k])) { const s = walk(a[k], b[k]); if (Object.keys(s).length) out[k] = s; }
      else if (JSON.stringify(a[k]) !== JSON.stringify(b[k])) out[k] = a[k];
    }
    return out;
  };
  return walk(dpNormalize(o), dpNormalize(base));
}
/** Deep merge of a sparse patch onto full settings (returns a new normalised object). */
export function dpMerge(full, patch) {
  const out = dpClone(dpNormalize(full));
  const walk = (dst, src) => {
    if (!isObj(src)) return;
    for (const [k, v] of Object.entries(src)) {
      if (isObj(v) && isObj(dst[k])) walk(dst[k], v); else if (k in dst) dst[k] = v;
    }
  };
  walk(out, patch);
  return dpNormalize(out);
}
export const dpIsDefault = o => Object.keys(dpDiff(o)).length === 0;
export const dpEqual = (a, b) => JSON.stringify(dpNormalize(a)) === JSON.stringify(dpNormalize(b));

/** Settings → persisted JSON text (versioned, sparse). */
export function dpSerialize(o) { return JSON.stringify({ v: DP_VERSION, s: dpDiff(o) }); }
/** Persisted JSON text (or object) → full settings. Tolerant: bad input → defaults. */
export function dpDeserialize(txt) {
  let o = txt;
  if (typeof txt === "string") { try { o = JSON.parse(txt); } catch { return dpDefaults(); } }
  if (!isObj(o)) return dpDefaults();
  if ("v" in o) {
    if (+o.v !== DP_VERSION) return dpDefaults();   // future / unknown versions: start clean
    return dpMerge(dpDefaults(), isObj(o.s) ? o.s : {});
  }
  return dpMerge(dpDefaults(), o);                    // bare (unversioned) settings
}

/** Whole store {windows: {key: settings}, default: settings|null} ↔ text. */
export function dpSerializeStore(st) {
  const w = {};
  for (const [k, v] of Object.entries((st && st.windows) || {})) {
    if (!DP_WINDOWS.includes(k)) continue;
    const s = dpDiff(v, st.default || dpDefaults());
    if (Object.keys(s).length) w[k] = s;
  }
  const out = { v: DP_VERSION, windows: w };
  if (st && st.default && !dpIsDefault(st.default)) out.default = dpDiff(st.default);
  return JSON.stringify(out);
}
export function dpDeserializeStore(txt) {
  let o = txt;
  if (typeof txt === "string") { try { o = JSON.parse(txt); } catch { o = null; } }
  const st = { windows: {}, default: null };
  if (!isObj(o) || +o.v !== DP_VERSION) return st;
  if (isObj(o.default)) st.default = dpMerge(dpDefaults(), o.default);
  const base = st.default || dpDefaults();
  for (const [k, v] of Object.entries(isObj(o.windows) ? o.windows : {}))
    if (DP_WINDOWS.includes(k) && isObj(v)) st.windows[k] = dpMerge(base, v);
  return st;
}

/* ================================================================
   3D: settings ↔ viewext ("vx") options. vx stays the single 3D
   implementation; options it does not know travel in vx.dp (sparse).
   ================================================================ */
const VX_SHOW = ["beam", "column", "brace", "wall", "slab", "link", "spring", "support", "joint", "load"];

/** settings → full vx options object (as accepted by sky.vx.set). */
export function dpToVx(s) {
  s = dpNormalize(s);
  const show = {};
  for (const k of VX_SHOW) show[k] = s.objects[k];
  show.axes = s.labels.frameAxes;
  show.label = s.labels.frameName;
  show.secname = s.labels.frameSection;
  show.grid = s.other.grid;
  const f = s.filter;
  const out = {
    extrude: s.view.extrude, extrudeShells: s.view.extrudeShells, edges: s.view.edges,
    colorBy: s.view.colorBy, shellTransp: s.view.shellTransp, show,
    loadPattern: f.loadPattern, storyFrom: f.storyFrom, storyTo: f.storyTo, group: f.group, selOnly: f.selOnly,
    cut: { ...f.cut },
  };
  // extras the 3D renderer reads from vx.dp (absent = default behaviour)
  const dp = {};
  if (!s.objects.tendon) dp.tendon = false;
  if (s.labels.shellName !== s.labels.frameName) dp.shellName = s.labels.shellName;
  if (s.labels.shellAxes !== s.labels.frameAxes) dp.shellAxes = s.labels.shellAxes;
  for (const k of ["frameReleases", "frameInsertion", "shellSection", "linkName"]) if (s.labels[k]) dp[k] = true;
  if (!s.view.fill) dp.fill = false;
  if (s.view.shrink) dp.shrink = true;
  if (!s.other.bubbles) dp.bubbles = false;
  if (s.other.background !== "theme") dp.background = s.other.background;
  if (s.other.jointSize !== 4) dp.jointSize = s.other.jointSize;
  if (s.other.lineScale !== 1) dp.lineScale = s.other.lineScale;
  if (!s.other.storyLabels) dp.storyLabels = false;
  if (!s.other.dims) dp.dims = false;
  out.dp = Object.keys(dp).length ? dp : null;
  return out;
}

/** vx options → settings (inverse of dpToVx; unknown vx keys ignored). */
export function dpFromVx(vx) {
  const s = dpDefaults();
  if (!isObj(vx)) return s;
  const sh = isObj(vx.show) ? vx.show : {};
  for (const k of VX_SHOW) if (k in sh) s.objects[k] = !!sh[k];
  if ("axes" in sh) s.labels.frameAxes = s.labels.shellAxes = !!sh.axes;
  if ("label" in sh) s.labels.frameName = s.labels.shellName = !!sh.label;
  if ("secname" in sh) s.labels.frameSection = !!sh.secname;
  if ("grid" in sh) s.other.grid = !!sh.grid;
  for (const k of ["extrude", "extrudeShells", "edges"]) if (k in vx) s.view[k] = !!vx[k];
  if ("colorBy" in vx) s.view.colorBy = vx.colorBy;
  if ("shellTransp" in vx) s.view.shellTransp = vx.shellTransp;
  for (const k of ["loadPattern", "storyFrom", "storyTo", "group"]) if (typeof vx[k] === "string") s.filter[k] = vx[k];
  if ("selOnly" in vx) s.filter.selOnly = !!vx.selOnly;
  if (isObj(vx.cut)) s.filter.cut = { ...s.filter.cut, ...vx.cut };
  const dp = isObj(vx.dp) ? vx.dp : {};
  if (dp.tendon === false) s.objects.tendon = false;
  if ("shellName" in dp) s.labels.shellName = !!dp.shellName;
  if ("shellAxes" in dp) s.labels.shellAxes = !!dp.shellAxes;
  for (const k of ["frameReleases", "frameInsertion", "shellSection", "linkName"]) if (dp[k]) s.labels[k] = true;
  if (dp.fill === false) s.view.fill = false;
  if (dp.shrink) s.view.shrink = true;
  if (dp.bubbles === false) s.other.bubbles = false;
  if (dp.background) s.other.background = dp.background;
  if ("jointSize" in dp) s.other.jointSize = dp.jointSize;
  if ("lineScale" in dp) s.other.lineScale = dp.lineScale;
  if (dp.storyLabels === false) s.other.storyLabels = false;
  if (dp.dims === false) s.other.dims = false;
  return dpNormalize(s);
}

/* ================================================================
   Colour by
   ================================================================ */
export const DP_TYPE_COLORS = { column: "#5f8fc9", beam: "#8d9db1", brace: "#d39316", wall: "#7fa6d6", slab: "#a7b1bc", link: "#34c384" };
export const DP_PALETTE = ["#4e9bd6", "#e0884a", "#5bbf7a", "#c9636f", "#9a7fd1", "#d1b24a", "#4fc1c9",
  "#d67fb5", "#8fb94a", "#b98a63", "#6f8fd6", "#c96f4a", "#62b39b", "#b06fc9", "#a0a0a0", "#d6d05c"];
export const DP_NONE_COLOR = "#6b7480";   // "not in any group" etc.
export const dpHash = s => { let h = 0; s = String(s); for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return Math.abs(h); };

/** Frame section → property type (analysis-only replacement for ETABS'
    "design procedure" colouring). */
export function dpPropertyType(model, secName) {
  const sec = ((model && model.sections) || {})[secName];
  if (!sec) return "General";
  if (Array.isArray(sec.segments) && sec.segments.length) return "Nonprismatic";
  const shape = String(sec.shape || "").toLowerCase();
  if (shape === "designer" || ((model.designer_sections || {})[secName] && !(sec.b > 0 && sec.h > 0))) return "Section Designer";
  if (shape === "i" || shape === "w" || (sec.tf > 0 && sec.tw > 0)) return "I / Wide Flange";
  if (shape === "pipe") return "Pipe";
  if (shape === "circle" || shape === "circular") return "Circular";
  if (shape === "box" || shape === "tube") return "Tube";
  if (shape === "rect" || shape === "rectangular" || (sec.b > 0 && sec.h > 0)) return "Rectangular";
  return "General";
}

/** Group an object belongs to (first by name), or "". type: member|shell|link. */
export function dpGroupOf(model, type, uid) {
  const key = type === "shell" ? "shells" : type === "link" ? "links" : "members";
  const gs = (model && model.groups) || {};
  for (const name of Object.keys(gs).sort()) if ((gs[name][key] || []).map(String).includes(String(uid))) return name;
  return "";
}

/** Colour-by key of an object: {type: member|shell|link, kind, uid, section, story}. */
export function dpColorKey(model, by, obj) {
  const kind = obj.kind || (obj.type === "link" ? "link" : "beam");
  if (by === "section") return obj.section ? "sec:" + obj.section : "";
  if (by === "material") {
    if (obj.type === "shell") { const ss = ((model && model.shell_sections) || {})[obj.section] || {}; return ss.material ? "mat:" + ss.material : ""; }
    const sec = ((model && model.sections) || {})[obj.section] || {};
    return sec.material ? "mat:" + sec.material : "";
  }
  if (by === "group") { const g = dpGroupOf(model, obj.type, obj.uid); return g ? "grp:" + g : ""; }
  if (by === "story") return obj.story ? "sty:" + obj.story : "";
  if (by === "property") {
    if (obj.type === "shell") return "prop:" + (kind === "wall" ? "Wall" : "Slab") + (obj.behavior === "membrane" ? " (membrane)" : "");
    if (obj.type === "link") return "prop:Link " + (obj.link_type || "elastic");
    return "prop:" + dpPropertyType(model, obj.section);
  }
  return "type:" + kind;
}

/** Colour of an object under a colour-by mode (deterministic; css hex). */
export function dpColorOf(model, by, obj) {
  const kind = obj.kind || (obj.type === "link" ? "link" : "beam");
  if (!by || by === "type") return DP_TYPE_COLORS[kind] || DP_TYPE_COLORS.beam;
  const key = dpColorKey(model, by, obj);
  if (!key) return DP_NONE_COLOR;
  if (by === "group") {
    const g = ((model && model.groups) || {})[key.slice(4)];
    if (g && typeof g.color === "string" && /^#[0-9a-f]{3,8}$/i.test(g.color)) return g.color;
  }
  if (by === "section") {
    // stable order = index of the section in the model (frames, then shells)
    const names = [...Object.keys((model && model.sections) || {}), ...Object.keys((model && model.shell_sections) || {})];
    const i = names.indexOf(obj.section);
    if (i >= 0) return DP_PALETTE[i % DP_PALETTE.length];
  }
  if (by === "story") {
    const st = ((model && model.stories) || []).slice().sort((a, b) => a.elevation - b.elevation).map(s => s.name);
    const i = st.indexOf(obj.story);
    if (i >= 0) return DP_PALETTE[i % DP_PALETTE.length];
  }
  return DP_PALETTE[(dpHash(key) + 3) % DP_PALETTE.length];
}

/** Legend rows [[label, colour]] of the objects in a model under a mode. */
export function dpLegend(model, by) {
  if (!model) return [];
  const seen = new Map();
  const add = obj => {
    const key = by === "type" ? "type:" + obj.kind : dpColorKey(model, by, obj);
    const label = key ? key.slice(key.indexOf(":") + 1) : "(none)";
    if (!seen.has(label)) seen.set(label, dpColorOf(model, by, obj));
  };
  for (const mm of model.members || []) add({ type: "member", kind: mm.kind, uid: mm.uid, section: mm.section, story: mm.story });
  for (const sh of model.shells || []) add({ type: "shell", kind: sh.kind, uid: sh.uid, section: sh.section, story: sh.story, behavior: sh.behavior });
  for (const lk of model.links || []) add({ type: "link", kind: "link", uid: lk.uid, story: lk.story, link_type: lk.link_type });
  return [...seen.entries()].slice(0, 40);
}

/* ================================================================
   Label helpers (releases, insertion points)
   ================================================================ */
/** Released ends of a frame: {i: [dof…], j: [dof…]} (legacy Mi/Mj tokens + end_releases). */
export function dpReleases(mm) {
  const out = { i: [], j: [] };
  const toks = String((mm && mm.releases) || "").split(",").map(t => t.trim());
  if (toks.includes("Mi")) out.i.push("M3");
  if (toks.includes("Mj")) out.j.push("M3");
  const er = mm && mm.end_releases;
  if (isObj(er)) for (const e of ["i", "j"]) for (const k of Object.keys(er[e] || {})) if (!out[e].includes(k)) out[e].push(k);
  return out;
}
/** Insertion-point label of a frame ("" when at the centroid with no offsets). */
export function dpInsertionLabel(mm) {
  if (!mm) return "";
  const cp = mm.cardinal_point == null ? 10 : +mm.cardinal_point;
  const jo = mm.joint_offsets;
  const off = isObj(jo) && ["i", "j"].some(e => Array.isArray(jo[e]) && jo[e].some(v => Math.abs(+v || 0) > 1e-12));
  if (cp === 10 && !off) return "";
  return (cp !== 10 ? "CP" + cp : "") + (off ? (cp !== 10 ? " + offset" : "offset") : "");
}

/** Shrink a polyline / polygon toward its centroid (factor 0 … 1). */
export function dpShrinkPts(pts, f = 0.8) {
  if (!pts || !pts.length) return pts;
  const n = pts.length, dim = pts[0].length;
  const c = Array.from({ length: dim }, (_, k) => pts.reduce((a, p) => a + p[k], 0) / n);
  return pts.map(p => p.map((v, k) => c[k] + (v - c[k]) * f));
}
export const DP_SHRINK = 0.8;

export default {
  DP_VERSION, DP_OBJECTS, DP_LABELS, DP_COLOR_BY, DP_BACKGROUNDS, DP_WINDOWS,
  dpDefaults, dpNormalize, dpDiff, dpMerge, dpIsDefault, dpEqual, dpSerialize, dpDeserialize,
  dpSerializeStore, dpDeserializeStore, dpToVx, dpFromVx, dpColorKey, dpColorOf, dpLegend,
  dpPropertyType, dpGroupOf, dpReleases, dpInsertionLabel, dpShrinkPts, dpClone,
};
