/* SkyFrame Wave H — docking layout model (pure, no DOM; node-testable).

   A layout is a tree of SPLITS (resizable, row = side by side / col =
   stacked) whose leaves are TAB GROUPS of view windows:

     { v: 1,
       root:   null | Node,                      null = the classic single view
       wins:   { "w1": WinState, ... },          every window, docked or popped
       active: "w1",                             the window menus act on
       next:   5,                                next window number (ids never reused)
       links:  [["w1","w2"], ...],               linked-camera groups
       popped: { "w3": { near, zone } },         windows living in their own OS window
       preset: "2v" | null }                     View › Windows preset it came from

     Node = { t: "split", dir: "row"|"col", sizes: [f, ...] (sum 1), kids: [Node, ...] }
          | { t: "tabs", wins: ["w1", ...], cur: "w1" }

     WinState = { id, kind: "plan"|"elev"|"3d"|"table"|"plot",
                  story?, line?, camera?, table?, plot?, res?, title?, cmp? }

   `res` is the window's own results state (Wave G layer: sel = case / step /
   envelope or null = follows the results toolbar, view = display choice).

   View › Windows presets 1 / 2v / 2h / 3 / 4 build exactly the old fixed
   tiles (js/wb_views.js); hMigrate() turns an old named-view
   `display.wb_windows` = {layout, panes} into a layout. Arrange Windows
   follows ETABS: up to 10 tiles (the 3rd window splits the 2nd, the 4th
   splits the 1st, …), further windows become tabs of the first window.
   CONTRACT "Windows, pop-outs and side-by-side compare (Wave H)". */

export const H_VERSION = 1;
export const H_KINDS = ["plan", "elev", "3d", "table", "plot"];
export const H_MAX_TILES = 10;
export const H_MAX_WINDOWS = 40;
export const H_PRESETS = { 1: "Single Window", "2v": "Two Windows (Side by Side)", "2h": "Two Windows (Stacked)", 3: "Three Windows", 4: "Four Windows (2 × 2)" };
const LEGACY_KINDS = ["plan", "3d", "elev", "plan"];
const ZONES = ["center", "left", "right", "top", "bottom"];
const MIN_SIZE = 0.05;

const clone = o => (o == null ? o : JSON.parse(JSON.stringify(o)));
const r4 = v => Math.round(v * 1e4) / 1e4;
const isId = s => typeof s === "string" && /^w\d{1,4}$/.test(s);

/* ================================================================ windows */
/** A clean window state (unknown keys dropped, kind checked). */
export function hWinState(id, st = {}) {
  const kind = H_KINDS.includes(st.kind) ? st.kind : "plan";
  const o = { id, kind };
  for (const k of ["story", "line", "title"]) if (typeof st[k] === "string" && st[k]) o[k] = st[k].slice(0, 120);
  if (st.camera && typeof st.camera === "object") {
    const c = {};
    for (const [k, v] of Object.entries(st.camera)) {
      if (Array.isArray(v) && v.length === 3 && v.every(Number.isFinite)) c[k] = v.slice();
      else if (Number.isFinite(v)) c[k] = v;
    }
    if (Object.keys(c).length) o.camera = c;
  }
  for (const k of ["table", "plot", "res", "cmp", "disp", "dp"]) if (st[k] && typeof st[k] === "object") o[k] = clone(st[k]);
  return o;
}

/* ================================================================ tree helpers */
export function hEmpty() { return { v: H_VERSION, root: null, wins: {}, active: null, next: 1, links: [], popped: {}, preset: null }; }
const tabs = (ids, cur) => ({ t: "tabs", wins: ids.slice(), cur: cur || ids[0] });
const split = (dir, kids, sizes) => ({ t: "split", dir, kids, sizes: sizes || kids.map(() => 1 / kids.length) });

/** Window ids in tree order (depth first, tab order). */
export function hWinIds(L) {
  const out = [];
  const walk = n => { if (!n) return; if (n.t === "tabs") out.push(...n.wins); else n.kids.forEach(walk); };
  walk(L && L.root);
  return out;
}
/** Tab groups in tree order. */
export function hGroups(L) {
  const out = [];
  const walk = n => { if (!n) return; if (n.t === "tabs") out.push(n); else n.kids.forEach(walk); };
  walk(L && L.root);
  return out;
}
/** {node, parent, index, path} of the tab group holding window `id`. */
export function hFind(L, id) {
  let hit = null;
  const walk = (n, parent, index, path) => {
    if (!n || hit) return;
    if (n.t === "tabs") { if (n.wins.includes(id)) hit = { node: n, parent, index, path }; return; }
    n.kids.forEach((k, i) => walk(k, n, i, path.concat(i)));
  };
  walk(L && L.root, null, -1, []);
  return hit;
}
/** The split node at a kid-index path from the root. */
export function hNodeAt(L, path) {
  let n = L && L.root;
  for (const i of path || []) { if (!n || n.t !== "split") return null; n = n.kids[i]; }
  return n || null;
}
/** Visible (current-tab) windows in tree order. */
export const hVisible = L => hGroups(L).map(g => g.cur);

/** Collapse empty groups, single-kid splits and same-direction nesting; renormalise sizes. */
export function hNormalize(L, keep = []) {
  const fix = n => {
    if (!n) return null;
    if (n.t === "tabs") {
      n.wins = n.wins.filter((id, i, a) => L.wins[id] && !L.popped[id] && a.indexOf(id) === i);
      if (!n.wins.length) return null;
      if (!n.wins.includes(n.cur)) n.cur = n.wins[0];
      return n;
    }
    const kids = [], sizes = [];
    n.kids.forEach((k, i) => {
      const f = fix(k);
      if (!f) return;
      const s = Number.isFinite(n.sizes[i]) && n.sizes[i] > 0 ? n.sizes[i] : 1 / n.kids.length;
      if (f.t === "split" && f.dir === n.dir) { const tot = f.sizes.reduce((a, b) => a + b, 0) || 1; f.kids.forEach((kk, j) => { kids.push(kk); sizes.push(s * f.sizes[j] / tot); }); }
      else { kids.push(f); sizes.push(s); }
    });
    if (!kids.length) return null;
    if (kids.length === 1) return kids[0];
    const tot = sizes.reduce((a, b) => a + b, 0) || 1;
    n.kids = kids;
    n.sizes = sizes.map(s => Math.max(MIN_SIZE, s / tot));
    const t2 = n.sizes.reduce((a, b) => a + b, 0);
    n.sizes = n.sizes.map(s => r4(s / t2));
    return n;
  };
  L.root = fix(L.root);
  const ids = new Set(hWinIds(L));
  for (const id of Object.keys(L.popped || {})) if (!L.wins[id]) delete L.popped[id];
  for (const id of Object.keys(L.wins)) if (!ids.has(id) && !(L.popped && L.popped[id]) && !keep.includes(id)) delete L.wins[id];
  L.links = (L.links || []).map(g => g.filter((id, i, a) => L.wins[id] && a.indexOf(id) === i)).filter(g => g.length > 1);
  if (!L.wins[L.active] || (L.popped && L.popped[L.active])) L.active = hWinIds(L)[0] || null;
  return L;
}

/** Insert window `id` next to window `target` in drop zone `zone`. */
function insertAt(L, id, target, zone) {
  if (!L.root) { L.root = tabs([id]); return; }
  const f = target ? hFind(L, target) : null;
  if (!f) {                                                     // no target: to the right of everything
    L.root = split("row", [L.root, tabs([id])], [0.5, 0.5]);
    return;
  }
  if (zone === "center" || !ZONES.includes(zone)) { f.node.wins.push(id); f.node.cur = id; return; }
  const dir = zone === "left" || zone === "right" ? "row" : "col";
  const before = zone === "left" || zone === "top";
  const nt = tabs([id]);
  const p = f.parent;
  if (p && p.dir === dir) {
    const s = p.sizes[f.index] / 2;
    p.sizes[f.index] = s;
    const at = before ? f.index : f.index + 1;
    p.kids.splice(at, 0, nt);
    p.sizes.splice(at, 0, s);
    return;
  }
  const sp = split(dir, before ? [nt, f.node] : [f.node, nt], [0.5, 0.5]);
  if (!p) L.root = sp; else p.kids[f.index] = sp;
}

/* ================================================================ builders */
/** Legacy View › Windows presets (exact old tile arrangement). */
export function hPreset(key, paneStates = [], base = null) {
  key = String(key);
  if (!H_PRESETS[key]) key = "1";
  const L = hEmpty();
  if (key === "1") return L;
  const n = key === "2v" || key === "2h" ? 2 : +key;
  const ids = [];
  for (let i = 0; i < n; i++) {
    const st = (paneStates && paneStates[i]) || (base && base[i]) || {};
    const id = "w" + L.next++;
    L.wins[id] = hWinState(id, { kind: LEGACY_KINDS[i], ...st });
    ids.push(id);
  }
  const T = ids.map(id => tabs([id]));
  if (key === "2v") L.root = split("row", T);
  else if (key === "2h") L.root = split("col", T);
  else if (key === "3") L.root = split("row", [T[0], split("col", [T[1], T[2]])]);
  else L.root = split("col", [split("row", [T[0], T[1]]), split("row", [T[2], T[3]])]);
  L.active = ids[0];
  L.preset = key;
  return L;
}

/** ETABS Arrange Windows: up to 10 tiles, the rest tabs of the first. */
export function hArrangeTree(ids) {
  const n = Math.min(ids.length, H_MAX_TILES);
  const tiles = ids.slice(0, n), extra = ids.slice(n);
  if (!n) return null;
  let root;
  if (n === 1) root = tabs([tiles[0]]);
  else if (n === 2) root = split("row", [tabs([tiles[0]]), tabs([tiles[1]])]);
  else {
    const cols = Math.ceil(n / 2);
    const colWins = tiles.slice(0, cols).map(id => [id]);
    // remaining windows split the columns from the LAST one backwards (3rd splits 2nd, 4th splits 1st)
    tiles.slice(cols).forEach((id, k) => { colWins[cols - 1 - k].push(id); });
    root = split("row", colWins.map(c => (c.length === 1 ? tabs(c) : split("col", c.map(id => tabs([id]))))));
  }
  if (extra.length) {
    const first = root.t === "tabs" ? root : (function find(nd) { return nd.t === "tabs" ? nd : find(nd.kids[0]); })(root);
    first.wins.push(...extra);
  }
  return root;
}

/** A layout from a saved object: Wave H layout (v: 1), legacy {layout, panes}, or a preset key. */
export function hMigrate(obj) {
  if (obj == null) return null;
  if (typeof obj === "string" || typeof obj === "number") return hPreset(obj);
  if (typeof obj !== "object") return null;
  if (obj.v === H_VERSION && "root" in obj) return hDeserialize(obj);
  if ("layout" in obj) return hPreset(obj.layout, Array.isArray(obj.panes) ? obj.panes : []);
  return null;
}

/* ================================================================ (de)serialise */
export function hSerialize(L) {
  const rnd = n => (!n ? null : n.t === "tabs" ? { t: "tabs", wins: n.wins.slice(), cur: n.cur } : { t: "split", dir: n.dir, sizes: n.sizes.map(r4), kids: n.kids.map(rnd) });
  const wins = {};
  for (const [id, w] of Object.entries(L.wins)) wins[id] = hWinState(id, w);
  const out = { v: H_VERSION, root: rnd(L.root), wins, active: L.active, next: L.next, links: clone(L.links || []), popped: clone(L.popped || {}) };
  if (L.preset) out.preset = L.preset;
  return out;
}
/** Validate and normalise a serialised layout (throws on a broken one). */
export function hDeserialize(o) {
  if (!o || typeof o !== "object" || o.v !== H_VERSION) throw new Error("not a Wave H window layout");
  const L = hEmpty();
  const wins = o.wins && typeof o.wins === "object" ? o.wins : {};
  const ids = Object.keys(wins);
  if (ids.length > H_MAX_WINDOWS) throw new Error(`too many windows (${ids.length} > ${H_MAX_WINDOWS})`);
  for (const id of ids) {
    if (!isId(id)) throw new Error(`bad window id ${JSON.stringify(id)}`);
    if (wins[id] && wins[id].kind != null && !H_KINDS.includes(wins[id].kind)) throw new Error(`window ${id}: unknown kind ${JSON.stringify(wins[id].kind)}`);
    L.wins[id] = hWinState(id, wins[id] || {});
  }
  const seen = new Set();
  const read = n => {
    if (n == null) return null;
    if (n.t === "tabs") {
      if (!Array.isArray(n.wins) || !n.wins.length) throw new Error("empty tab group");
      for (const id of n.wins) { if (!L.wins[id]) throw new Error(`tab group names unknown window ${id}`); if (seen.has(id)) throw new Error(`window ${id} appears twice`); seen.add(id); }
      return tabs(n.wins, n.wins.includes(n.cur) ? n.cur : n.wins[0]);
    }
    if (n.t === "split") {
      if (!["row", "col"].includes(n.dir) || !Array.isArray(n.kids) || n.kids.length < 1) throw new Error("bad split");
      const sizes = Array.isArray(n.sizes) && n.sizes.length === n.kids.length && n.sizes.every(s => Number.isFinite(s) && s > 0) ? n.sizes.slice() : null;
      return split(n.dir, n.kids.map(read), sizes || undefined);
    }
    throw new Error("unknown layout node");
  };
  L.root = read(o.root);
  for (const [id, p] of Object.entries(o.popped || {})) {
    if (!L.wins[id]) throw new Error(`popped window ${id} unknown`);
    if (seen.has(id)) throw new Error(`window ${id} both docked and popped`);
    L.popped[id] = { near: p && isId(p.near) ? p.near : null, zone: p && ZONES.includes(p.zone) ? p.zone : "right" };
    seen.add(id);
  }
  for (const id of ids) if (!seen.has(id)) delete L.wins[id];
  const maxN = Math.max(0, ...Object.keys(L.wins).map(id => +id.slice(1)));
  L.next = Math.max(Number.isInteger(o.next) ? o.next : 1, maxN + 1);
  L.active = o.active;
  L.links = Array.isArray(o.links) ? o.links.filter(Array.isArray).map(g => g.filter(isId)) : [];
  if (typeof o.preset === "string" && H_PRESETS[o.preset]) L.preset = o.preset;
  return hNormalize(L);
}

/* ================================================================ reducer */
/** Pure layout reducer: returns a NEW layout; the input is not changed. */
export function hReduce(L0, a) {
  const L = clone(L0 || hEmpty());
  L.links = L.links || []; L.popped = L.popped || {};
  const structural = !["activate", "patch", "link", "unlink", "tab"].includes(a.type);
  switch (a.type) {
    case "add": {
      if (Object.keys(L.wins).length >= H_MAX_WINDOWS) throw new Error(`at most ${H_MAX_WINDOWS} windows`);
      const id = "w" + L.next++;
      L.wins[id] = hWinState(id, a.win || {});
      insertAt(L, id, a.target || L.active, a.zone || (L.root ? "right" : "center"));
      L.active = id;
      break;
    }
    case "close": {
      delete L.wins[a.id]; delete L.popped[a.id];
      if (L.active === a.id) L.active = null;
      break;
    }
    case "activate": case "tab": {
      if (!L.wins[a.id]) break;
      const f = hFind(L, a.id);
      if (f) f.node.cur = a.id;
      if (a.type === "activate" || f) L.active = a.id;
      break;
    }
    case "move": {
      if (!L.wins[a.id] || a.id === a.target || L.popped[a.id]) break;
      const tf = hFind(L, a.target);
      if (!tf) break;
      const f = hFind(L, a.id);
      if (f && f.node === tf.node && (a.zone === "center" || !a.zone)) break;     // same group, no-op
      if (f) f.node.wins = f.node.wins.filter(x => x !== a.id);
      hNormalize(L, [a.id]);
      if (!hFind(L, a.target)) { insertAt(L, a.id, null, "right"); break; }
      insertAt(L, a.id, a.target, a.zone || "center");
      L.active = a.id;
      break;
    }
    case "resize": {
      const n = hNodeAt(L, a.path);
      if (!n || n.t !== "split" || !Array.isArray(a.sizes) || a.sizes.length !== n.kids.length) break;
      const s = a.sizes.map(v => Math.max(MIN_SIZE, +v || 0));
      const tot = s.reduce((x, y) => x + y, 0);
      n.sizes = s.map(v => r4(v / tot));
      return L;                                                  // keeps the preset (same arrangement)
    }
    case "patch": {
      const w = L.wins[a.id];
      if (!w) break;
      L.wins[a.id] = hWinState(a.id, { ...w, ...(a.patch || {}) });
      break;
    }
    case "popout": {
      if (!L.wins[a.id] || L.popped[a.id]) break;
      const f = hFind(L, a.id);
      let near = null, zone = "right";
      if (f) {
        const others = f.node.wins.filter(x => x !== a.id);
        if (others.length) { near = others[0]; zone = "center"; }
        else if (f.parent) {
          const sib = f.parent.kids[f.index > 0 ? f.index - 1 : 1];
          const firstOf = nd => (nd.t === "tabs" ? nd.wins[0] : firstOf(nd.kids[0]));
          near = sib ? firstOf(sib) : null;
          zone = f.parent.dir === "row" ? (f.index > 0 ? "right" : "left") : (f.index > 0 ? "bottom" : "top");
        }
        f.node.wins = f.node.wins.filter(x => x !== a.id);
      }
      L.popped[a.id] = { near, zone };
      if (L.active === a.id) L.active = null;
      break;
    }
    case "dock": {
      if (!L.wins[a.id]) break;
      const p = L.popped[a.id] || {};
      delete L.popped[a.id];
      if (a.state) L.wins[a.id] = hWinState(a.id, { ...L.wins[a.id], ...a.state, id: a.id });
      hNormalize(L, [a.id]);
      const target = a.target || (p.near && hFind(L, p.near) ? p.near : null);
      insertAt(L, a.id, target, a.target ? (a.zone || "center") : target ? p.zone || "right" : "right");
      L.active = a.id;
      break;
    }
    case "link": {
      const ids = (a.ids || []).filter(id => L.wins[id]);
      if (ids.length < 2) break;
      const groups = L.links.filter(g => g.some(id => ids.includes(id)));
      const merged = [...new Set([...groups.flat(), ...ids])];
      L.links = L.links.filter(g => !groups.includes(g)).concat([merged]);
      break;
    }
    case "unlink": {
      L.links = L.links.map(g => g.filter(id => id !== a.id));
      break;
    }
    case "arrange": {
      const ids = hWinIds(L).sort((x, y) => +x.slice(1) - +y.slice(1));   // window order (creation), as ETABS
      L.root = hArrangeTree(ids);
      break;
    }
    default: throw new Error(`unknown layout action ${a.type}`);
  }
  if (structural) L.preset = null;
  return hNormalize(L);
}

/** Windows whose camera follows window `id` (its linked group, without itself). */
export function hLinkedWith(L, id) {
  const g = (L.links || []).find(x => x.includes(id));
  return g ? g.filter(x => x !== id) : [];
}

/* ================================================================ monitors */
/** A stable key of the monitor setup: "x,y,WxH" of every screen, sorted. */
export function hMonitorKey(screens) {
  const list = (Array.isArray(screens) ? screens : [screens]).filter(s => s && s.width > 0 && s.height > 0);
  if (!list.length) return "unknown";
  return list.map(s => `${Math.round(s.x || 0)},${Math.round(s.y || 0)},${Math.round(s.width)}x${Math.round(s.height)}`).sort().join("|");
}
/** Keep a saved window rectangle on a screen that still exists (≥ 80×40 px of it visible). */
export function hClampRect(rect, screens) {
  const list = (Array.isArray(screens) ? screens : [screens]).filter(s => s && s.width > 0 && s.height > 0);
  if (!rect || !list.length) return rect || null;
  let w = Math.max(320, Math.round(rect.w || 0)), h = Math.max(240, Math.round(rect.h || 0));
  let x = Math.round(rect.x || 0), y = Math.round(rect.y || 0);
  const vis = s => Math.max(0, Math.min(x + w, s.x + s.width) - Math.max(x, s.x)) >= 80 && Math.max(0, Math.min(y + h, s.y + s.height) - Math.max(y, s.y)) >= 40;
  if (!list.some(s => vis({ x: s.x || 0, y: s.y || 0, width: s.width, height: s.height }))) {
    const s = list[0];
    w = Math.min(w, s.width); h = Math.min(h, s.height);
    x = Math.round((s.x || 0) + (s.width - w) / 2); y = Math.round((s.y || 0) + (s.height - h) / 2);
  }
  return { x, y, w, h };
}
