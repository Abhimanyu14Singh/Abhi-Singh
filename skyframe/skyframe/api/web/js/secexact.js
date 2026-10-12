/* SkyFrame — exact section solver UI (v1.22, CONTRACT "Exact section solver").

   The backend's in-house finite-element warping solver
   (skyframe/core/section_fem.py, POST /api/sections/exact) gives the exact
   torsion constant J, warping constant Cw, shear centre and shear areas of
   any outline. This module wires it into the section editors:

     · Section Designer (js/secdesigner.js): a "Torsion J" choice (Exact /
       Approx. A⁴/40·Ip) stored as designer_sections[name].torsion — NEW
       sections default to "exact", saved ones keep their stored method — and
       a live J / Cw / As readout with the method used;
     · parametric shapes (js/secshape_ui.js): an "Exact J / Cw (finite
       elements)" checkbox stored as dims.torsion = "exact" (closed forms stay
       the default; database imports keep their tabulated values);
     · secpreview (js/secpreview.js): seJMethod() names the J method of any
       frame section for the property readout.

   Backend calls are debounced (300 ms) and keyed by the geometry, so typing
   never floods the server; ?mock=1 (or an unreachable endpoint) answers from
   js/mock_secexact.js with the CLOSED-FORM numbers marked "approx". Every
   number goes through js/units.js; the model stays SI. */

import seU from "./units.js";
import { mseExact as seMockExact } from "./mock_secexact.js";

const seClone = o => JSON.parse(JSON.stringify(o));
const seIsMock = () => !!(window.__sky && window.__sky.store && window.__sky.store.mock);
const seFmt = (kind, v) => (v == null || !isFinite(v)) ? "—" : seU.sci(kind, v, 4);
const seCw = v => (v == null || !isFinite(v)) ? "—" : (v * seU.factor("dim") ** 6).toExponential(3);

/** POST /api/sections/exact (live) or the mock (closed form, "approx"). */
export async function seExact(body) {
  if (!seIsMock()) {
    try {
      const res = await fetch("/api/sections/exact", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      let data = null;
      try { data = await res.json(); } catch (e) { /* non-JSON */ }
      if (res.ok && data) return data;
      if (res.status === 400) { const e = new Error((data && data.error) || "invalid section"); e.status = 400; throw e; }
    } catch (e) {
      if (e.status === 400) throw e;
      console.warn("Exact section endpoint unavailable, using the closed form:", e.message);
    }
  }
  return seMockExact(body, (window.__sky && window.__sky.store && window.__sky.store.model) || {});
}

/* debounced, geometry-keyed requests: one timer + one result cache per slot */
const seSlots = {};
function seRequest(slot, key, body, done, ms = 300) {
  const S = seSlots[slot] || (seSlots[slot] = { timer: 0, key: null, res: null, seq: 0 });
  if (S.key === key && S.res) { done(S.res, null); return; }
  clearTimeout(S.timer);
  const seq = ++S.seq;
  S.timer = setTimeout(async () => {
    try {
      const r = await seExact(body);
      if (S.seq !== seq) return;
      S.key = key; S.res = r;
      done(r, null);
    } catch (e) { if (S.seq === seq) done(null, e); }
  }, ms);
}

/** J method of a frame section dict for display:
    {method: "exact" | "closed-form" | "approx" | "tabulated" | "entered", label}. */
export function seJMethod(sec, model = {}, name = "") {
  if (!sec) return null;
  name = name || sec.name || "";
  // the mock cannot run the solver: an "exact" section shows closed-form numbers there
  const EX = seIsMock() ? { method: "approx", label: "exact (mock: closed form)" } : { method: "exact", label: "exact (FE)" };
  const bu = (model.builtup_sections || {})[name];
  if (bu) {
    const t = bu.torsion || "auto";
    return t === "exact" || t === "fem" ? EX : { method: "closed-form", label: `closed form (${t})` };
  }
  const ds = (model.designer_sections || {})[name];
  if (ds) return ds.torsion === "exact" ? EX : { method: "approx", label: "approx. A⁴/40·Ip" };
  if (sec.db_ref) return { method: "tabulated", label: "tabulated (database)" };
  if (sec.dims && typeof sec.dims === "object") return sec.dims.torsion === "exact" ? EX : { method: "closed-form", label: "closed form" };
  return null;
}

/* ------------------------------------------------------------ designer */
function seDesignerBody(sec, model) {
  const polys = (sec.polygons || []).map(p => ({ vertices: p.vertices, material: p.material, hole: !!p.hole }));
  const first = polys.find(p => !p.hole) || polys[0] || {};
  return {
    kind: "designer",
    section: { name: sec.name || "DS", material: sec.material || first.material || "", base: sec.base || "concrete", polygons: polys, rebar: [] },
    materials: seClone((model && model.materials) || {}),
  };
}

/** Append the torsion choice + exact readout to the designer's property box.
    designer: the SectionDesigner instance (this.sec, this.model()). */
export function seDesignerBlock(designer, box) {
  const sec = designer && designer.sec;
  if (!sec || !box) return;
  const model = designer.model();
  const wrap = document.createElement("div");
  wrap.className = "ds-exact"; wrap.id = "dsExact";
  const lab = document.createElement("label");
  lab.className = "ds-prop-line";
  const span = document.createElement("span"); span.textContent = "Torsion J";
  const sel = document.createElement("select");
  sel.id = "dsTorsion";
  for (const [v, t] of [["exact", "Exact (finite elements)"], ["approx", "Approx. A⁴/(40·Ip)"]]) {
    const o = document.createElement("option"); o.value = v; o.textContent = t; sel.appendChild(o);
  }
  sel.value = sec.torsion === "exact" ? "exact" : "approx";
  sel.addEventListener("change", () => {
    if (sel.value === "exact") sec.torsion = "exact"; else delete sec.torsion;   // "approx" = absent (byte-identical files)
    paint();
  });
  lab.append(span, sel);
  const out = document.createElement("div");
  out.className = "ds-prop-line"; out.id = "dsJ";
  const extra = document.createElement("div");
  extra.className = "ds-prop-line muted"; extra.id = "dsJx";
  wrap.append(lab, out, extra);
  box.appendChild(wrap);

  function show(p, method, note) {
    out.innerHTML = "";
    const a = document.createElement("span");
    a.textContent = `J ${seFmt("inertia", p && p.J)} ${seU.label("inertia")}`;
    a.dataset.si = p && p.J != null ? String(p.J) : "";
    const b = document.createElement("b");
    b.id = "dsJMethod"; b.dataset.method = method; b.textContent = method + (note ? ` · ${note}` : "");
    out.append(a, b);
    extra.textContent = p && method === "exact"
      ? `Cw ${seCw(p.Cw)} ${seU.label("dim")}⁶ · As2 ${seFmt("area", p.As2)} · As3 ${seFmt("area", p.As3)} ${seU.label("area")}`
      : "";
  }
  function paint() {
    const valid = (sec.polygons || []).some(p => !p.hole && (p.vertices || []).length >= 3);
    if (!valid) { show(null, sec.torsion === "exact" ? "exact" : "approx", "draw a polygon"); return; }
    const body = seDesignerBody(sec, model);
    if (sec.torsion !== "exact") {
      // closed-form approximation, computed locally (no backend call)
      const r = seMockExact(body, model);
      show(r.props, "approx");
      return;
    }
    show(null, "exact", "computing…");
    seRequest("designer", JSON.stringify(body), body, (r, e) => {
      if (!document.body.contains(out)) return;
      if (e) { show(null, "exact", e.message); return; }
      show(r.props, r.method === "exact" ? "exact" : "approx", r.method === "exact" ? "" : "closed form (mock)");
    });
  }
  paint();
}

/** New designer sections default to the exact solver (v1.22). */
export function seDesignerNew(sec) {
  if (sec && !sec.torsion) sec.torsion = "exact";
  return sec;
}

/* ------------------------------------------------------- parametric shapes */
/** "Exact J / Cw" checkbox + readout row for a parametric section s (shape +
    dims). onApply(method, user): s changed ("exact" / "approx" result, or
    "closed" after un-ticking) — the caller re-paints; user = a user action. */
export function seShapeExact(s, typ, { onApply } = {}) {
  const wrap = document.createElement("div");
  wrap.className = "ss-exact"; wrap.id = "ssExact";
  const lab = document.createElement("label");
  lab.className = "dk-check";
  const cb = document.createElement("input");
  cb.type = "checkbox"; cb.id = "ssExactCb";
  cb.checked = !!(s.dims && s.dims.torsion === "exact");
  lab.append(cb, document.createTextNode(" Exact J / Cw / shear (finite elements)"));
  const out = document.createElement("p");
  out.className = "muted ss-exact-out"; out.id = "ssExactOut";
  wrap.append(lab, out);
  cb.addEventListener("change", () => {
    if (!s.dims) return;
    if (cb.checked) s.dims.torsion = "exact"; else delete s.dims.torsion;
    refresh(true);
    if (!cb.checked && onApply) onApply("closed", true);
  });
  function refresh(user = false) {
    const on = !!(s.dims && s.dims.torsion === "exact");
    cb.checked = on;
    if (!on) { out.textContent = "J method: closed form (thin-walled / Bredt / Roark)."; out.dataset.method = "closed-form"; return; }
    const dims = Object.fromEntries(Object.entries(s.dims || {}).filter(([, v]) => typeof v === "number"));
    const body = { kind: "shape", type: typ, dims };
    out.textContent = "J method: exact — computing…"; out.dataset.method = "pending";
    seRequest("shape", JSON.stringify(body), body, (r, e) => {
      if (!(s.dims && s.dims.torsion === "exact")) return;
      if (e) { out.textContent = `Exact solver: ${e.message}`; out.dataset.method = "error"; return; }
      const p = r.props || {};
      if (r.method === "exact") { s.J = p.J; s.As2 = p.As2; s.As3 = p.As3; }      // as section_from_dims (backend)
      out.dataset.method = r.method === "exact" ? "exact" : "approx";
      out.textContent = `J method: ${r.method === "exact" ? "exact (finite elements)" : "approx (closed form, mock)"} · J ${seFmt("inertia", p.J)} ${seU.label("inertia")} · Cw ${seCw(p.Cw)} ${seU.label("dim")}⁶`;
      if (onApply) onApply(r.method, user);
    });
  }
  refresh();
  return { node: wrap, refresh };
}

export default { seExact, seJMethod, seDesignerBlock, seDesignerNew, seShapeExact };
