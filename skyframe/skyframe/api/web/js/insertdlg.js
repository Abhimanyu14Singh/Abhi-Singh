/* SkyFrame — ETABS Assign > Frame > Insertion Point… and
   Assign > Frame > End Length Offsets… (G2).

   CONTRACT "Insertion point, joint offsets and automatic end offsets":
     FrameMember.cardinal_point        int 1..11 (10 = centroid, default)
     FrameMember.joint_offsets         null | {i:[d1,d2,d3], j:[...], system:"global"|"local"}
     FrameMember.no_transform_stiffness bool
     FrameMember.end_offsets           "manual" (rigid_i / rigid_j / rigid_factor) | "auto"
     FrameMember.auto_rigid_factor     [0, 1]

   Both dialogs edit a draft and write only the fields the user changed, to
   every selected frame, on OK / Apply (Cancel / Esc discard). Lengths go
   through units.js (store stays SI). The 3D viewer gets a hook
   (viewer.g2Overlay) drawing each eccentric member's analytical (offset)
   axis as a dashed line plus a "CPn" / "AUTO" badge — the viewer has no
   extruded section view, so the axis + badge is the visual cue. */

import { dialog as g2Dialog, btn as g2Btn, footBar as g2FootBar, errorLine as g2ErrorLine,
  showError as g2ShowError } from "./analysisdlg.js";
import IU from "./units.js";
import { CP_GRID, CP_NAMES, memberOffsets, autoEndLengths } from "./polygeom.js";

const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function h(tag, attrs = {}, kids = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k === "html") n.innerHTML = v;
    else if (v != null) n.setAttribute(k, v);
  }
  for (const c of [].concat(kids)) if (c != null) n.append(c);
  return n;
}
function fieldset(legend) {
  const g = h("fieldset", { class: "dlg-group" });
  g.appendChild(h("legend", { text: legend }));
  return g;
}
/** Number input bound to a units kind; get() returns the exact SI value while untouched. */
function num(kind, si, attrs = {}) {
  const i = h("input", { type: "number", step: "any", ...attrs });
  const shown = si == null ? "" : IU.inputValue(kind, si);
  i.value = shown;
  return { el: i, get: () => (i.value === shown ? si : IU.parse(kind, i.value)) };
}

const cpOf = m => (m.cardinal_point == null ? 10 : +m.cardinal_point);
const same = (arr, f) => arr.every(x => JSON.stringify(f(x)) === JSON.stringify(f(arr[0])));

/** ETABS 11-point cardinal diagram (3×3 grid + centroid + shear centre). */
export function cardinalSvg(sel) {
  const W = 180, H = 150, x0 = 30, y0 = 18, w = 120, hh = 114;
  let s = `<svg viewBox="0 0 ${W} ${H}" class="g2-cp-svg" role="radiogroup" aria-label="Cardinal point">`;
  s += `<rect x="${x0}" y="${y0}" width="${w}" height="${hh}" class="g2-cp-sec"/>`;
  s += `<line x1="${x0 - 14}" y1="${y0 + hh / 2}" x2="${x0 + w + 14}" y2="${y0 + hh / 2}" class="g2-cp-axis"/>`;
  s += `<line x1="${x0 + w / 2}" y1="${y0 - 10}" x2="${x0 + w / 2}" y2="${y0 + hh + 10}" class="g2-cp-axis"/>`;
  s += `<text x="${x0 + w / 2 + 4}" y="${y0 - 3}" class="g2-cp-lbl">2</text><text x="${x0 + w + 8}" y="${y0 + hh / 2 - 4}" class="g2-cp-lbl">3</text>`;
  const pos = cp => {
    if (cp === 10) return [x0 + w / 2 - 14, y0 + hh / 2 + 14];
    if (cp === 11) return [x0 + w / 2 + 14, y0 + hh / 2 + 14];
    const [r, c] = CP_GRID[cp];
    return [x0 + w / 2 + c * w / 2, y0 + hh / 2 - r * hh / 2];
  };
  for (let cp = 1; cp <= 11; cp++) {
    const [cx, cy] = pos(cp);
    const on = cp === sel;
    s += `<g class="g2-cp-pt${on ? " is-on" : ""}" data-cp="${cp}" role="radio" aria-checked="${on}" tabindex="0"><title>${cp} — ${CP_NAMES[cp]}</title>` +
      (cp === 11 ? `<rect x="${cx - 6}" y="${cy - 6}" width="12" height="12" rx="2"/>` : `<circle cx="${cx}" cy="${cy}" r="${cp === 10 ? 7 : 6.5}"/>`) +
      `<text x="${cx}" y="${cy + 0.5}">${cp}</text></g>`;
  }
  return s + "</svg>";
}

export function initInsertion(sky) {
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 4500) => sky.toast && sky.toast(t, m, k, ms);
  const frames = () => {
    const m = S.model;
    if (!m) return [];
    return (S.selection || []).filter(r => r.type === "member")
      .map(r => m.members.find(x => x.uid === r.uid)).filter(Boolean);
  };
  const after = what => {
    sky.markDirty();
    try { sky.renderProps(); } catch (e) { console.error(e); }
    if (sky.planEditor) sky.planEditor.renderStatic();
    if (sky.viewer) sky.viewer._dirty = true;      // offset axes redraw (viewer keeps its camera)
    document.dispatchEvent(new CustomEvent("sky:g2-insertion", { detail: { what } }));
  };

  /* ================= Insertion Point ================= */
  function openInsertionPoint() {
    const mem = frames();
    if (!mem.length) { toast("Insertion Point", "Select one or more frame members first (Select tool).", "error"); return null; }
    const m0 = mem[0];
    const mixed = k => !same(mem, k);
    let cp = cpOf(m0);
    const jo0 = m0.joint_offsets || null;
    const body = h("div", { class: "g2-ins" });
    body.appendChild(h("p", { class: "muted dlg-intro", html:
      `${mem.length} frame${mem.length > 1 ? "s" : ""} selected (${esc(mem.slice(0, 4).map(x => x.uid).join(", "))}${mem.length > 4 ? ", …" : ""}). ` +
      `The cardinal point is the section point lying on the line between the member's joints.` +
      (mixed(cpOf) || mixed(x => x.joint_offsets || null) || mixed(x => !!x.no_transform_stiffness)
        ? ` <b>Values differ across the selection — showing ${esc(m0.uid)}; only fields you change are written.</b>` : "") }));

    const gcp = fieldset("Cardinal Point");
    const row = h("div", { class: "g2-cp-row" });
    const pick = h("div", { class: "g2-cp-pick", id: "g2CpPick" });
    const selEl = h("select", { id: "g2CpSelect" });
    selEl.innerHTML = Array.from({ length: 11 }, (_, i) => i + 1)
      .map(n => `<option value="${n}"${n === cp ? " selected" : ""}>${n} (${CP_NAMES[n]})</option>`).join("");
    const drawPick = () => {
      pick.innerHTML = cardinalSvg(cp);
      pick.querySelectorAll(".g2-cp-pt").forEach(g => {
        const set = () => { cp = +g.dataset.cp; selEl.value = String(cp); drawPick(); preview(); };
        g.addEventListener("click", set);
        g.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); set(); } });
      });
    };
    selEl.addEventListener("change", () => { cp = +selEl.value; drawPick(); preview(); });
    const side = h("div", { class: "g2-cp-side" }, [h("label", { class: "g2-lbl", text: "Cardinal point" }), selEl]);
    const nts = h("input", { type: "checkbox", id: "g2NoTransform" });
    nts.checked = !!m0.no_transform_stiffness;
    side.appendChild(h("label", { class: "dlg-chk g2-chk" }, [nts, h("span", { text: "Do not transform frame stiffness for offsets from centroid" })]));
    const prev = h("p", { class: "muted g2-ins-prev", id: "g2InsPreview" });
    side.appendChild(prev);
    row.append(pick, side);
    gcp.appendChild(row);
    body.appendChild(gcp);

    const gjo = fieldset("Frame Joint Offsets to Cardinal Point");
    let sys = (jo0 && jo0.system) || "global";
    const sysRow = h("div", { class: "g2-radios" });
    for (const [v, t] of [["global", "Global coordinate system (X, Y, Z)"], ["local", "Member local axes (1, 2, 3)"]]) {
      const r = h("input", { type: "radio", name: "g2JoSys", value: v, id: "g2JoSys_" + v });
      r.checked = v === sys;
      r.addEventListener("change", () => { if (r.checked) { sys = v; relabel(); preview(); } });
      sysRow.appendChild(h("label", { class: "dlg-chk" }, [r, h("span", { text: t })]));
    }
    gjo.appendChild(sysRow);
    const grid = h("div", { class: "g2-jo-grid" });
    const heads = [h("span"), h("span", { class: "g2-jo-h" }), h("span", { class: "g2-jo-h" }), h("span", { class: "g2-jo-h" })];
    grid.append(...heads);
    const fi = [], fj = [];
    for (const [end, arr, lbl] of [["i", fi, "End-I"], ["j", fj, "End-J"]]) {
      grid.appendChild(h("span", { class: "g2-lbl", text: lbl }));
      for (let k = 0; k < 3; k++) {
        const f = num("length", jo0 && jo0[end] ? +jo0[end][k] : 0, { id: `g2Jo${end}${k}` });
        f.el.addEventListener("input", () => preview());
        arr.push(f);
        grid.appendChild(f.el);
      }
    }
    const relabel = () => {
      const n = sys === "local" ? ["1", "2", "3"] : ["X", "Y", "Z"];
      heads.slice(1).forEach((hd, k) => { hd.textContent = `${n[k]} (${IU.label("length")})`; });
    };
    relabel();
    gjo.appendChild(grid);
    body.appendChild(gjo);
    const err = g2ErrorLine();
    body.appendChild(err);

    const readJo = () => {
      const i = fi.map(f => f.get()), j = fj.map(f => f.get());
      if ([...i, ...j].some(v => v == null || !isFinite(v))) return { bad: true };
      if ([...i, ...j].every(v => Math.abs(v) < 1e-15)) return { jo: null };
      return { jo: { i, j, system: sys } };
    };
    function preview() {
      const r = readJo();
      if (r.bad) { prev.textContent = "Joint offsets must be numbers."; return; }
      const tmp = { ...m0, cardinal_point: cp, joint_offsets: r.jo, no_transform_stiffness: nts.checked };
      const e = memberOffsets(S.model, tmp);
      const f = v => IU.fmt("length", v, 3);
      prev.textContent = e
        ? `${m0.uid}: analytical axis offset  i (${e[0].map(f).join(", ")})  j (${e[1].map(f).join(", ")}) ${IU.label("length")}`
        : `${m0.uid}: on the reference line (no eccentricity).`;
    }
    nts.addEventListener("change", preview);
    drawPick(); preview();

    const initial = { cp, nts: nts.checked, jo: JSON.stringify(readJo().jo) };
    const apply = () => {
      const r = readJo();
      if (r.bad) { g2ShowError(err, "Joint offsets must be finite numbers."); return false; }
      const ch = { cp: cp !== initial.cp, nts: nts.checked !== initial.nts, jo: JSON.stringify(r.jo) !== initial.jo };
      const ins = (cp !== 10 && ch.cp) || (r.jo && ch.jo);
      const axial = mem.filter(x => x.axial_limit && x.axial_limit !== "both");
      if (ins && axial.length) {
        g2ShowError(err, `Member ${axial[0].uid}: axial-only members do not support insertion points / joint offsets`);
        return false;
      }
      g2ShowError(err, "");
      if (!ch.cp && !ch.nts && !ch.jo) return true;
      for (const x of mem) {
        if (ch.cp) x.cardinal_point = cp;
        if (ch.nts) x.no_transform_stiffness = nts.checked;
        if (ch.jo) x.joint_offsets = r.jo ? { i: [...r.jo.i], j: [...r.jo.j], system: r.jo.system } : null;
      }
      initial.cp = cp; initial.nts = nts.checked; initial.jo = JSON.stringify(r.jo);
      after("insertion");
      return true;
    };
    const fb = g2FootBar("Frame Assignment — Insertion Point", [
      g2Btn("Apply", "", () => apply()),
      g2Btn("Cancel", "", () => d.close()),
      g2Btn("OK", "btn-primary", () => { if (apply()) d.close(); }),
    ]);
    const d = g2Dialog("g2InsertionDlg", { title: "Frame Assignment — Insertion Point", iconId: null, body, foot: fb.wrap });
    return d;
  }

  /* ================= End Length Offsets ================= */
  function openEndOffsets() {
    const mem = frames();
    if (!mem.length) { toast("End Length Offsets", "Select one or more frame members first (Select tool).", "error"); return null; }
    const m0 = mem[0];
    let mode = m0.end_offsets === "auto" ? "auto" : "manual";
    const body = h("div", { class: "g2-eo" });
    body.appendChild(h("p", { class: "muted dlg-intro", html:
      `${mem.length} frame${mem.length > 1 ? "s" : ""} selected. Rigid end zones shorten the flexible length of the member; ` +
      `the rigid-zone factor sets how much of each end length is rigid (0 = fully flexible, ETABS default for automatic).` +
      (!same(mem, x => [x.end_offsets || "manual", x.auto_rigid_factor || 0, x.rigid_i || 0, x.rigid_j || 0, x.rigid_factor ?? 1])
        ? ` <b>Values differ across the selection — showing ${esc(m0.uid)}; only fields you change are written.</b>` : "") }));
    const g = fieldset("Frame Length Offsets");
    const rAuto = h("input", { type: "radio", name: "g2EoMode", value: "auto", id: "g2EoAuto" });
    const rMan = h("input", { type: "radio", name: "g2EoMode", value: "manual", id: "g2EoManual" });
    rAuto.checked = mode === "auto"; rMan.checked = mode === "manual";
    const autoBox = h("div", { class: "g2-eo-box", id: "g2EoAutoBox" });
    const manBox = h("div", { class: "g2-eo-box", id: "g2EoManBox" });
    const fAuto = num("none", +m0.auto_rigid_factor || 0, { id: "g2EoAutoFactor", min: 0, max: 1, step: 0.05 });
    autoBox.appendChild(h("label", { class: "g2-eo-row" }, [h("span", { text: "Rigid-zone factor" }), fAuto.el, h("span", { class: "muted", text: "0 – 1" })]));
    const fI = num("length", +m0.rigid_i || 0, { id: "g2EoRi", min: 0 });
    const fJ = num("length", +m0.rigid_j || 0, { id: "g2EoRj", min: 0 });
    const fF = num("none", m0.rigid_factor == null ? 1 : +m0.rigid_factor, { id: "g2EoRf", min: 0, max: 1, step: 0.05 });
    manBox.append(
      h("label", { class: "g2-eo-row" }, [h("span", { text: "End-I length" }), fI.el, h("span", { class: "muted", text: IU.label("length") })]),
      h("label", { class: "g2-eo-row" }, [h("span", { text: "End-J length" }), fJ.el, h("span", { class: "muted", text: IU.label("length") })]),
      h("label", { class: "g2-eo-row" }, [h("span", { text: "Rigid-zone factor" }), fF.el, h("span", { class: "muted", text: "0 – 1" })]));
    g.append(
      h("label", { class: "dlg-chk" }, [rAuto, h("span", { text: "Automatic from Connectivity" })]), autoBox,
      h("label", { class: "dlg-chk" }, [rMan, h("span", { text: "Define Lengths" })]), manBox);
    body.appendChild(g);

    // ETABS "Frame End Length Offsets" preview (client mirror of end_offset_report)
    const tb = fieldset("Automatic lengths (from connectivity)");
    const tbl = h("table", { class: "g2-eo-tbl", id: "g2EoTable" });
    const lu = IU.label("length");
    let rows = `<thead><tr><th>Frame</th><th>L<sub>i</sub> ${esc(lu)}</th><th>L<sub>j</sub> ${esc(lu)}</th><th>Clear span ${esc(lu)}</th></tr></thead><tbody>`;
    for (const x of mem.slice(0, 40)) {
      const [li, lj] = autoEndLengths(S.model, x);
      const L = Math.hypot(x.pj[0] - x.pi[0], x.pj[1] - x.pi[1], x.pj[2] - x.pi[2]);
      rows += `<tr><td>${esc(x.uid)}</td><td>${IU.fmt("length", li, 3)}</td><td>${IU.fmt("length", lj, 3)}</td><td>${IU.fmt("length", L - li - lj, 3)}</td></tr>`;
    }
    if (mem.length > 40) rows += `<tr><td colspan="4" class="muted">… ${mem.length - 40} more</td></tr>`;
    tbl.innerHTML = rows + "</tbody>";
    tb.appendChild(tbl);
    tb.appendChild(h("p", { class: "muted g2-note", text: "Half the depth/width of the members framing into each end, measured along this member (collinear members ignored). A user-defined End-I/J length > 0 wins per end." }));
    body.appendChild(tb);
    const err = g2ErrorLine();
    body.appendChild(err);
    const sync = () => {
      mode = rAuto.checked ? "auto" : "manual";
      autoBox.classList.toggle("is-off", mode !== "auto");
      manBox.classList.toggle("is-off", mode !== "manual");
      fAuto.el.disabled = mode !== "auto";
    };
    rAuto.addEventListener("change", sync); rMan.addEventListener("change", sync);
    sync();
    const init = { mode, fa: fAuto.get(), ri: fI.get(), rj: fJ.get(), rf: fF.get() };
    const apply = () => {
      const fa = fAuto.get(), ri = fI.get(), rj = fJ.get(), rf = fF.get();
      if (!(isFinite(fa) && fa >= 0 && fa <= 1)) { g2ShowError(err, `auto_rigid_factor must be in [0, 1] (got ${fa})`); return false; }
      if (!(isFinite(rf) && rf >= 0 && rf <= 1)) { g2ShowError(err, "Rigid-zone factor must be in [0, 1]"); return false; }
      if (!(isFinite(ri) && ri >= 0 && isFinite(rj) && rj >= 0)) { g2ShowError(err, "End lengths must be finite and >= 0"); return false; }
      g2ShowError(err, "");
      const ch = { mode: mode !== init.mode, fa: fa !== init.fa, ri: ri !== init.ri, rj: rj !== init.rj, rf: rf !== init.rf };
      if (!Object.values(ch).some(Boolean)) return true;
      for (const x of mem) {
        if (ch.mode) x.end_offsets = mode;
        if (ch.fa) x.auto_rigid_factor = fa;
        if (ch.ri) x.rigid_i = ri;
        if (ch.rj) x.rigid_j = rj;
        if (ch.rf) x.rigid_factor = rf;
      }
      Object.assign(init, { mode, fa, ri, rj, rf });
      after("end-offsets");
      return true;
    };
    const fb = g2FootBar("Frame Assignment — End Length Offsets", [
      g2Btn("Apply", "", () => apply()),
      g2Btn("Cancel", "", () => d.close()),
      g2Btn("OK", "btn-primary", () => { if (apply()) d.close(); }),
    ]);
    const d = g2Dialog("g2EndOffsetDlg", { title: "Frame Assignment — End Length Offsets", iconId: null, body, foot: fb.wrap });
    return d;
  }

  /* ================= Properties-panel summary ================= */
  function decorateInsertion(box) {
    const mem = frames();
    if (!mem.length || !box) return;
    const head = [...box.querySelectorAll("h3.group-title")].find(x => /Frame assignments/.test(x.textContent));
    if (!head) return;
    const cps = [...new Set(mem.map(cpOf))];
    const eos = [...new Set(mem.map(x => x.end_offsets === "auto" ? `auto ×${+x.auto_rigid_factor || 0}` : "manual"))];
    const jo = mem.some(x => x.joint_offsets);
    const wrap = h("div", { class: "g2-ins-sum", id: "g2InsSum" });
    wrap.innerHTML =
      `<div class="g2-ins-line"><span>Insertion</span><b id="g2InsCp">${cps.length === 1 ? `${cps[0]} · ${esc(CP_NAMES[cps[0]])}` : "mixed"}</b>${jo ? ` <span class="g2-badge">joint offsets</span>` : ""}</div>` +
      `<div class="g2-ins-line"><span>End offsets</span><b id="g2InsEo">${eos.length === 1 ? esc(eos[0]) : "mixed"}</b></div>`;
    const bar = h("div", { class: "g2-ins-btns" });
    bar.append(g2Btn("Insertion Point…", "btn-small", () => openInsertionPoint()),
      g2Btn("End Offsets…", "btn-small", () => openEndOffsets()));
    wrap.appendChild(bar);
    head.after(wrap);
  }

  /* ================= 3D: offset axes + badges ================= */
  let cache = { model: null, ver: -1, items: [] };
  let ver = 0;
  document.addEventListener("sky:model-changed", () => { ver++; });
  const items = model => {
    if (cache.model === model && cache.ver === ver) return cache.items;
    const out = [];
    for (const mm of model.members || []) {
      const e = memberOffsets(model, mm);
      const auto = mm.end_offsets === "auto";
      if (!e && !auto) continue;
      out.push({
        a: e ? mm.pi.map((v, k) => v + e[0][k]) : null,
        b: e ? mm.pj.map((v, k) => v + e[1][k]) : null,
        mid: mm.pi.map((v, k) => (v + mm.pj[k]) / 2),
        label: [cpOf(mm) !== 10 ? `CP${cpOf(mm)}` : (mm.joint_offsets ? "JO" : ""), auto ? "AUTO" : ""].filter(Boolean).join(" · "),
      });
    }
    cache = { model, ver, items: out };
    return out;
  };
  function draw3d(ctx, P, viewer) {
    if (!viewer.model) return;
    const its = items(viewer.model);
    if (!its.length) return;
    ctx.save();
    ctx.strokeStyle = "rgba(245, 190, 60, 0.9)";
    ctx.lineWidth = 1.4;
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    for (const it of its) {
      if (!it.a) continue;
      const s = viewer._projSeg(P, it.a, it.b);
      if (s) { ctx.moveTo(s.a.x, s.a.y); ctx.lineTo(s.b.x, s.b.y); }
    }
    ctx.stroke();
    ctx.setLineDash([]);
    if (its.length <= 400) {
      ctx.font = "700 8.5px -apple-system, 'Segoe UI', sans-serif";
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      for (const it of its) {
        const pc = P.toCam(it.mid);
        if (pc[2] < P.near) continue;
        const sp = P.proj(pc);
        const w = ctx.measureText(it.label).width + 8;
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(sp.x - w / 2, sp.y + 6, w, 12, 6); else ctx.rect(sp.x - w / 2, sp.y + 6, w, 12);
        ctx.fillStyle = "rgba(40, 30, 6, 0.75)"; ctx.fill();
        ctx.strokeStyle = "rgba(245, 190, 60, 0.9)"; ctx.lineWidth = 1; ctx.stroke();
        ctx.fillStyle = "#f5be3c";
        ctx.fillText(it.label, sp.x, sp.y + 12.5);
      }
    }
    ctx.restore();
  }
  if (sky.viewer) sky.viewer.g2Overlay = draw3d;

  /* ================= styles ================= */
  if (!document.getElementById("g2InsStyles")) {
    const s = document.createElement("style");
    s.id = "g2InsStyles";
    s.textContent = `
      .g2-cp-row { display: flex; gap: 14px; align-items: flex-start; flex-wrap: wrap; }
      .g2-cp-pick { flex: 0 0 180px; }
      .g2-cp-svg { width: 180px; height: 150px; display: block; }
      .g2-cp-sec { fill: rgba(95,143,201,0.14); stroke: rgba(125,168,216,0.8); stroke-width: 1.2; }
      .g2-cp-axis { stroke: rgba(140,160,185,0.45); stroke-dasharray: 3 3; }
      .g2-cp-lbl { fill: rgba(140,160,185,0.8); font-size: 9px; }
      .g2-cp-pt { cursor: pointer; }
      .g2-cp-pt circle, .g2-cp-pt rect { fill: var(--bg-2, #161b22); stroke: rgba(53,181,229,0.8); stroke-width: 1.2; }
      .g2-cp-pt text { fill: var(--text, #e8edf3); font-size: 8px; font-weight: 700; text-anchor: middle; dominant-baseline: middle; pointer-events: none; }
      .g2-cp-pt:hover circle, .g2-cp-pt:hover rect { stroke: #f5be3c; }
      .g2-cp-pt.is-on circle, .g2-cp-pt.is-on rect { fill: #35b5e5; stroke: #bfeaff; }
      .g2-cp-pt.is-on text { fill: #0d1117; }
      .g2-cp-side { flex: 1 1 220px; display: flex; flex-direction: column; gap: 8px; }
      .g2-lbl { font-size: 11.5px; }
      .g2-ins-prev { font-size: 11px; margin: 0; }
      .g2-radios { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 6px; }
      .g2-jo-grid { display: grid; grid-template-columns: 54px repeat(3, 1fr); gap: 4px 6px; align-items: center; }
      .g2-jo-h { font-size: 10.5px; color: var(--text-3, #7d8a97); }
      .g2-jo-grid input, .g2-eo-row input { min-width: 0; padding: 3px 5px; }
      .g2-eo-box { margin: 4px 0 8px 22px; display: flex; flex-direction: column; gap: 4px; }
      .g2-eo-box.is-off { opacity: 0.45; }
      .g2-eo-row { display: grid; grid-template-columns: 120px 110px auto; gap: 6px; align-items: center; font-size: 11.5px; }
      .g2-eo-tbl { width: 100%; border-collapse: collapse; font-size: 11px; font-variant-numeric: tabular-nums; }
      .g2-eo-tbl th, .g2-eo-tbl td { padding: 2px 6px; text-align: right; border-bottom: 1px solid rgba(120,140,165,0.15); }
      .g2-eo-tbl th:first-child, .g2-eo-tbl td:first-child { text-align: left; }
      .g2-note { font-size: 10.5px; margin: 4px 0 0; }
      .g2-ins-sum { margin: 4px 0 8px; font-size: 11.5px; }
      .g2-ins-line { display: flex; gap: 8px; align-items: baseline; }
      .g2-ins-line > span:first-child { color: var(--text-3, #7d8a97); min-width: 72px; }
      .g2-badge { font-size: 10px; padding: 0 5px; border: 1px solid rgba(245,190,60,0.7); border-radius: 6px; color: #f5be3c; }
      .g2-ins-btns { display: flex; gap: 6px; margin-top: 4px; }
    `;
    document.head.appendChild(s);
  }

  sky.openInsertionPoint = openInsertionPoint;
  sky.openEndOffsets = openEndOffsets;
  sky.g2DecorateInsertion = decorateInsertion;
  sky.insertion = { openInsertionPoint, openEndOffsets, cardinalSvg, memberOffsets, autoEndLengths };
  return sky.insertion;
}
