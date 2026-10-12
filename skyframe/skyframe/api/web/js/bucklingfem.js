/* SkyFrame — BucklingFEM (shell-FEM member buckling) and the v1.24 buckling
   case options (CONTRACT "Buckling with shells and links, sparse eigen and
   BucklingFEM").

   * Analyze › Member Buckling…  (act an-member-buckling) and the frame
     right-click menu (js/workflow.js hook) open the BucklingFEM dialog:
     member, end forces (typed, or "from case" → POST
     /api/member_buckling/forces), boundary, load position, mesh, modes →
     POST /api/member_buckling → mode table (λ, class, half-waves) +
     closed-form references + an animated 3D mode-shape viewer of the
     generated shell model.
   * window.__sky.bucklingFem.caseFields(F, c, D) — the Formulation /
     Include shells / Include links rows of the standard Buckling case
     dialog (js/dlgstd_cases.js hook).  Only touched fields are written;
     the defaults are never stored (byte-identical round trip).

   Every number goes through js/units.js (store SI).  ?mock=1 →
   js/mock_bucklingfem.js. */

import bfU from "./units.js";
import {
  el as bfEl, esc as bfEsc, isNum as bfIsNum, dkForm as bfForm, dkGroup as bfGroup,
  dkAdvanced as bfAdvanced, dkNote as bfNote, dkDialog as bfDialog,
} from "./dlgkit.js";
import { mockMemberBuckling as bfMockRun, mockMemberForces as bfMockForces } from "./mock_bucklingfem.js";

const FORCE_ROWS = [
  ["P", "Axial force P (tension +)", "force"],
  ["M3i", "M3 at end i", "moment"], ["M3j", "M3 at end j", "moment"],
  ["M2i", "M2 at end i", "moment"], ["M2j", "M2 at end j", "moment"],
  ["w2", "Uniform load w2 (along −2)", "line_force"],
];
const CLASS_LABEL = {
  local: "Local (plate)", distortional: "Distortional", "lateral-torsional": "Lateral-torsional",
  torsional: "Torsional", "flexural-minor": "Flexural (minor axis)", "flexural-major": "Flexural (major axis)",
};

export function initBucklingFem(sky) {
  if (!sky || !sky.store) return;
  const S = sky.store;
  if (!document.querySelector("link[data-bfem-css]")) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/static/bucklingfem.css"; l.setAttribute("data-bfem-css", "1");
    document.head.appendChild(l);
  }
  const toast = (t, m, k) => sky.toast && sky.toast(t, m, k || "info", 4500);
  const modelBody = () => JSON.parse(JSON.stringify(S.model, (k, v) => k === "_mock_params" ? undefined : v));
  async function post(path, body) {
    const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
    return data;
  }
  const api = {
    run: body => S.mock ? Promise.resolve().then(() => bfMockRun(modelBody(), body)) : post("/api/member_buckling", { ...body, model: modelBody() }),
    forces: body => S.mock ? Promise.resolve().then(() => bfMockForces(modelBody(), body)) : post("/api/member_buckling/forces", { ...body, model: modelBody() }),
  };
  const st = { last: null, prev: {} };

  /* ------------------------------------------------ buckling case rows */
  function caseFields(F, c) {
    const box = bfEl("div");
    const sync = () => box.classList.toggle("dk-disabled", (c.formulation || "frame") !== "fem");
    box.append(
      F.check(c, "include_shells", { label: "Include shells (plate geometric stiffness)", id: "dcBuckShells",
        read: () => c.include_shells !== false, write: v => { if (v) delete c.include_shells; else c.include_shells = false; } }),
      F.check(c, "include_links", { label: "Include links", id: "dcBuckLinks",
        read: () => c.include_links !== false, write: v => { if (v) delete c.include_links; else c.include_links = false; } }));
    const sel = F.select(c, "formulation", [["frame", "Frames only (legacy)"], ["fem", "Finite-element model (frames, shells, links, releases)"]], {
      label: "Formulation", id: "dcBuckForm", read: () => c.formulation || "frame",
      write: v => { if (v === "frame") delete c.formulation; else c.formulation = v; }, onChange: sync,
      hint: "The finite-element formulation uses the analysis model itself: shell membrane stresses, links, end releases and diaphragms enter the buckling solve.",
    });
    sync();
    return [sel, box];
  }

  /* ------------------------------------------------------------ dialog */
  function selectedMember() {
    const sel = (S.selection || []).find(r => r.type === "member");
    return sel ? sel.uid : ((S.model.members || [])[0] || {}).uid;
  }

  function open(uid) {
    const m = S.model;
    if (!m || !(m.members || []).length) { toast("Member Buckling", "Draw a frame member first.", "warn"); return null; }
    uid = uid || selectedMember();
    const p = st.prev;
    const s = {
      member: uid, case: "", boundary: p.boundary || "fork", load_position: p.load_position || "shear_center",
      num_modes: p.num_modes || 4, n_long: null, n_web: p.n_web || 8, n_flange: p.n_flange || 4,
      forces: Object.fromEntries(FORCE_ROWS.map(([k]) => [k, 0])),
    };
    if (p.member === uid && p.forces) Object.assign(s.forces, p.forces);
    const F = bfForm();
    const out = bfEl("div", { class: "bfem-out", id: "bfemOut" });
    const memberOpts = () => (m.members || []).map(x => [x.uid, `${x.uid} — ${x.section}`]);
    const caseOpts = () => [["", "(type the forces)"], ...Object.keys(m.cases || {}).map(n => [n, n])];
    const forceRows = FORCE_ROWS.map(([k, lbl, kind]) => F.num(s.forces, k, { label: lbl, kind, id: "bfemF" + k }));
    const prefill = async () => {
      if (!s.case) return;
      try {
        const r = await api.forces({ member: s.member, case: s.case });
        Object.assign(s.forces, r.forces || {});
        F.refresh && F.refresh();
        toast("Member Buckling", `Forces of ${s.member} under ${s.case} loaded.`);
      } catch (e) { toast("Member Buckling", e.message, "error"); }
    };
    const body = [
      bfGroup("Member", [
        F.select(s, "member", memberOpts, { label: "Frame member", id: "bfemMember" }),
        F.select(s, "case", caseOpts, { label: "Forces from case", id: "bfemCase", onChange: prefill,
          hint: "Fills the end forces below from a linear static case (mean N, end moments, equivalent span load)." }),
      ]),
      bfGroup("End forces", forceRows, { note: "M3 positive compresses the +2 face (sagging); M2 positive puts the +3 face in tension." }),
      bfGroup("Model", [
        F.select(s, "boundary", [["fork", "Fork supports (simply supported, warping free)"], ["fixed", "Fixed ends (warping restrained)"]], { label: "Boundary", id: "bfemBoundary" }),
        F.num(s, "num_modes", { label: "Number of modes", int: true, min: 1, max: 20, id: "bfemModes" }),
      ]),
      bfAdvanced([
        F.select(s, "load_position", [["shear_center", "Shear centre"], ["top", "Top flange"], ["bottom", "Bottom flange"]], { label: "w2 acts at", id: "bfemLoadPos" }),
        F.num(s, "n_long", { label: "Elements along the length", int: true, min: 2, max: 400, allowNull: true, nullText: "auto", id: "bfemNLong" }),
        F.num(s, "n_web", { label: "Elements across the web", int: true, min: 1, max: 40, id: "bfemNWeb" }),
        F.num(s, "n_flange", { label: "Elements per flange outstand", int: true, min: 1, max: 40, id: "bfemNFl" }),
      ], { summary: "Advanced — mesh and load position" }),
      out,
    ];
    let dlg = null;
    const runBtn = bfEl("button", { type: "button", class: "btn btn-run", id: "bfemRun" }, ["Run"]);
    runBtn.addEventListener("click", async () => {
      if (F.validate && F.validate()) return;   // returns the first bad field
      const req = {
        member: s.member, forces: { ...s.forces }, boundary: s.boundary, load_position: s.load_position,
        num_modes: s.num_modes, mesh: { n_web: s.n_web, n_flange: s.n_flange, ...(s.n_long ? { n_long: s.n_long } : {}) },
      };
      st.prev = { ...req, n_web: s.n_web, n_flange: s.n_flange, forces: { ...s.forces } };
      runBtn.disabled = true;
      out.innerHTML = `<p class="muted">Solving the shell model…</p>`;
      try {
        const r = await api.run(req);
        st.last = r;
        renderResult(out, r);
      } catch (e) {
        out.innerHTML = "";
        out.appendChild(bfEl("p", { class: "dk-err is-on", role: "alert", text: e.message }));
      } finally { runBtn.disabled = false; }
    });
    dlg = bfDialog({
      id: "bfemDlg", title: "Member Buckling (Shell FEM)", iconId: "exleaf-buckling", size: "lg", form: F, body,
      intro: "Rebuilds the member as a shell model of its cross-section (flanges and web) and finds its local, distortional and global buckling modes under the end forces.",
      extra: [runBtn], noCancel: true,
    });
    return dlg;
  }

  /* ----------------------------------------------------------- results */
  function renderResult(out, r) {
    out.innerHTML = "";
    const f = (k, v) => bfIsNum(v) ? bfEsc(bfU.fmtU(k, v, 3)) : "—";
    const head = bfEl("p", { class: "muted", html:
      `${bfEsc(r.member)} · ${bfEsc(r.section.type)} ${bfEsc(r.section.name)} · L = ${f("length", r.length)} · ` +
      `${r.mesh.shells} shells, ${r.mesh.equations} equations` });
    const rows = (r.modes || []).map(md => `<tr data-mode="${md.mode}"><td>${md.mode}</td><td class="num">${(+md.factor).toPrecision(5)}</td>` +
      `<td>${bfEsc(CLASS_LABEL[md.class] || md.class)}</td><td class="num">${md.half_waves}</td><td class="num">${(+md.global_fraction).toFixed(2)}</td></tr>`).join("");
    const tbl = bfEl("table", { class: "bfem-tbl", id: "bfemTable", html:
      `<thead><tr><th>Mode</th><th>λ</th><th>Type</th><th>Half-waves</th><th>Global share</th></tr></thead>` +
      `<tbody>${rows || `<tr><td colspan="5" class="muted">No positive buckling factor</td></tr>`}</tbody>` });
    const ref = r.references || {};
    const refs = [];
    if (bfIsNum(ref.Mcr_ltb)) refs.push(`M<sub>cr</sub> (closed form, fork, uniform M) = ${f("moment", ref.Mcr_ltb)}`);
    if (bfIsNum(ref.P_euler_minor)) refs.push(`P<sub>E,minor</sub> = ${f("force", ref.P_euler_minor)}`);
    for (const pl of ref.plates || []) if (pl.role === "web") { refs.push(`web σ<sub>cr</sub> (k = ${pl.k}) = ${f("stress", pl.sigma_cr)}`); break; }
    const view = bfEl("div", { class: "bfem-view" });
    const canvas = bfEl("canvas", { class: "bfem-canvas", id: "bfemCanvas", width: 640, height: 300, "aria-label": "Buckling mode shape" });
    const modeSel = bfEl("select", { id: "bfemModeSel", "aria-label": "Mode" }, (r.modes || []).map(md => bfEl("option", { value: String(md.mode) }, [`Mode ${md.mode} · λ = ${(+md.factor).toPrecision(4)}`])));
    const anim = bfEl("input", { type: "checkbox", id: "bfemAnim", checked: true });
    view.append(bfEl("div", { class: "bfem-viewbar" }, [modeSel, bfEl("label", {}, [anim, " Animate"]),
      bfEl("span", { class: "muted", text: "Drag to rotate" })]), canvas);
    out.append(head, tbl, refs.length ? bfNote(refs.join(" · ")) : bfEl("span"), view);
    for (const w of r.warnings || []) out.appendChild(bfNote("⚠ " + bfEsc(w)));
    const v = viewer(canvas, r);
    modeSel.addEventListener("change", () => v.mode(modeSel.value));
    anim.addEventListener("change", () => v.animate(anim.checked));
    tbl.addEventListener("click", e => { const tr = e.target.closest("tr[data-mode]"); if (tr) { modeSel.value = tr.dataset.mode; v.mode(tr.dataset.mode); } });
    v.mode(modeSel.value || "1");
  }

  /* Small orthographic mode-shape viewer of the generated shell mesh. */
  function viewer(canvas, r) {
    const ctx = canvas.getContext("2d");
    const nodes = r.nodes || {};
    let shape = {}, yaw = -0.6, pitch = 0.45, t0 = performance.now(), anim = true, raf = 0;
    const pts = Object.values(nodes);
    const c = [0, 1, 2].map(k => pts.reduce((a, p) => a + p[k], 0) / Math.max(pts.length, 1));
    const span = Math.max(1e-9, ...[0, 1, 2].map(k => Math.max(...pts.map(p => p[k])) - Math.min(...pts.map(p => p[k]))));
    const dpr = window.devicePixelRatio || 1;
    const proj = (p) => {
      const x = p[0] - c[0], y = p[1] - c[1], z = p[2] - c[2];
      const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
      const x1 = cy * x - sy * y, y1 = sy * x + cy * y;
      return [x1, cp * z - sp * y1, sp * z + cp * y1];
    };
    function draw() {
      const W = canvas.clientWidth || 640, H = canvas.clientHeight || 300;
      if (canvas.width !== W * dpr) { canvas.width = W * dpr; canvas.height = H * dpr; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      const amp = 0.08 * span * (anim ? Math.sin((performance.now() - t0) / 1800 * 2 * Math.PI) : 1);
      const sc = 0.85 * Math.min(W, H * 2.2) / span;
      const P = {};
      for (const [t, p] of Object.entries(nodes)) {
        const u = shape[t] || [0, 0, 0];
        const q = proj([p[0] + amp * u[0], p[1] + amp * u[1], p[2] + amp * u[2]]);
        P[t] = [W / 2 + sc * q[0], H / 2 - sc * q[1], q[2], Math.hypot(u[0], u[1], u[2])];
      }
      const quads = (r.quads || []).map(qd => qd.map(String)).filter(qd => qd.every(t => P[t]));
      quads.sort((a, b) => a.reduce((s, t) => s + P[t][2], 0) - b.reduce((s, t) => s + P[t][2], 0));
      const css = getComputedStyle(document.documentElement);
      const line = css.getPropertyValue("--text-3").trim() || "#888";
      for (const qd of quads) {
        const m = qd.reduce((s, t) => s + P[t][3], 0) / 4;
        ctx.beginPath();
        qd.forEach((t, i) => i ? ctx.lineTo(P[t][0], P[t][1]) : ctx.moveTo(P[t][0], P[t][1]));
        ctx.closePath();
        ctx.fillStyle = `hsla(${210 - 210 * Math.min(1, m)}, 70%, 55%, 0.85)`;
        ctx.fill();
        ctx.strokeStyle = line; ctx.lineWidth = 0.3; ctx.stroke();
      }
      if (anim) raf = requestAnimationFrame(draw);
    }
    let drag = null;
    canvas.addEventListener("pointerdown", e => { drag = [e.clientX, e.clientY, yaw, pitch]; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener("pointermove", e => { if (!drag) return; yaw = drag[2] + (e.clientX - drag[0]) * 0.01; pitch = Math.max(-1.5, Math.min(1.5, drag[3] + (e.clientY - drag[1]) * 0.01)); if (!anim) draw(); });
    canvas.addEventListener("pointerup", () => { drag = null; });
    const stop = () => { cancelAnimationFrame(raf); raf = 0; };
    const obs = new MutationObserver(() => { if (!document.body.contains(canvas)) { stop(); obs.disconnect(); } });
    obs.observe(document.body, { childList: true, subtree: true });
    return {
      mode: k => { shape = (r.shapes || {})[String(k)] || {}; stop(); draw(); },
      animate: on => { anim = on; stop(); draw(); },
    };
  }

  sky.bucklingFem = { open, caseFields, last: () => st.last };
  if (sky.menu && sky.menu.add) {
    try {
      sky.menu.add("Analyze", { label: "Member Buckling…", act: "an-member-buckling", hint: "shell FEM of one frame member",
        fn: () => open(), before: "Run Analysis" });
    } catch (e) { console.error("member buckling menu failed", e); }
  }
}
