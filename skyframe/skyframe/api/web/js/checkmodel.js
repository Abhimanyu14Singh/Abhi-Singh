/* SkyFrame — Analyze > Check Model… / Check Stability… (ETABS parity).

   * Check Model…      options dialog (tolerance, check groups) → POST /api/check
                       with the CURRENT unsaved working model → dockable results
                       panel (issues grouped by severity → code, with counts).
                       Click an issue → select + zoom to its objects / location
                       in the 3D view or the plan view (existing selection and
                       highlight machinery). "Fix" only where trivially safe
                       (delete a duplicate / zero-length frame or duplicate
                       shell, merge coincident joints).
   * Check Stability…  POST /api/check/stability → mechanisms (joints / DOFs with
                       participation, colour-coded 3D markers with DOF arrows),
                       condition numbers, max diagonal ratio, ill-conditioning
                       notes, the STABILITY_TOO_LARGE / build-failed cases.
   * Pre-run check     Options > Check Model Before Run (default ON, persisted in
                       localStorage): the fast /api/check runs before every Run
                       (skipped for a model unchanged since the last clean
                       check); errors → Cancel / Run Anyway prompt.
   * Status bar chip   last check result (OK / N errors / N warnings / stale).

   Mock mode (?mock=1) computes both locally (js/mock_check.js). The model is
   never changed except by an explicit Fix click. Analysis only. */

import U from "./units.js";
import * as ME from "./modeledit.js";
import { dialog, btn, footBar, errorLine, showError } from "./analysisdlg.js";
import { mockCheckModel, mockCheckStability } from "./mock_check.js";

const LS_OPTS = "skyframe.checkModel.options";
const LS_PRERUN = "skyframe.checkModel.beforeRun";
const lsGet = k => { try { return localStorage.getItem(k); } catch (e) { return null; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } };

const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function el(tag, attrs = {}, kids = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
    else if (v != null && v !== false) n.setAttribute(k, v === true ? "" : v);
  }
  (Array.isArray(kids) ? kids : [kids]).forEach(c =>
    c != null && n.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
  return n;
}
const SVGNS = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs = {}) => {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};

/* ------------------------------------------------ inline SVG icons */
const ICO = {
  check: `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 2.5h8l3 3v8h-11z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M5 8.6l2 2 4-4.4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  stab: `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 14V5l5-3 5 3v9" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M3 14h10M6 14V9h4v5" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M1.5 6.5l1.5-1.5M14.5 6.5L13 5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>`,
  error: `<svg viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="5" fill="currentColor"/><path d="M4 4l4 4M8 4l-4 4" stroke="#fff" stroke-width="1.4" stroke-linecap="round"/></svg>`,
  warning: `<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M6 1l5.2 9.5H.8z" fill="currentColor"/><path d="M6 4.3v3" stroke="#1a1300" stroke-width="1.3" stroke-linecap="round"/><circle cx="6" cy="8.9" r=".75" fill="#1a1300"/></svg>`,
  info: `<svg viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="5" fill="currentColor"/><path d="M6 5.4v3.2" stroke="#0b1a22" stroke-width="1.3" stroke-linecap="round"/><circle cx="6" cy="3.6" r=".75" fill="#0b1a22"/></svg>`,
  ok: `<svg viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="5" fill="currentColor"/><path d="M3.6 6.2l1.7 1.7 3.2-3.5" fill="none" stroke="#06170f" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  dock: `<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="1.5" y="2.5" width="13" height="11" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M10 2.5v11" stroke="currentColor" stroke-width="1.3"/></svg>`,
  float: `<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="4.5" y="1.5" width="10" height="8" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M2.5 5v8.5H11" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>`,
  redo: `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M13 8a5 5 0 1 1-1.6-3.7" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><path d="M12 1.5v3.2H8.8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
};
const ico = (k, cls = "ck-ico") => ICO[k].replace("<svg ", `<svg class="${cls}" `);

/* ------------------------------------------------ issue codes */
export const CODE_TITLE = {
  JOINT_COINCIDENT: "Coincident joints",
  FRAME_ZERO_LENGTH: "Zero-length frames",
  FRAME_DUPLICATE: "Duplicate frames",
  FRAME_OVERLAP: "Overlapping frames",
  FRAME_INTERSECTION: "Frames crossing without a joint",
  FRAME_JOINT_ON_SPAN: "Frame end on another frame's span",
  FRAME_UNCONNECTED: "Unconnected frames",
  SHELL_UNCONNECTED: "Unconnected shells",
  LINK_UNCONNECTED: "Unconnected links",
  STRUCTURE_UNSUPPORTED_PART: "Unsupported structure parts",
  NO_SUPPORTS: "No supports",
  JOINT_UNCONNECTED: "Assignments off any joint",
  STORY_NO_SUPPORT_PATH: "Story without support path",
  STORY_NO_VERTICAL_ELEMENTS: "Story without vertical elements",
  STORY_EMPTY: "Empty stories",
  SHELL_CORNER_COUNT: "Shell corner count",
  SHELL_SELF_INTERSECTING: "Self-intersecting shells",
  SHELL_ZERO_AREA: "Zero-area shells",
  SHELL_WARPED: "Warped shells",
  SHELL_CONCAVE: "Concave shells",
  SHELL_ASPECT_RATIO: "Shell aspect ratio",
  SHELL_DUPLICATE: "Duplicate shells",
  DUPLICATE_UID: "Duplicate object IDs",
  FRAME_SECTION_MISSING: "Missing frame sections",
  SHELL_SECTION_MISSING: "Missing shell sections",
  SECTION_MATERIAL_MISSING: "Missing section materials",
  INVALID_DATA: "Invalid data",
  PATTERN_EMPTY: "Empty load patterns",
  CASE_NO_PATTERNS: "Cases without patterns",
  CASE_EMPTY_PATTERN: "Cases using empty patterns",
  CASE_PATTERN_MISSING: "Cases with missing patterns",
  LOAD_TARGET_MISSING: "Loads on missing objects",
  COMBO_EMPTY: "Empty combinations",
  COMBO_CASE_MISSING: "Combinations with missing cases",
  COMBO_CASE_INVALID: "Invalid combination cases",
  STABILITY_MECHANISM: "Mechanisms",
  STABILITY_ILL_CONDITIONED: "Ill-conditioning",
  STABILITY_TOO_LARGE: "Too large for the stability check",
  STABILITY_BUILD_FAILED: "Build failed",
};

/* Check groups (ETABS Check Model dialog sections). The backend runs every
   check; the groups filter what is reported. Unknown codes → "other" (shown). */
export const GROUPS = [
  { key: "joints", label: "Joint checks", hint: "coincident joints, supports / loads / springs / masses off any joint" },
  { key: "frames", label: "Frame checks", hint: "zero length, duplicates, overlaps, crossings, ends on spans" },
  { key: "shells", label: "Shell checks", hint: "corners, warping, concavity, area, aspect ratio, duplicates" },
  { key: "links", label: "Link checks", hint: "unconnected links" },
  { key: "support", label: "Connectivity & support checks", hint: "no supports, unsupported parts, stories without a support path" },
  { key: "data", label: "Property & data checks", hint: "missing sections / materials, duplicate IDs, invalid data" },
  { key: "loads", label: "Load checks", hint: "empty patterns, cases and combinations; loads on missing objects" },
];
export function groupOf(code) {
  const c = String(code || "");
  if (c === "DUPLICATE_UID" || c.endsWith("_SECTION_MISSING") || c === "SECTION_MATERIAL_MISSING" || c === "INVALID_DATA") return "data";
  if (c === "NO_SUPPORTS" || c === "STRUCTURE_UNSUPPORTED_PART" || c.startsWith("STORY_")) return "support";
  if (c.startsWith("JOINT_")) return "joints";
  if (c.startsWith("FRAME_")) return "frames";
  if (c.startsWith("SHELL_")) return "shells";
  if (c.startsWith("LINK_")) return "links";
  if (/^(PATTERN_|CASE_|COMBO_|LOAD_)/.test(c)) return "loads";
  return "other";
}

const SEV = ["error", "warning", "info"];
const SEV_LABEL = { error: "Errors", warning: "Warnings", info: "Information" };
const SEV_COLOR = { error: "#e66767", warning: "#e5a50a", info: "#35b5e5" };
const DOF_COLOR = { UX: "#ef6b6b", UY: "#3fcf8e", UZ: "#4aa8ff", RX: "#ef6b6b", RY: "#3fcf8e", RZ: "#4aa8ff" };
const MECH_COLOR = ["#e66767", "#e5a50a", "#c27cf0", "#35b5e5", "#3fcf8e", "#f08a4b", "#e85fae", "#9bd14b", "#57d3d3", "#b7a0ff"];
const AXIS = { X: [1, 0, 0], Y: [0, 1, 0], Z: [0, 0, 1] };

const DEFAULT_OPTS = () => ({
  tol: 0.001,
  groups: Object.fromEntries(GROUPS.map(g => [g.key, true])),
  withStability: false,
  nav: "auto",                 // auto | 3d | plan
  maxDofs: 3000,
});
function loadOpts() {
  const o = DEFAULT_OPTS();
  try {
    const raw = JSON.parse(lsGet(LS_OPTS) || "null");
    if (raw && typeof raw === "object") {
      if (+raw.tol > 0 && +raw.tol <= 1) o.tol = +raw.tol;
      if (raw.groups && typeof raw.groups === "object")
        for (const g of GROUPS) if (typeof raw.groups[g.key] === "boolean") o.groups[g.key] = raw.groups[g.key];
      if (typeof raw.withStability === "boolean") o.withStability = raw.withStability;
      if (["auto", "3d", "plan"].includes(raw.nav)) o.nav = raw.nav;
      if (Number.isInteger(raw.maxDofs) && raw.maxDofs >= 1 && raw.maxDofs <= 6000) o.maxDofs = raw.maxDofs;
    }
  } catch (e) { /* defaults */ }
  return o;
}

/* ================================================================ */
export function initCheckModel(sky) {
  const S = sky.store;
  const $ = id => document.getElementById(id);
  const toast = (t, m, k, ms) => sky.toast && sky.toast(t, m, k || "info", ms || 4500);

  // stylesheet (separate file — no inline <style>)
  if (!document.querySelector('link[data-ck-css]')) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/static/checkmodel.css"; l.setAttribute("data-ck-css", "1");
    document.head.appendChild(l);
  }

  const st = {
    opts: loadOpts(),
    check: null,        // {raw, issues, summary, key, stale, at}
    stab: null,         // stability response (+ stale)
    lastCleanKey: null, // model text key of the last check without errors
    markers: [],        // [{p, color, dofs:[{dof, w}], label}]
    hlOwned: false,
    tab: "check",
    docked: true,
    activeKey: null,    // highlighted row key
    activeMech: null,
    prerunCount: 0,     // number of pre-run checks that hit the server/mock
    busy: false,
  };

  /* ---------------- helpers */
  const modelText = () => JSON.stringify(S.model, (k, v) => k === "_mock_params" ? undefined : v);
  const groupsKey = () => GROUPS.map(g => st.opts.groups[g.key] ? "1" : "0").join("");
  const keyOf = txt => `${st.opts.tol}|${groupsKey()}|${txt}`;
  const fmtLen = v => U.fmt("length", v, 3);
  const fmtPt = p => p ? `(${p.map(fmtLen).join(", ")}) ${U.label("length")}` : "";
  const sci = v => (v == null || !isFinite(v)) ? "—" : (Math.abs(v) >= 1e5 || (Math.abs(v) < 1e-3 && v !== 0) ? v.toExponential(2) : v.toLocaleString("en-US", { maximumFractionDigits: 2 }));

  async function post(path, bodyText) {
    const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: bodyText });
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
    return data;
  }

  function filterResult(raw) {
    const keep = i => { const g = groupOf(i.code); return g === "other" || st.opts.groups[g] !== false; };
    const issues = (raw.issues || []).filter(keep);
    const by_code = {};
    for (const i of issues) by_code[i.code] = (by_code[i.code] || 0) + 1;
    const cnt = s => issues.filter(i => i.severity === s).length;
    const summary = { ...(raw.summary || {}), errors: cnt("error"), warnings: cnt("warning"), info: cnt("info"), by_code };
    summary.ok = summary.errors === 0;
    return { issues, summary };
  }

  /** Run /api/check on the CURRENT working model. */
  async function runCheck(txt = modelText()) {
    if (!S.model) throw new Error("No model loaded");
    let raw;
    if (S.mock) raw = mockCheckModel(JSON.parse(txt), { tolerance_m: st.opts.tol });
    else raw = await post("/api/check", `{"model":${txt},"tolerance_m":${st.opts.tol}}`);
    const { issues, summary } = filterResult(raw);
    const key = keyOf(txt);
    st.check = { raw, issues, summary, key, stale: false, at: new Date() };
    if (summary.ok) st.lastCleanKey = key;
    syncChip();
    if (panel && !panel.classList.contains("hidden")) renderPanel();
    return st.check;
  }

  async function runStability() {
    if (!S.model) throw new Error("No model loaded");
    const txt = modelText();
    let res;
    if (S.mock) res = mockCheckStability(JSON.parse(txt), { max_dofs: st.opts.maxDofs });
    else res = await post("/api/check/stability", `{"model":${txt},"max_dofs":${st.opts.maxDofs}}`);
    st.stab = { ...res, stale: false, at: new Date() };
    return st.stab;
  }

  /* ================= status-bar chip ================= */
  const chip = el("button", { class: "sb-chip ck-chip", id: "ckChip", title: "Check Model — not run yet. Click to check." });
  const chipTxt = el("span", { class: "ck-chip-txt", text: "Check —" });
  chip.insertAdjacentHTML("afterbegin", ico("check", "sb-ico"));
  chip.appendChild(chipTxt);
  chip.addEventListener("click", () => { if (st.check) openPanel("check"); else openCheckModel(); });
  (function mountChip() {
    const bar = $("etabsStatus");
    if (!bar) return;
    const anchor = bar.querySelector("#sbStatusText");
    const sep = el("span", { class: "sb-sepv" });
    if (anchor) { bar.insertBefore(chip, anchor); bar.insertBefore(sep, anchor); }
    else bar.append(sep, chip);
  })();
  function syncChip() {
    const c = st.check;
    chip.classList.remove("is-ok", "is-warn", "is-err", "is-stale");
    if (!c) { chipTxt.textContent = "Check —"; chip.title = "Check Model — not run yet. Click to check."; return; }
    const s = c.summary;
    if (s.errors) { chipTxt.textContent = `${s.errors} error${s.errors > 1 ? "s" : ""}`; chip.classList.add("is-err"); }
    else if (s.warnings) { chipTxt.textContent = `${s.warnings} warning${s.warnings > 1 ? "s" : ""}`; chip.classList.add("is-warn"); }
    else { chipTxt.textContent = "Check OK"; chip.classList.add("is-ok"); }
    if (c.stale) chip.classList.add("is-stale");
    chip.title = `Last Check Model: ${s.errors} error(s), ${s.warnings} warning(s), ${s.info} info · ` +
      `${c.at.toLocaleTimeString()}` + (c.stale ? " · model changed since" : "") + " — click for details";
  }

  /* ================= object lookup / geometry ================= */
  function lookup(name) {
    const m = S.model;
    if (!m) return null;
    const mm = (m.members || []).find(x => x.uid === name);
    if (mm) return { type: "member", uid: name, pts: [mm.pi, mm.pj] };
    const sh = (m.shells || []).find(x => x.uid === name);
    if (sh) return { type: "shell", uid: name, pts: sh.corners || [] };
    const lk = (m.links || []).find(x => x.uid === name);
    if (lk) return { type: "link", uid: name, pts: [lk.pi, lk.pj] };
    const sto = (m.stories || []).find(x => x.name === name);
    if (sto) return { type: "story", name, elevation: sto.elevation };
    if ((m.patterns || {})[name]) return { type: "pattern", name };
    if ((m.combos || {})[name]) return { type: "combo", name };
    for (const k of ["cases", "rs_cases", "th_cases", "pushover_cases", "staged_cases", "buckling_cases"])
      if ((m[k] || {})[name]) return { type: "case", name };
    return null;
  }
  const bboxOf = pts => {
    const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (const p of pts) for (let k = 0; k < 3; k++) { lo[k] = Math.min(lo[k], +p[k]); hi[k] = Math.max(hi[k], +p[k]); }
    return { lo, hi, c: lo.map((v, k) => (v + hi[k]) / 2), ext: Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) / 2 };
  };
  function storyAtZ(z) {
    const ss = (S.model && S.model.stories) || [];
    if (!ss.length) return null;
    let best = ss[0], bd = Infinity;
    for (const s of ss) { const d = Math.abs(s.elevation - z); if (d < bd) { bd = d; best = s; } }
    return best.name;
  }

  /* ================= 3D + plan markers ================= */
  function draw3d(ctx, P, v) {
    if (!st.markers.length) return;
    const L = Math.max(0.4, (v.radius || 10) * 0.12);
    ctx.save();
    for (const m of st.markers) {
      const pc = P.toCam(m.p);
      if (pc[2] < P.near) continue;
      const s = P.proj(pc);
      for (const d of m.dofs || []) {
        const ax = AXIS[d.dof[1]];
        if (!ax) continue;
        const len = L * (0.55 + 0.45 * (d.w || 1));
        const e = [m.p[0] + ax[0] * len, m.p[1] + ax[1] * len, m.p[2] + ax[2] * len];
        const ec = P.toCam(e);
        if (ec[2] < P.near) continue;
        const se = P.proj(ec);
        const col = DOF_COLOR[d.dof] || "#fff";
        ctx.strokeStyle = col; ctx.fillStyle = col;
        ctx.lineWidth = 1.5 + 2 * (d.w || 0.5);
        ctx.beginPath(); ctx.moveTo(s.x, s.y); ctx.lineTo(se.x, se.y); ctx.stroke();
        const ang = Math.atan2(se.y - s.y, se.x - s.x);
        const head = (x, y) => {
          ctx.beginPath();
          ctx.moveTo(x, y);
          ctx.lineTo(x - 9 * Math.cos(ang - 0.42), y - 9 * Math.sin(ang - 0.42));
          ctx.lineTo(x - 9 * Math.cos(ang + 0.42), y - 9 * Math.sin(ang + 0.42));
          ctx.closePath(); ctx.fill();
        };
        head(se.x, se.y);
        if (d.dof[0] === "R") head(se.x - 7 * Math.cos(ang), se.y - 7 * Math.sin(ang));   // double head = rotation
      }
      const r = m.r || 7;
      ctx.beginPath(); ctx.arc(s.x, s.y, r, 0, Math.PI * 2);
      ctx.fillStyle = m.fill || "rgba(230,103,103,0.22)"; ctx.fill();
      ctx.lineWidth = 2; ctx.strokeStyle = m.color || SEV_COLOR.error; ctx.stroke();
      if (m.label) {
        ctx.font = "700 10px -apple-system, 'Segoe UI', sans-serif";
        ctx.textAlign = "left"; ctx.textBaseline = "middle";
        const w = ctx.measureText(m.label).width + 8;
        ctx.fillStyle = "rgba(10,14,20,0.82)";
        ctx.fillRect(s.x + r + 3, s.y - 8, w, 16);
        ctx.fillStyle = m.color || "#fff";
        ctx.fillText(m.label, s.x + r + 7, s.y + 0.5);
      }
    }
    ctx.restore();
  }
  if (sky.viewer) sky.viewer.extraOverlay = draw3d;

  let planG = null;
  function renderPlanMarkers() {
    const pe = sky.planEditor;
    if (!pe || !pe.gWorld) return;
    if (!planG) planG = svgEl("g", { class: "ck-plan-markers" });
    if (planG.parentNode !== pe.gWorld) pe.gWorld.appendChild(planG);
    planG.textContent = "";
    if (!st.markers.length) return;
    const sto = (S.model && S.model.stories || []).find(s => s.name === S.story);
    const zs = sto ? sto.elevation : null;
    const sc = pe.scale || 40;
    for (const m of st.markers) {
      if (zs != null && Math.abs(m.p[2] - zs) > 0.05) continue;
      const r = (m.r || 7) * 1.3 / sc;
      for (const d of m.dofs || []) {
        if (d.dof !== "UX" && d.dof !== "UY") continue;
        const ax = AXIS[d.dof[1]], len = 30 / sc;
        planG.appendChild(svgEl("line", {
          x1: m.p[0], y1: m.p[1], x2: m.p[0] + ax[0] * len, y2: m.p[1] + ax[1] * len,
          stroke: DOF_COLOR[d.dof], "stroke-width": 2.5, "vector-effect": "non-scaling-stroke", "stroke-linecap": "round",
        }));
      }
      planG.appendChild(svgEl("circle", {
        cx: m.p[0], cy: m.p[1], r, fill: m.fill || "rgba(230,103,103,0.22)",
        stroke: m.color || SEV_COLOR.error, "stroke-width": 2, "vector-effect": "non-scaling-stroke",
        class: "ck-plan-marker",
      }));
    }
  }
  if (sky.planEditor && sky.planEditor.svg) {
    sky.planEditor.svg.addEventListener("wheel", () => requestAnimationFrame(renderPlanMarkers), { passive: true });
  }
  const badge = $("planStoryBadge");
  if (badge) new MutationObserver(() => renderPlanMarkers()).observe(badge, { childList: true, characterData: true, subtree: true });

  function setMarkers(list) {
    st.markers = list || [];
    if (sky.viewer) sky.viewer._dirty = true;
    renderPlanMarkers();
  }
  function setHl(uids, color) {
    const v = sky.viewer;
    if (!v) return;
    if (uids && uids.length) { v.setHighlight(uids, color); st.hlOwned = true; }
    else if (st.hlOwned) { v.setHighlight(null); st.hlOwned = false; }
  }
  function clearMarks() { setMarkers([]); setHl(null); st.activeKey = null; st.activeMech = null; }

  /* ================= navigation ================= */
  function zoom3d(pts) {
    const v = sky.viewer;
    if (!v || !pts.length) return;
    const b = bboxOf(pts);
    const R = v.radius || 10;
    const r = Math.max(b.ext * 1.35, R * 0.22, 1.5);
    v.target = b.c;
    v.dist = r / Math.tan(v.fov / 2) * 1.15;
    v.vyaw = 0; v.vpitch = 0;
    v._dirty = true;
  }
  function zoomPlan(pts) {
    const pe = sky.planEditor;
    if (!pe || !pts.length) return;
    const b = bboxOf(pts);
    const span = Math.max(b.hi[0] - b.lo[0], b.hi[1] - b.lo[1], 4) * 1.6;
    pe.cx = b.c[0]; pe.cy = b.c[1];
    pe.scale = Math.max(6, Math.min(220, Math.min((pe.w - 60) / span, (pe.h - 60) / span)));
    pe._fitted = true;
    pe.refresh();
    renderPlanMarkers();
  }
  function navTarget() {
    if (st.opts.nav === "3d") return "3d";
    if (st.opts.nav === "plan") return "plan";
    return S.mode === "model" ? "plan" : "3d";
  }
  function show3d(pts) {
    if (S.mode !== "analyze") sky.setMode("analyze");
    if (S.tab !== "view3d") sky.switchTab("view3d");
    if (pts && pts.length) zoom3d(pts);
    sky.etabs && sky.etabs.refresh && sky.etabs.refresh();
  }
  function showPlan(pts, storyName) {
    if (S.mode !== "model") sky.setMode("model");
    if (S.view !== "plan") sky.setView("plan");
    if (storyName) sky.setStory(storyName);
    if (pts && pts.length) {
      zoomPlan(pts);
      // the plan may only now get its real size (first show) → re-apply
      setTimeout(() => zoomPlan(pts), 80);
    }
    sky.etabs && sky.etabs.refresh && sky.etabs.refresh();
  }

  /** Select + zoom to an issue's objects / location (3D or plan). */
  function navigate(issue, key) {
    if (!issue || !S.model) return null;
    st.activeKey = key || null;
    st.activeMech = null;
    const objs = (issue.objects || []).map(lookup).filter(Boolean);
    const geo = objs.filter(o => o.pts);
    const refs = geo.map(o => ({ type: o.type, uid: o.uid }));
    const pts = geo.flatMap(o => o.pts);
    const loc = issue.location || (pts.length ? bboxOf(pts).c : null);
    const color = SEV_COLOR[issue.severity] || SEV_COLOR.error;
    const memberUids = geo.filter(o => o.type === "member").map(o => o.uid);
    if (loc) pts.push(loc);
    // non-geometric targets: loads / story issues
    if (!pts.length) {
      const story = objs.find(o => o.type === "story");
      const gotoLoads = sky.etabs && sky.etabs.gotoLoads;
      if (story) { showPlan(null, story.name); if (sky.planEditor) sky.planEditor.fit(); setMarkers([]); renderPanelActive(); return "plan"; }
      const loadKind = objs.find(o => o.type === "pattern" || o.type === "case" || o.type === "combo");
      if (loadKind || /^(PATTERN_|CASE_|COMBO_|LOAD_)/.test(issue.code)) {
        const t = loadKind ? loadKind.type : (issue.code.startsWith("COMBO_") ? "combo" : issue.code.startsWith("CASE_") ? "case" : "pattern");
        if (gotoLoads) gotoLoads(t === "combo" ? "ls-combos" : t === "case" ? "ls-cases" : "ls-patterns");
        setMarkers([]); renderPanelActive();
        return "loads";
      }
      // whole-model issue (NO_SUPPORTS …): fit everything
      setMarkers([]); setHl(null);
      show3d(null);
      if (sky.viewer) sky.viewer.fit();
      renderPanelActive();
      return "3d";
    }
    if (refs.length) sky.handleSelect(refs, false);
    setHl(memberUids, color);
    setMarkers(loc ? [{ p: loc, color, fill: color + "33", label: issue.code }] : []);
    const target = navTarget();
    if (target === "plan") showPlan(pts, storyAtZ(loc ? loc[2] : pts[0][2]));
    else show3d(pts);
    renderPanelActive();
    return target;
  }

  /** Show one mechanism (index) or all unstable DOFs (null) as 3D markers. */
  function showMechanism(k) {
    const sres = st.stab;
    if (!sres) return;
    const list = k == null ? (sres.mechanisms || []) : [(sres.mechanisms || [])[k]].filter(Boolean);
    const byPt = new Map();
    let pmax = 0;
    list.forEach((mech) => {
      for (const d of mech.dofs || []) pmax = Math.max(pmax, d.participation || 0);
    });
    list.forEach((mech, li) => {
      const mi = k == null ? li : k;
      const color = MECH_COLOR[mi % MECH_COLOR.length];
      for (const d of mech.dofs || []) {
        if (!d.point) continue;
        const pk = d.point.map(v => (+v).toFixed(4)).join(",");
        if (!byPt.has(pk)) byPt.set(pk, { p: d.point, color, fill: color + "33", dofs: [], label: null, objs: new Set() });
        const mk = byPt.get(pk);
        const w = pmax ? (d.participation || 0) / pmax : 1;
        const ex = mk.dofs.find(x => x.dof === d.dof);
        if (ex) ex.w = Math.max(ex.w, w); else mk.dofs.push({ dof: d.dof, w });
        (d.objects || []).forEach(o => mk.objs.add(o));
      }
    });
    const marks = [...byPt.values()];
    for (const mk of marks) {
      const wmax = Math.max(...mk.dofs.map(d => d.w), 0);
      mk.r = 5 + 5 * wmax;
      mk.label = mk.dofs.slice().sort((a, b) => b.w - a.w).map(d => d.dof).join(" ");
    }
    st.activeMech = k == null ? "all" : k;
    st.activeKey = null;
    const uids = [...new Set(marks.flatMap(m => [...m.objs]))].filter(u => (S.model.members || []).some(x => x.uid === u));
    setHl(uids, k == null ? SEV_COLOR.error : MECH_COLOR[k % MECH_COLOR.length]);
    setMarkers(marks);
    show3d(marks.map(m => m.p));
    renderPanelActive();
    return marks;
  }

  /** Zoom to one DOF entry (stability tables / max diagonal ratio). */
  function showDof(d, color) {
    if (!d || !d.point) return;
    const c = color || SEV_COLOR.warning;
    setMarkers([{ p: d.point, color: c, fill: c + "33", dofs: d.dof ? [{ dof: d.dof, w: 1 }] : [], label: d.dof || null, r: 9 }]);
    const uids = (d.objects || []).filter(u => (S.model.members || []).some(x => x.uid === u));
    setHl(uids, c);
    const pts = [d.point];
    for (const u of d.objects || []) { const o = lookup(u); if (o && o.pts) pts.push(...o.pts); }
    if (navTarget() === "plan") showPlan(pts, storyAtZ(d.point[2])); else show3d(pts);
  }

  /* ================= safe quick fixes ================= */
  const uniqueUid = (arr, uid) => arr.filter(x => x.uid === uid).length === 1;
  function fixFor(issue) {
    const m = S.model;
    if (!m || !issue) return null;
    const tol = (st.check && st.check.summary && st.check.summary.tolerance_m) || st.opts.tol;
    if (issue.code === "FRAME_DUPLICATE" || issue.code === "SHELL_DUPLICATE") {
      const coll = issue.code === "FRAME_DUPLICATE" ? m.members : m.shells;
      const type = issue.code === "FRAME_DUPLICATE" ? "member" : "shell";
      const objs = issue.objects || [];
      if (objs.length < 2 || !objs.every(u => uniqueUid(coll, u))) return null;
      const del = objs.slice(1);
      return { label: "Delete duplicate", title: `Delete ${del.join(", ")} (keeps ${objs[0]})`,
        apply: () => { del.forEach(uid => ME.eraseElement(m, { type, uid })); return `Deleted ${del.join(", ")}`; } };
    }
    if (issue.code === "FRAME_ZERO_LENGTH") {
      const uid = (issue.objects || [])[0];
      if (!uid || !uniqueUid(m.members, uid)) return null;
      return { label: "Delete frame", title: `Delete zero-length frame ${uid}`,
        apply: () => { ME.eraseElement(m, { type: "member", uid }); return `Deleted ${uid}`; } };
    }
    if (issue.code === "JOINT_COINCIDENT") {
      const loc = issue.location;
      if (!loc) return null;
      const rad = Math.max(2.5 * tol, 1e-6);
      const near = p => p && Math.hypot(p[0] - loc[0], p[1] - loc[1], p[2] - loc[2]) <= rad;
      const objs = (issue.objects || []).map(lookup).filter(o => o && o.pts);
      if (!objs.length) return null;
      // refuse when snapping would collapse an object (two of its points merge)
      for (const o of objs) if (o.pts.filter(near).length > 1) return null;
      return { label: "Merge joints", title: `Snap every joint within ${U.fmtU("small", rad, 1)} of ${fmtPt(loc)} to that point`,
        apply: () => {
          let n = 0;
          const snap = p => { if (p && near(p) && (p[0] !== loc[0] || p[1] !== loc[1] || p[2] !== loc[2])) { p[0] = loc[0]; p[1] = loc[1]; p[2] = loc[2]; n++; } };
          for (const o of objs) {
            if (o.type === "member") {
              const mm = m.members.find(x => x.uid === o.uid);
              snap(mm.pi); snap(mm.pj);
              mm.length = Math.hypot(mm.pj[0] - mm.pi[0], mm.pj[1] - mm.pi[1], mm.pj[2] - mm.pi[2]);
            } else if (o.type === "shell") (m.shells.find(x => x.uid === o.uid).corners || []).forEach(snap);
            else if (o.type === "link") { const l = m.links.find(x => x.uid === o.uid); snap(l.pi); snap(l.pj); }
          }
          for (const k of ["supports", "spring_supports", "nodal_masses"]) for (const a of m[k] || []) snap(a && a.point);
          for (const p of Object.values(m.patterns || {})) for (const a of p.nodal_loads || []) snap(a && a.point);
          return `Merged ${n} joint coordinate(s) into ${fmtPt(loc)}`;
        } };
    }
    return null;
  }
  async function applyFix(issue) {
    const f = fixFor(issue);
    if (!f) return false;
    const msg = f.apply();
    const live = new Set();
    for (const k of ["members", "shells", "links"]) for (const o of S.model[k] || []) live.add(o.uid);
    S.selection = (S.selection || []).filter(r => !["member", "shell", "link"].includes(r.type) || live.has(r.uid));
    clearMarks();
    sky.markDirty();
    const v = sky.viewer;
    if (v) { v.model = S.model; v._buildScene(); v._dirty = true; }
    if (sky.planEditor) sky.planEditor.refresh();
    if (sky.elevEditor) sky.elevEditor.refresh();
    if (sky.renderProps) sky.renderProps();
    toast("Check Model · Fix", msg);
    try { await runCheck(); } catch (e) { toast("Check Model failed", e.message, "error", 8000); }
    return true;
  }

  /* ================= results panel ================= */
  let panel = null, body = null, tabsEl = null, dockBtn = null;
  function buildPanel() {
    panel = el("aside", { class: "ck-panel is-docked hidden", id: "ckPanel", role: "complementary", "aria-label": "Check Model results" });
    const head = el("div", { class: "ck-head" });
    head.insertAdjacentHTML("afterbegin", ico("check", "ck-head-ico"));
    tabsEl = el("div", { class: "ck-tabs", role: "tablist" });
    for (const [t, lbl] of [["check", "Check Model"], ["stability", "Stability"]]) {
      const b = el("button", { class: "ck-tab", "data-tab": t, role: "tab", text: lbl });
      b.addEventListener("click", () => { st.tab = t; renderPanel(); });
      tabsEl.appendChild(b);
    }
    head.appendChild(tabsEl);
    head.appendChild(el("span", { class: "ck-spacer" }));
    const re = el("button", { class: "ck-hbtn", id: "ckRecheck", title: "Run again with the same options", html: ico("redo") });
    re.addEventListener("click", () => st.tab === "stability" ? doStability() : doCheck());
    dockBtn = el("button", { class: "ck-hbtn", id: "ckDock", title: "Float panel", html: ico("float") });
    dockBtn.addEventListener("click", () => setDocked(!st.docked));
    const x = el("button", { class: "ck-hbtn ck-close", id: "ckClose", title: "Close (clears highlights)", "aria-label": "Close", text: "×" });
    x.addEventListener("click", () => closePanel());
    head.append(re, dockBtn, x);
    body = el("div", { class: "ck-body" });
    panel.append(head, body);
    (document.getElementById("app") || document.body).appendChild(panel);
    // drag (floating only)
    let drag = null;
    head.addEventListener("mousedown", e => {
      if (st.docked || e.target.closest("button")) return;
      const r = panel.getBoundingClientRect();
      drag = { dx: e.clientX - r.left, dy: e.clientY - r.top };
      e.preventDefault();
    });
    window.addEventListener("mousemove", e => {
      if (!drag) return;
      panel.style.left = Math.max(0, Math.min(window.innerWidth - 120, e.clientX - drag.dx)) + "px";
      panel.style.top = Math.max(0, Math.min(window.innerHeight - 40, e.clientY - drag.dy)) + "px";
    });
    window.addEventListener("mouseup", () => { drag = null; });
    window.addEventListener("resize", () => placePanel());
  }
  const syncDockClass = () => document.body.classList.toggle("ck-docked",
    !!panel && st.docked && !panel.classList.contains("hidden"));
  function placePanel() {
    syncDockClass();
    if (!panel || !st.docked) return;
    const ws = document.querySelector(".workspace");
    const r = ws ? ws.getBoundingClientRect() : { top: 90, bottom: window.innerHeight - 28 };
    panel.style.top = Math.round(r.top) + "px";
    panel.style.height = Math.round(r.bottom - r.top) + "px";
    panel.style.left = "";
  }
  function setDocked(on) {
    st.docked = on;
    panel.classList.toggle("is-docked", on);
    panel.classList.toggle("is-floating", !on);
    dockBtn.innerHTML = ico(on ? "float" : "dock");
    dockBtn.title = on ? "Float panel" : "Dock panel right";
    syncDockClass();
    if (on) placePanel();
    else {
      const r = panel.getBoundingClientRect();
      panel.style.height = Math.min(520, window.innerHeight - 140) + "px";
      panel.style.left = Math.max(8, r.left - 40) + "px";
      panel.style.top = (r.top + 40) + "px";
    }
  }
  function openPanel(tab) {
    if (!panel) buildPanel();
    if (tab) st.tab = tab;
    panel.classList.remove("hidden");
    placePanel();
    renderPanel();
  }
  function closePanel() {
    if (panel) panel.classList.add("hidden");
    syncDockClass();
    clearMarks();
  }

  function sevIcon(sev) { return ico(sev === "error" ? "error" : sev === "warning" ? "warning" : "info", `ck-sev ck-sev-${sev}`); }
  function objText(objs) {
    if (!objs || !objs.length) return "";
    const shown = objs.slice(0, 6).join(", ");
    return objs.length > 6 ? `${shown} +${objs.length - 6} more` : shown;
  }

  function issueRow(issue, key) {
    const row = el("div", { class: "ck-row ck-row-" + issue.severity, "data-key": key, tabindex: "0",
      title: "Click to select and zoom to the objects / location" });
    row.innerHTML = sevIcon(issue.severity);
    const main = el("div", { class: "ck-row-main" }, [el("div", { class: "ck-msg", text: issue.message || issue.code })]);
    const meta = [];
    if (issue.objects && issue.objects.length) meta.push(objText(issue.objects));
    if (issue.location) meta.push(fmtPt(issue.location));
    if (meta.length) main.appendChild(el("div", { class: "ck-meta", text: meta.join(" · ") }));
    row.appendChild(main);
    const f = fixFor(issue);
    if (f) {
      const fb = el("button", { class: "ck-fix", title: f.title, text: f.label });
      fb.addEventListener("click", e => { e.stopPropagation(); applyFix(issue); });
      row.appendChild(fb);
    }
    const go = () => navigate(issue, key);
    row.addEventListener("click", go);
    row.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
    return row;
  }

  function issueList(issues, prefix) {
    const wrap = el("div", { class: "ck-list" });
    for (const sev of SEV) {
      const ofSev = issues.map((iss, i) => [iss, i]).filter(([iss]) => iss.severity === sev);
      if (!ofSev.length) continue;
      const sec = el("div", { class: "ck-sev-sec ck-sev-sec-" + sev });
      const sh = el("div", { class: "ck-sev-head" });
      sh.innerHTML = sevIcon(sev);
      sh.append(el("span", { text: SEV_LABEL[sev] }), el("span", { class: "ck-count", text: String(ofSev.length) }));
      sec.appendChild(sh);
      const byCode = new Map();
      for (const [iss, i] of ofSev) { if (!byCode.has(iss.code)) byCode.set(iss.code, []); byCode.get(iss.code).push([iss, i]); }
      for (const [code, lst] of byCode) {
        const grp = el("div", { class: "ck-code open", "data-code": code });
        const gh = el("button", { class: "ck-code-head" }, [
          el("span", { class: "ck-caret", html: "&#9656;" }),
          el("span", { class: "ck-code-name", text: code }),
          el("span", { class: "ck-code-title", text: CODE_TITLE[code] || "" }),
          el("span", { class: "ck-count", text: String(lst.length) }),
        ]);
        gh.addEventListener("click", () => grp.classList.toggle("open"));
        const rows = el("div", { class: "ck-rows" });
        for (const [iss, i] of lst) rows.appendChild(issueRow(iss, `${prefix}:${i}`));
        grp.append(gh, rows);
        sec.appendChild(grp);
      }
      wrap.appendChild(sec);
    }
    return wrap;
  }

  function renderCheckTab() {
    const c = st.check;
    if (!c) {
      body.appendChild(el("div", { class: "ck-empty" }, [
        el("p", { text: "The model has not been checked yet." }),
        el("button", { class: "btn btn-run", text: "Check Model…", onclick: () => openCheckModel() }),
      ]));
      return;
    }
    const s = c.summary;
    const sum = el("div", { class: "ck-summary" });
    const pill = (cls, n, lbl) => el("span", { class: "ck-pill " + cls + (n ? "" : " is-zero"), "data-sev": cls, text: `${n} ${lbl}` });
    sum.append(pill("error", s.errors, s.errors === 1 ? "error" : "errors"),
      pill("warning", s.warnings, s.warnings === 1 ? "warning" : "warnings"),
      pill("info", s.info, "info"));
    body.appendChild(sum);
    const stats = [];
    if (s.n_joints != null) stats.push(`${s.n_joints} joints`);
    if (s.n_frames != null) stats.push(`${s.n_frames} frames`);
    if (s.n_shells != null) stats.push(`${s.n_shells} shells`);
    if (s.n_links != null) stats.push(`${s.n_links} links`);
    if (s.n_supported_joints != null) stats.push(`${s.n_supported_joints} supported`);
    stats.push(`tolerance ${U.fmtU("small", s.tolerance_m != null ? s.tolerance_m : st.opts.tol, 2)}`);
    if (s.elapsed_s != null) stats.push(`${(+s.elapsed_s).toFixed(3)} s`);
    body.appendChild(el("div", { class: "ck-stats muted", text: stats.join(" · ") }));
    const off = GROUPS.filter(g => st.opts.groups[g.key] === false);
    if (off.length) body.appendChild(el("div", { class: "ck-note muted", text: `Not reported: ${off.map(g => g.label).join(", ")}` }));
    if (c.stale) body.appendChild(el("div", { class: "ck-note ck-stale" }, [
      "The model changed since this check. ",
      el("button", { class: "ck-link", text: "Check again", onclick: () => doCheck() }),
    ]));
    if (!c.issues.length) {
      const ok = el("div", { class: "ck-ok" });
      ok.innerHTML = ico("ok", "ck-ok-ico");
      ok.appendChild(el("span", { text: "No problems found — the model passed every selected check." }));
      body.appendChild(ok);
    } else body.appendChild(issueList(c.issues, "c"));
    // navigation preference
    const navRow = el("label", { class: "ck-navpref muted" }, ["Show issues in "]);
    const sel = el("select", { id: "ckNavSel" });
    sel.innerHTML = `<option value="auto">current view</option><option value="3d">3D view</option><option value="plan">plan view</option>`;
    sel.value = st.opts.nav;
    sel.addEventListener("change", () => { st.opts.nav = sel.value; lsSet(LS_OPTS, JSON.stringify(st.opts)); });
    navRow.appendChild(sel);
    body.appendChild(navRow);
  }

  function metric(label, value, title) {
    return el("div", { class: "ck-metric", title: title || "" }, [el("span", { class: "ck-metric-l", text: label }), el("span", { class: "ck-metric-v", text: value })]);
  }
  function renderStabTab() {
    const r = st.stab;
    if (!r) {
      body.appendChild(el("div", { class: "ck-empty" }, [
        el("p", { text: "Assembles the elastic stiffness matrix (no case is solved) and looks for mechanisms and ill-conditioning." }),
        el("button", { class: "btn btn-run", text: "Check Stability…", onclick: () => openCheckStability() }),
      ]));
      return;
    }
    const tooLarge = (r.issues || []).find(i => i.code === "STABILITY_TOO_LARGE");
    const failed = (r.issues || []).find(i => i.code === "STABILITY_BUILD_FAILED");
    const state = r.skipped || tooLarge ? "skipped" : failed || r.built === false ? "failed" : r.stable ? "stable" : "unstable";
    const ban = el("div", { class: "ck-banner ck-banner-" + state, id: "ckStabBanner", "data-state": state });
    ban.innerHTML = ico(state === "stable" ? "ok" : state === "skipped" ? "info" : "error", "ck-ban-ico");
    const btxt = { stable: "Stable — no mechanism found", unstable: `Unstable — ${r.n_mechanisms} mechanism${r.n_mechanisms === 1 ? "" : "s"}`,
      skipped: "Stability check skipped (model too large)", failed: "Model could not be built" }[state];
    ban.append(el("div", {}, [el("b", { text: btxt }), el("div", { class: "ck-ban-msg", text: r.message || "" })]));
    body.appendChild(ban);
    if (r.stale) body.appendChild(el("div", { class: "ck-note ck-stale" }, [
      "The model changed since this check. ",
      el("button", { class: "ck-link", text: "Check again", onclick: () => doStability() }),
    ]));
    if (state === "skipped") {
      body.appendChild(el("p", { class: "ck-note", text:
        `About ${r.n_equations || "?"} equations exceed the limit of ${(r.thresholds && r.thresholds.max_dofs) || st.opts.maxDofs}. ` +
        "The dense eigen-analysis was skipped. Raise the limit (up to 6000) in Check Stability… or check a reduced model." }));
    }
    // metrics
    const grid = el("div", { class: "ck-metrics" });
    grid.append(
      metric("Equations", r.n_equations != null ? String(r.n_equations) : "—"),
      metric("Mechanisms", String(r.n_mechanisms || 0)),
      metric("Condition number (K)", r.condition_number == null ? (r.stable === false ? "singular" : "—") : sci(r.condition_number), "Raw stiffness matrix; null when singular"),
      metric("Scaled condition number", sci(r.scaled_condition_number), "Jacobi-scaled D^-1/2 K D^-1/2"),
      metric("Scaled (non-singular part)", sci(r.scaled_condition_number_nonsingular)),
      metric("Digits lost", r.digits_lost == null ? "—" : (+r.digits_lost).toFixed(2), "log10 of the condition number — double precision carries ~15.9 digits"),
      metric("Max diagonal ratio", sci(r.max_diagonal_ratio), "max K_ii / D_ii of the LDLᵀ factorization"),
      metric("Ill-conditioned equations", String(r.n_ill_conditioned_equations || 0), "diagonal ratio > 1e8"),
    );
    body.appendChild(grid);
    const at = r.max_diagonal_ratio_at;
    if (at && at.point) {
      const a = el("button", { class: "ck-link ck-maxdiag", id: "ckMaxDiag",
        text: `Max diagonal ratio at joint ${fmtPt(at.point)} · ${at.dof}${at.diaphragm ? ` (diaphragm ${at.diaphragm})` : ""}` });
      a.addEventListener("click", () => showDof(at, SEV_COLOR.warning));
      body.appendChild(a);
    }
    // notes
    const notes = [];
    const th = r.thresholds || {};
    if (r.n_ill_conditioned_equations > 0)
      notes.push(`${r.n_ill_conditioned_equations} equation(s) lose more than 8 digits (diagonal ratio > ${sci(th.diagonal_ratio_warning || 1e8)}): typically very stiff and very flexible elements meeting, or a near-mechanism.`);
    if (r.condition_number != null && r.condition_number > (th.condition_warning || 1e12))
      notes.push(`Condition number ${sci(r.condition_number)} exceeds ${sci(th.condition_warning || 1e12)}: displacements may carry large round-off error.`);
    if (r.digits_lost != null && r.digits_lost > 6 && !(r.n_ill_conditioned_equations > 0))
      notes.push(`About ${(+r.digits_lost).toFixed(1)} of ~15.9 significant digits are lost when solving K·u = F.`);
    if (state === "unstable")
      notes.push("K is singular: the structure (or a part of it) can move without straining. Add supports, restraints or members, or remove end releases, at the joints shown.");
    if (state === "stable" && !notes.length)
      notes.push("The stiffness matrix is well conditioned.");
    if (r.lowest_eigenvalues && r.lowest_eigenvalues.length)
      notes.push(`Lowest scaled eigenvalues: ${r.lowest_eigenvalues.map(v => (+v).toExponential(2)).join(", ")}`);
    if (notes.length) body.appendChild(el("ul", { class: "ck-notes" }, notes.map(n => el("li", { text: n }))));
    // issues (non-mechanism ones listed; mechanisms get their own cards)
    const other = (r.issues || []).filter(i => i.code !== "STABILITY_MECHANISM");
    if (other.length) body.appendChild(issueList(other, "s"));
    // mechanisms
    const mechs = r.mechanisms || [];
    if (mechs.length) {
      const hd = el("div", { class: "ck-mech-head" }, [
        el("span", { text: "Mechanisms" }),
        el("button", { class: "ck-link", id: "ckShowAllDofs", text: "Show all unstable DOFs", onclick: () => showMechanism(null) }),
      ]);
      body.appendChild(hd);
      body.appendChild(el("div", { class: "ck-legend" }, [
        ...["UX", "UY", "UZ"].map(d => el("span", { class: "ck-leg" }, [el("i", { style: `background:${DOF_COLOR[d]}` }), `${d} / R${d[1]}`])),
        el("span", { class: "ck-leg muted", text: "double arrowhead = rotation" }),
      ]));
      mechs.forEach((mech, k) => {
        const color = MECH_COLOR[k % MECH_COLOR.length];
        const card = el("div", { class: "ck-mech" + (st.activeMech === k ? " is-active" : ""), "data-m": String(k) });
        const ch = el("button", { class: "ck-mech-title", title: "Show this mechanism's joints and DOFs in 3D" }, [
          el("i", { class: "ck-swatch", style: `background:${color}` }),
          el("b", { text: `Mechanism ${mech.mode != null ? mech.mode : k + 1}` }),
          el("span", { class: "muted", text: ` · ${(mech.dofs || []).length} DOF(s) · λ = ${(+mech.scaled_eigenvalue || 0).toExponential(1)}` }),
        ]);
        ch.addEventListener("click", () => showMechanism(k));
        card.appendChild(ch);
        const tbl = el("table", { class: "ck-dofs" });
        tbl.innerHTML = `<thead><tr><th>Joint (${esc(U.label("length"))})</th><th>DOF</th><th>Participation</th><th>Objects</th></tr></thead>`;
        const tb = el("tbody");
        const pmax = Math.max(...(mech.dofs || []).map(d => d.participation || 0), 1e-12);
        for (const d of (mech.dofs || []).slice(0, 40)) {
          const tr = el("tr", { class: "ck-dof-row", title: "Zoom to this joint" });
          tr.innerHTML = `<td>${esc(d.point ? d.point.map(fmtLen).join(", ") : "—")}${d.diaphragm ? ` <span class="muted">(${esc(d.diaphragm)})</span>` : ""}</td>` +
            `<td><b style="color:${DOF_COLOR[d.dof] || "inherit"}">${esc(d.dof)}</b></td>` +
            `<td><span class="ck-bar"><i style="width:${Math.round(100 * (d.participation || 0) / pmax)}%;background:${color}"></i></span>${(+d.participation || 0).toFixed(3)}</td>` +
            `<td class="muted">${esc(objText(d.objects))}</td>`;
          tr.addEventListener("click", () => showDof(d, color));
          tb.appendChild(tr);
        }
        tbl.appendChild(tb);
        card.appendChild(tbl);
        if ((mech.dofs || []).length > 40) card.appendChild(el("div", { class: "muted ck-more", text: `+${mech.dofs.length - 40} more DOF(s)` }));
        body.appendChild(card);
      });
    }
    if (r.elapsed_s != null) body.appendChild(el("div", { class: "ck-stats muted", text: `${(+r.elapsed_s).toFixed(3)} s` }));
  }

  function renderPanel() {
    if (!panel) return;
    const sc = body.scrollTop;
    body.textContent = "";
    tabsEl.querySelectorAll(".ck-tab").forEach(b => {
      b.classList.toggle("is-active", b.dataset.tab === st.tab);
      if (b.dataset.tab === "check" && st.check) {
        const s = st.check.summary;
        b.textContent = `Check Model${s.errors + s.warnings + s.info ? ` (${s.errors + s.warnings + s.info})` : " ✓"}`;
      }
      if (b.dataset.tab === "stability" && st.stab)
        b.textContent = st.stab.stable === false ? `Stability (${st.stab.n_mechanisms})` : st.stab.stable ? "Stability ✓" : "Stability";
    });
    if (st.tab === "stability") renderStabTab(); else renderCheckTab();
    renderPanelActive();
    body.scrollTop = sc;
  }
  function renderPanelActive() {
    if (!panel) return;
    panel.querySelectorAll(".ck-row").forEach(r => r.classList.toggle("is-active", r.dataset.key === st.activeKey));
    panel.querySelectorAll(".ck-mech").forEach(c => c.classList.toggle("is-active", String(st.activeMech) === c.dataset.m));
  }

  /* ================= actions ================= */
  async function doCheck() {
    if (st.busy) return null;
    st.busy = true;
    chip.classList.add("is-busy");
    try {
      const c = await runCheck();
      openPanel("check");
      if (st.opts.withStability) await doStability({ keepTab: true });
      return c;
    } catch (e) {
      toast("Check Model failed", e.message, "error", 8000);
      return null;
    } finally {
      st.busy = false;
      chip.classList.remove("is-busy");
    }
  }
  async function doStability({ keepTab = false } = {}) {
    try {
      const r = await runStability();
      openPanel(keepTab ? st.tab : "stability");
      if (!keepTab && r.mechanisms && r.mechanisms.length) showMechanism(0);
      return r;
    } catch (e) {
      toast("Check Stability failed", e.message, "error", 8000);
      return null;
    }
  }

  /* ---------------- Check Model options dialog */
  function openCheckModel() {
    if (!S.model) { toast("Check Model", "Load a model first."); return null; }
    const o = JSON.parse(JSON.stringify(st.opts));
    const bodyEl = el("div", { class: "dlg-check" });
    bodyEl.appendChild(el("p", { class: "dlg-intro muted", text:
      "Checks the current working model (including unsaved edits) for geometry, connectivity, property and load problems. The model is not changed." }));
    const g1 = el("fieldset", { class: "dlg-group" }, [el("legend", { text: "Tolerance" })]);
    const tolIn = el("input", { type: "number", id: "ckTolInput", min: "0", step: U.step("small", 0.001), value: U.inputValue("small", o.tol) });
    g1.appendChild(el("label", { class: "dlg-sub-row ck-tol-row" }, [
      el("span", { text: "Joint / length tolerance" }), tolIn, el("span", { class: "muted", text: U.label("small") }),
    ]));
    g1.appendChild(el("div", { class: "muted ck-dlg-hint", text: "Joints closer than this are reported as coincident; frames shorter are zero-length." }));
    bodyEl.appendChild(g1);
    const g2 = el("fieldset", { class: "dlg-group" }, [el("legend", { text: "Checks to report" })]);
    const boxes = [];
    for (const g of GROUPS) {
      const cb = el("input", { type: "checkbox", "data-group": g.key });
      cb.checked = o.groups[g.key] !== false;
      cb.addEventListener("change", () => { o.groups[g.key] = cb.checked; });
      boxes.push(cb);
      g2.appendChild(el("label", { class: "dlg-chk ck-grp", title: g.hint }, [cb, el("span", {}, [g.label, el("span", { class: "muted ck-grp-hint", text: ` — ${g.hint}` })])]));
    }
    const allNone = el("div", { class: "ck-allnone" }, [
      btn("Select All", "btn-small", () => boxes.forEach(b => { b.checked = true; o.groups[b.dataset.group] = true; })),
      btn("Clear All", "btn-small", () => boxes.forEach(b => { b.checked = false; o.groups[b.dataset.group] = false; })),
    ]);
    g2.appendChild(allNone);
    bodyEl.appendChild(g2);
    const g3 = el("fieldset", { class: "dlg-group" }, [el("legend", { text: "Options" })]);
    const cbStab = el("input", { type: "checkbox", id: "ckWithStab" }); cbStab.checked = !!o.withStability;
    cbStab.addEventListener("change", () => { o.withStability = cbStab.checked; });
    const cbPre = el("input", { type: "checkbox", id: "ckBeforeRunChk" }); cbPre.checked = beforeRunEnabled();
    g3.append(
      el("label", { class: "dlg-chk", title: "Also assemble K and look for mechanisms (slower on large models)" }, [cbStab, el("span", { text: "Also run Check Stability" })]),
      el("label", { class: "dlg-chk", title: "Run the fast check before every analysis run; errors prompt Cancel / Run Anyway" }, [cbPre, el("span", { text: "Check model before every run" })]),
    );
    bodyEl.appendChild(g3);
    const err = errorLine();
    bodyEl.appendChild(err);
    const fb = footBar("Analyze › Check Model", [
      btn("Cancel", "", () => dlg.close()),
      btn("Check", "btn-run", async () => {
        const tol = U.parse("small", tolIn.value);
        if (!(tol > 0 && tol <= 1)) { showError(err, `Tolerance must be > 0 and ≤ ${U.fmtU("small", 1, 0)}.`); return; }
        o.tol = tol;
        st.opts = o;
        lsSet(LS_OPTS, JSON.stringify(o));
        setBeforeRun(cbPre.checked);
        dlg.close();
        await doCheck();
      }),
    ]);
    fb.wrap.querySelectorAll(".btn")[1].id = "ckRunBtn";
    const dlg = dialog("ckOptionsModal", { title: "Check Model", body: bodyEl, foot: fb.wrap });
    const h = dlg.el.querySelector(".dlg-title");
    if (h) h.insertAdjacentHTML("afterbegin", ico("check", "dlg-ico"));
    tolIn.focus();
    return dlg;
  }

  /* ---------------- Check Stability dialog */
  function openCheckStability() {
    if (!S.model) { toast("Check Stability", "Load a model first."); return null; }
    const bodyEl = el("div", { class: "dlg-check" });
    bodyEl.appendChild(el("p", { class: "dlg-intro muted", text:
      "Builds the current working model, assembles its elastic stiffness matrix K (no load case is solved) and reports mechanisms (zero-stiffness modes: the joints / DOFs that move), the condition number and the LDLᵀ diagonal ratios." }));
    const g = el("fieldset", { class: "dlg-group" }, [el("legend", { text: "Limits" })]);
    const mx = el("input", { type: "number", id: "ckMaxDofs", min: "1", max: "6000", step: "100", value: String(st.opts.maxDofs) });
    g.appendChild(el("label", { class: "dlg-sub-row ck-tol-row" }, [el("span", { text: "Max equations (dense analysis)" }), mx]));
    g.appendChild(el("div", { class: "muted ck-dlg-hint", text: "Larger models are skipped (STABILITY_TOO_LARGE). Upper bound 6000." }));
    bodyEl.appendChild(g);
    const err = errorLine();
    bodyEl.appendChild(err);
    const fb = footBar("Analyze › Check Stability", [
      btn("Cancel", "", () => dlg.close()),
      btn("Check", "btn-run", async () => {
        const v = Number(mx.value);
        if (!Number.isInteger(v) || v < 1 || v > 6000) { showError(err, "Max equations must be an integer between 1 and 6000."); return; }
        st.opts.maxDofs = v;
        lsSet(LS_OPTS, JSON.stringify(st.opts));
        dlg.close();
        await doStability();
      }),
    ]);
    fb.wrap.querySelectorAll(".btn")[1].id = "ckStabRunBtn";
    const dlg = dialog("ckStabModal", { title: "Check Stability", narrow: true, body: bodyEl, foot: fb.wrap });
    const h = dlg.el.querySelector(".dlg-title");
    if (h) h.insertAdjacentHTML("afterbegin", ico("stab", "dlg-ico"));
    return dlg;
  }

  /* ================= check before run ================= */
  function beforeRunEnabled() { return lsGet(LS_PRERUN) !== "0"; }
  function setBeforeRun(on) { lsSet(LS_PRERUN, on ? "1" : "0"); }
  function toggleBeforeRun() {
    setBeforeRun(!beforeRunEnabled());
    toast("Check Model Before Run", beforeRunEnabled() ? "ON — the model is checked before every run" : "OFF");
  }

  function prerunPrompt(c) {
    return new Promise(resolve => {
      let settled = false;
      const done = v => { if (settled) return; settled = true; resolve(v); };
      const s = c.summary;
      const bodyEl = el("div", { class: "dlg-check ck-prerun" });
      bodyEl.appendChild(el("p", { class: "ck-prerun-lead", text:
        `Check Model found ${s.errors} error${s.errors === 1 ? "" : "s"}` + (s.warnings ? ` and ${s.warnings} warning${s.warnings === 1 ? "" : "s"}` : "") +
        ". The analysis may fail or give meaningless results." }));
      const errs = c.issues.filter(i => i.severity === "error");
      const byCode = {};
      for (const i of errs) byCode[i.code] = (byCode[i.code] || 0) + 1;
      const ul = el("ul", { class: "ck-prerun-list", id: "ckPrerunList" });
      for (const [code, n] of Object.entries(byCode)) {
        const first = errs.find(i => i.code === code);
        const li = el("li", { "data-code": code });
        li.innerHTML = sevIcon("error");
        li.append(el("b", { text: code }), el("span", { class: "ck-count", text: String(n) }),
          el("div", { class: "muted", text: first.message }));
        ul.appendChild(li);
      }
      bodyEl.appendChild(ul);
      const fb = footBar("Options › Check Model Before Run", [
        btn("Cancel", "", () => { done(false); dlg.close(); }),
        btn("Show Issues", "", () => { done(false); dlg.close(); openPanel("check"); }),
        btn("Run Anyway", "btn-run", () => { done(true); dlg.close(); }),
      ]);
      const bs = fb.wrap.querySelectorAll(".btn");
      bs[0].id = "ckPrerunCancel"; bs[1].id = "ckPrerunShow"; bs[2].id = "ckPrerunRun";
      const dlg = dialog("ckPrerunModal", { title: "Check Model — errors found", body: bodyEl, foot: fb.wrap, onClose: () => done(false) });
      const h = dlg.el.querySelector(".dlg-title");
      if (h) h.insertAdjacentHTML("afterbegin", ico("warning", "dlg-ico ck-warn-ico"));
      bs[0].focus();
    });
  }

  /** doRun hook — resolves true = run, false = cancel. */
  async function beforeRun() {
    if (!beforeRunEnabled() || !S.model) return true;
    const txt = modelText();
    if (keyOf(txt) === st.lastCleanKey) return true;          // unchanged since the last clean check
    let c;
    st.prerunCount++;
    try { c = await runCheck(txt); }
    catch (e) { console.warn("Pre-run Check Model unavailable:", e.message); return true; }
    if (c.summary.ok) return true;
    return prerunPrompt(c);
  }

  /* ================= model change → stale ================= */
  document.addEventListener("sky:model-changed", () => {
    if (st.check && !st.check.stale) {
      st.check.stale = true;
      syncChip();
      if (panel && !panel.classList.contains("hidden") && st.tab === "check") renderPanel();
    }
    if (st.stab && !st.stab.stale) {
      st.stab.stale = true;
      if (panel && !panel.classList.contains("hidden") && st.tab === "stability") renderPanel();
    }
  });
  document.addEventListener("sky:units-changed", () => {
    if (panel && !panel.classList.contains("hidden")) renderPanel();
  });

  /* ================= public hooks (window.__sky) ================= */
  sky.openCheckModel = openCheckModel;
  sky.openCheckStability = openCheckStability;
  sky.beforeRun = beforeRun;
  sky.checkModel = {
    run: async (opts = {}) => {
      if (opts.tol != null) st.opts.tol = +opts.tol;
      if (opts.groups) Object.assign(st.opts.groups, opts.groups);
      const c = await runCheck();
      if (opts.open !== false) openPanel("check");
      return c;
    },
    runStability: async (opts = {}) => {
      if (opts.maxDofs != null) st.opts.maxDofs = opts.maxDofs;
      const r = await runStability();
      if (opts.open !== false) { openPanel("stability"); if (r.mechanisms && r.mechanisms.length && opts.show !== false) showMechanism(0); }
      return r;
    },
    openPanel, closePanel, renderPanel, navigate, showMechanism, showDof, fixFor, applyFix,
    beforeRun, beforeRunEnabled, setBeforeRun, toggleBeforeRun,
    options: () => st.opts,
    last: () => st.check, lastStability: () => st.stab,
    markers: () => st.markers, chip, panel: () => panel,
    prerunCount: () => st.prerunCount,
    setDocked: on => { if (!panel) buildPanel(); setDocked(on); },
    groupOf, CODE_TITLE,
  };
}

export default initCheckModel;
