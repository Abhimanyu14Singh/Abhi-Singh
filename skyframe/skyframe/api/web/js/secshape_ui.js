/* SkyFrame — "Shape" selector + parametric dimension forms for the Frame
   Section Property Data dialog (v1.21, js/dlgstd_defs.js editFrameSection).

     Steel   I / wide flange, I with unequal flanges, channel, angle, double
             angle, tee, rectangular tube, round tube / pipe, solid
             rectangle, solid circle
     Concrete rectangular (b × h — the legacy rectangle, unchanged),
             circle, T-beam, L-beam
     Other   General (A, I33, I22, J typed in)
   plus read-only views of library W-shapes, Section Designer sections and
   database imports (js/steeldb.js; "Edit as Parametric" converts one).

   Parametric shapes store `shape` + `dims` (SI) and the exact A / I33 /
   I22 / J / b / h / As2 / As3 (+ `principal` for angles and L-beams) from
   js/secshapes.js — the same formulas as skyframe/core/shape_props.py, so a
   section saved here reloads byte-identically. Properties are recomputed
   live on every keystroke (readouts + preview); the section itself only
   changes on a committed edit (change event), like every dlgkit field.

   Hooks (js/dlgstd_defs.js): the Shape readout row → ssEd.select, the
   dims + properties groups → ssEd.node, the preview's section() →
   ssEd.current(). */

import ssU from "./units.js";
import { el as ssEl, esc as ssEsc, dkGroup as ssGroup } from "./dlgkit.js";
import { rectSectionProps as ssRect } from "./modeledit.js";
import { SS_TYPES, SS_PARAM, SS_LABEL, ssTypeInfo, ssValidate, ssProps, ssApply, SHEAR_AREA_FORMULAS } from "./secshapes.js";

let cssDone = false;
function ensureCss() {
  if (cssDone || document.querySelector("link[data-sdb-css]")) { cssDone = true; return; }
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = "/static/steeldb.css"; l.setAttribute("data-sdb-css", "1");
  document.head.appendChild(l);
  cssDone = true;
}

const isConc = (D, s) => (((D && D.materials) || {})[s.material] || {}).material_type === "concrete";
const nums = o => Object.fromEntries(Object.entries(o || {}).filter(([, v]) => typeof v === "number"));

/** Current shape key of a section dict (a SS_TYPES key, "W" or "designer"). */
export function ssShapeKey(s, D) {
  if (!s) return "general";
  if (s.shape === "W" || s.shape === "designer") return s.shape;
  if (SS_PARAM.includes(s.shape) && s.dims) return s.shape === "round_solid" && isConc(D, s) ? "round_solid_c" : s.shape;
  return s.b > 0 && s.h > 0 ? "rect" : "general";
}

/** List-column label for the new shapes (null → the caller's own rule). */
export function ssShapeLabel(s) {
  if (!s || !SS_PARAM.includes(s.shape) || !s.dims) return null;
  const base = SS_LABEL[s.shape] || s.shape;
  return s.db_ref ? `${base} · ${String(s.db_ref.standard || "").toUpperCase()}` : base;
}

/** Build the selector + dynamic form. F: the dialog's dkForm, s: the
    section copy being edited, opts.model: draft D, opts.onChange(). */
export function ssShapeEditor(F, s, { model: D, onChange } = {}) {
  ensureCss();
  const st = { key: ssShapeKey(s, D) };
  let live = null;                                  // uncommitted (typing) section
  const options = () => {
    const o = SS_TYPES.filter(t => !t.hidden || t.key === st.key).map(t => [t.key, `${t.group} — ${t.label}`]);
    if (st.key === "W") o.unshift(["W", "Steel — W-shape (library, read-only)"]);
    if (st.key === "designer") o.unshift(["designer", "Section Designer (read-only)"]);
    return o;
  };
  const select = F.select(st, "key", options, {
    label: "Shape", id: "dsSecShape", hint: "Pick a shape to enter its dimensions; properties are computed exactly.",
    write: v => switchShape(v), onChange: () => rebuild(),
  });
  const node = ssEl("div", { class: "ss-shape", id: "ssShapeForm" });
  const kick = () => onChange && onChange();

  function clearParam() {
    const hadDims = !!s.dims;
    for (const k of ["dims", "db_ref", "principal", "use_principal"]) delete s[k];
    if (SS_PARAM.includes(s.shape)) delete s.shape;
    if (hadDims) { delete s.As2; delete s.As3; }     // auto shear areas of the shape
  }
  function switchShape(v) {
    live = null;
    if (v === "W" || v === "designer" || v === st.key) { st.key = v; return; }
    if (v === "rect") {
      clearParam();
      if (!(s.b > 0 && s.h > 0)) { s.b = 0.3; s.h = 0.6; }
      ssRect(s);
    } else if (v === "general") {
      clearParam();
      if (s.shape === "W" || s.shape === "designer") delete s.shape;
      for (const k of ["A", "I33", "I22", "J"]) if (!(s[k] > 0)) s[k] = { A: 0.18, I33: 5.4e-3, I22: 1.35e-3, J: 3.7e-3 }[k];
      s.b = 0; s.h = 0;
    } else {
      const info = ssTypeInfo(v), typ = info.alias || info.key;
      const dims = s.shape === typ && s.dims && !s.db_ref ? { ...s.dims } : { ...info.def };
      if (s.shape === "W" || s.shape === "designer") delete s.shape;
      ssApply(s, typ, dims);
    }
    st.key = v;
  }

  /* ---- read-only property rows (own elements: never re-rendered by F) */
  const outs = [];
  const outRow = (label, kind, get, { unit, fmt } = {}) => {
    const o = ssEl("output", { class: "dk-readout", "data-ss": label });
    outs.push(() => {
      const v = get(live || s);
      o.textContent = v == null || !isFinite(v) ? "—" : fmt ? fmt(v) : ssU.sci(kind, v, 4);
    });
    return F.row(label, o, { unit: unit != null ? unit : ssU.label(kind) });
  };
  const paint = () => outs.forEach(f => f());
  const cwUnit = () => `${ssU.label("dim")}⁶`;
  const cwVal = v => (v * ssU.factor("dim") ** 6).toExponential(4);
  const degFmt = v => (v * 180 / Math.PI).toFixed(3);

  function propsGroup(typ, stored) {
    const pp = sec => (sec.dims && SS_PARAM.includes(typ) ? ssProps(typ, nums(sec.dims)) : {});
    const rows = [
      outRow("Cross-section Area, A", "area", sec => sec.A),
      outRow("Moment of Inertia about 3 axis, I33", "inertia", sec => sec.I33),
      outRow("Moment of Inertia about 2 axis, I22", "inertia", sec => sec.I22),
      outRow("Torsional Constant, J", "inertia", sec => sec.J),
      outRow("Shear Area in 2 direction, As2", "area", sec => sec.As2),
      outRow("Shear Area in 3 direction, As3", "area", sec => sec.As3),
    ];
    if (!stored) rows.push(
      outRow("Section Modulus about 3 axis, S33", "secmod", sec => pp(sec).S33),
      outRow("Section Modulus about 2 axis, S22", "secmod", sec => pp(sec).S22),
      outRow("Plastic Modulus about 3 axis, Z33", "secmod", sec => pp(sec).Z33),
      outRow("Plastic Modulus about 2 axis, Z22", "secmod", sec => pp(sec).Z22),
      outRow("Warping Constant, Cw", "none", sec => pp(sec).Cw, { unit: cwUnit(), fmt: cwVal }),
      outRow("Shear Centre Offset along 3, x0", "dim", sec => pp(sec).x0, { fmt: v => String(+ssU.toDisplay("dim", v).toPrecision(4)) }),
      outRow("Shear Centre Offset along 2, y0", "dim", sec => pp(sec).y0, { fmt: v => String(+ssU.toDisplay("dim", v).toPrecision(4)) }),
    );
    const g = ssGroup("Section Properties", rows, { cls: "ss-props", note: stored
      ? "Published (tabulated) database values — read-only."
      : `Exact from the dimensions (sharp corners). Shear areas: ${ssEsc(SHEAR_AREA_FORMULAS[typ] || "")} — shear deformation is included.` });
    return g;
  }
  function principalGroup() {
    if (!(s.principal || (live && live.principal))) return null;
    return ssGroup("Principal Axes (unsymmetric section)", [
      outRow("Major Principal Moment, I_major", "inertia", sec => (sec.principal || {}).I_major),
      outRow("Minor Principal Moment, I_minor", "inertia", sec => (sec.principal || {}).I_minor),
      outRow("Angle from 3 to Major Axis, α", "none", sec => (sec.principal || {}).alpha, { unit: "°", fmt: degFmt }),
      F.check(s, "use_principal", { label: "Analyze about the principal axes", id: "ssPrincipal",
        hint: "Off (ETABS default): bending about the leg axes 2 / 3 with I33 / I22, the product of inertia I23 ignored. On: the members use I_major / I_minor and are turned by −α about local 1 (exact unsymmetric bending).",
        write: v => { if (v) s.use_principal = true; else delete s.use_principal; } }),
    ]);
  }

  function liveRead(typ) {
    const dims = { ...(s.dims || {}) };
    let bad = "";
    node.querySelectorAll("input[data-ss-dim]").forEach(inp => {
      const k = inp.dataset.ssDim, txt = inp.value.trim();
      if (txt === "") { if (inp.dataset.ssOpt) dims[k] = 0; else bad = bad || `Enter ${k}.`; return; }
      const v = ssU.parse("dim", txt);
      if (!isFinite(v)) bad = bad || `${k}: enter a number in ${ssU.label("dim")}.`;
      else dims[k] = v;
    });
    if (bad) return { error: bad };
    const t = { ...s };
    const p = ssApply(t, typ, dims);
    return p.error ? p : t;
  }

  function rebuild() {
    F.forget(node);
    node.textContent = "";
    outs.length = 0;
    live = null;
    const key = st.key;
    if (key === "rect") {
      node.appendChild(ssGroup("Dimensions", [
        F.num(s, "h", { label: "Depth (t3)", kind: "dim", gt: 0, id: "dsSecH", write: v => { s.h = v; ssRect(s); }, onChange: () => { F.refresh(); paint(); kick(); } }),
        F.num(s, "b", { label: "Width (t2)", kind: "dim", gt: 0, id: "dsSecB", write: v => { s.b = v; ssRect(s); }, onChange: () => { F.refresh(); paint(); kick(); } }),
      ]));
      node.appendChild(ssGroup("Section Properties", [
        outRow("Cross-section Area", "area", sec => sec.A), outRow("Moment of Inertia about 3 axis", "inertia", sec => sec.I33),
        outRow("Moment of Inertia about 2 axis", "inertia", sec => sec.I22), outRow("Torsional Constant", "inertia", sec => sec.J),
      ], { note: "Computed from b × h (Roark torsion constant)." }));
    } else if (key === "general") {
      node.appendChild(ssGroup("Section Properties", [["A", "Cross-section Area", "area"], ["I33", "Moment of Inertia about 3 axis", "inertia"],
        ["I22", "Moment of Inertia about 2 axis", "inertia"], ["J", "Torsional Constant", "inertia"]].map(([k, lbl, kind]) =>
        F.num(s, k, { label: lbl, kind, gt: 0, id: "dsSec" + k, onChange: () => kick() })), { note: "General section: enter the analysis properties directly." }));
    } else if (key === "W" || key === "designer") {
      node.appendChild(ssGroup("Section Properties", [
        outRow("Cross-section Area", "area", sec => sec.A), outRow("Moment of Inertia about 3 axis", "inertia", sec => sec.I33),
        outRow("Moment of Inertia about 2 axis", "inertia", sec => sec.I22), outRow("Torsional Constant", "inertia", sec => sec.J),
      ], { note: key === "W" ? "From the steel shape library (read-only)." : "From the Section Designer polygon (read-only)." }));
    } else if (s.db_ref) {
      const info = ssTypeInfo(key), typ = info.alias || info.key;
      const edit = ssEl("button", { type: "button", class: "btn btn-small", id: "ssDbEdit", title: "Detach from the database: recompute every property from the dimensions" }, ["Edit as Parametric"]);
      edit.addEventListener("click", () => {
        const dims = { ...s.dims };
        delete s.db_ref;
        ssApply(s, typ, dims);
        F.touched = true; F.onTouch && F.onTouch();
        rebuild(); F.refresh(); kick();
      });
      const dimRows = (info.dims || []).filter(([k]) => typeof (s.dims || {})[k] === "number")
        .map(([k, lbl]) => outRow(lbl, "dim", sec => (sec.dims || {})[k], { fmt: v => String(+ssU.toDisplay("dim", v).toPrecision(5)) }));
      node.appendChild(ssGroup("Database Shape", [
        ssEl("p", { class: "muted ss-db-note", id: "ssDbNote", html: `<b>${ssEsc(String(s.db_ref.standard).toUpperCase())}</b> · ${ssEsc(s.db_ref.name)} — ${ssEsc(SS_LABEL[typ] || typ)}` }),
        ...dimRows, edit]));
      node.appendChild(propsGroup(typ, true));
      const pg = principalGroup();
      if (pg) node.appendChild(pg);
    } else {
      const info = ssTypeInfo(key), typ = info.alias || info.key;
      if (!s.dims || s.shape !== typ) ssApply(s, typ, { ...info.def });
      const fields = info.dims.map(([k, lbl, opt]) => {
        const row = F.num(s.dims, k, {
          label: lbl, kind: "dim", id: "ssDim_" + k, ...(opt ? { min: 0, def: 0 } : { gt: 0 }), read: () => (s.dims || {})[k],
          validate: v => { const r = ssValidate(typ, { ...nums(s.dims), [k]: v }); return r.error || ""; },
          write: v => { s.dims[k] = v == null ? 0 : v; ssApply(s, typ, s.dims); },
          onChange: () => { live = null; F.refresh(); paint(); kick(); },
        });
        const inp = row.querySelector("input");
        inp.dataset.ssDim = k;
        if (opt) inp.dataset.ssOpt = "1";
        return row;
      });
      node.appendChild(ssGroup("Dimensions", fields, { note: typ === "angle" ? "Leg d along local 2 (vertical), leg b along local 3; heel at the bottom-left." :
        typ === "channel" ? "Web on the left, flanges toward +3." : typ === "L_concrete" ? "Web flush with the left edge of the flange (spandrel / edge beam)." : null }));
      node.appendChild(propsGroup(typ, false));
      const pg = principalGroup();
      if (pg) node.appendChild(pg);
      const err = ssEl("p", { class: "dk-err", role: "alert", id: "ssLiveErr" });
      node.appendChild(err);
      node.oninput = e => {
        if (!e.target.matches("input[data-ss-dim]")) return;
        const t = liveRead(typ);
        if (t.error) { live = null; err.textContent = t.error; }
        else { live = t; err.textContent = ""; }
        paint();
      };
    }
    paint();
  }
  rebuild();
  return {
    select, node,
    key: () => st.key,
    current: () => live || s,
    refresh: paint,
  };
}

export default { ssShapeEditor, ssShapeKey, ssShapeLabel };
