/* SkyFrame — Built-up Section Editor (CONTRACT "Built-up sections").

   A visual section editor modelled on the ETABS/SAP Section Designer, but
   simpler: the section is a list of components (plates, database shapes,
   parametric shapes) placed on a canvas.

     · canvas: grid + snapping (grid and the edges / corners / mid-lines of
       other components), wheel zoom, drag-pan, drag to move, ±90° rotation,
       typed angle, mirror, duplicate, delete (keys: Del, R / Shift+R, M,
       arrows nudge one grid step);
     · palette: + Plate, + Database shape… (standard → family → search,
       js/units.js values), + Parametric shape, Templates… (gallery with
       parameter forms);
     · inspector: numeric position / rotation / dimensions / material of the
       selected component in display units (js/units.js), section name,
       material, analysis axes (geometric | principal) and torsion method;
     · live properties: A, I33, I22, J (+ method), centroid, principal angle,
       I major/minor, Ixy, S, Z, r, shear areas, shear centre — computed by
       js/builtup_geom.js (the exact backend formulas) and, live, confirmed
       by POST /api/sections/builtup {action: "properties"};
     · canvas overlays: centroid, principal axes, local 2 (up, green) / 3
       (right, blue) arrows — the convention of js/secpreview.js — and the
       shear centre.

   OK writes model.builtup_sections[name] + the mirrored model.sections[name]
   (shape "builtup") into the working model and marks it dirty (no change →
   nothing written: byte-identical); Cancel / Esc discard. Opened from
   Define › Section Properties › Built-up Section…, the Frame Properties list
   ("Add Built-up Section…"; Modify on a built-up section reopens it) and
   __sky.openBuiltup(name?). ?mock=1 → js/mock_builtup.js. */

import BuU from "./units.js";
import { openMatLib as buOpenMatLib } from "./matlib.js";   // "Add steel grade…" when the model has no steel material
import { dialog as buDialog, btn as buBtn, footBar as buFootBar, errorLine as buErrorLine,
  showError as buShowError, closeDialog as buCloseDialog } from "./analysisdlg.js";
import { buSectionProperties as buProps, buComponentGeometry as buGeom, buFrameSection as buFrame,
  buNormSection as buNorm, buNormComponent as buNormComp, buTemplateSection as buTemplate, buSnapshotRecord as buSnap,
  buPiecesPath as buPath, buValidate as buValid, BU_TEMPLATES as BU_TPL, BU_TEMPLATE_CHOICES as BU_TPL_CH,
  BU_SHAPE_TYPES as BU_TYPES, BU_REQ as BU_REQ_DIMS, BU_TYPE_LABEL as BU_TLABEL, BU_DIM_LABEL as BU_DLABEL,
  BU_DEFAULT_DIMS as BU_DEF_DIMS } from "./builtup_geom.js";
import { mbuStandards as buMockStandards, mbuFamilies as buMockFamilies, mbuShapes as buMockShapes,
  mbuShape as buMockShape, mbuTemplate as buMockTemplate, mbuProperties as buMockProperties } from "./mock_builtup.js";

const DLG_ID = "buEditor";
const SVGNS = "http://www.w3.org/2000/svg";
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function el(tag, attrs = {}, kids = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  (Array.isArray(kids) ? kids : [kids]).forEach(c => c != null && n.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
  return n;
}
const svgEl = (tag, attrs = {}) => { const n = document.createElementNS(SVGNS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); return n; };
const clone = o => JSON.parse(JSON.stringify(o));
const normAng = a => { let x = ((+a % 360) + 360) % 360; if (x > 180) x -= 360; return Math.abs(x) < 1e-12 ? 0 : x; };
const fmtNum = (kind, si, digits = 4) => {
  if (si == null || !isFinite(si)) return "—";
  const v = BuU.toDisplay(kind, si), a = Math.abs(v);
  if (v === 0) return "0";
  return (a >= 1e5 || a < 1e-3) ? v.toExponential(digits - 1) : String(+v.toPrecision(digits));
};

export function initBuiltup(sky) {
  const S = sky.store;
  if (!document.querySelector("link[data-bu-css]")) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/static/builtup.css"; l.setAttribute("data-bu-css", "1");
    document.head.appendChild(l);
  }
  const toast = (t, m, k, ms) => sky.toast && sky.toast(t, m, k || "info", ms || 4000);
  const isMock = () => !!S.mock;

  /* ---------------- backend (live) with mock fallback ---------------- */
  async function req(path, body) {
    const res = await fetch(path, body === undefined ? undefined
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (!res.ok) { const e = new Error((data && data.error) || `${res.status} ${res.statusText}`); e.status = res.status; throw e; }
    return data;
  }
  async function via(liveFn, mockFn) {
    if (!isMock()) {
      try { return await liveFn(); }
      catch (e) { if (e.status === 400) throw e; console.warn("Built-up endpoint unavailable, using mock:", e.message); }
    }
    return mockFn();
  }
  const materialsOf = () => clone((S.model && S.model.materials) || {});
  const backend = {
    properties: sec => via(() => req("/api/sections/builtup", { action: "properties", section: sec, materials: materialsOf() }),
      () => buMockProperties(S.model, sec)),
    template: (id, params, name, material) => via(() => req("/api/sections/builtup/template", { id, params, name, material }),
      () => buMockTemplate(S.model, { id, params, name, material })),
    standards: () => via(() => req("/api/sections/builtup/db/standards"), () => buMockStandards()),
    families: std => via(() => req(`/api/sections/builtup/db/families?standard=${encodeURIComponent(std)}`), () => buMockFamilies(std)),
    shapes: (std, fam, q) => via(() => req(`/api/sections/builtup/db/shapes?standard=${encodeURIComponent(std)}&family=${encodeURIComponent(fam || "")}&q=${encodeURIComponent(q || "")}&limit=200`),
      () => buMockShapes(std, fam, q)),
  };

  /* ---------------- editor state ---------------- */
  let st = null;          // {sec, orig, origName, sel, view, grid, snap, edge, props, err, ...}
  const defaultMaterial = () => {
    const mats = Object.entries((S.model && S.model.materials) || {});
    const steel = mats.find(([, m]) => m && m.material_type === "steel");
    return (steel || mats[0] || [""])[0];
  };
  const uniqueName = base => { const taken = S.model.sections || {}; let i = 1; while (taken[`${base}${i}`]) i++; return `${base}${i}`; };
  const defaultGrid = () => (BuU.label("dim") === "in" ? 0.25 * 0.0254 : 0.005);

  function open(name, { copy = false } = {}) {
    if (!S.model) return null;
    const bus = S.model.builtup_sections || {};
    const exist = name && bus[name] ? clone(bus[name]) : null;
    let sec;
    if (exist) {
      sec = buNorm(exist);
      if (copy) sec.name = uniqueName(`${name}-copy`);
    } else sec = buNorm({ name: uniqueName("BU"), material: defaultMaterial(), components: [], torsion: "exact" });   // v1.22: NEW sections use the exact solver
    st = {
      sec, origName: exist && !copy ? name : null, orig: exist && !copy ? JSON.stringify(buNorm(exist)) : null,
      sel: sec.components.length ? 0 : -1, grid: defaultGrid(), snap: true, edge: true,
      view: null, props: null, err: "", srv: null, srvSeq: 0, drag: null, overlay: null,
    };
    build();
    return st;
  }

  /* ---------------- DOM ---------------- */
  let D = {};       // element refs of the open dialog
  function build() {
    const body = el("div", { class: "bu-root", id: "buRoot" });
    // palette
    const typeSel = el("select", { id: "buParamType", "aria-label": "Parametric shape type" },
      BU_TYPES.filter(t => t !== "plate").map(t => el("option", { value: t }, [BU_TLABEL[t]])));
    const left = el("div", { class: "bu-left" }, [
      el("div", { class: "bu-group-title" }, ["Add component"]),
      buBtn("+ Plate", "btn-small bu-add", () => addPlate(), "Add a rectangular plate (b × t)"),
      buBtn("+ Database shape…", "btn-small bu-add", () => openDbPicker(), "Pick a rolled shape from the steel database"),
      el("div", { class: "bu-row" }, [typeSel, buBtn("+ Add", "btn-small", () => addParam(typeSel.value), "Add a parametric shape")]),
      buBtn("Templates…", "btn-small bu-add bu-tpl-btn", () => openTemplates(), "Start from a template (plate girder, box, 2L, …)"),
      el("div", { class: "bu-group-title" }, ["Components"]),
      el("ul", { class: "bu-list", id: "buCompList", role: "listbox", "aria-label": "Components" }),
    ]);
    left.querySelectorAll("button")[0].id = "buAddPlate";
    left.querySelectorAll("button")[1].id = "buAddDb";
    left.querySelectorAll("button")[2].id = "buAddParam";
    left.querySelector(".bu-tpl-btn").id = "buTemplates";
    // canvas + toolbar
    const tb = (lab, title, fn, id) => { const b = buBtn(lab, "btn-small bu-tool", fn, title); b.id = id; return b; };
    const gridIn = el("input", { type: "number", id: "buGrid", class: "bu-num-sm", min: "0", step: "any", "aria-label": "Grid spacing", value: BuU.inputValue("dim", st.grid) });
    gridIn.addEventListener("change", () => { const v = BuU.parse("dim", gridIn.value); if (v > 0) { st.grid = v; draw(); } else gridIn.value = BuU.inputValue("dim", st.grid); });
    const snapCb = el("input", { type: "checkbox", id: "buSnap", checked: st.snap });
    snapCb.addEventListener("change", () => { st.snap = snapCb.checked; });
    const edgeCb = el("input", { type: "checkbox", id: "buEdgeSnap", checked: st.edge });
    edgeCb.addEventListener("change", () => { st.edge = edgeCb.checked; });
    const toolbar = el("div", { class: "bu-toolbar" }, [
      tb("+", "Zoom in", () => zoom(1.25), "buZoomIn"), tb("−", "Zoom out", () => zoom(0.8), "buZoomOut"), tb("Fit", "Zoom to fit", () => fit(), "buFit"),
      el("span", { class: "bu-sep" }),
      el("label", { class: "bu-inline" }, ["Grid ", gridIn, el("span", { class: "muted", text: " " + BuU.label("dim") })]),
      el("label", { class: "bu-inline" }, [snapCb, " Snap"]), el("label", { class: "bu-inline" }, [edgeCb, " Edges"]),
      el("span", { class: "bu-sep" }),
      tb("⟲ 90°", "Rotate +90° (R)", () => rotateSel(90), "buRotL"), tb("⟳ 90°", "Rotate −90° (Shift+R)", () => rotateSel(-90), "buRotR"),
      tb("Mirror", "Mirror about the local vertical axis (M)", () => mirrorSel(), "buMirror"),
      tb("Align", "Snap the selection against the nearest component edge", () => alignSel(), "buAlign"),
      tb("Duplicate", "Duplicate beside the selection", () => duplicateSel(), "buDup"),
      tb("Delete", "Delete the selection (Del)", () => deleteSel(), "buDelete"),
    ]);
    const svg = svgEl("svg", { id: "buCanvas", class: "bu-canvas", tabindex: "0", role: "img", "aria-label": "Built-up section canvas" });
    const coord = el("div", { class: "bu-status muted", id: "buCoord" }, [" "]);
    const center = el("div", { class: "bu-center" }, [toolbar, el("div", { class: "bu-canvas-wrap" }, [svg]), coord]);
    // inspector
    const right = el("div", { class: "bu-right" }, [
      el("div", { class: "bu-group-title" }, ["Section"]), el("div", { class: "bu-form", id: "buSecForm" }),
      el("div", { class: "bu-group-title" }, ["Selected component"]), el("div", { class: "bu-form", id: "buCompForm" }),
      el("div", { class: "bu-group-title" }, ["Properties ", el("span", { class: "bu-src muted", id: "buPropSrc" })]),
      el("table", { class: "bu-props", id: "buProps" }),
      el("ul", { class: "bu-warn", id: "buWarn" }),
      el("p", { class: "bu-notes muted", id: "buNotes" }),
    ]);
    body.append(left, center, right);
    const err = buErrorLine(); err.id = "buError";
    body.appendChild(el("div", { class: "bu-err-wrap" }, [err]));
    const okB = buBtn("OK", "btn-primary", () => commit(), "Save as a frame section");
    okB.id = "buOk";
    const caB = buBtn("Cancel", "", () => buCloseDialog(DLG_ID));
    caB.id = "buCancel";
    const foot = buFootBar("Sections are SI internally; inputs in " + BuU.label("dim") + ".", [caB, okB]);
    const dlg = buDialog(DLG_ID, { title: st.origName ? `Built-up Section — ${st.origName}` : "Built-up Section", iconId: "exleaf-frames", wide: true, body, foot: foot.wrap,
      onClose: () => { document.removeEventListener("keydown", onKey, true); st = null; D = {}; } });
    dlg.el.querySelector(".modal").classList.add("bu-modal");
    D = { dlg, body, svg, list: body.querySelector("#buCompList"), secForm: body.querySelector("#buSecForm"), compForm: body.querySelector("#buCompForm"),
      props: body.querySelector("#buProps"), warn: body.querySelector("#buWarn"), notes: body.querySelector("#buNotes"), err, coord,
      src: body.querySelector("#buPropSrc"), gridIn };
    wireCanvas(svg);
    document.addEventListener("keydown", onKey, true);
    renderSecForm();
    recompute();
    requestAnimationFrame(() => { fit(); });
  }

  /* ---------------- section form ---------------- */
  function field(label, input, unit) {
    return el("label", { class: "bu-field" }, [el("span", { class: "bu-lab", text: label }), input, unit ? el("span", { class: "bu-unit muted", text: unit }) : null]);
  }
  function numInput(id, kind, si, onSet, opts = {}) {
    // display rounded to 12 significant digits (float noise); the SI value changes only on a user edit
    const inp = el("input", { type: "number", step: "any", id, class: "bu-num", value: kind === "deg" ? String(+(+si).toFixed(6)) : String(+(+BuU.inputValue(kind, si)).toPrecision(12)) });
    inp.addEventListener("change", () => {
      const v = kind === "deg" ? parseFloat(inp.value) : BuU.parse(kind, inp.value);
      if (!isFinite(v) || (opts.gt0 && !(v > 0)) || (opts.ge0 && v < 0)) { inp.classList.add("bu-bad"); return; }
      inp.classList.remove("bu-bad");
      onSet(v);
      recompute();
    });
    return inp;
  }
  function selInput(id, options, value, onSet) {
    const s = el("select", { id }, options.map(([v, l]) => el("option", { value: v, selected: v === value }, [l])));
    s.addEventListener("change", () => { onSet(s.value); recompute(); });
    return s;
  }
  function renderSecForm() {
    const f = D.secForm; f.textContent = "";
    const nm = el("input", { type: "text", id: "buName", value: st.sec.name, "aria-label": "Section name" });
    nm.addEventListener("input", () => { st.sec.name = nm.value.trim(); });
    const mats = Object.keys(S.model.materials || {}).map(n => [n, n]);
    f.append(
      field("Name", nm),
      field("Material", selInput("buMaterial", mats, st.sec.material, v => { st.sec.material = v; renderSecForm(); })),
      field("Analysis axes", selInput("buAxes", [["geometric", "Geometric (x / y)"], ["principal", "Principal (major / minor)"]], st.sec.axes, v => { st.sec.axes = v; })),
      field("Torsion J", selInput("buTorsion", [["exact", "Exact (finite elements)"], ["auto", "Auto (open / Bredt cell)"], ["open", "Open Σ b·t³/3"], ["fem", "FEM (legacy = exact)"]], st.sec.torsion, v => { st.sec.torsion = v; })),
    );
    const curMat = (S.model.materials || {})[st.sec.material];
    if (!curMat || curMat.material_type !== "steel") {
      // no steel material picked: offer the material library (deliberate import)
      const add = el("button", { type: "button", class: "btn", id: "buAddSteel" }, ["Add steel grade…"]);
      add.addEventListener("click", () => buOpenMatLib(window.__sky, { done: res => {
        const got = (res || []).map(r => r.as || r.name).filter(n => (S.model.materials || {})[n]);
        if (got.length) st.sec.material = got[0];
        renderSecForm();
      } }));
      f.append(el("p", { class: "muted", id: "buMatHint" },
        [curMat ? `“${st.sec.material}” is not a steel material. ` : "No steel material in the model yet. ", add]));
    }
  }
  function renderCompForm() {
    const f = D.compForm; f.textContent = "";
    const c = st.sec.components[st.sel];
    if (!c) { f.appendChild(el("p", { class: "muted bu-empty", text: st.sec.components.length ? "Click a component to edit it." : "Add a plate, a database shape, a parametric shape or start from a template." })); return; }
    const kindTxt = c.kind === "plate" ? "Plate" : c.kind === "db" ? `${c.name} (${String(c.standard).toUpperCase()})` : BU_TLABEL[c.type] || c.type;
    const lab = el("input", { type: "text", id: "buCompLabel", value: c.label || "", placeholder: kindTxt });
    lab.addEventListener("change", () => { if (lab.value.trim()) c.label = lab.value.trim(); else delete c.label; renderList(); });
    f.append(el("div", { class: "bu-kind", text: kindTxt }), field("Label", lab));
    const du = BuU.label("dim");
    f.append(field("x (centre)", numInput("buX", "dim", c.x, v => { c.x = v; }), du), field("y (centre)", numInput("buY", "dim", c.y, v => { c.y = v; }), du));
    if (c.kind === "plate") {
      f.append(field("b (length)", numInput("buDim_b", "dim", c.b, v => { c.b = v; }, { gt0: true }), du),
        field("t (thickness)", numInput("buDim_t", "dim", c.t, v => { c.t = v; }, { gt0: true }), du),
        field("Angle", numInput("buAngle", "deg", c.angle, v => { c.angle = normAng(v); }), "°"));
    } else {
      f.append(field("Rotation", numInput("buAngle", "deg", c.rotation, v => { c.rotation = normAng(v); }), "°"));
      const mir = el("input", { type: "checkbox", id: "buMirrorCb", checked: !!c.mirror });
      mir.addEventListener("change", () => { c.mirror = mir.checked; recompute(); });
      f.append(field("Mirror", mir));
      if (c.kind === "param") {
        for (const k of BU_REQ_DIMS[c.type]) f.append(field(BU_DLABEL[k] || k, numInput("buDim_" + k, "dim", c.dims[k], v => { c.dims[k] = v; }, k === "s" ? { ge0: true } : { gt0: true }), du));
        if (c.type === "double_angle")
          f.append(field("Orientation", selInput("buDimOrient", [["LLBB", "LLBB"], ["SLBB", "SLBB"]], c.dims.orientation || "LLBB", v => { c.dims.orientation = v; })));
      } else {
        const dimTxt = Object.entries(c.dims || {}).filter(([, v]) => typeof v === "number").map(([k, v]) => `${k} ${fmtNum("dim", v, 4)}`).join(" · ");
        f.append(el("p", { class: "muted bu-dbdims", text: `${c.type} · ${dimTxt} ${du}` }));
        if (c.tab) f.append(field("Properties from", selInput("buPropsFrom", [["table", "Tabulated (database)"], ["geometry", "Geometry (no fillets)"]], c.props_from === "geometry" ? "geometry" : "table",
          v => { if (v === "geometry") c.props_from = "geometry"; else delete c.props_from; })));
      }
    }
    const mats = [["", `(section: ${st.sec.material})`], ...Object.keys(S.model.materials || {}).map(n => [n, n])];
    f.append(field("Material", selInput("buCompMat", mats, c.material || "", v => { if (v) c.material = v; else delete c.material; })));
  }
  function renderList() {
    const ul = D.list; ul.textContent = "";
    st.sec.components.forEach((c, i) => {
      const name = c.label || (c.kind === "plate" ? "Plate" : c.kind === "db" ? c.name : BU_TLABEL[c.type] || c.type);
      const li = el("li", { class: "bu-li" + (i === st.sel ? " is-sel" : ""), role: "option", "aria-selected": i === st.sel ? "true" : "false", "data-idx": String(i) },
        [el("span", { class: "bu-li-n", text: String(i + 1) }), el("span", { text: name }), c.material ? el("span", { class: "bu-li-m muted", text: c.material }) : null]);
      li.addEventListener("click", () => select(i));
      ul.appendChild(li);
    });
    if (!st.sec.components.length) ul.appendChild(el("li", { class: "muted bu-li-empty", text: "No components yet." }));
  }
  function select(i) { st.sel = i; renderList(); renderCompForm(); draw(); }

  /* ---------------- properties ---------------- */
  function recompute({ keepForms = false } = {}) {
    if (!st) return;
    st.err = "";
    try { st.props = st.sec.components.length ? buProps(st.sec, S.model.materials || {}) : null; }
    catch (e) { st.props = null; st.err = e.message; }
    st.srv = null;
    if (!keepForms) { renderList(); renderCompForm(); }
    renderProps(); draw();
    scheduleServer();
  }
  let srvTimer = 0;
  function scheduleServer() {
    clearTimeout(srvTimer);
    if (isMock() || !st || !st.props) return;
    const seq = ++st.srvSeq, snap = clone(st.sec);
    srvTimer = setTimeout(async () => {
      try {
        const r = await backend.properties(snap);
        if (st && st.srvSeq === seq && r && r.properties) { st.srv = r.properties; renderProps(); draw(); }
      } catch (e) { if (st && st.srvSeq === seq) { st.err = e.message; renderProps(); } }
    }, 350);
  }
  const shown = () => (st.srv || st.props);
  function renderProps() {
    const p = shown(), t = D.props;
    t.textContent = "";
    D.warn.textContent = "";
    buShowError(D.err, st.err);
    D.src.textContent = !p ? "" : st.srv ? "· backend ✓" : isMock() ? "· mock (closed form)" : "· client (closed form)";
    D.notes.textContent = st.sec.notes || "";
    if (!p) { t.appendChild(el("tr", {}, [el("td", { class: "muted", text: "—" })])); return; }
    const row = (lab, kind, v, id) => t.appendChild(el("tr", {}, [el("th", { text: lab }), el("td", { id: id || null, "data-si": v == null ? "" : String(v), text: fmtNum(kind, v) }), el("td", { class: "muted", text: kind === "none" ? "" : BuU.label(kind) })]));
    row("A", "area", p.A, "buPA");
    row("I33", "inertia", p.I33, "buPI33");
    row("I22", "inertia", p.I22, "buPI22");
    t.appendChild(el("tr", {}, [el("th", { text: "J" }), el("td", { id: "buPJ", "data-si": String(p.J), "data-method": p.J_method, text: fmtNum("inertia", p.J) }), el("td", { class: "muted", id: "buPJm", text: `${BuU.label("inertia")} · ${p.J_method}` })]));
    if (p.Cw != null) t.appendChild(el("tr", {}, [el("th", { text: "Cw" }), el("td", { id: "buPCw", "data-si": String(p.Cw), text: String(+(p.Cw * BuU.factor("dim") ** 6).toPrecision(4)) }), el("td", { class: "muted", text: `${BuU.label("dim")}⁶ · exact` })]));
    row("Centroid x", "dim", p.cx, "buPcx");
    row("Centroid y", "dim", p.cy, "buPcy");
    t.appendChild(el("tr", {}, [el("th", { text: "Principal θ" }), el("td", { id: "buPth", "data-si": String(p.theta), text: (+p.theta_deg.toFixed(3)).toString() }), el("td", { class: "muted", text: "° (major, CCW)" })]));
    row("I major", "inertia", p.I_major, "buPImaj");
    row("I minor", "inertia", p.I_minor, "buPImin");
    row("Ixy", "inertia", p.Ixy, "buPIxy");
    row("S33 (min)", "secmod", p.S33, "buPS33");
    row("S22 (min)", "secmod", p.S22, "buPS22");
    row("Z33", "secmod", p.Z33, "buPZ33");
    row("Z22", "secmod", p.Z22, "buPZ22");
    row("r33", "dim", p.r33, "buPr33");
    row("r22", "dim", p.r22, "buPr22");
    row("As2", "area", p.As2, "buPAs2");
    row("As3", "area", p.As3, "buPAs3");
    const sc = p.shear_centre;
    t.appendChild(el("tr", {}, [el("th", { text: "Shear centre" }), el("td", { id: "buPsc", text: sc ? `${fmtNum("dim", sc.x)}, ${fmtNum("dim", sc.y)}` : "n/a" }), el("td", { class: "muted", text: sc ? `${BuU.label("dim")} · ${sc.method}` : "use Exact" })]));
    for (const w of p.warnings || []) D.warn.appendChild(el("li", { text: w }));
  }

  /* ---------------- canvas ---------------- */
  function size() { const r = D.svg.getBoundingClientRect(); return [Math.max(200, r.width || 600), Math.max(200, r.height || 440)]; }
  const T = ([x, y]) => [st.view.ox + x * st.view.sc, st.view.oy - y * st.view.sc];
  const W = (px, py) => [(px - st.view.ox) / st.view.sc, (st.view.oy - py) / st.view.sc];
  function bboxAll() {
    let b = null;
    for (const c of st.sec.components) {
      let g; try { g = buGeom(c); } catch (e) { continue; }
      b = b ? [Math.min(b[0], g.bbox[0]), Math.max(b[1], g.bbox[1]), Math.min(b[2], g.bbox[2]), Math.max(b[3], g.bbox[3])] : g.bbox.slice();
    }
    return b;
  }
  function fit() {
    if (!st) return;
    const [w, h] = size(), b = bboxAll() || [-0.15, 0.15, -0.15, 0.15];
    const bw = Math.max(b[1] - b[0], 1e-3), bh = Math.max(b[3] - b[2], 1e-3);
    const sc = 0.75 * Math.min(w / bw, h / bh);
    st.view = { sc, ox: w / 2 - sc * (b[0] + b[1]) / 2, oy: h / 2 + sc * (b[2] + b[3]) / 2 };
    draw();
  }
  function zoom(f, px, py) {
    if (!st.view) fit();
    const [w, h] = size();
    if (px == null) { px = w / 2; py = h / 2; }
    const [wx, wy] = W(px, py);
    st.view.sc = Math.max(1, Math.min(1e6, st.view.sc * f));
    st.view.ox = px - wx * st.view.sc; st.view.oy = py + wy * st.view.sc;
    draw();
  }
  function gridStep() { let g = st.grid; while (g * st.view.sc < 6) g *= 5; return g; }
  function draw() {
    if (!st || !D.svg) return;
    if (!st.view) { fit(); return; }
    const svg = D.svg, [w, h] = size();
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.textContent = "";
    const defs = svgEl("defs");
    defs.innerHTML = ["2", "3"].map(k => `<marker id="buArr${k}" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0L8,4L0,8Z" class="bu-ax${k}f"/></marker>`).join("");
    svg.appendChild(defs);
    // grid
    const g = gridStep(), [x0, y1] = W(0, 0), [x1, y0] = W(w, h);
    const gG = svgEl("g", { class: "bu-grid" });
    let d = "", dM = "";
    for (let i = Math.ceil(x0 / g); i * g <= x1; i++) { const X = T([i * g, 0])[0].toFixed(1); (i % 5 ? (s => { d += s; }) : (s => { dM += s; }))(`M${X},0V${h}`); }
    for (let j = Math.ceil(y0 / g); j * g <= y1; j++) { const Y = T([0, j * g])[1].toFixed(1); (j % 5 ? (s => { d += s; }) : (s => { dM += s; }))(`M0,${Y}H${w}`); }
    gG.appendChild(svgEl("path", { d, class: "bu-grid-minor" }));
    gG.appendChild(svgEl("path", { d: dM, class: "bu-grid-major" }));
    const [oX, oY] = T([0, 0]);
    gG.appendChild(svgEl("path", { d: `M${oX},0V${h}M0,${oY}H${w}`, class: "bu-origin" }));
    svg.appendChild(gG);
    // components
    const mats = Object.keys(S.model.materials || {});
    st.sec.components.forEach((c, i) => {
      let geo; try { geo = buGeom(c); } catch (e) { return; }
      const mi = mats.indexOf(c.material || st.sec.material);
      const p = svgEl("path", { d: buPath(geo.pieces, T), class: `bu-comp bu-mat${mi % 4}` + (i === st.sel ? " is-sel" : ""), "fill-rule": "evenodd", "data-idx": String(i) });
      svg.appendChild(p);
    });
    // selection box + reference point
    const c = st.sec.components[st.sel];
    if (c) {
      try {
        const b = buGeom(c).bbox, [ax, ay] = T([b[0], b[3]]), [bx, by] = T([b[1], b[2]]);
        svg.appendChild(svgEl("rect", { x: ax - 3, y: ay - 3, width: bx - ax + 6, height: by - ay + 6, class: "bu-selbox" }));
        const [rx, ry] = T([c.x, c.y]);
        svg.appendChild(svgEl("path", { d: `M${rx - 5},${ry}H${rx + 5}M${rx},${ry - 5}V${ry + 5}`, class: "bu-ref" }));
      } catch (e) { /* invalid dims: no box */ }
    }
    // overlays: principal axes, local 2/3, centroid, shear centre
    const p = shown();
    if (p) {
      const [cx, cy] = T([p.cx, p.cy]), Lp = Math.max(w, h);
      const pa = (ang, cls) => { const ux = Math.cos(ang), uy = Math.sin(ang); svg.appendChild(svgEl("line", { x1: cx - ux * Lp, y1: cy + uy * Lp, x2: cx + ux * Lp, y2: cy - uy * Lp, class: cls })); };
      pa(p.theta, "bu-pax bu-pax-major"); pa(p.theta + Math.PI / 2, "bu-pax bu-pax-minor");
      const th = p.axes === "principal" ? p.theta : 0, L = 46;
      const ax = svgEl("g", { class: "bu-axes", id: "buAxes" });
      const a3 = [cx + L * Math.cos(th), cy - L * Math.sin(th)], a2 = [cx - L * Math.sin(th), cy - L * Math.cos(th)];
      ax.appendChild(svgEl("line", { x1: cx, y1: cy, x2: a3[0], y2: a3[1], class: "bu-ax3", "marker-end": "url(#buArr3)" }));
      ax.appendChild(svgEl("line", { x1: cx, y1: cy, x2: a2[0], y2: a2[1], class: "bu-ax2", "marker-end": "url(#buArr2)" }));
      const t3 = svgEl("text", { x: a3[0] + 5, y: a3[1] + 4, class: "bu-axl3" }); t3.textContent = "3";
      const t2 = svgEl("text", { x: a2[0] - 9, y: a2[1] + 2, class: "bu-axl2" }); t2.textContent = "2";
      ax.append(t3, t2);
      svg.appendChild(ax);
      const cg = svgEl("g", { class: "bu-cg", id: "buCentroid", "data-x": String(p.cx), "data-y": String(p.cy) });
      cg.innerHTML = `<circle cx="${cx}" cy="${cy}" r="5"/><path class="bu-cg-q" d="M${cx},${cy - 5}A5,5 0 0 1 ${cx + 5},${cy}L${cx},${cy}Z M${cx},${cy + 5}A5,5 0 0 1 ${cx - 5},${cy}L${cx},${cy}Z"/>`;
      svg.appendChild(cg);
      const sc = p.shear_centre;
      if (sc && Math.hypot(sc.x - p.cx, sc.y - p.cy) > 1e-6 * Math.max(p.b, p.h)) {
        const [sx, sy] = T([sc.x, sc.y]);
        const sg = svgEl("g", { class: "bu-sc" });
        sg.innerHTML = `<path d="M${sx},${sy - 5}L${sx + 5},${sy}L${sx},${sy + 5}L${sx - 5},${sy}Z"/><text x="${sx + 8}" y="${sy + 4}">SC</text>`;
        svg.appendChild(sg);
      }
    }
  }

  /* drag / pan / zoom */
  function wireCanvas(svg) {
    const pt = e => { const r = svg.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    svg.addEventListener("pointerdown", e => {
      svg.focus({ preventScroll: true });
      const [px, py] = pt(e);
      const t = e.target.closest && e.target.closest(".bu-comp");
      if (t && e.button === 0) {
        const i = +t.getAttribute("data-idx");
        if (i !== st.sel) select(i);
        const c = st.sec.components[i];
        let bb = null; try { bb = buGeom(c).bbox; } catch (err) { /* ignore */ }
        st.drag = { mode: "move", i, start: W(px, py), x: c.x, y: c.y, bb, moved: false };
      } else {
        if (e.button === 0 && st.sel >= 0) select(-1);
        st.drag = { mode: "pan", px, py, ox: st.view.ox, oy: st.view.oy };
      }
      svg.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    svg.addEventListener("pointermove", e => {
      const [px, py] = pt(e), [wx, wy] = W(px, py);
      D.coord.textContent = `x ${fmtNum("dim", wx)}  y ${fmtNum("dim", wy)} ${BuU.label("dim")}`;
      const dr = st && st.drag;
      if (!dr) return;
      if (dr.mode === "pan") { st.view.ox = dr.ox + px - dr.px; st.view.oy = dr.oy + py - dr.py; draw(); return; }
      const c = st.sec.components[dr.i];
      let nx = dr.x + wx - dr.start[0], ny = dr.y + wy - dr.start[1];
      if (st.snap && st.grid > 0) { nx = Math.round(nx / st.grid) * st.grid; ny = Math.round(ny / st.grid) * st.grid; }
      if (st.edge && dr.bb) { const [sx, sy] = edgeSnap(dr.i, dr.bb, nx - dr.x, ny - dr.y); nx += sx; ny += sy; }
      nx = +nx.toPrecision(12); ny = +ny.toPrecision(12);
      if (nx !== c.x || ny !== c.y) { c.x = nx; c.y = ny; dr.moved = true; liveRecompute(); }
    });
    const end = e => {
      const dr = st && st.drag;
      if (!dr) return;
      st.drag = null;
      try { svg.releasePointerCapture(e.pointerId); } catch (err) { /* already released */ }
      if (dr.mode === "move" && dr.moved) recompute();
    };
    svg.addEventListener("pointerup", end);
    svg.addEventListener("pointercancel", end);
    svg.addEventListener("wheel", e => { e.preventDefault(); const [px, py] = pt(e); zoom(Math.pow(1.15, -e.deltaY / 100), px, py); }, { passive: false });
  }
  let raf = 0;
  function liveRecompute() {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      if (!st) return;
      try { st.props = buProps(st.sec, S.model.materials || {}); st.err = ""; } catch (e) { st.props = null; st.err = e.message; }
      st.srv = null;
      renderProps(); draw();
      const c = st.sec.components[st.sel];
      if (c) {
        const ix = D.compForm.querySelector("#buX"), iy = D.compForm.querySelector("#buY");
        if (ix) ix.value = String(+(+BuU.inputValue("dim", c.x)).toPrecision(12));
        if (iy) iy.value = String(+(+BuU.inputValue("dim", c.y)).toPrecision(12));
      }
    });
  }
  /** Shift (dx, dy) so an edge / mid-line of the moving bbox meets one of
      another component (tolerance 8 px) — edges + corners snapping. */
  function edgeSnap(i, bb, dx, dy) {
    const tol = 8 / st.view.sc;
    const mx = [bb[0] + dx, bb[1] + dx, (bb[0] + bb[1]) / 2 + dx], my = [bb[2] + dy, bb[3] + dy, (bb[2] + bb[3]) / 2 + dy];
    let bx = null, by = null;
    st.sec.components.forEach((o, j) => {
      if (j === i) return;
      let b; try { b = buGeom(o).bbox; } catch (e) { return; }
      for (const tx of [b[0], b[1], (b[0] + b[1]) / 2]) for (const m of mx) { const dd = tx - m; if (Math.abs(dd) < tol && (bx == null || Math.abs(dd) < Math.abs(bx))) bx = dd; }
      for (const ty of [b[2], b[3], (b[2] + b[3]) / 2]) for (const m of my) { const dd = ty - m; if (Math.abs(dd) < tol && (by == null || Math.abs(dd) < Math.abs(by))) by = dd; }
    });
    return [bx || 0, by || 0];
  }

  /* ---------------- component commands ---------------- */
  function placeAbove(h) {
    const b = bboxAll();
    return b ? [(b[0] + b[1]) / 2, b[3] + h / 2] : [0, 0];
  }
  function pushComp(c) {
    st.sec.components.push(buNormComp(c));
    st.sel = st.sec.components.length - 1;
    recompute();
    fit();
  }
  function addPlate() {
    const b = 0.2, t = BuU.label("dim") === "in" ? 0.5 * 0.0254 : 0.012;
    const [x, y] = placeAbove(t);
    pushComp({ kind: "plate", b, t, x, y, angle: 0 });
  }
  function addParam(type) {
    const dims = clone(BU_DEF_DIMS[type]);
    let h = dims.d || dims.D || dims.t || 0.1;
    if (type === "plate") h = dims.t;
    const [x, y] = placeAbove(h);
    pushComp({ kind: "param", type, dims, x, y, rotation: 0, mirror: false });
  }
  function addDbRecord(std, rec) {
    const c = { kind: "db", standard: std, name: rec.name, x: 0, y: 0, rotation: 0, mirror: false };
    buSnap(c, rec);
    const h = (c.dims && (c.dims.d || c.dims.D)) || 0.2;
    [c.x, c.y] = placeAbove(h);
    pushComp(c);
  }
  function rotateSel(deg) {
    const c = st.sec.components[st.sel]; if (!c) return;
    if (c.kind === "plate") c.angle = normAng(c.angle + deg); else c.rotation = normAng(c.rotation + deg);
    recompute();
  }
  function mirrorSel() {
    const c = st.sec.components[st.sel]; if (!c) return;
    if (c.kind === "plate") c.angle = normAng(-c.angle); else c.mirror = !c.mirror;
    recompute();
  }
  function alignSel() {
    const c = st.sec.components[st.sel]; if (!c) return;
    let bb; try { bb = buGeom(c).bbox; } catch (e) { return; }
    const save = st.view.sc; st.view.sc = save / 6;        // generous tolerance (48 px)
    const [dx, dy] = edgeSnap(st.sel, bb, 0, 0);
    st.view.sc = save;
    if (dx || dy) { c.x = +(c.x + dx).toPrecision(12); c.y = +(c.y + dy).toPrecision(12); recompute(); }
  }
  function duplicateSel() {
    const c = st.sec.components[st.sel]; if (!c) return;
    let bb; try { bb = buGeom(c).bbox; } catch (e) { return; }
    const n = clone(c); n.x = +(c.x + (bb[1] - bb[0])).toPrecision(12); delete n.label;
    st.sec.components.splice(st.sel + 1, 0, n);
    st.sel += 1;
    recompute();
  }
  function deleteSel() {
    if (st.sel < 0) return;
    st.sec.components.splice(st.sel, 1);
    st.sel = Math.min(st.sel, st.sec.components.length - 1);
    recompute();
  }
  function nudge(dx, dy) {
    const c = st.sec.components[st.sel]; if (!c) return;
    c.x = +(c.x + dx).toPrecision(12); c.y = +(c.y + dy).toPrecision(12);
    recompute();
  }
  function onKey(e) {
    if (!st || st.overlay) return;
    const tag = (e.target && e.target.tagName) || "";
    if (/INPUT|SELECT|TEXTAREA/.test(tag)) return;
    if (!D.body || !document.getElementById(DLG_ID)) return;
    const k = e.key;
    let hit = true;
    if (k === "Delete" || k === "Backspace") deleteSel();
    else if (k === "r" || k === "R") rotateSel(e.shiftKey ? -90 : 90);
    else if (k === "m" || k === "M") mirrorSel();
    else if (k === "ArrowLeft") nudge(-st.grid, 0);
    else if (k === "ArrowRight") nudge(st.grid, 0);
    else if (k === "ArrowUp") nudge(0, st.grid);
    else if (k === "ArrowDown") nudge(0, -st.grid);
    else hit = false;
    if (hit) { e.preventDefault(); e.stopPropagation(); }
  }

  /* ---------------- overlays: database picker, template gallery ---------------- */
  function overlay(title, content, buttons) {
    closeOverlay();
    const box = el("div", { class: "bu-overlay", id: "buOverlay", role: "dialog", "aria-label": title }, [
      el("div", { class: "bu-ov-head" }, [el("b", { text: title }), buBtn("×", "icon-btn bu-ov-x", () => closeOverlay(), "Close")]),
      content, el("div", { class: "bu-ov-foot" }, buttons)]);
    D.body.appendChild(box);
    const onEsc = e => { if (e.key === "Escape") { e.stopImmediatePropagation(); e.preventDefault(); closeOverlay(); } };
    document.addEventListener("keydown", onEsc, true);
    st.overlay = { box, onEsc };
    return box;
  }
  function closeOverlay() {
    if (!st || !st.overlay) return;
    document.removeEventListener("keydown", st.overlay.onEsc, true);
    st.overlay.box.remove();
    st.overlay = null;
  }
  async function openDbPicker() {
    const stdSel = el("select", { id: "buDbStd", "aria-label": "Standard" });
    const famSel = el("select", { id: "buDbFam", "aria-label": "Family" });
    const q = el("input", { type: "search", id: "buDbQ", placeholder: "Search (e.g. W14)", "aria-label": "Search shapes" });
    const tbl = el("table", { class: "bu-dbtable", id: "buDbTable" });
    const msg = el("p", { class: "muted", id: "buDbMsg" });
    let rows = [], pick = null;
    const addB = buBtn("Add shape", "btn-primary", () => { if (pick) { const std = stdSel.value; closeOverlay(); addDbRecord(std, pick); } }, "Add the selected shape");
    addB.id = "buDbAdd"; addB.disabled = true;
    overlay("Database shape", el("div", { class: "bu-dbpick" }, [
      el("div", { class: "bu-row" }, [field("Standard", stdSel), field("Family", famSel)]), q, el("div", { class: "bu-dbscroll" }, [tbl]), msg]),
    [buBtn("Close", "", () => closeOverlay()), addB]);
    const render = () => {
      tbl.textContent = "";
      tbl.appendChild(el("tr", {}, ["Shape", `A (${BuU.label("area")})`, `I33 (${BuU.label("inertia")})`, `I22 (${BuU.label("inertia")})`].map(h => el("th", { text: h }))));
      for (const r of rows) {
        const pr = r.props || {};
        const tr = el("tr", { class: "bu-dbrow" + (pick === r ? " is-sel" : ""), "data-name": r.name }, [el("td", { text: r.name }),
          el("td", { text: pr.A != null ? fmtNum("area", pr.A) : "calc" }), el("td", { text: pr.I33 != null ? fmtNum("inertia", pr.I33) : "calc" }),
          el("td", { text: pr.I22 != null ? fmtNum("inertia", pr.I22) : "calc" })]);
        tr.addEventListener("click", () => { pick = r; addB.disabled = false; render(); });
        tr.addEventListener("dblclick", () => { pick = r; addB.click(); });
        tbl.appendChild(tr);
      }
    };
    const load = async () => {
      try {
        const r = await backend.shapes(stdSel.value, famSel.value, q.value);
        rows = r.shapes || []; pick = rows.includes(pick) ? pick : null; addB.disabled = !pick;
        msg.textContent = `${rows.length} of ${r.total ?? rows.length} shapes`;
        render();
      } catch (e) { msg.textContent = e.message; }
    };
    try {
      const s = await backend.standards();
      stdSel.textContent = "";
      for (const x of s.standards || []) stdSel.appendChild(el("option", { value: x.id, title: x.edition || "" }, [`${x.standard}${x.count != null ? ` (${x.count})` : ""}`]));
      const fams = async () => {
        const f = await backend.families(stdSel.value);
        famSel.textContent = "";
        famSel.appendChild(el("option", { value: "" }, ["All"]));
        for (const x of f.families || []) famSel.appendChild(el("option", { value: x }, [x]));
        await load();
      };
      stdSel.addEventListener("change", fams);
      famSel.addEventListener("change", load);
      let t = 0;
      q.addEventListener("input", () => { clearTimeout(t); t = setTimeout(load, 150); });
      await fams();
    } catch (e) { msg.textContent = e.message; }
  }
  function openTemplates() {
    const grid = el("div", { class: "bu-tplgrid", id: "buTplGrid" });
    const form = el("div", { class: "bu-tplform", id: "buTplForm" });
    const msg = el("p", { class: "field-error hidden", id: "buTplErr" });
    let cur = null, params = {};
    const useB = buBtn("Use template", "btn-primary", () => useTemplate(), "Replace the components with this template");
    useB.id = "buTplUse"; useB.disabled = true;
    overlay("Templates", el("div", { class: "bu-tpl" }, [grid, form, msg]), [buBtn("Close", "", () => closeOverlay()), useB]);
    for (const [id, t] of Object.entries(BU_TPL)) {
      const card = el("button", { type: "button", class: "bu-tplcard", "data-tpl": id, title: t.label });
      card.appendChild(thumb(id));
      card.appendChild(el("span", { text: t.label }));
      card.addEventListener("click", () => pickTpl(id));
      grid.appendChild(card);
    }
    function pickTpl(id) {
      cur = id; params = clone(BU_TPL[id].params); useB.disabled = false;
      grid.querySelectorAll(".bu-tplcard").forEach(c => c.classList.toggle("is-sel", c.dataset.tpl === id));
      form.textContent = "";
      for (const [k, v] of Object.entries(params)) {
        if (typeof v === "string") {
          const ch = BU_TPL_CH[k];
          if (ch) { const s = el("select", { id: "buTp_" + k }, ch.map(o => el("option", { value: o, selected: o === v }, [o]))); s.addEventListener("change", () => { params[k] = s.value; }); form.appendChild(field(k, s)); }
          else { const inp = el("input", { type: "text", id: "buTp_" + k, value: v }); inp.addEventListener("change", () => { params[k] = inp.value.trim(); }); form.appendChild(field(k, inp)); }
        } else {
          const inp = el("input", { type: "number", step: "any", id: "buTp_" + k, class: "bu-num", value: BuU.inputValue("dim", v) });
          inp.addEventListener("change", () => { const x = BuU.parse("dim", inp.value); if (isFinite(x) && x >= 0) { params[k] = x; inp.classList.remove("bu-bad"); } else inp.classList.add("bu-bad"); });
          form.appendChild(field(k, inp, BuU.label("dim")));
        }
      }
    }
    async function useTemplate() {
      if (!cur) return;
      try {
        const r = await backend.template(cur, params, st.sec.name || "BU1", st.sec.material);
        const s = r.section;
        st.sec.components = s.components.map(buNormComp);
        st.sec.template = s.template;
        if (s.notes) st.sec.notes = s.notes; else delete st.sec.notes;
        st.sel = st.sec.components.length ? 0 : -1;
        closeOverlay();
        recompute();
        fit();
      } catch (e) { buShowError(msg, e.message); }
    }
  }
  function thumb(id) {
    const svg = svgEl("svg", { class: "bu-thumb", viewBox: "0 0 90 70" });
    try {
      const s = buTemplate(id, {}, "T", "", buMockShape);
      const geos = s.components.map(buGeom);
      const b = [Math.min(...geos.map(g => g.bbox[0])), Math.max(...geos.map(g => g.bbox[1])), Math.min(...geos.map(g => g.bbox[2])), Math.max(...geos.map(g => g.bbox[3]))];
      const sc = 0.8 * Math.min(90 / (b[1] - b[0]), 70 / (b[3] - b[2]));
      const Tt = ([x, y]) => [45 + (x - (b[0] + b[1]) / 2) * sc, 35 - (y - (b[2] + b[3]) / 2) * sc];
      for (const g of geos) svg.appendChild(svgEl("path", { d: buPath(g.pieces, Tt), class: "bu-comp bu-mat0", "fill-rule": "evenodd" }));
    } catch (e) { /* thumbnail only */ }
    return svg;
  }

  /* ---------------- OK: write the working model ---------------- */
  async function commit() {
    const sec = buNorm(st.sec);
    const m = S.model;
    const name = sec.name;
    if (!name) { buShowError(D.err, "Enter a section name."); return; }
    if (/["\\]/.test(name)) { buShowError(D.err, "Names cannot contain quotes or backslashes."); return; }
    const other = (m.sections || {})[name];
    if (name !== st.origName && other) { buShowError(D.err, `“${name}” is already a frame section — pick another name.`); return; }
    if ((m.designer_sections || {})[name]) { buShowError(D.err, `“${name}” is a Section Designer section.`); return; }
    if (st.orig && st.orig === JSON.stringify(sec)) { buCloseDialog(DLG_ID); return; }   // unchanged: byte-identical
    const e0 = buValid(sec, m.materials || {});
    if (e0) { buShowError(D.err, e0); return; }
    D.dlg.el.querySelector("#buOk").disabled = true;
    let props;
    try {
      const r = await backend.properties(sec);
      props = r.properties;
      if (r.section) Object.assign(sec, buNorm(r.section));     // resolved snapshots
    } catch (e) {
      buShowError(D.err, e.message);
      const ok = D.dlg.el.querySelector("#buOk"); if (ok) ok.disabled = false;
      return;
    }
    if (st.origName && st.origName !== name) {
      if (m.sections[st.origName]) {
        m.sections[name] = m.sections[st.origName];
        delete m.sections[st.origName];
        for (const mm of m.members || []) if (mm.section === st.origName) mm.section = name;
        for (const s of Object.values(m.sections)) for (const g of (s && s.kind === "nonprismatic" && s.segments) || []) { if (g.start_section === st.origName) g.start_section = name; if (g.end_section === st.origName) g.end_section = name; }
      }
      if (m.builtup_sections) delete m.builtup_sections[st.origName];
    }
    m.builtup_sections = m.builtup_sections || {};
    m.builtup_sections[name] = sec;
    m.sections[name] = buFrame(sec, props, m.sections[name]);
    buCloseDialog(DLG_ID);
    sky.markDirty && sky.markDirty();
    try { sky.renderSectionMgr && sky.renderSectionMgr(); sky.renderProps && sky.renderProps(); } catch (e) { console.warn(e); }
    toast("Built-up section saved", `${name}: A ${BuU.sci("area", props.A, 3)} ${BuU.label("area")} · I33 ${BuU.sci("inertia", props.I33, 3)} ${BuU.label("inertia")} — usable in assignments now.`);
  }

  /* ---------------- hooks: menu + __sky ---------------- */
  sky.openBuiltup = (name, opts) => open(name, opts);
  sky.builtup = {
    open, close: () => buCloseDialog(DLG_ID), state: () => st, props: () => st && st.props, serverProps: () => st && st.srv,
    commit: () => commit(), select: i => select(i), fit: () => fit(), backend,
    computeProps: sec => buProps(buNorm(sec), S.model.materials || {}),
  };
  if (sky.menu && sky.menu.add)
    sky.menu.add("Define/Section Properties", { label: "Built-up Section…", act: "def-builtup", hint: "plates + shapes", fn: () => sky.openBuiltup() });
}
