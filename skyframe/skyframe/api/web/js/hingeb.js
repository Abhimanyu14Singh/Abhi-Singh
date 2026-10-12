/* SkyFrame — Wave B hinges UI (CONTRACT "Auto hinges from code tables",
   "Parametric interacting hinges", "Wall fiber hinges").

     Define › Section Properties › Frame Hinge Properties…  (B10 dialog)
         + types V3 / T (B10 machinery), PMM / M2M3 (interaction-surface
           editor below) and Auto (code-table editor below) — the B10
           dialog calls hbPropertyEditor for PMM / M2M3 / auto
     Assign › Frame › Hinges › Auto Hinges (Code Tables)…   asn-hb-auto
     Assign › Shell › Wall Hinges (Fiber P-M3)…            asn-hb-wall
     Display › Hinge Results…   extra info line (auto table / PMM / wall)

   Every dialog edits a draft and writes only what the user changed (OK with
   defaults leaves the model byte-identical); numbers go through units.js;
   ?mock=1 → js/mock_hingeb.js. */

import HBU from "./units.js";
import { el as hbEl, esc as hbEsc, dkForm as hbForm, dkGroup as hbGroup, dkAdvanced as hbAdvanced, dkNote as hbNote,
  dkDialog as hbDialog, dkDraft as hbDraft, dkCommit as hbCommit, uniqueName as hbUnique } from "./dlgkit.js";
import { b9num as hbNum, b9h as hbH, b9group as hbFs } from "./b9common.js";
import { HB_SUPPORT, HB_CODE_LABEL, HB_COMP_LABEL, HB_DOF_LABEL, HB_PARAM_DEFAULTS, HB_WALL_DEFAULTS, hbParamsFor,
  hbNeedsAxial, hbDefaultDistances, hbTableInfo, hbValidateProperty, hbEllipseAt, hbNormalizeWall, hbValidateWall,
  hbPierLabels, hbClone, hbIsNum } from "./hingeb_core.js";
import { hbMockPreview as hbMockPrev, hbMockSurface as hbMockSurf } from "./mock_hingeb.js";

let hbSky = null;
const hbIsMock = () => !!(hbSky && hbSky.store && hbSky.store.mock);
async function hbReq(path, body) {
  const res = await fetch(path, body === undefined ? undefined
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let data = null;
  try { data = await res.json(); } catch (e) { /* non-JSON */ }
  if (!res.ok) { const e = new Error((data && data.error) || `${res.status}`); e.status = res.status; throw e; }
  return data;
}
async function hbVia(live, mock) {
  if (!hbIsMock()) {
    try { return await live(); } catch (e) { if (e.status === 400) throw e; console.warn("hinge endpoint unavailable, using mock:", e.message); }
  }
  return mock();
}
const hbPreview = (model, prop, members, P_G) => hbVia(
  () => hbReq("/api/hinges/auto-preview", { model, property: prop, members, P_G }),
  () => hbMockPrev(model, { property: prop, members, P_G }));
const hbFmt = (kind, v, d = 3) => v == null || !isFinite(v) ? "—" : HBU.fmt(kind, v, d);
const hbOpts = (pairs, cur) => pairs.map(([v, l]) => `<option value="${hbEsc(v)}"${String(v) === String(cur) ? " selected" : ""}>${hbEsc(l)}</option>`).join("");

/* ================================================================
   B10 dialog: editors for PMM / M2M3 / auto properties
   ================================================================ */
export function hbPropertyEditor(sky, host, p, ctx = {}) {
  hbSky = hbSky || sky;
  const err = ctx.err;
  const show = m => ctx.showError ? ctx.showError(err, m || "") : null;
  const check = () => show(hbValidateProperty(ctx.name || "?", p));
  if (p.type === "auto") return autoEditor(sky, host, p, { ...ctx, check });
  return surfaceEditor(sky, host, p, { ...ctx, check });
}

function numInput(kind, si, id, onSet, attrs = {}) {
  const f = hbNum(kind, si, { id, ...attrs });
  f.el.addEventListener("change", () => onSet(f.get()));
  return f;
}

/* ---- PMM / M2M3: interaction surface + normalized M-θ ---- */
function surfaceEditor(sky, host, p, ctx) {
  const pmm = p.type === "PMM";
  const cols = hbH("div", { class: "uh-cols" });
  const colL = hbH("div", { class: "uh-col" }), colR = hbH("div", { class: "uh-col" });
  cols.append(colL, colR);
  host.appendChild(cols);
  /* surface rows: flattened (angle, P, M) */
  const gS = hbFs("Interaction surface — yield moments");
  gS.appendChild(hbH("p", { class: "muted b9-small", text: pmm
    ? "Curves at angles (0° = +M2, 90° = +M3), each a list of (P, M). P tension +, compression − (ETABS). The hinge yields on the ellipse through the 0°/90° moments at its axial force; other angles are reported against it."
    : "Yield moment per angle (0° = +M2, 90° = +M3). The hinge yields on the ellipse through the 0°/90° moments." }));
  const tbl = hbH("div", { class: "uh-bbt hb-surf", id: "hbSurfTable" });
  gS.appendChild(tbl);
  const rows = () => p.surface.curves.flatMap(c => c.points.map((q, j) => ({ c, j, q })));
  const renderSurf = () => {
    tbl.textContent = "";
    tbl.appendChild(hbH("div", { class: "uh-bbt-row head hb-row" + (pmm ? " hb-row3" : "") }, [
      hbH("span", { text: "Angle (°)" }), ...(pmm ? [hbH("span", { text: `P (${HBU.label("force")})` })] : []),
      hbH("span", { text: `M (${HBU.label("moment")})` }), hbH("span")]));
    rows().forEach(({ c, j, q }, i) => {
      const r = hbH("div", { class: "uh-bbt-row hb-row" + (pmm ? " hb-row3" : "") });
      const a = numInput("none", c.angle, `hbSurf_${i}_a`, v => {
        if (!hbIsNum(v)) return;
        const tgt = p.surface.curves.find(x => x.angle === v && x !== c);
        c.points.splice(j, 1);
        if (tgt) tgt.points.push(q); else p.surface.curves.push({ angle: v, points: [q] });
        p.surface.curves = p.surface.curves.filter(x => x.points.length).sort((x, y) => x.angle - y.angle);
        renderSurf(); redraw();
      });
      r.appendChild(a.el);
      if (pmm) r.appendChild(numInput("force", q[0], `hbSurf_${i}_P`, v => { q[0] = hbIsNum(v) ? v : NaN; redraw(); }).el);
      r.appendChild(numInput("moment", q[1], `hbSurf_${i}_M`, v => { q[1] = hbIsNum(v) ? v : NaN; redraw(); }).el);
      const x = hbH("button", { type: "button", class: "btn-small", text: "×", title: "Remove this point" });
      x.addEventListener("click", () => { c.points.splice(j, 1); p.surface.curves = p.surface.curves.filter(z => z.points.length); renderSurf(); redraw(); });
      r.appendChild(x);
      tbl.appendChild(r);
    });
    const add = hbH("button", { type: "button", class: "btn-small", id: "hbSurfAdd", text: "+ Point" });
    add.addEventListener("click", () => {
      const last = p.surface.curves[p.surface.curves.length - 1];
      if (pmm) { const lp = last.points[last.points.length - 1]; last.points.push([lp[0] + 500, 0]); }
      else p.surface.curves.push({ angle: Math.min(359, last.angle + 45), points: [[0, last.points[0][1]]] });
      renderSurf(); redraw();
    });
    const addA = hbH("button", { type: "button", class: "btn-small", id: "hbSurfAddAngle", text: "+ Angle" });
    addA.addEventListener("click", () => {
      const used = new Set(p.surface.curves.map(c => c.angle));
      let a = 45; while (used.has(a) && a < 360) a += 45;
      if (a >= 360) return;
      p.surface.curves.push({ angle: a, points: hbClone(p.surface.curves[0].points) });
      p.surface.curves.sort((x, y) => x.angle - y.angle);
      renderSurf(); redraw();
    });
    tbl.appendChild(hbH("div", { class: "b9-sp-list-btns" }, [add, addA]));
  };
  colL.appendChild(gS);
  /* preview */
  const gP = hbFs("M2 – M3 yield contour");
  const pRow = hbH("label", { class: "asn-num-row b9-num-row" });
  let Pv = pmm && p.axial && p.axial.source === "value" ? p.axial.value : 0;
  const pIn = numInput("force", Pv, "hbSurfP", v => { Pv = hbIsNum(v) ? v : 0; redraw(); });
  if (pmm) { pRow.append(hbH("span", { text: "at axial P" }), pIn.el, hbH("span", { class: "asn-unit", text: HBU.label("force") })); gP.appendChild(pRow); }
  const svgBox = hbH("div", { id: "hbSurfPlot" });
  const fitTxt = hbH("p", { class: "muted b9-small", id: "hbSurfFit" });
  gP.append(svgBox, fitTxt);
  colR.appendChild(gP);
  function redraw() {
    const e = hbEllipseAt(p.surface, Pv);
    svgBox.innerHTML = contourSvg(e);
    fitTxt.textContent = `M2y = ${hbFmt("moment", e.M2y)} · M3y = ${hbFmt("moment", e.M3y)} ${HBU.label("moment")}` +
      (e.fit.length > 4 ? " · " + e.fit.filter(f => f.angle % 90).map(f => `${f.angle}°: ${hbFmt("moment", f.M_surface)} vs ellipse ${hbFmt("moment", f.M_ellipse)}`).join(" · ") : "");
    ctx.check();
  }
  /* normalized backbone */
  const gB = hbFs("Moment – rotation (M / My vs plastic rotation)");
  const yd = (p.scale || {}).yield_deformation;
  const scaleSel = hbH("select", { id: "hbSurfScale" });
  scaleSel.innerHTML = hbOpts([["abs", "Absolute plastic rotation (rad)"], ["auto", "× member yield rotation (auto)"]], yd === "auto" ? "auto" : "abs");
  scaleSel.addEventListener("change", () => { p.scale = { yield_value: 1.0, yield_deformation: scaleSel.value === "auto" ? "auto" : null }; ctx.check(); });
  gB.appendChild(hbH("label", { class: "uh-fld" }, [hbH("span", { text: "Rotation scale" }), scaleSel]));
  const bbt = hbH("div", { class: "uh-bbt", id: "hbSurfBackbone" });
  const renderBB = () => {
    bbt.textContent = "";
    bbt.appendChild(hbH("div", { class: "uh-bbt-row head" }, [hbH("span", { text: "Pt" }), hbH("span", { text: "Plastic rot." }), hbH("span", { text: "M / My" })]));
    p.backbone.forEach((q, i) => {
      const r = hbH("div", { class: "uh-bbt-row" }, [hbH("b", { text: "ABCDEF"[i] })]);
      if (i === 0) r.append(hbH("span", { class: "muted", text: "0" }), hbH("span", { class: "muted", text: "0" }));
      else for (const j of [0, 1]) r.appendChild(numInput("none", q[j], `hbSurfBB_${i}_${j}`, v => { q[j] = hbIsNum(v) ? Math.abs(v) : NaN; ctx.check(); }).el);
      bbt.appendChild(r);
    });
    const add = hbH("button", { type: "button", class: "btn-small", id: "hbSurfBBAdd", text: "+ Point" });
    add.addEventListener("click", () => { if (p.backbone.length >= 6) return; const l = p.backbone[p.backbone.length - 1]; p.backbone.push([l[0] + 0.01, l[1]]); renderBB(); ctx.check(); });
    const rm = hbH("button", { type: "button", class: "btn-small", id: "hbSurfBBDel", text: "− Point" });
    rm.addEventListener("click", () => { if (p.backbone.length > 2) { p.backbone.pop(); renderBB(); ctx.check(); } });
    bbt.appendChild(hbH("div", { class: "b9-sp-list-btns" }, [add, rm]));
  };
  gB.appendChild(bbt);
  gB.appendChild(hbH("p", { class: "muted b9-small", text: "Coupled elliptical plasticity with linear hardening from B → C; points past C are state markers unless Drop strength = Drops (below)." }));
  colL.appendChild(gB);
  /* acceptance + axial + stiffness */
  const gA = hbFs("Acceptance (plastic rotation)");
  for (const k of ["IO", "LS", "CP"]) {
    const v = (p.acceptance || {})[k];
    const f = numInput("none", v == null ? null : v, `hbSurfAcc${k}`, x => {
      const a = { ...(p.acceptance || {}) };
      if (x == null) delete a[k]; else a[k] = x;
      p.acceptance = Object.keys(a).length ? a : null; ctx.check();
    });
    gA.appendChild(hbH("label", { class: "asn-num-row b9-num-row" }, [hbH("span", { text: k }), f.el, hbH("span", { class: "asn-unit", text: "rad / SF" })]));
  }
  colR.appendChild(gA);
  if (pmm) {
    const gX = hbFs("Axial force P for the yield moments");
    p.axial = p.axial || { source: "gravity", value: null, gravity: null };
    const src = hbH("select", { id: "hbSurfAxSrc" });
    src.innerHTML = hbOpts([["gravity", "From the case gravity (pushover gravity loads)"], ["value", "Fixed value"],
      ["analysis", "Updated every step (starts at gravity)"]], p.axial.source);   // wave C: P-M capacity tracks P
    const val = numInput("force", p.axial.value, "hbSurfAxVal", v => { p.axial.value = v; Pv = hbIsNum(v) ? v : Pv; pIn.set(Pv); redraw(); });
    const valRow = hbH("label", { class: "asn-num-row b9-num-row" }, [hbH("span", { text: "P (compression −)" }), val.el, hbH("span", { class: "asn-unit", text: HBU.label("force") })]);
    const sync = () => { valRow.style.display = p.axial.source === "value" ? "" : "none"; };
    src.addEventListener("change", () => { p.axial = { source: src.value, value: src.value === "value" ? (val.get() ?? 0) : null, gravity: p.axial.gravity ?? null }; sync(); ctx.check(); });
    gX.append(hbH("label", { class: "uh-fld" }, [hbH("span", { text: "Source" }), src]), valRow);
    sync();
    colR.appendChild(gX);
  }
  { // wave C: strength drop past C (capacity updates, engine/pmm_update.py)
    const gD = hbFs("Strength past C");
    const ds = hbH("select", { id: "hbSurfDrop" });
    ds.innerHTML = hbOpts([["holds", "Holds (B → C hardening continues)"], ["drops", "Drops (follows C → D → E, residual past E)"]], p.drop_strength || "holds");
    ds.addEventListener("change", () => { p.drop_strength = ds.value; ctx.check(); });
    gD.append(hbH("label", { class: "uh-fld" }, [hbH("span", { text: "Drop strength" }), ds]),
      hbH("p", { class: "muted b9-small", text: "Drops / axial ‘updated every step’ drive the yield moments step by step (capacity lags by one step)." }));
    colR.appendChild(gD);
  }
  const gK = hbFs("Hinge elastic stiffness");
  const kf = numInput("rot_stiffness", p.k_elastic, "hbSurfKe", v => { p.k_elastic = v == null ? null : v; ctx.check(); }, { placeholder: "10 × 6EI/L" });
  gK.appendChild(hbH("label", { class: "asn-num-row b9-num-row" }, [hbH("span", { text: "k elastic (blank = default)" }), kf.el, hbH("span", { class: "asn-unit", text: HBU.label("rot_stiffness") })]));
  colR.appendChild(gK);
  renderSurf(); renderBB(); redraw();
  return true;
}

function contourSvg(e) {
  const W = 300, H = 220, cx = W / 2, cy = H / 2;
  const pts = e.fit.map(f => [f.M_surface * Math.cos(f.angle * Math.PI / 180), f.M_surface * Math.sin(f.angle * Math.PI / 180)]);
  const R = Math.max(1e-9, e.M2y, e.M3y, ...pts.map(q => Math.hypot(q[0], q[1]))) * 1.15;
  const s = Math.min(W, H) / 2 / R;
  let svg = `<svg viewBox="0 0 ${W} ${H}" class="uh-plot" role="img" aria-label="M2-M3 yield contour">`;
  svg += `<line x1="0" y1="${cy}" x2="${W}" y2="${cy}" class="uh-axis"/><line x1="${cx}" y1="0" x2="${cx}" y2="${H}" class="uh-axis"/>`;
  if (e.M2y > 0 && e.M3y > 0) svg += `<ellipse cx="${cx}" cy="${cy}" rx="${(e.M2y * s).toFixed(1)}" ry="${(e.M3y * s).toFixed(1)}" class="uh-bb"/>`;
  for (const [x, y] of pts) svg += `<circle cx="${(cx + x * s).toFixed(1)}" cy="${(cy - y * s).toFixed(1)}" r="3.5" class="uh-mk-pt"/>`;
  svg += `<text x="${W - 4}" y="${cy - 4}" text-anchor="end" class="uh-tick">M2</text><text x="${cx + 4}" y="12" class="uh-tick">M3</text>`;
  return svg + "</svg>";
}

/* ---- auto (code-table) property editor ---- */
function autoEditor(sky, host, p, ctx) {
  const a = p.auto;
  a.params = a.params || {};
  const box = hbH("div", { class: "uh-cols" });
  const colL = hbH("div", { class: "uh-col" }), colR = hbH("div", { class: "uh-col" });
  box.append(colL, colR);
  host.appendChild(box);
  const g = hbFs("Code table");
  const code = hbH("select", { id: "hbAutoCode" }), comp = hbH("select", { id: "hbAutoComp" }), dof = hbH("select", { id: "hbAutoDof" });
  const info = hbH("p", { class: "muted b9-small", id: "hbAutoInfo" });
  const fill = () => {
    code.innerHTML = hbOpts(Object.keys(HB_SUPPORT).map(c => [c, HB_CODE_LABEL[c]]), a.code);
    const comps = HB_SUPPORT[a.code] || {};
    if (!comps[a.component]) a.component = Object.keys(comps)[0];
    comp.innerHTML = hbOpts(Object.keys(comps).map(c => [c, HB_COMP_LABEL[c]]), a.component);
    const dofs = comps[a.component] || [];
    if (!dofs.includes(a.dof)) a.dof = dofs[0];
    dof.innerHTML = hbOpts(dofs.map(d => [d, HB_DOF_LABEL[d] || d]), a.dof);
    const t = hbTableInfo(a.code, a.component);
    info.innerHTML = `${hbEsc(t.table)} — ${hbEsc(t.clause)}. ` + (t.verified ? "<b>Verified</b> table." : "<b>Preliminary</b>: transcription not cross-checked against the printed standard.");
    renderParams();
    ctx.check();
  };
  code.addEventListener("change", () => { a.code = code.value; fill(); });
  comp.addEventListener("change", () => { a.component = comp.value; a.params = {}; fill(); });
  dof.addEventListener("change", () => { a.dof = dof.value; ctx.check(); });
  g.append(hbH("label", { class: "uh-fld" }, [hbH("span", { text: "Code" }), code]), hbH("label", { class: "uh-fld" }, [hbH("span", { text: "Component / table" }), comp]),
    hbH("label", { class: "uh-fld" }, [hbH("span", { text: "Degree of freedom" }), dof]), info);
  colL.appendChild(g);
  const gX = hbFs("Axial load P_G (columns)");
  const src = hbH("select", { id: "hbAutoAxSrc" });
  src.innerHTML = hbOpts([["gravity", "From the case gravity (linear gravity solve)"], ["value", "Fixed value"]], a.P == null ? "gravity" : "value");
  const pv = numInput("force", a.P, "hbAutoP", v => { if (src.value === "value") { a.P = v; ctx.check(); } });
  const pRow = hbH("label", { class: "asn-num-row b9-num-row" }, [hbH("span", { text: "P_G (compression +)" }), pv.el, hbH("span", { class: "asn-unit", text: HBU.label("force") })]);
  const syncP = () => { pRow.style.display = src.value === "value" ? "" : "none"; };
  src.addEventListener("change", () => { a.P = src.value === "value" ? (pv.get() ?? 0) : null; syncP(); ctx.check(); });
  gX.append(hbH("label", { class: "uh-fld" }, [hbH("span", { text: "Source" }), src]), pRow);
  syncP();
  colL.appendChild(gX);
  const gH = hbFs("Hysteresis");
  const hy = hbH("select", { id: "hbAutoHyst" });
  hy.innerHTML = hbOpts([["kinematic", "Kinematic"], ["takeda", "Takeda"], ["pivot", "Pivot"], ["isotropic", "Isotropic"], ["concrete", "Concrete"]], p.hysteresis || "kinematic");
  hy.addEventListener("change", () => { p.hysteresis = hy.value; ctx.check(); });
  gH.appendChild(hbH("label", { class: "uh-fld" }, [hbH("span", { text: "Rule (two-sided brace backbones use Takeda when kinematic is chosen)" }), hy]));
  colL.appendChild(gH);
  const gP = hbFs("Table parameters (blank = default)");
  colR.appendChild(gP);
  function renderParams() {
    gP.querySelectorAll(".b9-num-row, .uh-fld").forEach(n => n.remove());
    for (const [k, label, kind, , defTxt] of hbParamsFor(a.code, a.component)) {
      const def = HB_PARAM_DEFAULTS[k];
      const f = numInput(kind, a.params[k] ?? null, "hbAutoPar_" + k, v => {
        if (v == null) delete a.params[k]; else a.params[k] = v;
        ctx.check();
      }, { placeholder: def == null ? (defTxt || "auto") : HBU.inputValue(kind, def) });
      gP.appendChild(hbH("label", { class: "asn-num-row b9-num-row" }, [hbH("span", { text: label }), f.el, hbH("span", { class: "asn-unit", text: HBU.label(kind) })]));
    }
    if (a.component === "steel_brace") {
      const sh = hbH("select", { id: "hbAutoShape" });
      sh.innerHTML = hbOpts([["auto", "From the section name"], ["W", "W / I"], ["2L_in", "2L in-plane"], ["2L_out", "2L out-of-plane"], ["HSS", "HSS rectangular"], ["pipe", "Pipe / round HSS"], ["angle", "Single angle"]], a.params.shape || "auto");
      sh.addEventListener("change", () => { if (sh.value === "auto") delete a.params.shape; else a.params.shape = sh.value; ctx.check(); });
      gP.appendChild(hbH("label", { class: "uh-fld" }, [hbH("span", { text: "Brace shape class" }), sh]));
    }
  }
  /* preview on the members using it / selected */
  const gV = hbFs("Preview");
  const pvBox = hbH("div", { class: "table-scroll", id: "hbAutoPreview" });
  const btn = hbH("button", { type: "button", class: "btn-small", id: "hbAutoPreviewBtn", text: "Preview on selected frames" });
  btn.addEventListener("click", () => runPreview(sky, p, pvBox, a.P ?? 0));
  gV.append(hbH("p", { class: "muted b9-small", text: "Generated backbones at the fixed P_G (0 when from gravity) — the analysis uses the gravity solve." }), btn, pvBox);
  colR.appendChild(gV);
  fill();
  return true;
}

async function runPreview(sky, prop, box, P_G) {
  const S = sky.store;
  const sel = (S.selection || []).filter(r => r.type === "member").map(r => r.uid);
  const uids = (sel.length ? sel : (S.model.members || []).map(m => m.uid)).slice(0, 8);
  if (!uids.length) { box.innerHTML = `<p class="muted b9-small">No frames to preview.</p>`; return; }
  box.innerHTML = `<p class="muted b9-small">Computing…</p>`;
  try {
    const res = await hbPreview(S.model, prop, uids, P_G || 0);
    box.innerHTML = previewTable(res.previews || []);
  } catch (e) { box.innerHTML = `<p class="field-error">${hbEsc(e.message)}</p>`; }
}

function previewTable(pv) {
  const rows = [];
  for (const m of pv) {
    if (!m.hinges.length) { rows.push(`<tr><td class="txt">${hbEsc(m.uid)}</td><td class="txt" colspan="8">${hbEsc(m.reason || "not sized")}</td></tr>`); continue; }
    for (const h of m.hinges) {
      const bb = h.property.backbone, pos = Array.isArray(bb) ? bb : bb.positive;
      const fk = h.type === "P" || h.type === "V2" || h.type === "V3" ? "force" : "moment";
      const dk = fk === "force" ? "disp" : "rotation";
      const acc = h.property.acceptance || {};
      rows.push(`<tr><td class="txt">${hbEsc(m.uid)}</td><td class="txt">${hbEsc(h.type)}</td><td>${hbFmt(fk, pos[1][1])}</td>` +
        `<td>${hbFmt(dk, pos[2] ? pos[2][0] : null, 4)}</td><td>${hbFmt(dk, pos[pos.length - 1][0], 4)}</td>` +
        `<td>${hbFmt(dk, acc.IO, 4)}</td><td>${hbFmt(dk, acc.LS, 4)}</td><td>${hbFmt(dk, acc.CP, 4)}</td>` +
        `<td class="txt">${h.meta && h.meta.preliminary ? "preliminary" : "verified"}</td></tr>`);
    }
  }
  return `<table class="data-table" id="hbPreviewTable"><thead><tr><th class="txt">Frame</th><th class="txt">DOF</th><th>F<sub>B</sub></th><th>a</th><th>b</th><th>IO</th><th>LS</th><th>CP</th><th class="txt">Table</th></tr></thead><tbody>${rows.join("")}</tbody></table>`;
}

/* ================================================================
   Assign › Frame › Hinges › Auto Hinges (Code Tables)…
   ================================================================ */
export function hbOpenAutoAssign(sky) {
  hbSky = hbSky || sky;
  const S = sky.store;
  const sel = (S.selection || []).filter(r => r.type === "member").map(r => (S.model.members || []).find(m => m.uid === r.uid)).filter(Boolean);
  if (!sel.length)
    return hbDialog({ id: "hbAutoAssign", title: "Assign Frame Hinges — Auto", size: "sm", noCancel: true, iconId: "exleaf-frames",
      body: [hbNote("Nothing to assign — select one or more <b>frame objects</b> first, then choose this command again.")] });
  const kinds = new Set(sel.map(m => m.kind));
  const guessComp = kinds.size === 1 && kinds.has("brace") ? "steel_brace" : kinds.has("column") && kinds.size === 1 ? "steel_column" : "steel_beam";
  const V = { code: "ASCE41-17", component: guessComp, dof: HB_SUPPORT["ASCE41-17"][guessComp][0], loc: "", custom: "", axial: "gravity", P: 0,
    name: "", hysteresis: "kinematic" };
  const P = {};                                       // params (only changed keys)
  const F = hbForm();
  const tblInfo = hbH("p", { class: "muted dk-note", id: "hbAssignTable" });
  const locOpts = () => [["", `Default (${hbDefaultDistances(V.component).join(", ")})`], ["0,1", "Both ends (0, 1)"], ["0.5", "Mid-length (0.5)"],
    ["0.05,0.95", "Near ends (0.05, 0.95)"], ["custom", "Custom…"]];
  const nameOf = () => hbUnique(new Set(Object.keys(S.model.hinge_properties || {})), `AUTO_${V.code}_${V.component}_${V.dof}`.replace(/[^A-Za-z0-9_]/g, "").toUpperCase());
  V.name = nameOf();
  const syncInfo = () => {
    const t = hbTableInfo(V.code, V.component);
    tblInfo.innerHTML = `${hbEsc(t.table)} — ${hbEsc(t.clause)}. ` + (t.verified ? "<b>Verified</b> table." : "<b>Preliminary</b>: values not cross-checked against the printed standard.");
  };
  const paramBox = hbH("div", { id: "hbAssignParams" });
  const renderParams = () => {
    F.forget(paramBox);
    paramBox.textContent = "";
    for (const [k, label, kind, , defTxt] of hbParamsFor(V.code, V.component)) {
      const def = HB_PARAM_DEFAULTS[k];
      paramBox.appendChild(F.num(P, k, { label, kind, id: "hbAP_" + k, allowNull: true, nullText: def == null ? (defTxt || "auto") : HBU.inputValue(kind, def),
        def: def == null ? undefined : def, min: ["alpha_rho_sx", "rho_d", "rho_prime", "av"].includes(k) ? 0 : undefined,
        gt: ["alpha_rho_sx", "rho_d", "rho_prime", "av"].includes(k) ? undefined : 0,
        write: v => { if (v == null) delete P[k]; else P[k] = v; } }));
    }
    if (V.component === "steel_brace")
      paramBox.appendChild(F.select(P, "shape", [["", "From the section name"], ["W", "W / I"], ["2L_in", "2L in-plane"], ["2L_out", "2L out-of-plane"],
        ["HSS", "HSS rectangular"], ["pipe", "Pipe / round HSS"], ["angle", "Single angle"]], { label: "Brace shape class", id: "hbAP_shape",
        write: v => { if (v) P.shape = v; else delete P.shape; } }));
  };
  const refresh = () => { syncInfo(); renderParams(); F.refresh(); };
  const comps = () => Object.keys(HB_SUPPORT[V.code]).map(c => [c, HB_COMP_LABEL[c]]);
  const dofs = () => (HB_SUPPORT[V.code][V.component] || []).map(d => [d, HB_DOF_LABEL[d] || d]);
  const pvBox = hbH("div", { class: "table-scroll", id: "hbAssignPreview" });
  const body = [
    hbGroup("Hinge Table", [
      F.select(V, "code", Object.keys(HB_SUPPORT).map(c => [c, HB_CODE_LABEL[c]]), { label: "Code", id: "hbAssignCode", write: v => {
        V.code = v; if (!HB_SUPPORT[v][V.component]) V.component = Object.keys(HB_SUPPORT[v])[0];
        if (!HB_SUPPORT[v][V.component].includes(V.dof)) V.dof = HB_SUPPORT[v][V.component][0]; V.name = nameOf(); refresh(); } }),
      F.select(V, "component", comps, { label: "Component", id: "hbAssignComp", write: v => {
        V.component = v; V.dof = HB_SUPPORT[V.code][v][0]; for (const k of Object.keys(P)) delete P[k]; V.name = nameOf(); refresh(); } }),
      F.select(V, "dof", dofs, { label: "Degree of freedom", id: "hbAssignDof", write: v => { V.dof = v; V.name = nameOf(); F.refresh(); } }),
      tblInfo,
    ]),
    hbGroup("Hinge Locations", [
      F.select(V, "loc", locOpts, { label: "Relative distance", id: "hbAssignLoc", write: v => { V.loc = v; } }),
      F.text(V, "custom", { label: "Custom distances", id: "hbAssignCustom", required: false, placeholder: "e.g. 0.1, 0.9",
        hint: "comma-separated, each in [0, 1]; used with Custom…",
        validate: v => !v ? "" : v.split(",").every(x => { const n = +x; return x.trim() !== "" && isFinite(n) && n >= 0 && n <= 1; }) ? "" : "Enter numbers in [0, 1] separated by commas." }),
    ]),
    hbGroup("Axial Load P_G", [
      F.select(V, "axial", [["gravity", "From the case gravity (linear gravity solve)"], ["value", "Fixed value"],
        ["analysis", "Gravity, updated every step (coupled P-M2-M3; PMM only)"]], { label: "Source", id: "hbAssignAxial" }),   // wave C
      F.num(V, "P", { label: "P_G (compression +)", kind: "force", id: "hbAssignP", hint: "used with Fixed value" }),
    ], { note: "Column tables size the P-M interaction and a / b / c at P_G. The gravity solve uses the hinged case's gravity loads (pushover gravity)." }),
    hbAdvanced([paramBox, F.select(V, "hysteresis", [["kinematic", "Kinematic"], ["takeda", "Takeda"], ["pivot", "Pivot"], ["isotropic", "Isotropic"], ["concrete", "Concrete"]],
      { label: "Hysteresis", id: "hbAssignHyst" })], { summary: "Advanced — table parameters", countFn: () => Object.keys(P).length + (V.hysteresis !== "kinematic" ? 1 : 0) }),
    hbGroup("Property", [F.text(V, "name", { label: "Hinge property name", id: "hbAssignName",
      validate: v => (S.model.hinge_properties || {})[v] ? `“${v}” exists — it will be replaced.` : "" })]),
    hbGroup("Preview", [pvBox], { note: "Generated backbones at the fixed P_G (0 when from gravity), per selected frame." }),
  ];
  const buildProp = () => {
    const params = {};
    for (const [k, v] of Object.entries(P)) if (v != null && v !== "") params[k] = v;
    const prop = { type: "auto", auto: { code: V.code, component: V.component, dof: V.dof, params, gravity: null, P: V.axial === "value" ? (V.P || 0) : null },
      hysteresis: V.hysteresis || "kinematic", k_elastic: null, hysteresis_params: null };
    if (V.axial === "analysis" && V.dof === "PMM") prop.auto.axial_update = true;   // wave C: capacity tracks P
    return prop;
  };
  const distances = () => {
    if (V.loc === "custom") return (V.custom || "").split(",").map(x => +x).filter(x => isFinite(x));
    if (V.loc) return V.loc.split(",").map(Number);
    return hbDefaultDistances(V.component);
  };
  const doPreview = async () => runPreview({ store: { ...S, selection: sel.map(m => ({ type: "member", uid: m.uid })) } }, buildProp(), pvBox, V.axial === "value" ? V.P : 0);
  const pvBtn = hbH("button", { type: "button", class: "btn", id: "hbAssignPreviewBtn", text: "Preview" });
  pvBtn.addEventListener("click", doPreview);
  const apply = () => {
    const prop = buildProp();
    const e = hbValidateProperty(V.name, prop);
    if (e) return e;
    const ds = [...new Set(distances())].sort((x, y) => x - y);
    if (!ds.length || ds.some(d => !(d >= 0 && d <= 1))) return "Enter at least one relative distance in [0, 1].";
    const name = String(V.name || "").trim();
    if (!name) return "Enter a property name.";
    const D = hbDraft(sky);
    D.hinge_properties = { ...(D.hinge_properties || {}), [name]: prop };
    const uids = new Set(sel.map(m => m.uid));
    for (const mm of D.members || []) {
      if (!uids.has(mm.uid)) continue;
      if ((mm.axial_limit || "both") !== "both") continue;
      mm.hinges = ds.map(d => ({ property: name, relative_distance: d }));
    }
    hbCommit(sky, D);
    return "";
  };
  syncInfo(); renderParams();
  return hbDialog({ id: "hbAutoAssign", title: "Assign Frame Hinges — Auto (Code Tables)", size: "lg", form: F, body, iconId: "exleaf-frames",
    intro: `${sel.length} frame object${sel.length > 1 ? "s" : ""} selected. ETABS Auto hinges: the backbone, acceptance criteria and P-M interaction are generated per member from the chosen code table; the property replaces the members' hinge lists.`,
    extra: [pvBtn], onOk: () => { const m = apply(); if (m) return m; }, onApply: () => apply() });
}

/* ================================================================
   Assign › Shell › Wall Hinges (Fiber P-M3)…
   ================================================================ */
export function hbOpenWallHinges(sky) {
  hbSky = hbSky || sky;
  const S = sky.store;
  const m = S.model;
  const selWalls = (S.selection || []).filter(r => r.type === "shell").map(r => (m.shells || []).find(s => s.uid === r.uid)).filter(s => s && s.kind === "wall");
  let labels = hbPierLabels(m);
  const fromSel = [...new Set(selWalls.map(w => w.pier || (m.auto_pier_walls ? w.uid : "")).filter(Boolean))];
  const unlabeled = selWalls.filter(w => !(w.pier || (m.auto_pier_walls ? w.uid : "")));
  const all = [...new Set([...labels, ...unlabeled.map(w => w.uid)])];
  if (!all.length)
    return hbDialog({ id: "hbWallHinges", title: "Wall Hinges (Fiber P-M3)", size: "sm", noCancel: true, iconId: "exleaf-shells",
      body: [hbNote("The model has no shell walls. Draw walls first; a wall hinge acts per pier label per story.")] });
  const wh = hbClone(m.wall_hinges || {});
  const V = { pier: fromSel[0] || (unlabeled[0] && unlabeled[0].uid) || all[0] };
  const F = hbForm();
  const box = hbH("div", { id: "hbWallBox" });
  const state = {};                                   // pier -> {on, def}
  for (const lab of all) state[lab] = { on: !!wh[lab], def: hbNormalizeWall(wh[lab] || {}), touched: false };
  const storyNames = (m.stories || []).map(s => s.name);
  const render = () => {
    F.forget(box);
    box.textContent = "";
    const st = state[V.pier];
    const d = st.def;
    const mark = () => { st.touched = true; };
    box.appendChild(hbGroup("Wall Hinge", [
      F.check(st, "on", { label: "Fiber P-M3 hinge on this pier (hinged cases only)", id: "hbWallOn", write: v => { st.on = v; mark(); } }),
    ]));
    box.appendChild(hbGroup("Reinforcement & Materials", [
      F.num(d, "rho_v", { label: "Distributed vertical steel ratio", id: "hbWallRhoV", min: 0, lt: 0.1, def: HB_WALL_DEFAULTS.rho_v, write: v => { d.rho_v = v; mark(); } }),
      F.num(d, "rho_b", { label: "Boundary steel ratio", id: "hbWallRhoB", min: 0, lt: 0.1, def: HB_WALL_DEFAULTS.rho_b, write: v => { d.rho_b = v; mark(); } }),
      F.num(d, "boundary_ratio", { label: "Boundary length / wall length", id: "hbWallLb", gt: 0, max: 0.5, def: HB_WALL_DEFAULTS.boundary_ratio, write: v => { d.boundary_ratio = v; mark(); } }),
      F.num(d, "fy", { label: "Rebar fy", kind: "stress", id: "hbWallFy", gt: 0, def: HB_WALL_DEFAULTS.fy, write: v => { d.fy = v; mark(); } }),
      F.num(d, "fc", { label: "Concrete f'c", kind: "stress", id: "hbWallFc", allowNull: true, nullText: "from material", gt: 0, write: v => { d.fc = v; mark(); } }),
    ]));
    box.appendChild(hbAdvanced([
      F.num(d, "n_fibers", { label: "Fibers along the length", id: "hbWallNf", int: true, min: 4, max: 200, def: HB_WALL_DEFAULTS.n_fibers, write: v => { d.n_fibers = v; mark(); } }),
      F.num(d, "lp_ratio", { label: "Hinge length / wall length", id: "hbWallLp", gt: 0, max: 2, def: HB_WALL_DEFAULTS.lp_ratio, write: v => { d.lp_ratio = v; mark(); } }),
      F.check(d, "confined", { label: "Confined boundary (ASCE 41 Table 10-19 'Yes' rows)", id: "hbWallConf", write: v => { d.confined = v; mark(); } }),
      F.text(d, "stories", { label: "Stories (blank = all)", id: "hbWallStories", required: false, placeholder: storyNames.join(", "),
        read: () => d.stories ? d.stories.join(", ") : "",
        validate: v => !v ? "" : v.split(",").map(x => x.trim()).filter(Boolean).every(x => storyNames.includes(x)) ? "" : "Unknown story name.",
        write: v => { const l = v.split(",").map(x => x.trim()).filter(Boolean); d.stories = l.length ? l : null; mark(); } }),
    ], { summary: "Advanced — fibers, hinge length, stories" }));
  };
  const body = [
    hbGroup("Pier", [F.select(V, "pier", all.map(l => [l, l + (labels.includes(l) ? "" : " (selected wall — labelled on OK)")]), { label: "Pier label", id: "hbWallPier", write: v => { V.pier = v; render(); } })],
      { note: "One fiber wide column per story the wall spans replaces the wall's in-plane stiffness in pushover / nonlinear static / nonlinear TH cases; linear cases keep the elastic shell." }),
    box,
  ];
  const apply = () => {
    const D = hbDraft(sky);
    const out = hbClone(D.wall_hinges || {});
    let any = false;
    for (const [lab, st] of Object.entries(state)) {
      if (!st.touched) continue;
      any = true;
      if (!st.on) { delete out[lab]; continue; }
      const e = hbValidateWall(lab, st.def, D);
      if (e) return e;
      out[lab] = hbNormalizeWall(st.def);
      if (!labels.includes(lab)) {                   // unlabeled selected wall → pier label = its uid
        const w = (D.shells || []).find(s => s.uid === lab);
        if (w) w.pier = lab;
      }
    }
    if (!any) return "";
    if (Object.keys(out).length) D.wall_hinges = out; else delete D.wall_hinges;
    hbCommit(sky, D);
    labels = hbPierLabels(S.model);
    for (const st of Object.values(state)) st.touched = false;
    return "";
  };
  render();
  return hbDialog({ id: "hbWallHinges", title: "Assign Wall Hinges (Fiber P-M3)", size: "md", form: F, body, iconId: "exleaf-shells",
    intro: "ETABS Assign › Shell › Wall Hinge: a fiber P-M3 hinge per pier per story — a lighter alternative to nonlinear layered shells.",
    onOk: () => { const r = apply(); if (r) return r; }, onApply: () => apply() });
}

/* ================================================================
   Display › Hinge Results… — extra info for the new kinds
   ================================================================ */
export function hbHingeInfo(h, k) {
  if (!h) return "";
  const at = (arr) => Array.isArray(arr) && arr.length ? arr[Math.min(k, arr.length - 1)] : null;
  if (h.auto) {
    const a = h.auto;
    return `<br><span class="hb-info" id="hbResInfo">Auto: ${hbEsc(HB_CODE_LABEL[a.code] || a.code)} ${hbEsc(a.table || "")}` +
      (a.P_G != null && a.P_G !== 0 ? ` · P<sub>G</sub> = ${hbEsc(HBU.fmtU("force", a.P_G, 1))}` : "") +
      ` · ${a.preliminary ? "<b>preliminary</b>" : "verified"}</span>`;
  }
  if (h.kind === "user_interaction") {
    return `<br><span class="hb-info" id="hbResInfo">${hbEsc(h.type)}: P = ${hbEsc(HBU.fmtU("force", h.P, 1))} (${hbEsc(h.axial_source || "")}) · ` +
      `M2y = ${hbEsc(HBU.fmtU("moment", h.My2, 1))} · M3y = ${hbEsc(HBU.fmtU("moment", h.My3, 1))} · ` +
      `M2 = ${hbEsc(HBU.fmtU("moment", at(h.m2), 1))} · M3 = ${hbEsc(HBU.fmtU("moment", at(h.m3), 1))}</span>`;
  }
  if (h.kind === "wall") {
    return `<br><span class="hb-info" id="hbResInfo">Wall pier ${hbEsc(h.pier)} @ ${hbEsc(h.story)} · P = ${hbEsc(HBU.fmtU("force", at(h.axial), 1))} · ` +
      `κ = ${hbEsc(String(+(at(h.curvature) || 0).toPrecision(3)))} 1/${hbEsc(HBU.label("length"))} (κy ${hbEsc(String(+(h.kappa_y || 0).toPrecision(3)))}) · ` +
      `${hbEsc(h.table || "")} IO/LS/CP ${[h.IO, h.LS, h.CP].map(v => (+v).toFixed(4)).join(" / ")} rad (preliminary)</span>`;
  }
  return "";
}

/* ================================================================
   install
   ================================================================ */
export function initHingeB(sky) {
  if (!sky) return;
  hbSky = sky;
  if (!document.querySelector("link[data-hb-css]")) {
    const st = document.createElement("style");
    st.setAttribute("data-hb-css", "1");
    st.textContent = ".hb-row{grid-template-columns:minmax(0,1fr) minmax(0,1fr) 28px}.hb-row3{grid-template-columns:minmax(0,.7fr) minmax(0,1fr) minmax(0,1fr) 28px}" +
      ".hb-surf{max-height:260px;overflow:auto}.hb-info{color:var(--text-2)}";
    document.head.appendChild(st);
  }
  sky.hbHingeInfo = hbHingeInfo;
  sky.hingeB = { openAutoAssign: () => hbOpenAutoAssign(sky), openWallHinges: () => hbOpenWallHinges(sky), surface: hbEllipseAt };
  if (!(sky.menu && sky.menu.add)) return;
  sky.menu.add("Assign/Frame/Hinges", { label: "Auto Hinges (Code Tables)…", act: "asn-hb-auto", before: "asn-fhinge",
    hint: "ASCE 41 · FEMA 356 · EC8-3 · Caltrans · TBI", fn: () => hbOpenAutoAssign(sky) });
  sky.menu.add("Assign/Shell", { label: "Wall Hinges (Fiber P-M3)…", act: "asn-hb-wall", hint: "per pier · per story", fn: () => hbOpenWallHinges(sky) });
}
export { hbMockSurf };
