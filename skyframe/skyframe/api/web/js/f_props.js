/* SkyFrame Wave F — dockable Properties / Assign panel in Model AND Analyze
   mode, plus the joint and tendon sections of the panel.

   The panel is the existing #propsPanel (renderProps in js/app.js writes
   #propsContent; every edit goes through the model-edit path: markDirty →
   undo history, model lock, views). In Analyze mode it docks to the right of
   the 3D view (or floats over it, header button ⇱ / ⇲); × or View › Windows ›
   Properties Panel turns it off / on there (a per-browser preference).
   Analyze-mode edits rebuild the 3D scene without moving the camera.

   Joint section (selection of joints): coordinates / story, restraints
   UX UY UZ RX RY RZ (common value or mixed; explicit supports replace the
   automatic base fixity, so the first explicit restraint keeps today's base
   supports by writing them out explicitly), and buttons to the joint assign
   dialogs. Tendon section: uid, pattern, hosts, Edit Tendon…. */

import * as PG from "./f_pickgeom.js";
import fpU from "./units.js";

const PREF_KEY = "sky.f.props";
const DOFS = ["UX", "UY", "UZ", "RX", "RY", "RZ"];
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const same3 = (a, b) => a.every((v, i) => Math.abs(+v - +b[i]) < 1e-6);

export function initFProps(sky, F) {
  const S = sky.store;
  const $ = id => document.getElementById(id);
  const panel = $("propsPanel");
  if (!panel) return null;
  let pref = { on: true, float: false, x: null, y: null };
  try { const o = JSON.parse(localStorage.getItem(PREF_KEY) || "null"); if (o && typeof o === "object") pref = { ...pref, ...o }; } catch { /* private mode */ }
  const save = () => { try { localStorage.setItem(PREF_KEY, JSON.stringify(pref)); } catch { /* private mode */ } };
  const home = panel.parentElement, homeNext = panel.nextSibling;

  /* ---------------------------------------------------------------- header */
  const title = panel.querySelector(".panel-title");
  const bar = document.createElement("div");
  bar.className = "f-props-bar";
  const dockBtn = document.createElement("button");
  dockBtn.type = "button"; dockBtn.className = "icon-btn f-props-dock"; dockBtn.id = "fPropsDock";
  dockBtn.addEventListener("click", () => { pref.float = !pref.float; save(); place(); });
  const closeBtn = document.createElement("button");
  closeBtn.type = "button"; closeBtn.className = "icon-btn f-props-close"; closeBtn.id = "fPropsClose"; closeBtn.textContent = "×";
  closeBtn.title = "Hide the Properties panel in the 3D view (View › Windows › Properties Panel shows it again)";
  closeBtn.addEventListener("click", () => { pref.on = false; save(); place(); });
  if (title) { title.parentNode.insertBefore(bar, title); bar.appendChild(title); bar.append(dockBtn, closeBtn); }

  // float: drag by the header
  let drag = null;
  bar.addEventListener("pointerdown", e => {
    if (!panel.classList.contains("f-float") || e.target.closest("button")) return;
    const r = panel.getBoundingClientRect();
    drag = { dx: e.clientX - r.left, dy: e.clientY - r.top };
    bar.setPointerCapture(e.pointerId);
  });
  bar.addEventListener("pointermove", e => {
    if (!drag) return;
    const host = panel.offsetParent || document.body, hr = host.getBoundingClientRect();
    pref.x = Math.max(0, Math.min(hr.width - 120, e.clientX - hr.left - drag.dx));
    pref.y = Math.max(0, Math.min(hr.height - 60, e.clientY - hr.top - drag.dy));
    panel.style.left = pref.x + "px"; panel.style.top = pref.y + "px"; panel.style.right = "auto";
  });
  bar.addEventListener("pointerup", () => { if (drag) { drag = null; save(); } });

  /* ---------------------------------------------------------------- placement */
  const dock3d = () => document.querySelector("#pane-view3d .viewer-row");
  let where = "home";
  function place() {
    const analyze = S.mode === "analyze";
    // shown whenever it is on (not only with a selection): the 3D view must not change size under the cursor
    const hSlot = sky.hDock && sky.hDock.propsSlot ? sky.hDock.propsSlot(pref.on) : null;   // Wave H: docks right of the window layout (js/h_dock.js)
    const want = hSlot ? "hdock" : analyze && pref.on ? (pref.float ? "float" : "dock") : "home";
    dockBtn.textContent = pref.float ? "⇲" : "⇱";
    dockBtn.title = pref.float ? "Dock the panel to the right of the 3D view" : "Float the panel over the 3D view (drag its header)";
    dockBtn.hidden = !analyze || !!hSlot; closeBtn.hidden = !analyze && !hSlot;
    if (want === where && (want !== "home" || panel.parentElement === home)) { panel.classList.toggle("f-props-hidden", false); return; }
    where = want;
    panel.classList.remove("f-float", "f-dock3d");
    panel.style.left = panel.style.top = panel.style.right = "";
    if (want === "hdock") { hSlot.appendChild(panel); panel.classList.add("f-dock3d"); }
    else if (want === "home") {
      if (panel.parentElement !== home) home.insertBefore(panel, homeNext && homeNext.parentNode === home ? homeNext : null);
    } else if (want === "dock") {
      const row = dock3d();
      if (row) { row.insertBefore(panel, $("memberPanel")); panel.classList.add("f-dock3d"); }
    } else {
      const host = $("viewerWrap");
      if (host) {
        host.appendChild(panel); panel.classList.add("f-float");
        if (pref.x != null) { panel.style.left = pref.x + "px"; panel.style.top = pref.y + "px"; panel.style.right = "auto"; }
      }
    }
    if (sky.viewer && sky.viewer._resize) requestAnimationFrame(() => sky.viewer._resize());
  }
  const mo = new MutationObserver(() => place());
  document.addEventListener("sky:wb-windows", () => setTimeout(place, 0));   // Wave H: dock opened / closed
  for (const id of ["drawMain", "analyzeMain"]) { const n = $(id); if (n) mo.observe(n, { attributes: true, attributeFilter: ["class"] }); }
  document.querySelectorAll(".tab[data-tab]").forEach(t => t.addEventListener("click", () => setTimeout(place, 0)));

  /* Analyze-mode edits: rebuild the 3D scene in place (camera kept) */
  document.addEventListener("sky:model-changed", () => {
    if (S.mode !== "analyze" || !sky.viewer || !S.model) return;
    const v = sky.viewer;
    if (v.model !== S.model) return;
    queueMicrotask(() => {
      try { v._buildScene(); v._dirty = true; S.modelEdited = false; } catch (e) { console.error(e); }
    });
  });

  /* ---------------------------------------------------------------- joint / tendon sections */
  const selJoints = () => (S.selection || []).filter(r => r.type === "joint");
  const selTendons = () => (S.selection || []).filter(r => r.type === "tendon");
  function restraintOf(m, p) {
    const s = (m.supports || []).find(x => same3(x.point, p));
    if (s) return s.restraints.map(Number);
    if ((m.supports || []).length) return [0, 0, 0, 0, 0, 0];
    // automatic base fixity (no explicit supports): base-level joints
    const zb = baseZ(m);
    if (zb != null && Math.abs(p[2] - zb) < 1e-6) return (m.base_fixity || "fixed") === "pinned" ? [1, 1, 1, 0, 0, 0] : [1, 1, 1, 1, 1, 1];
    return [0, 0, 0, 0, 0, 0];
  }
  function baseZ(m) {
    let z = Infinity;
    for (const mm of m.members || []) if (mm.kind === "column") z = Math.min(z, mm.pi[2], mm.pj[2]);
    for (const s of m.shells || []) if (s.kind === "wall") for (const c of s.corners) z = Math.min(z, c[2]);
    return isFinite(z) ? z : null;
  }
  function autoBaseJoints(m) {
    const z = baseZ(m);
    if (z == null) return [];
    const out = new Map();
    for (const mm of m.members || []) for (const p of [mm.pi, mm.pj]) if (Math.abs(p[2] - z) < 1e-6) out.set(PG.jointKey(p), p.map(Number));
    for (const s of m.shells || []) for (const c of s.corners) if (Math.abs(c[2] - z) < 1e-6) out.set(PG.jointKey(c), c.map(Number));
    return [...out.values()];
  }
  /** Set one DOF on the selected joints (explicit supports). */
  function setRestraint(points, i, on) {
    const m = S.model;
    m.supports = m.supports || [];
    let materialized = 0;
    if (!m.supports.length) {
      const r = (m.base_fixity || "fixed") === "pinned" ? [1, 1, 1, 0, 0, 0] : [1, 1, 1, 1, 1, 1];
      for (const p of autoBaseJoints(m)) { m.supports.push({ point: p, restraints: r.slice() }); materialized++; }
    }
    for (const p of points) {
      const k = m.supports.findIndex(x => same3(x.point, p));
      const r = k >= 0 ? m.supports[k].restraints.map(Number) : [0, 0, 0, 0, 0, 0];
      r[i] = on ? 1 : 0;
      if (k >= 0) { if (r.some(Boolean)) m.supports[k].restraints = r; else m.supports.splice(k, 1); }
      else if (r.some(Boolean)) m.supports.push({ point: p.map(Number), restraints: r });
    }
    if (!m.supports.length) delete m.supports;
    sky.markDirty();
    if (materialized) sky.toast && sky.toast("Joint restraints", `${materialized} automatic base support${materialized > 1 ? "s were" : " was"} written as explicit restraints, so the base stays supported.`, "info", 5000);
    sky.renderProps();
  }

  function jointHtml(m, refs) {
    const pts = refs.map(r => PG.jointPoint(r.uid));
    const rs = pts.map(p => restraintOf(m, p));
    const head = refs.length === 1
      ? `<div class="f-props-coord">${pts[0].map((v, i) => `<span><i>${"XYZ"[i]}</i> ${esc(fpU.fmt("length", v, 3))}</span>`).join("")} <span class="unit">${esc(fpU.label("length"))}</span>${PG.storyAtZ(m, pts[0][2]) ? ` <span class="muted">· ${esc(PG.storyAtZ(m, pts[0][2]))}</span>` : ""}</div>` : "";
    const boxes = DOFS.map((d, i) => {
      const all = rs.every(r => r[i] === rs[0][i]);
      return `<label><input type="checkbox" class="f-jdof" data-dof="${i}" id="fJointDof${d}"${all && rs[0][i] ? " checked" : ""}${all ? "" : ' data-mixed="1"'}> ${d}</label>`;
    }).join("");
    const btn = (act, lbl) => sky.etabs && sky.etabs.hasItem && sky.etabs.hasItem(act) ? `<button class="btn btn-small" data-f-act="${act}">${lbl}</button>` : "";
    return `<h3 class="group-title">Joint${refs.length > 1 ? `s <span class="unit">${refs.length}</span>` : ""}</h3>${head}
      <div class="field"><label>Restraints <span class="unit">explicit supports</span></label>
        <div class="check-row f-jdofs">${boxes}</div></div>
      <p class="muted" style="font-size:11px">Explicit supports replace the automatic base fixity; the first one also writes today's base supports out explicitly.</p>
      <div class="f-props-btns">${btn("asn-support", "Restraints…")}${btn("asn-jforce", "Joint Loads…")}${btn("asn-b9-jsprings", "Springs…")}${btn("asn-jmass", "Additional Mass…")}</div>`;
  }
  function tendonHtml(m, refs) {
    return `<h3 class="group-title">Tendon${refs.length > 1 ? `s <span class="unit">${refs.length}</span>` : ""}</h3>` + refs.map(r => {
      const t = (m.tendons || []).find(x => x.uid === r.uid) || {};
      const host = Array.isArray(t.host) ? t.host.join(", ") : (t.host || "—");
      return `<div class="f-props-row"><b>${esc(r.uid)}</b> <span class="muted">· ${esc(t.pattern || "")} · host ${esc(host)}</span>${sky.openTendonProfile ? ` <button class="btn btn-small" data-f-tendon="${esc(r.uid)}">Edit Tendon…</button>` : ""}</div>`;
    }).join("");
  }
  function resultsHtml() {
    if (!S.results || !(S.selection || []).length) return "";
    return `<div class="f-props-btns"><button class="btn btn-small" id="fPropsResults">Show results…</button></div>`;
  }
  function wire(box) {
    box.querySelectorAll(".f-jdof").forEach(cb => {
      if (cb.dataset.mixed) cb.indeterminate = true;
      cb.addEventListener("change", () => setRestraint(selJoints().map(r => PG.jointPoint(r.uid)), +cb.dataset.dof, cb.checked));
    });
    box.querySelectorAll("[data-f-act]").forEach(b => b.addEventListener("click", () => sky.etabs.clickItem(b.dataset.fAct)));
    box.querySelectorAll("[data-f-tendon]").forEach(b => b.addEventListener("click", () => sky.openTendonProfile(b.dataset.fTendon)));
    const rb = box.querySelector("#fPropsResults");
    if (rb) rb.addEventListener("click", () => F.onShowResults((S.selection || [])[0]));
  }
  /** renderProps hook: selection of joints / tendons only (else false). */
  sky.fPropsEmpty = box => {
    const j = selJoints(), t = selTendons(), m = S.model;
    if (!m || (!j.length && !t.length)) return false;
    box.innerHTML = `<div class="sel-count"><span>${j.length + t.length} selected</span><button class="link-3d" id="propClear">Clear</button></div>
      <div class="sel-kinds">${[[j.length, "joint"], [t.length, "tendon"]].filter(([n]) => n).map(([n, k]) => `${n} ${k}${n > 1 ? "s" : ""}`).join(" · ")}</div>` +
      (j.length ? jointHtml(m, j) : "") + (t.length ? tendonHtml(m, t) : "") + resultsHtml();
    box.querySelector("#propClear").addEventListener("click", () => sky.handleSelect([], false));
    wire(box);
    return true;
  };
  /** renderProps hook: mixed selections (frames / shells / … plus joints / tendons). */
  sky.fDecorateProps = box => {
    const j = selJoints(), t = selTendons(), m = S.model;
    if (!m) return;
    const extra = document.createElement("div");
    extra.className = "f-props-extra";
    extra.innerHTML = (j.length ? jointHtml(m, j) : "") + (t.length ? tendonHtml(m, t) : "") + resultsHtml();
    if (!extra.innerHTML.trim()) return;
    const del = box.querySelector("#propDelete");
    const anchor = del ? del.previousElementSibling : null;
    if (anchor && anchor.parentNode === box) box.insertBefore(extra, anchor); else box.appendChild(extra);
    const kinds = box.querySelector(".sel-kinds");
    if (kinds && j.length) kinds.textContent += ` · ${j.length} joint${j.length > 1 ? "s" : ""}`;
    if (kinds && t.length) kinds.textContent += ` · ${t.length} tendon${t.length > 1 ? "s" : ""}`;
    const cnt = box.querySelector(".sel-count span");
    if (cnt) cnt.textContent = `${(S.selection || []).length} selected`;
    wire(extra);
  };

  F.onSelectionProps = () => place();
  place();
  return { place, pref: () => ({ ...pref }), setOn: on => { pref.on = !!on; save(); place(); }, setFloat: f => { pref.float = !!f; save(); place(); }, where: () => where };
}
