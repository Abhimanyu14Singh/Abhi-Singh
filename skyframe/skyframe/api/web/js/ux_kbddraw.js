/* SkyFrame UX pass 2 — keyboard drawing / selection in the Plan view.
   The plan canvas (#planSvg) becomes focusable (Tab, or automatically after a
   Draw / Select command chosen from the keyboard). While it has focus:
     ←/→/↑/↓        move a cursor to the next grid line (primary grid)
     Shift+arrow    half step (mid-span points, e.g. to pick a beam)
     Enter / Space  the same as a mouse click at the cursor: places a point
                    with the active draw tool, or selects with the Select
                    tool (Shift+Enter adds to the selection, Enter again on
                    the same spot cycles through coincident objects)
     Esc            unchanged (ends the chain / clears the selection)
     Home           back to the first grid intersection
   Tool keys (V, B, C …), F, Delete keep working. Everything is delegated to
   the plan editor's own click path (_toolClick / hitTestCycle / onSelect),
   so a keyboard-drawn beam is identical to a mouse-drawn one. */

export function installKbdDraw(sky) {
  const S = sky.store;
  const svg = document.getElementById("planSvg");
  if (!svg) return;
  const ed = () => sky.planEditor;
  svg.setAttribute("tabindex", "0");
  svg.setAttribute("role", "application");
  svg.setAttribute("aria-roledescription", "plan drawing canvas");
  svg.setAttribute("aria-label", "Plan view. Arrow keys move the cursor between grid lines (Shift for half steps); Enter places a point or selects; Esc cancels.");
  const live = document.createElement("div");
  live.className = "ux-sr-only";
  live.setAttribute("aria-live", "polite");
  live.id = "uxPlanLive";
  svg.after(live);

  let cur = null;
  const EPS = 1e-6;
  function axes() {
    const m = S.model;
    const g = m && (m.grid || (m.grid_systems || [])[0]);
    const xs = g && Array.isArray(g.x_lines) ? [...g.x_lines].map(Number).sort((a, b) => a - b) : [];
    const ys = g && Array.isArray(g.y_lines) ? [...g.y_lines].map(Number).sort((a, b) => a - b) : [];
    if (!xs.length || !ys.length) {
      // no orthogonal grid: use member end points
      const px = new Set(), py = new Set();
      for (const mb of (m && m.members) || []) for (const p of [mb.pi, mb.pj]) { px.add(+p[0]); py.add(+p[1]); }
      return { xs: [...px].sort((a, b) => a - b), ys: [...py].sort((a, b) => a - b), g: null };
    }
    return { xs, ys, g };
  }
  const withHalves = a => {
    const out = [];
    a.forEach((v, i) => { out.push(v); if (i < a.length - 1) out.push((v + a[i + 1]) / 2); });
    return out;
  };
  const step = (arr, v, dir) => {
    if (!arr.length) return v;
    if (dir > 0) { const n = arr.find(x => x > v + EPS); return n == null ? v : n; }
    for (let i = arr.length - 1; i >= 0; i--) if (arr[i] < v - EPS) return arr[i];
    return v;
  };
  function label(p) {
    const { xs, ys, g } = axes();
    const lx = g && g.x_labels ? g.x_labels[g.x_lines.findIndex(x => Math.abs(x - p.x) < EPS)] : null;
    const ly = g && g.y_labels ? g.y_labels[g.y_lines.findIndex(y => Math.abs(y - p.y) < EPS)] : null;
    const f = v => String(+v.toFixed(3));
    return (lx && ly ? `grid ${lx}/${ly} · ` : lx ? `grid ${lx} · ` : ly ? `grid ${ly} · ` : "") + `x ${f(p.x)}, y ${f(p.y)}`;
  }
  function show() {
    const e = ed();
    if (!e || !cur) return;
    e._mouseWorld = { x: cur.x, y: cur.y };
    e.hoverSnap = e.tool !== "select" && e.tool !== "erase" ? e.snap(cur) : null;
    e.hoverRef = e.tool === "select" || e.tool === "erase" ? e.hitTest(cur) : null;
    // keep the cursor on screen
    if (e.toScreen && e.svg) {
      const [sx, sy] = e.toScreen(cur.x, cur.y);
      const r = e.svg.getBoundingClientRect();
      if (sx < 0 || sy < 0 || sx > r.width || sy > r.height) { e.cx = cur.x; e.cy = cur.y; e._applyTransform && e._applyTransform(); }
    }
    e.renderOverlay();
    e._readout && e._readout(cur);
    const ref = e.hoverRef ? ` · on ${e.hoverRef.type} ${e.hoverRef.uid}` : "";
    live.textContent = `${label(cur)}${e.pending ? " (second point)" : ""}${ref}`;
  }
  function ensureCur() {
    if (cur) return;
    const { xs, ys } = axes();
    cur = { x: xs.length ? xs[0] : 0, y: ys.length ? ys[0] : 0 };
  }
  svg.addEventListener("focus", () => { ensureCur(); show(); });
  svg.addEventListener("blur", () => {
    const e = ed();
    if (e) { e.hoverSnap = null; e.hoverRef = null; e._mouseWorld = null; e.renderOverlay(); }
  });
  svg.addEventListener("pointerdown", () => { cur = null; });
  svg.addEventListener("keydown", e => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const pe = ed();
    if (!pe || S.mode !== "model") return;
    const k = e.key;
    const arrows = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] };
    if (arrows[k]) {
      ensureCur();
      const { xs, ys } = axes();
      const [dx, dy] = arrows[k];
      const ax = e.shiftKey ? withHalves(xs) : xs, ay = e.shiftKey ? withHalves(ys) : ys;
      if (dx) cur = { x: step(ax, cur.x, dx), y: cur.y };
      if (dy) cur = { x: cur.x, y: step(ay, cur.y, dy) };
      show();
    } else if (k === "Home") {
      cur = null; ensureCur(); show();
    } else if (k === "Enter" || k === " ") {
      ensureCur();
      if (pe.tool === "select") {
        const ref = e.shiftKey ? pe.hitTest(cur) : pe.hitTestCycle(cur);
        pe.opts.onSelect(ref ? [ref] : [], e.shiftKey);
        pe.renderOverlay();
      } else {
        const [px, py] = pe.toScreen ? pe.toScreen(cur.x, cur.y) : [0, 0];
        pe._toolClick({ x: cur.x, y: cur.y }, px, py);
      }
      show();
    } else return;
    e.preventDefault();
    e.stopPropagation();   // arrows must not also step the story (app.js)
  });

  /* a draw / select command chosen from the keyboard (menu Enter, palette,
     tool key while nothing is focused) moves focus to the plan canvas */
  const focusPlan = () => setTimeout(() => {
    if (S.mode === "model" && S.view !== "elev" && svg.isConnected && svg.getBoundingClientRect().width > 0
        && !(sky.ux && sky.ux.dialogs && sky.ux.dialogs.top && sky.ux.dialogs.top())) {
      try { svg.focus({ preventScroll: true }); } catch { /* ignore */ }
    }
  }, 30);
  const mb = document.getElementById("etabsMenubar");
  if (mb) mb.addEventListener("click", e => {
    const it = e.target.closest && e.target.closest(".etabs-menu-item[data-act]");
    if (it && /^draw-(select|column|beam|brace|wall|slab|spring|linespring|link|erase|plan)$/.test(it.dataset.act) && e.detail === 0) focusPlan();
  });
  document.addEventListener("keydown", e => {
    if (e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented) return;
    const t = e.target;
    if (t !== document.body && t !== document.documentElement) return;
    if (S.mode === "model" && /^[vcbxwslgke]$/i.test(e.key)) focusPlan();
  });

  sky.ux = sky.ux || {};
  sky.ux.kbdDraw = { cursor: () => cur && { ...cur }, focus: () => { svg.focus(); }, set: p => { cur = { x: p.x, y: p.y }; show(); } };
}
