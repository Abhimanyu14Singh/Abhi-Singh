/* SkyFrame W8 — object snapping in the plan view (ETABS Draw › Snap Options).

   Modes: Points & ends (joint), Midpoints (mid), Intersections (inter),
   Perpendicular (perp, from the pending first click). The existing grid
   snap (Options › Snap to Grid, status "Snap" chip) is unchanged and used
   whenever no object snap is in range. Geometry: js/w8_snapgeom.js (pure,
   mirrored by skyframe/core/snapgeom.py and unit-tested).

   UI: Draw › Snap Options… (dlgkit dialog), status-bar toggle chips
   (Pt · Mid · Int · ⊥) and a snap-kind glyph at the cursor. The modes are a
   per-browser preference (localStorage), never part of the model.
   Hook: PlanEditor.w8SnapHook (one line in js/draw.js). */

import SG from "./w8_snapgeom.js";
import { el as snEl, dkForm as snForm, dkGroup as snGroup, dkDialog as snDialog, dkNote as snNote } from "./dlgkit.js";

const KEY = "sky.w8.snap";
const DEFAULTS = { joint: true, mid: true, inter: true, perp: true, tolPx: 10 };
const LABEL = { joint: "Points & ends", mid: "Midpoints", inter: "Intersections", perp: "Perpendicular" };
const CHIP = { joint: "Pt", mid: "Mid", inter: "Int", perp: "⊥" };
const NS = "http://www.w3.org/2000/svg";

function load() {
  try { const o = JSON.parse(localStorage.getItem(KEY) || "null"); if (o && typeof o === "object") return { ...DEFAULTS, ...o }; } catch { /* ignore */ }
  return { ...DEFAULTS };
}

export function initW8Snap(sky) {
  const S = sky.store;
  const st = load();
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(st)); } catch { /* ignore */ } };
  const pe = sky.planEditor;
  let lastKind = null;

  /* ---------------------------------------------- snap hook */
  const fromOf = ed => {
    if (ed.pending) return [ed.pending.x, ed.pending.y];
    if (ed.w8From) return ed.w8From;
    return null;
  };
  if (pe) pe.w8SnapHook = (w, ed) => {
    lastKind = null;
    if (!(st.joint || st.mid || st.inter || st.perp)) return null;
    const m = ed.opts.getModel();
    if (!m) return null;
    const tgt = SG.storyTargets(m, ed.opts.getStory());
    const tol = (st.tolPx || 10) / ed.scale;
    const r = SG.snapObjects(w, tgt, tol, st, fromOf(ed));
    if (!r) return null;
    lastKind = r.kind;
    return { x: r.x, y: r.y, dPx: r.d * ed.scale, kind: r.kind };
  };

  /* ---------------------------------------------- cursor glyph */
  const prevOverlay = pe && pe.w8Overlay;
  if (pe) pe.w8Overlay = (g, ed) => {
    if (prevOverlay) prevOverlay(g, ed);
    const hs = ed.hoverSnap;
    if (!hs || !hs.kind || ed.tool === "select" || ed.tool === "erase") return;
    const [x, y] = ed.toScreen(hs.x, hs.y);
    const mk = (tag, a) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(a)) e.setAttribute(k, v); g.appendChild(e); return e; };
    const col = "#f5be3c", sw = 1.6;
    if (hs.kind === "joint") mk("rect", { x: x - 6, y: y - 6, width: 12, height: 12, fill: "none", stroke: col, "stroke-width": sw, "data-w8snap": "joint" });
    else if (hs.kind === "mid") mk("path", { d: `M${x},${y - 7} L${x + 7},${y + 5} L${x - 7},${y + 5} Z`, fill: "none", stroke: col, "stroke-width": sw, "data-w8snap": "mid" });
    else if (hs.kind === "inter") mk("path", { d: `M${x - 6},${y - 6} L${x + 6},${y + 6} M${x + 6},${y - 6} L${x - 6},${y + 6}`, stroke: col, "stroke-width": sw + 0.4, "data-w8snap": "inter" });
    else if (hs.kind === "perp") mk("path", { d: `M${x - 7},${y + 6} L${x + 7},${y + 6} M${x},${y + 6} L${x},${y - 7}`, fill: "none", stroke: col, "stroke-width": sw, "data-w8snap": "perp" });
    const t = mk("text", { x: x + 9, y: y - 8, fill: col, "font-size": 10, "font-weight": 700, "font-family": "inherit" });
    t.textContent = { joint: "Point", mid: "Mid", inter: "Intersection", perp: "Perpendicular" }[hs.kind];
  };

  /* ---------------------------------------------- status-bar chips */
  const chips = {};
  const status = sky.etabs && sky.etabs.el && sky.etabs.el.status;
  const syncChips = () => { for (const k of Object.keys(chips)) { chips[k].classList.toggle("is-on", !!st[k]); chips[k].setAttribute("aria-pressed", st[k] ? "true" : "false"); } };
  if (status) {
    const grp = snEl("span", { class: "sb-item w8-osnap", id: "w8OsnapChips", role: "group", "aria-label": "Object snaps" }, [snEl("span", { class: "sb-lbl", text: "OSnap" })]);
    for (const k of Object.keys(CHIP)) {
      const b = snEl("button", { type: "button", class: "sb-chip w8-osnap-chip", id: "w8Snap_" + k, title: `Snap to ${LABEL[k].toLowerCase()} (click to toggle)` }, [CHIP[k]]);
      b.addEventListener("click", () => { st[k] = !st[k]; save(); syncChips(); });
      chips[k] = b;
      grp.appendChild(b);
    }
    const snapChip = [...status.querySelectorAll(".sb-chip")].find(b => /Snap/.test(b.textContent));
    if (snapChip && snapChip.nextSibling) status.insertBefore(grp, snapChip.nextSibling);
    else status.appendChild(grp);
    syncChips();
  }

  /* ---------------------------------------------- Snap Options dialog */
  function openSnapOptions() {
    const D = { ...st, grid: sky.etabs && sky.etabs.snapOn ? sky.etabs.snapOn() : true };
    const F = snForm();
    const body = [
      snGroup("Snap To", [
        F.check(D, "grid", { label: "Grid lines and intersections", id: "w8SnapGrid" }),
        ...Object.keys(LABEL).map(k => F.check(D, k, { label: LABEL[k], id: "w8SnapChk_" + k })),
      ]),
      snGroup("Tolerance", [F.num(D, "tolPx", { label: "Snap radius", unit: "px", int: true, min: 2, max: 40, id: "w8SnapTol" })]),
      snNote("Object snaps win over the grid inside the snap radius, in the order points & ends → intersections → midpoints → perpendicular. Perpendicular snaps from the first click of a two-click tool."),
    ];
    return snDialog({
      id: "w8SnapDlg", title: "Snap Options", iconId: "status-snap", size: "sm", form: F, body,
      onOk: () => {
        for (const k of Object.keys(LABEL)) st[k] = !!D[k];
        st.tolPx = D.tolPx;
        save(); syncChips();
        if (sky.etabs && sky.etabs.snapOn && sky.etabs.snapOn() !== !!D.grid) sky.etabs.toggleSnap();
      },
    });
  }
  if (sky.menu) sky.menu.add("Draw", { label: "Snap Options…", act: "draw-w8-snap", hint: "points · ends · mid · intersections · ⊥", fn: openSnapOptions });

  sky.w8snap = {
    modes: () => ({ ...st }),
    set: (k, v) => { if (k in st) { st[k] = v; save(); syncChips(); } },
    lastKind: () => lastKind,
    open: openSnapOptions,
    geom: SG,
  };
}
