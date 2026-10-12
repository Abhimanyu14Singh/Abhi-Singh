/* SkyFrame W8 — ETABS selection tools.

   * Select › Intersecting Line: click two points in the plan; every object of
     the story whose plan trace the line crosses is ADDED to the selection
     (frames, walls, slab outlines, links; columns within the pick radius).
     The tool stays active for more lines (V / Esc leaves it).
   * Select › Window / Crossing Box: when on, a box dragged left → right is a
     WINDOW (only objects entirely inside), right → left a CROSSING box
     (anything the box touches). Off (default) keeps the legacy box rule.
   * Select › By Object Type / Label / Link-Spring Property / Load Pattern…
     (one "Select by Attribute" dialog that also covers frame / shell
     sections and groups): Select adds, Deselect removes.

   Pure geometry: js/w8_snapgeom.js. Hooks: PlanEditor.w8ToolHook /
   w8BoxHook / w8Overlay (js/draw.js). Box mode is a per-browser preference. */

import G from "./w8_snapgeom.js";
import { springKey as slSpringKey, lineSpringKey as slLineSpringKey } from "./modeledit.js";
import { el as slEl, esc as slEsc, dkForm as slForm, dkGroup as slGroup, dkNote as slNote, dkDialog as slDialog } from "./dlgkit.js";

const NS = "http://www.w3.org/2000/svg";
const TOOL = "w8selline";
const BOX_KEY = "sky.w8.boxmode";

/* ------------------------------------------------------------ plan traces */
function wallTrace(cs) {
  let best = [cs[0], cs[1]], bl = -1;
  for (let i = 0; i < cs.length; i++) for (let j = i + 1; j < cs.length; j++) {
    const l = Math.hypot(cs[j][0] - cs[i][0], cs[j][1] - cs[i][1]);
    if (l > bl) { bl = l; best = [cs[i], cs[j]]; }
  }
  return best.map(c => [c[0], c[1]]);
}
function inPoly(x, y, cs) {
  let inside = false;
  for (let i = 0, j = cs.length - 1; i < cs.length; j = i++) {
    const xi = cs[i][0], yi = cs[i][1], xj = cs[j][0], yj = cs[j][1];
    if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) inside = !inside;
  }
  return inside;
}

/** Plan objects of a story: [{ref, segs: [[a, b]], pts: [[x, y]], poly?}]. */
export function planObjects(m, story, arcOf) {
  const out = [];
  const st = (m.stories || []).find(s => s.name === story);
  for (const mm of m.members || []) {
    if (mm.story !== story) continue;
    const a = [mm.pi[0], mm.pi[1]], b = [mm.pj[0], mm.pj[1]];
    if (mm.kind === "column" && Math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-9) { out.push({ ref: { type: "member", uid: mm.uid }, segs: [], pts: [a] }); continue; }
    const arc = mm.curve && arcOf ? arcOf(mm) : null;
    const pl = arc ? arc.points.map(p => [p[0], p[1]]) : [a, b];
    const segs = [];
    for (let i = 0; i + 1 < pl.length; i++) segs.push([pl[i], pl[i + 1]]);
    out.push({ ref: { type: "member", uid: mm.uid }, segs, pts: pl });
  }
  for (const s of m.shells || []) {
    if (s.story !== story) continue;
    const cs = s.corners.map(c => [c[0], c[1]]);
    if (s.kind === "wall") { const t = wallTrace(cs); out.push({ ref: { type: "shell", uid: s.uid }, segs: [t], pts: t }); }
    else out.push({ ref: { type: "shell", uid: s.uid }, segs: cs.map((c, i) => [c, cs[(i + 1) % cs.length]]), pts: cs, poly: cs });
  }
  if (st) {
    const zb = st.elevation - st.height - 1e-6, zt = st.elevation + 1e-6;
    for (const lk of m.links || []) {
      if (!(lk.pi[2] > zb && lk.pi[2] <= zt && lk.pj[2] > zb && lk.pj[2] <= zt)) continue;
      const a = [lk.pi[0], lk.pi[1]], b = [lk.pj[0], lk.pj[1]];
      out.push({ ref: { type: "link", uid: lk.uid }, segs: Math.hypot(b[0] - a[0], b[1] - a[1]) > 1e-9 ? [[a, b]] : [], pts: [a, b] });
    }
  }
  for (const sp of m.spring_supports || []) out.push({ ref: { type: "spring", uid: slSpringKey(sp.point) }, segs: [], pts: [[sp.point[0], sp.point[1]]] });
  for (const ls of m.line_springs || []) out.push({ ref: { type: "linespring", uid: slLineSpringKey(ls) }, segs: [[[ls.p1[0], ls.p1[1]], [ls.p2[0], ls.p2[1]]]], pts: [[ls.p1[0], ls.p1[1]], [ls.p2[0], ls.p2[1]]] });
  return out;
}

/** Objects whose plan trace the segment p→q crosses (points within tol). */
export function refsCrossingLine(objs, p, q, tol) {
  const out = [];
  for (const o of objs) {
    let hit = o.segs.some(([a, b]) => !!G.segIntersect(p, q, a, b));
    if (!hit && !o.segs.length) hit = o.pts.some(pt => G.distToSeg(pt, p, q) <= tol);
    if (hit) out.push(o.ref);
  }
  return out;
}

/** Window (fully inside) or crossing (touching) box selection. */
export function refsInBox(objs, b, mode) {
  const xa = Math.min(b.x0, b.x1), xb = Math.max(b.x0, b.x1), ya = Math.min(b.y0, b.y1), yb = Math.max(b.y0, b.y1);
  const inBox = p => p[0] >= xa && p[0] <= xb && p[1] >= ya && p[1] <= yb;
  const edges = [[[xa, ya], [xb, ya]], [[xb, ya], [xb, yb]], [[xb, yb], [xa, yb]], [[xa, yb], [xa, ya]]];
  const out = [];
  for (const o of objs) {
    let hit;
    if (mode === "window") hit = o.pts.length > 0 && o.pts.every(inBox);
    else {
      hit = o.pts.some(inBox) || o.segs.some(([a, c]) => edges.some(([e0, e1]) => !!G.segIntersect(a, c, e0, e1)))
        || (o.poly && inPoly(xa, ya, o.poly));
    }
    if (hit) out.push(o.ref);
  }
  return out;
}

/* ------------------------------------------------------------ select by attribute */
const TYPES = [["column", "Columns"], ["beam", "Beams"], ["brace", "Braces"], ["curved", "Curved frames"], ["wall", "Walls"],
  ["slab", "Slabs (shell)"], ["membrane", "Slabs (membrane)"], ["link", "Links"], ["spring", "Point springs"], ["linespring", "Line springs"]];

export function refsByAttribute(m, mode, vals) {
  const V = new Set(vals);
  const out = [];
  const mem = (pred) => { for (const x of m.members || []) if (pred(x)) out.push({ type: "member", uid: x.uid }); };
  const shl = (pred) => { for (const x of m.shells || []) if (pred(x)) out.push({ type: "shell", uid: x.uid }); };
  if (mode === "type") {
    mem(x => V.has(x.kind) || (V.has("curved") && !!x.curve));
    shl(x => (V.has("wall") && x.kind === "wall") || (V.has("slab") && x.kind === "slab" && (x.behavior || "shell") === "shell")
      || (V.has("membrane") && x.kind === "slab" && x.behavior === "membrane"));
    if (V.has("link")) for (const l of m.links || []) out.push({ type: "link", uid: l.uid });
    if (V.has("spring")) for (const s of m.spring_supports || []) out.push({ type: "spring", uid: slSpringKey(s.point) });
    if (V.has("linespring")) for (const s of m.line_springs || []) out.push({ type: "linespring", uid: slLineSpringKey(s) });
  } else if (mode === "label") {
    const pats = [...V].flatMap(v => String(v).split(/[,\s]+/)).filter(Boolean)
      .map(p => new RegExp("^" + p.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".") + "$", "i"));
    const ok = u => pats.some(r => r.test(u));
    mem(x => ok(x.uid)); shl(x => ok(x.uid));
    for (const l of m.links || []) if (ok(l.uid)) out.push({ type: "link", uid: l.uid });
  } else if (mode === "frame_section") mem(x => V.has(x.section));
  else if (mode === "shell_section") shl(x => V.has(x.section));
  else if (mode === "link_type") { for (const l of m.links || []) if (V.has(l.link_type || "elastic")) out.push({ type: "link", uid: l.uid }); }
  else if (mode === "spring_prop") { for (const s of m.spring_supports || []) if (V.has(s.property || "(inline)")) out.push({ type: "spring", uid: slSpringKey(s.point) }); }
  else if (mode === "pattern") {
    const mu = new Set(), su = new Set();
    for (const [pn, p] of Object.entries(m.patterns || {})) {
      if (!V.has(pn)) continue;
      for (const k of ["member_udls", "member_loads", "thermal_loads"]) for (const e of p[k] || []) mu.add(e.member_uid);
      for (const k of ["area_loads", "shell_thermal_loads"]) for (const e of p[k] || []) su.add(e.region_uid);
    }
    for (const [sn, set] of Object.entries(m.shell_load_sets || {})) if (Object.keys(set).some(p => V.has(p))) for (const r of m.shells || []) if (r.load_set === sn) su.add(r.uid);
    for (const t of m.tendons || []) if (V.has(t.pattern)) for (const h of t.host || []) { if ((m.members || []).some(x => x.uid === h)) mu.add(h); else su.add(h); }
    mem(x => mu.has(x.uid)); shl(x => su.has(x.uid));
  } else if (mode === "group") {
    for (const g of [...V]) {
      const gr = (m.groups || {})[g];
      if (!gr) continue;
      for (const u of gr.members || []) out.push({ type: "member", uid: u });
      for (const u of gr.shells || []) out.push({ type: "shell", uid: u });
      for (const u of gr.links || []) out.push({ type: "link", uid: u });
    }
  }
  const seen = new Set();
  return out.filter(r => { const k = `${r.type}:${r.uid}`; if (seen.has(k)) return false; seen.add(k); return true; });
}

const MODES = [["type", "Object type"], ["label", "Label (uid, * ? wildcards)"], ["frame_section", "Frame section"], ["shell_section", "Slab / wall section"],
  ["link_type", "Link property (type)"], ["spring_prop", "Spring property"], ["pattern", "Load pattern"], ["group", "Group"]];

function valuesFor(m, mode) {
  const cnt = vals => refsByAttribute(m, mode, vals).length;
  if (mode === "type") return TYPES.map(([v, l]) => [v, l, cnt([v])]);
  if (mode === "frame_section") return Object.keys(m.sections || {}).map(n => [n, n, cnt([n])]);
  if (mode === "shell_section") return Object.keys(m.shell_sections || {}).map(n => [n, n, cnt([n])]);
  if (mode === "link_type") return [...new Set((m.links || []).map(l => l.link_type || "elastic"))].map(n => [n, n, cnt([n])]);
  if (mode === "spring_prop") return [...new Set((m.spring_supports || []).map(s => s.property || "(inline)"))].map(n => [n, n, cnt([n])]);
  if (mode === "pattern") return Object.keys(m.patterns || {}).map(n => [n, n, cnt([n])]);
  if (mode === "group") return Object.keys(m.groups || {}).map(n => [n, n, cnt([n])]);
  return [];
}

export function initW8Select(sky) {
  const S = sky.store, pe = sky.planEditor;
  const toast = (t, msg, k = "info") => sky.toast && sky.toast(t, msg, k, 3500);
  let boxMode = false;
  try { boxMode = localStorage.getItem(BOX_KEY) === "1"; } catch { /* ignore */ }
  const arcOf = mm => (sky.w8curved ? sky.w8curved.memberArc(mm, 48) : null);
  const line = { p: null };

  /* intersecting line tool */
  const startLine = () => {
    if (S.mode !== "model" && sky.setMode) sky.setMode("model");
    if (S.view === "elev" && sky.setView) sky.setView("plan");
    sky.setTool(TOOL);
    line.p = null;
    toast("Intersecting line", "Click two points: every object the line crosses is added to the selection.");
  };
  if (pe) {
    const prevTool = pe.w8ToolHook;
    pe.w8ToolHook = (tool, pt, w, ed) => {
      if (tool !== TOOL) return prevTool && prevTool(tool, pt, w, ed);
      if (!line.p) { line.p = [w.x, w.y]; return; }
      const q = [w.x, w.y], p = line.p;
      line.p = null;
      if (Math.hypot(q[0] - p[0], q[1] - p[1]) < 1e-9) return;
      const refs = refsCrossingLine(planObjects(ed.opts.getModel(), ed.opts.getStory(), arcOf), p, q, 9 / ed.scale);
      ed.opts.onSelect(refs, true);
    };
    const prevOv = pe.w8Overlay;
    pe.w8Overlay = (g, ed) => {
      if (prevOv) prevOv(g, ed);
      if (ed.tool !== TOOL || !line.p || !ed._mouseWorld) return;
      const [x1, y1] = ed.toScreen(line.p[0], line.p[1]), [x2, y2] = ed.toScreen(ed._mouseWorld.x, ed._mouseWorld.y);
      const e = document.createElementNS(NS, "line");
      for (const [k, v] of Object.entries({ x1, y1, x2, y2, stroke: "#f5be3c", "stroke-width": 1.6, "stroke-dasharray": "6 4", "data-w8": "selline" })) e.setAttribute(k, v);
      g.appendChild(e);
    };
    const prevBox = pe.w8BoxHook;
    pe.w8BoxHook = (b, ed) => {
      if (!boxMode) return prevBox ? prevBox(b, ed) : null;
      const mode = b.x1 >= b.x0 ? "window" : "crossing";
      return refsInBox(planObjects(ed.opts.getModel(), ed.opts.getStory(), arcOf), b, mode);
    };
    const os = pe.setTool.bind(pe);
    pe.setTool = t => { line.p = null; os(t); };
  }
  document.addEventListener("keydown", e => { if (e.key === "Escape" && pe && pe.tool === TOOL && line.p) { line.p = null; pe.renderOverlay(); } });

  /* select by attribute */
  function openByAttribute(mode0 = "type") {
    const m = S.model;
    if (!m) return null;
    const V = { mode: mode0, action: "select", label: "" };
    const F = slForm();
    const box = slEl("div", { class: "w8-sel-list", id: "w8SelList" });
    const labelRow = F.text(V, "label", { label: "Labels", id: "w8SelLabel", required: false, placeholder: "e.g. B1, C*, W?" });
    const fill = () => {
      box.textContent = "";
      labelRow.hidden = V.mode !== "label";
      if (V.mode === "label") return;
      const items = valuesFor(m, V.mode);
      if (!items.length) { box.appendChild(slNote("Nothing of this kind in the model.")); return; }
      for (const [v, l, n] of items) {
        const cb = slEl("input", { type: "checkbox", value: v, "data-w8val": v });
        box.appendChild(slEl("label", { class: "dk-check" }, [cb, slEl("span", { html: `${slEsc(l)} <span class="muted">(${n})</span>` })]));
      }
    };
    const body = [
      slGroup("Select By", [F.select(V, "mode", MODES, { label: "Attribute", id: "w8SelMode", onChange: () => fill() }), labelRow, box]),
      slGroup("Action", [F.radio(V, "action", [["select", "Select (add to the selection)"], ["deselect", "Deselect (remove)"]], { idPrefix: "w8SelAct_" })]),
    ];
    fill();
    return slDialog({
      id: "w8SelAttr", title: "Select by Attribute", iconId: "exleaf-groups", size: "md", form: F, body, okLabel: "Apply",
      intro: "ETABS Select › by object type, label, section, link / spring property, load pattern or group.",
      onOk: () => {
        const vals = V.mode === "label" ? [V.label] : [...box.querySelectorAll("input:checked")].map(c => c.value);
        if (!vals.length || (V.mode === "label" && !V.label.trim())) return "Choose at least one value.";
        const refs = refsByAttribute(m, V.mode, vals);
        if (V.action === "select") sky.handleSelect(refs, true);
        else {
          const drop = new Set(refs.map(r => `${r.type}:${r.uid}`));
          sky.handleSelect((S.selection || []).filter(r => !drop.has(`${r.type}:${r.uid}`)), false);
        }
        toast("Selection", `${refs.length} object${refs.length === 1 ? "" : "s"} ${V.action === "select" ? "selected" : "deselected"} · ${(S.selection || []).length} in the selection`);
      },
    });
  }

  const setBox = on => { boxMode = !!on; try { localStorage.setItem(BOX_KEY, boxMode ? "1" : "0"); } catch { /* ignore */ } };
  if (sky.menu) {
    sky.menu.add("Select", { sep: true });
    sky.menu.add("Select", { label: "Intersecting Line", act: "sel-w8-line", fn: startLine });
    sky.menu.add("Select", { label: "Window / Crossing Box", act: "sel-w8-box", check: () => boxMode, hint: "drag → window · ← crossing", fn: () => setBox(!boxMode) });
    sky.menu.add("Select", { label: "By Object Type…", act: "sel-w8-type", fn: () => openByAttribute("type") });
    sky.menu.add("Select", { label: "By Label…", act: "sel-w8-label", fn: () => openByAttribute("label") });
    sky.menu.add("Select", { label: "By Link / Spring Property…", act: "sel-w8-linkprop", fn: () => openByAttribute("link_type") });
    sky.menu.add("Select", { label: "By Load Pattern…", act: "sel-w8-pattern", fn: () => openByAttribute("pattern") });
  }
  sky.w8select = { startLine, openByAttribute, refsByAttribute: (mode, vals) => refsByAttribute(S.model, mode, vals),
    setBoxMode: setBox, boxMode: () => boxMode, lineState: () => line.p, TOOL,
    crossing: (p, q) => refsCrossingLine(planObjects(S.model, S.story, arcOf), p, q, 0.2) };
}
