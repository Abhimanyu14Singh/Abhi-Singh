/* SkyFrame Wave F — ETABS mouse grammar in every 3D view (main 3D view and
   View › Windows 3D panes, Classic and GPU renderers).

     click                select the object under the cursor (replaces the
                          selection); clicking an object that is already
                          selected DESELECTS it (ETABS); empty space clears
     Shift / Ctrl + click add the object (or remove it when selected); Ctrl +
                          click where several objects overlap lists them to
                          choose from (ETABS)
     Ctrl + drag          box: left → right = WINDOW (only objects entirely
                          inside), right → left = CROSSING (anything touched);
                          + Shift adds to the selection
     3D drag mode Select  (viewer toolbar Orbit | Select, Select › Select
                          Using › 3D Drag Selects) plain left-drag draws the
                          box; Alt + drag or middle-drag orbits, right-drag pans
     Intersecting Line    two clicks: every object the screen line crosses is
                          added (stays on for more lines; Esc / V ends)
     hover                pre-highlights the object and shows its label
     Esc                  ends the line tool, else clears the selection
     right-click          context menu by object type (js/f_ctxmenu.js)

   The shared selection (store.selection, every object type incl. joints) is
   drawn in every 3D view as a dashed outline + tint (theme tokens in
   js/f_init.js), the hover object as a solid outline. Line springs, which
   the 3D view never drew, are drawn here so they can be seen and picked. */

import { fSetAttach as fsSetAttach, fViewers as fsViewers } from "./f_hook.js";
import * as PG from "./f_pickgeom.js";
import fsU from "./units.js";
import { canonical as fsTgCanonical, samplePolyline as fsTgSample } from "./tendon_geom.js";
import { springKey as fsSpringKey, lineSpringKey as fsLineSpringKey } from "./modeledit.js";

const DRAG_KEY = "sky.f.dragmode";
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

export function initFSelect3d(sky, F) {
  const S = sky.store;
  const pick = F.pick;
  let dragMode = "orbit";
  try { dragMode = localStorage.getItem(DRAG_KEY) === "select" ? "select" : "orbit"; } catch { /* private mode */ }
  const line = { v: null, p: null, mouse: null };

  /* ---------------------------------------------------------------- theme tokens */
  let tok = null, tokAt = 0;
  const tokens = () => {
    const now = performance.now();
    if (tok && now - tokAt < 500) return tok;
    const cs = getComputedStyle(document.documentElement);
    const g = (k, d) => (cs.getPropertyValue(k) || "").trim() || d;
    tok = { sel: g("--f-sel", "#35b5e5"), selTint: g("--f-sel-tint", "rgba(53,181,229,0.22)"), hover: g("--f-hover", "#ffffff"),
      spring: g("--f-linespring", "#2fbf74"), line: g("--f-line", "#f5be3c") };
    tokAt = now;
    return tok;
  };
  F.tokens = tokens;

  /* ---------------------------------------------------------------- selection helpers */
  const isSel = ref => (S.selection || []).some(r => r.type === ref.type && r.uid === ref.uid);
  function clickSelect(ref, add) {
    if (!ref) { if (!add && (S.selection || []).length) sky.handleSelect([], false); return; }
    if (isSel(ref)) sky.handleSelect([ref], true);          // ETABS: clicking a selected object deselects it
    else sky.handleSelect([ref], !!add);
  }
  let selSig = "";
  function selChanged() {
    const sig = JSON.stringify(S.selection || []);
    if (sig === selSig) return false;
    selSig = sig;
    for (const v of fsViewers) v._dirty = true;
    if (F.onSelection) { try { F.onSelection(); } catch (e) { console.error(e); } }
    return true;
  }
  F.selChanged = selChanged;

  /* ---------------------------------------------------------------- labels */
  const L = (p) => p.map(x => fsU.fmt("length", x, 2)).join(", ") + " " + fsU.label("length");
  function label(v, ref) {
    const m = S.model;
    if (!m || !ref) return "";
    if (ref.type === "member") {
      const seg = (v.segs || []).find(s => s.uid === ref.uid);
      if (v.getMemberTooltip && seg) { try { return v.getMemberTooltip(seg); } catch { /* fall through */ } }
      const x = (m.members || []).find(o => o.uid === ref.uid);
      return x ? `<span class="tt-uid">${esc(x.uid)}</span><br>${esc(x.kind)} · ${esc(x.section)} · ${esc(x.story || "")}` : esc(ref.uid);
    }
    if (ref.type === "shell") {
      const x = (m.shells || []).find(o => o.uid === ref.uid);
      return x ? `<span class="tt-uid">${esc(x.uid)}</span><br>${esc(x.kind)} · ${esc(x.section)} · ${esc(x.story || "")}${x.corners.length !== 4 ? ` · ${x.corners.length}-sided` : ""}` : esc(ref.uid);
    }
    if (ref.type === "joint") {
      const p = PG.jointPoint(ref.uid);
      const sup = (m.supports || []).find(s => s.point.every((c, i) => Math.abs(c - p[i]) < 1e-6));
      const st = PG.storyAtZ(m, p[2]);
      return `<span class="tt-uid">Joint</span> ${esc(L(p))}${st ? `<br>${esc(st)}` : ""}${sup ? ` · restraints ${sup.restraints.join("")}` : ""}`;
    }
    if (ref.type === "link") {
      const x = (m.links || []).find(o => o.uid === ref.uid);
      return x ? `<span class="tt-uid">${esc(x.uid)}</span><br>link · ${esc(x.link_type || "elastic")}` : esc(ref.uid);
    }
    if (ref.type === "spring") return `<span class="tt-uid">Point spring</span> ${esc(L(PG.jointPoint(ref.uid)))}`;
    if (ref.type === "linespring") return `<span class="tt-uid">Line spring</span> ${esc(ref.uid.replace("|", " → "))}`;
    if (ref.type === "tendon") return `<span class="tt-uid">${esc(ref.uid)}</span><br>tendon`;
    return esc(ref.uid);
  }
  F.label = (ref, v) => label(v || sky.viewer, ref);

  /* ---------------------------------------------------------------- overlay */
  const memberIdx = { m: null, n: -1, map: null };
  const memberOf = uid => {
    const m = S.model;
    if (memberIdx.m !== m || memberIdx.n !== m.members.length) { memberIdx.m = m; memberIdx.n = m.members.length; memberIdx.map = new Map(m.members.map(x => [x.uid, x])); }
    return memberIdx.map.get(uid);
  };
  function geomOf(v, ref) {
    const m = S.model;
    if (ref.type === "member") {
      const pieces = (v.segs || []).filter(s => s.uid === ref.uid);
      if (pieces.length) return { segs: pieces.map(s => [s.p1, s.p2]) };
      const x = memberOf(ref.uid);
      return x ? { segs: [[x.pi, x.pj]] } : null;
    }
    if (ref.type === "shell") { const x = (m.shells || []).find(o => o.uid === ref.uid); return x ? { poly: x.corners, holes: (v.shellPolys || []).find(s => s.uid === ref.uid)?.openings || [] } : null; }
    if (ref.type === "link") { const x = (m.links || []).find(o => o.uid === ref.uid); return x ? { segs: [[x.pi, x.pj]] } : null; }
    if (ref.type === "joint") return { pt: PG.jointPoint(ref.uid), r: 5 };
    if (ref.type === "spring") { const x = (m.spring_supports || []).find(s => fsSpringKey(s.point) === ref.uid); return x ? { pt: x.point, r: 9 } : null; }
    if (ref.type === "linespring") { const x = (m.line_springs || []).find(s => fsLineSpringKey(s) === ref.uid); return x ? { segs: [[x.p1, x.p2]] } : null; }
    if (ref.type === "tendon") {
      const t = (m.tendons || []).find(o => o.uid === ref.uid);
      if (!t) return null;
      try { const c = fsTgCanonical(t); const pts = fsTgSample(c, 8); const segs = []; for (let i = 1; i < pts.length; i++) segs.push([pts[i - 1], pts[i]]); return { segs }; } catch { return null; }
    }
    return null;
  }
  function strokeGeom(ctx, P, v, g, style) {
    if (!g) return;
    if (g.segs) {
      ctx.beginPath();
      for (const [a, b] of g.segs) { const s = v._projSeg(P, a, b); if (s) { ctx.moveTo(s.a.x, s.a.y); ctx.lineTo(s.b.x, s.b.y); } }
      if (style.under) { ctx.save(); ctx.setLineDash([]); ctx.strokeStyle = style.under; ctx.lineWidth = style.w + 4; ctx.stroke(); ctx.restore(); }
      ctx.strokeStyle = style.color; ctx.lineWidth = style.w; ctx.stroke();
    } else if (g.poly) {
      const pts = [];
      for (const p of g.poly) { const pc = P.toCam(p); if (pc[2] < P.near) return; pts.push(P.proj(pc)); }
      ctx.beginPath();
      pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
      ctx.closePath();
      if (style.fill) { ctx.fillStyle = style.fill; ctx.fill(); }
      ctx.strokeStyle = style.color; ctx.lineWidth = style.w; ctx.stroke();
    } else if (g.pt) {
      const pc = P.toCam(g.pt);
      if (pc[2] < P.near) return;
      const q = P.proj(pc), r = g.r;
      ctx.beginPath();
      if (r > 6) ctx.arc(q.x, q.y, r, 0, 2 * Math.PI); else ctx.rect(q.x - r, q.y - r, 2 * r, 2 * r);
      if (style.fill) { ctx.fillStyle = style.fill; ctx.fill(); }
      ctx.strokeStyle = style.color; ctx.lineWidth = Math.max(1.5, style.w - 1); ctx.stroke();
    }
  }
  function overlay(ctx, P, v) {
    const m = S.model;
    if (!m || v.model !== m) return;
    const T = tokens();
    ctx.save();
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    // line springs (not drawn by the 3D view itself)
    const vxo = v === sky.viewer && sky.vx && sky.vx.opts ? sky.vx.opts() : null;
    if ((m.line_springs || []).length && (!vxo || !vxo.show || vxo.show.spring !== false)) {
      ctx.strokeStyle = T.spring; ctx.lineWidth = 1.4; ctx.setLineDash([2, 3]);
      ctx.beginPath();
      for (const ls of m.line_springs) { const s = v._projSeg(P, ls.p1, ls.p2); if (s) { ctx.moveTo(s.a.x, s.a.y); ctx.lineTo(s.b.x, s.b.y); } }
      ctx.stroke();
      ctx.setLineDash([]);
    }
    // the shared selection: tint + dashed outline
    const sel = S.selection || [];
    if (sel.length) {
      ctx.setLineDash([7, 4]);
      for (const ref of sel) {
        const g = geomOf(v, ref);
        strokeGeom(ctx, P, v, g, { color: T.sel, w: ref.type === "shell" ? 2 : 2.6, fill: T.selTint, under: ref.type !== "shell" ? T.selTint : null });
      }
      ctx.setLineDash([]);
    }
    // hover pre-highlight
    if (v._fHover && v._fHover.ref) {
      const g = geomOf(v, v._fHover.ref);
      strokeGeom(ctx, P, v, g, { color: T.hover, w: 2.4, fill: v._fHover.ref.type === "shell" ? "rgba(255,255,255,0.08)" : null });
    }
    // intersecting-line rubber band
    if (line.v === v && line.p && line.mouse) {
      ctx.strokeStyle = T.line; ctx.lineWidth = 1.6; ctx.setLineDash([6, 4]);
      ctx.beginPath(); ctx.moveTo(line.p[0], line.p[1]); ctx.lineTo(line.mouse[0], line.mouse[1]); ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.restore();
  }

  /* ---------------------------------------------------------------- overlap chooser */
  let chooserEl = null;
  function closeChooser() { if (chooserEl) { chooserEl.remove(); chooserEl = null; } }
  document.addEventListener("pointerdown", e => { if (chooserEl && !chooserEl.contains(e.target)) closeChooser(); }, true);
  document.addEventListener("keydown", e => { if (e.key === "Escape" && chooserEl) { e.stopImmediatePropagation(); closeChooser(); } }, true);

  /* ---------------------------------------------------------------- per-viewer input */
  function tooltipOf(v) {
    if (v.tooltipEl) return v.tooltipEl;
    if (!v._fTip) {
      const t = document.createElement("div");
      t.className = "viewer-tooltip f-tip hidden";
      (v.canvas.parentElement || document.body).appendChild(t);
      v._fTip = t;
    }
    return v._fTip;
  }
  function setHover(v, ref, mx, my) {
    const k = ref ? PG.refKey(ref) : "";
    const prev = v._fHover ? PG.refKey(v._fHover.ref) : "";
    v._fHover = ref ? { ref } : null;
    if (k !== prev) v._dirty = true;
    const tt = tooltipOf(v);
    if (!tt) return;
    if (!ref || v.labelsOn === false) { tt.classList.add("hidden"); return; }
    tt.innerHTML = label(v, ref);
    tt.classList.remove("hidden");
    tt.style.left = Math.min(mx + 14, Math.max(0, v.canvas.clientWidth - 220)) + "px";
    tt.style.top = (my + 14) + "px";
  }
  const draw3dActive = () => !!(sky.wb && sky.wb.draw3d && sky.wb.draw3d.active && sky.wb.draw3d.active());
  const ensureGpuPick = v => {
    if (!v.gpu || !v.gpu.on || v.gpu.fPick || v.gpu._fPickLoading) return;
    v.gpu._fPickLoading = true;
    import("./gpu/f_gpupick.js").then(mod => { if (v.gpu && v.gpu.on) v.gpu.fPick = mod.createGpuPick(v.gpu); }).catch(() => { /* CPU fallback */ });
  };

  function attach(v) {
    if (v._fAttached) return;
    v._fAttached = true;
    const c = v.canvas;
    v.fSelOverlay = (ctx, P, vv) => overlay(ctx, P, vv);
    // an edit of the SAME model (assign / undo / Properties panel) rebuilds the scene without
    // re-fitting the camera — assigning from the 3D view keeps the view where it is
    const sm0 = v.setModel.bind(v);
    v.setModel = model => { if (model && model === v.model && v._bbox) { v._buildScene(); pick.invalidate(v); v._dirty = true; return; } sm0(model); };
    const origClick = v.onMemberClick;            // main view: Analyze-mode Member panel
    v.onMemberClick = null;                       // clicks are handled below
    v._hitTest = () => {};                        // hover is handled below (all object types)
    const isMain = () => v === sky.viewer;
    let down = null, box = null, rq = 0, lastMove = null;
    const pos = e => { const r = c.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };

    const onDown = e => {
      ensureGpuPick(v);
      if (draw3dActive()) return;
      if (e.button === 2) { down = { x: e.clientX, y: e.clientY, button: 2 }; return; }
      if (e.button !== 0) return;
      const [x, y] = pos(e);
      if (line.v === v) {                                   // Intersecting Line
        e.stopImmediatePropagation(); e.preventDefault();
        if (!line.p) { line.p = [x, y]; line.mouse = [x, y]; v._dirty = true; return; }
        const p = line.p; line.p = null;
        if (Math.hypot(x - p[0], y - p[1]) < 2) { v._dirty = true; return; }
        const refs = pick.pickLine(v, p, [x, y]);
        if (refs.length) sky.handleSelect(refs, true);
        selChanged();
        v._dirty = true;
        return;
      }
      const boxGesture = (e.ctrlKey || e.metaKey) ? true : (dragMode === "select" && !e.altKey);
      if (boxGesture) {
        e.stopImmediatePropagation(); e.preventDefault();
        try { c.setPointerCapture(e.pointerId); } catch { /* synthetic */ }
        box = { x0: x, y0: y, add: e.shiftKey || (dragMode === "select" && (e.ctrlKey || e.metaKey)), moved: false, el: null, ctrl: e.ctrlKey || e.metaKey };
        setHover(v, null);
        return;
      }
      down = { x: e.clientX, y: e.clientY, button: 0, add: e.shiftKey || e.ctrlKey || e.metaKey, ctrl: e.ctrlKey || e.metaKey };
    };
    const onMove = e => {
      const [x, y] = pos(e);
      if (box) {
        e.stopImmediatePropagation();
        if (!box.moved && Math.hypot(x - box.x0, y - box.y0) > 4) {
          box.moved = true;
          box.el = document.createElement("div");
          box.el.className = "v3-boxsel f-boxsel";
          (c.parentElement || document.body).appendChild(box.el);
        }
        if (box.el) {
          const crossing = x < box.x0;
          Object.assign(box.el.style, { left: Math.min(x, box.x0) + "px", top: Math.min(y, box.y0) + "px", width: Math.abs(x - box.x0) + "px", height: Math.abs(y - box.y0) + "px" });
          box.el.classList.toggle("is-crossing", crossing);
        }
        return;
      }
      if (line.v === v && line.p) { line.mouse = [x, y]; v._dirty = true; }
      if (e.buttons) return;                               // orbit / pan in progress
      lastMove = [x, y];
      if (rq) return;
      rq = requestAnimationFrame(() => {
        rq = 0;
        if (!lastMove) return;
        let ref = null;
        try { ref = pick.pickAt(v, lastMove[0], lastMove[1], 7); } catch (err) { ref = null; }
        setHover(v, ref, lastMove[0], lastMove[1]);
      });
    };
    const onUp = e => {
      if (box) {
        e.stopImmediatePropagation();
        const b = box; box = null;
        if (b.el) b.el.remove();
        const [x, y] = pos(e);
        if (!b.moved) {                                      // a click in box mode = a click
          let ref = null;
          try { ref = pick.pickAt(v, x, y, 7); } catch { ref = null; }
          if (b.ctrl && chooser(v, e, x, y)) return;          // ETABS Ctrl+click on overlapping objects → list
          clickSelect(ref, b.add || b.ctrl);
          afterClick(ref);
          return;
        }
        let refs = [];
        try { refs = pick.pickBox(v, b.x0, b.y0, x, y, x < b.x0); } catch (err) { console.error(err); }
        sky.handleSelect(refs, b.add);
        selChanged();
        return;
      }
      if (!down) return;
      const d = down; down = null;
      const moved = Math.hypot(e.clientX - d.x, e.clientY - d.y);
      if (d.button === 2) {
        if (moved > 4 || isMain()) return;                 // the main canvas: js/workflow.js (Wave F hook)
        const [x, y] = pos(e);
        let ref = null;
        try { ref = pick.pickAt(v, x, y, 8); } catch { ref = null; }
        if (sky.ctxMenu && sky.ctxMenu.open) sky.ctxMenu.open(e.clientX + 2, e.clientY + 2, ref);
        return;
      }
      if (moved >= 5 || e.button !== 0) return;
      const [x, y] = pos(e);
      let ref = null;
      try { ref = pick.pickAt(v, x, y, 8); } catch (err) { console.error(err); }
      if (d.ctrl && chooser(v, e, x, y)) return;
      clickSelect(ref, d.add);
      afterClick(ref);
    };
    /** ETABS: Ctrl+click where several objects overlap → a list to pick from (adds the choice). */
    function chooser(vv, e, x, y) {
      // only genuinely coincident objects: line objects within 3 px of the cursor and shells under it
      // (a joint / point spring under the cursor wins on its own — Ctrl+click then just adds it)
      let hits = [];
      try { hits = pick.hitsAt(vv, x, y, 8); } catch { hits = []; }
      const top = PG.resolvePick(hits);
      if (!top || top.type === "joint" || top.type === "spring") return false;
      const list = hits.filter(h => h.ref.type !== "joint" && h.ref.type !== "spring" && (h.ref.type === "shell" || h.d <= 3))
        .sort((a, b) => (PG.TYPE_PRIORITY[a.ref.type] - PG.TYPE_PRIORITY[b.ref.type]) || (a.ref.type === "shell" ? a.z - b.z : a.d - b.d) || (a.z - b.z))
        .map(h => h.ref);
      if (list.length < 2) return false;
      closeChooser();
      const menu = document.createElement("div");
      menu.className = "wf-ctx f-chooser"; menu.id = "fPickList"; menu.setAttribute("role", "menu");
      const head = document.createElement("div");
      head.className = "wf-ctx-head"; head.textContent = "Select object";
      menu.appendChild(head);
      for (const ref of list) {
        const it = document.createElement("button");
        it.type = "button"; it.className = "wf-ctx-item"; it.setAttribute("role", "menuitem");
        it.dataset.ref = PG.refKey(ref);
        it.textContent = `${PG.TYPE_LABEL[ref.type] || ref.type} ${ref.type === "joint" ? PG.jointPoint(ref.uid).map(c => +c.toFixed(3)).join(", ") : ref.uid}${isSel(ref) ? " ✓" : ""}`;
        it.addEventListener("click", () => { closeChooser(); clickSelect(ref, true); selChanged(); });
        it.addEventListener("pointerenter", () => setHover(vv, ref, x, y));
        menu.appendChild(it);
      }
      document.body.appendChild(menu);
      const r = menu.getBoundingClientRect();
      menu.style.left = Math.max(4, Math.min(e.clientX + 4, window.innerWidth - r.width - 6)) + "px";
      menu.style.top = Math.max(4, Math.min(e.clientY + 4, window.innerHeight - r.height - 6)) + "px";
      chooserEl = menu;
      return true;
    }
    function afterClick(ref) {
      selChanged();
      // Analyze mode with results: a frame click also opens the Member panel (as before)
      if (origClick && ref && ref.type === "member" && isSel(ref)) { try { origClick({ uid: ref.uid }); } catch (err) { console.error(err); } }
    }
    const onLeave = () => { lastMove = null; setHover(v, null); if (line.v === v) { line.mouse = null; v._dirty = true; } };
    c.addEventListener("pointerdown", onDown, true);
    c.addEventListener("pointermove", onMove, true);
    c.addEventListener("pointerup", onUp, true);
    c.addEventListener("pointercancel", () => { if (box && box.el) box.el.remove(); box = null; down = null; }, true);
    c.addEventListener("pointerleave", onLeave);
    v._fDetach = () => {
      c.removeEventListener("pointerdown", onDown, true);
      c.removeEventListener("pointermove", onMove, true);
      c.removeEventListener("pointerup", onUp, true);
      if (v._fTip) v._fTip.remove();
      if (line.v === v) line.v = null;
    };
    v._dirty = true;
  }

  /* ---------------------------------------------------------------- drag mode + line tool */
  function setDragMode(mode) {
    dragMode = mode === "select" ? "select" : "orbit";
    try { localStorage.setItem(DRAG_KEY, dragMode); } catch { /* private mode */ }
    syncToolbar();
  }
  function syncToolbar() {
    document.querySelectorAll("[data-f-drag]").forEach(b => b.classList.toggle("is-active", b.dataset.fDrag === dragMode));
    const hint = document.querySelector("#viewerToolbar .hint");
    if (hint) hint.textContent = dragMode === "select"
      ? "drag box (→ window · ← crossing) · click select · shift add · alt-drag orbit · right-drag pan"
      : "drag orbit · ctrl-drag box (→ window · ← crossing) · click select · shift add · right-drag pan";
    for (const v of fsViewers) v.canvas.classList.toggle("f-select-mode", dragMode === "select");
  }
  function installToolbar() {
    const tb = document.getElementById("viewerToolbar");
    if (!tb || tb.querySelector("[data-f-drag]")) return;
    const seg = document.createElement("div");
    seg.className = "seg-toggle f-drag-toggle";
    seg.id = "fDragToggle";
    seg.title = "Left-drag in the 3D view: orbit the camera, or draw a selection box (→ window, ← crossing). Ctrl-drag always draws the box.";
    for (const [k, l] of [["orbit", "Orbit"], ["select", "Select"]]) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "seg-btn"; b.dataset.fDrag = k; b.id = k === "orbit" ? "fDragOrbit" : "fDragSelect"; b.textContent = l;
      b.addEventListener("click", () => setDragMode(k));
      seg.appendChild(b);
    }
    const sp = tb.querySelector(".toolbar-spacer");
    tb.insertBefore(seg, sp ? sp.nextSibling : null);
  }
  /** The 3D view a Select command should act in (focused pane, else main). */
  function activeViewer() {
    const W = sky.wb && sky.wb.windows;
    if (W && W.layout && W.layout() !== "1") {
      const panes = W.panes();
      const f = panes.find(p => p.el && p.el.classList.contains("is-focus"));
      if (f && f.v) return f.v;
      if (f && !f.v) return null;
    }
    return S.mode === "analyze" && (S.tab || "view3d") === "view3d" ? sky.viewer : null;
  }
  function startLine(v) {
    v = v || activeViewer() || sky.viewer;
    line.v = v; line.p = null; line.mouse = null;
    for (const w of fsViewers) w.canvas.classList.toggle("f-line-mode", w === v);
    sky.toast && sky.toast("Intersecting line", "Click two points in the 3D view: every object the line crosses is added to the selection. Esc ends the tool.", "info", 4000);
  }
  function endLine() {
    if (!line.v) return false;
    const v = line.v;
    line.v = null; line.p = null; line.mouse = null;
    for (const w of fsViewers) w.canvas.classList.remove("f-line-mode");
    v._dirty = true;
    return true;
  }

  document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    const tag = (e.target && e.target.tagName || "").toLowerCase();
    if (["input", "select", "textarea"].includes(tag)) return;
    if (document.querySelector(".modal-backdrop:not(.hidden), .wf-ctx")) return;
    if (endLine()) return;
    if (S.mode !== "model" && (S.selection || []).length) { sky.handleSelect([], false); selChanged(); }
  });
  document.addEventListener("keydown", e => {
    if ((e.key === "v" || e.key === "V") && !e.ctrlKey && !e.metaKey && !e.altKey && line.v) {
      const tag = (e.target && e.target.tagName || "").toLowerCase();
      if (!["input", "select", "textarea"].includes(tag)) endLine();
    }
  });
  document.addEventListener("sky:model-changed", () => { for (const v of fsViewers) { pick.invalidate(v); v._dirty = true; } });
  document.addEventListener("sky:vx-changed", () => { for (const v of fsViewers) v._dirty = true; });

  installToolbar();
  fsSetAttach(attach);
  syncToolbar();
  selChanged();
  return {
    attach, setDragMode, dragMode: () => dragMode, startLine, endLine, lineState: () => ({ on: !!line.v, p: line.p }),
    activeViewer, clickSelect, viewers: () => [...fsViewers],
    hover: v => (v || sky.viewer)._fHover ? { ...(v || sky.viewer)._fHover.ref } : null,
  };
}
