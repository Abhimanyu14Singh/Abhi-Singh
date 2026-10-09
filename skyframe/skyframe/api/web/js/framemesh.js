/* SkyFrame — ETABS Assign > Frame > Frame Auto Mesh Options… and
   Assign > Frame > Output Stations… (CONTRACT "Frame auto-mesh and output
   stations").

     FrameMember.auto_mesh        null (use the model default) |
                                  {at_intermediate_joints, at_intersections,
                                   max_length, min_segments}  (replaces it)
     BuildingModel.frame_auto_mesh  model-wide default (absent = all off)
     FrameMember.output_stations  null (11 stations) | {max_spacing: s} |
                                  {min_number: n}

   Both dialogs edit a draft and write the model only on OK, and only the
   tab / field the user actually changed — OK with defaults leaves the model
   byte-identical. Lengths go through units.js (the store stays SI). The
   previews divide the first selected member with the same rules as the
   engine (js/framemesh_geom.js). Also exposes the Check Model quick fix
   (enable auto mesh at intersections / joints for the listed members). */

import { dialog as fmDialog, btn as fmBtn, footBar as fmFootBar, errorLine as fmErrorLine,
  showError as fmShowError } from "./analysisdlg.js";
import FMU from "./units.js";
import { b9h as fmH, b9group as fmGroup, b9num as fmNum, b9numRow as fmNumRow, b9tabs as fmTabs,
  b9after as fmAfter, b9css as fmCss, b9esc as fmEsc, b9same as fmSame } from "./b9common.js";
import { canonAutoMesh as fmCanon, autoMeshActive as fmActive, autoMeshPoints as fmPoints,
  stationXs as fmStationXs, memberLength as fmLen, concentratedLoadXs as fmLoadXs,
  DEFAULT_N_STATIONS as FM_NS } from "./framemesh_geom.js";

const OFF = { at_intermediate_joints: false, at_intersections: false, max_length: null, min_segments: null };

function summary(am) {
  if (!fmActive(am)) return "off";
  const p = [];
  if (am.at_intermediate_joints) p.push("intermediate joints");
  if (am.at_intersections) p.push("intersections");
  if (am.max_length != null) p.push(`max ${FMU.fmtU("length", am.max_length, 2)}`);
  if ((am.min_segments || 1) > 1) p.push(`≥ ${am.min_segments} segments`);
  return p.join(" · ");
}

/** Member line SVG with cut / station ticks. */
function lineSvg(L, marks, { stations = null, loads = [], caption = "" } = {}) {
  const W = 460, H = 92, x0 = 22, x1 = W - 22, y = 44;
  const X = t => x0 + (L > 0 ? t / L : 0) * (x1 - x0);
  let s = `<svg viewBox="0 0 ${W} ${H}" class="b9-line-svg" role="img" aria-label="${fmEsc(caption)}">`;
  s += `<line x1="${x0}" y1="${y}" x2="${x1}" y2="${y}" class="b9-mem"/>`;
  if (stations) for (const t of stations)
    s += `<line x1="${X(t).toFixed(1)}" y1="${y + 7}" x2="${X(t).toFixed(1)}" y2="${y + 17}" class="b9-sta"/>`;
  for (const a of loads)
    s += `<path d="M${X(a).toFixed(1)},${y - 26} v18 m-4,-6 l4,6 l4,-6" class="b9-load"/>`;
  for (const c of marks)
    s += `<g class="b9-cut b9-cut-${c.why}"><title>${fmEsc(c.why)} @ ${FMU.fmtU("length", c.t, 3)}</title>` +
      `<line x1="${X(c.t).toFixed(1)}" y1="${y - 9}" x2="${X(c.t).toFixed(1)}" y2="${y + 9}"/>` +
      `<circle cx="${X(c.t).toFixed(1)}" cy="${y}" r="3.2"/></g>`;
  s += `<circle cx="${x0}" cy="${y}" r="4.5" class="b9-end"/><circle cx="${x1}" cy="${y}" r="4.5" class="b9-end"/>`;
  s += `<text x="${x0}" y="${H - 6}" class="b9-tick">0</text><text x="${x1}" y="${H - 6}" class="b9-tick" text-anchor="end">${fmEsc(FMU.fmtU("length", L, 2))}</text>`;
  if (caption) s += `<text x="${(x0 + x1) / 2}" y="${H - 6}" class="b9-tick" text-anchor="middle">${fmEsc(caption)}</text>`;
  return s + "</svg>";
}

export function initFrameMesh(sky) {
  fmCss();
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 4500) => sky.toast && sky.toast(t, m, k, ms);
  const frames = () => {
    const m = S.model;
    if (!m) return [];
    return (S.selection || []).filter(r => r.type === "member")
      .map(r => m.members.find(x => x.uid === r.uid)).filter(Boolean);
  };

  /* -------- option block (checkboxes + max length + min segments) -------- */
  function optionBlock(prefix, am0, onChange) {
    const am = fmCanon(am0) || { ...OFF };
    const wrap = fmH("div", { class: "b9-am-opts", id: prefix + "Opts" });
    const chk = (key, label) => {
      const c = fmH("input", { type: "checkbox", id: `${prefix}_${key}` });
      c.checked = !!am[key];
      c.addEventListener("change", onChange);
      wrap.appendChild(fmH("label", { class: "dlg-chk" }, [c, fmH("span", { text: label })]));
      return c;
    };
    const cJ = chk("at_intermediate_joints", "Auto mesh at intermediate joints (frame ends, links, supports, shell corners on the span)");
    const cI = chk("at_intersections", "Auto mesh at intersections with other frames");
    const mlOn = fmH("input", { type: "checkbox", id: prefix + "_mlOn" });
    mlOn.checked = am.max_length != null;
    const ml = fmNum("length", am.max_length, { id: prefix + "_max_length", min: "0", placeholder: "—" });
    const nsOn = fmH("input", { type: "checkbox", id: prefix + "_nsOn" });
    nsOn.checked = am.min_segments != null;
    const ns = fmNum("none", am.min_segments, { id: prefix + "_min_segments", min: "1", step: "1", placeholder: "—" });
    const row = (on, label, f) => fmH("div", { class: "b9-opt-row" }, [
      fmH("label", { class: "dlg-chk" }, [on, fmH("span", { text: label })]), f.el,
      fmH("span", { class: "asn-unit", text: f.unit() })]);
    wrap.append(row(mlOn, "Maximum segment length", ml), row(nsOn, "Minimum number of segments", ns));
    const sync = () => { ml.el.disabled = !mlOn.checked || wrap.classList.contains("is-off"); ns.el.disabled = !nsOn.checked || wrap.classList.contains("is-off"); };
    for (const n of [mlOn, nsOn]) n.addEventListener("change", () => {
      if (n === mlOn && mlOn.checked && ml.get() == null) ml.set(1.0);
      if (n === nsOn && nsOn.checked && ns.get() == null) ns.set(2);
      sync(); onChange();
    });
    for (const f of [ml, ns]) f.el.addEventListener("input", onChange);
    sync();
    return {
      el: wrap,
      setEnabled(on) {
        wrap.classList.toggle("is-off", !on);
        for (const c of [cJ, cI, mlOn, nsOn]) c.disabled = !on;
        sync();
      },
      /** canonical dict, or a string error */
      read() {
        const out = { at_intermediate_joints: cJ.checked, at_intersections: cI.checked, max_length: null, min_segments: null };
        if (mlOn.checked) {
          const v = ml.get();
          if (!(isFinite(v) && v > 0)) return "Maximum segment length must be a number > 0.";
          out.max_length = v;
        }
        if (nsOn.checked) {
          const v = ns.get();
          if (!(isFinite(v) && v >= 1 && Math.abs(v - Math.round(v)) < 1e-9)) return "Minimum number of segments must be an integer ≥ 1.";
          out.min_segments = Math.round(v);
        }
        return out;
      },
    };
  }

  /* ================= Frame Auto Mesh Options ================= */
  function openAutoMesh(tab) {
    const m = S.model;
    if (!m) return null;
    const mem = frames();
    let cur = tab || (mem.length ? "frames" : "default");
    const body = fmH("div", { class: "b9-am" });
    const tabs = fmTabs([["frames", `Selected Frames (${mem.length})`], ["default", "Model Default"]], k => { cur = k; show(); }, "fmAmTabs");
    body.appendChild(fmH("div", { class: "b9-top" }, [tabs.el]));

    /* --- selected frames --- */
    const paneF = fmH("div", { class: "b9-pane", id: "fmAmPaneFrames" });
    const mixed = mem.length > 1 && !mem.every(x => fmSame(fmCanon(x.auto_mesh), fmCanon(mem[0].auto_mesh)));
    const m0 = mem[0];
    const useDef0 = !m0 || m0.auto_mesh == null;
    if (!mem.length) {
      paneF.appendChild(fmH("p", { class: "muted dlg-intro", text: "No frames selected — select one or more frame members (Select tool) to give them their own auto-mesh options. The Model Default tab applies to every frame without its own options." }));
    } else {
      paneF.appendChild(fmH("p", { class: "muted dlg-intro", html:
        `${mem.length} frame${mem.length > 1 ? "s" : ""} selected (${fmEsc(mem.slice(0, 5).map(x => x.uid).join(", "))}${mem.length > 5 ? ", …" : ""}). ` +
        `The analysis member is divided; the drawn object and its results stay one entry.` +
        (mixed ? ` <b>Values differ across the selection — showing ${fmEsc(m0.uid)}; nothing is written unless you change something.</b>` : "") }));
    }
    const gF = fmGroup("Auto Mesh Options");
    const rDef = fmH("input", { type: "radio", name: "fmAmMode", id: "fmAmUseDefault", value: "default" });
    const rCus = fmH("input", { type: "radio", name: "fmAmMode", id: "fmAmCustom", value: "custom" });
    rDef.checked = useDef0; rCus.checked = !useDef0;
    const defNote = fmH("span", { class: "muted b9-def-note", id: "fmAmDefNote" });
    gF.append(fmH("label", { class: "dlg-chk" }, [rDef, fmH("span", { text: "Use model default" }), defNote]),
      fmH("label", { class: "dlg-chk" }, [rCus, fmH("span", { text: "Custom options for these frames (replace the default completely)" })]));
    const optF = optionBlock("fmAmF", m0 && m0.auto_mesh != null ? m0.auto_mesh : m.frame_auto_mesh, () => { touchedF = true; preview(); });
    gF.appendChild(optF.el);
    if (!mem.length) { rDef.disabled = rCus.disabled = true; optF.setEnabled(false); }
    paneF.appendChild(gF);
    let touchedF = false;
    for (const r of [rDef, rCus]) r.addEventListener("change", () => { touchedF = true; optF.setEnabled(rCus.checked); preview(); });
    optF.setEnabled(!useDef0 && !!mem.length);

    /* --- model default --- */
    const paneD = fmH("div", { class: "b9-pane", id: "fmAmPaneDefault" });
    paneD.appendChild(fmH("p", { class: "muted dlg-intro", text: "Model-wide default (frame_auto_mesh): applies to every frame member that has no options of its own. All off = the default is removed from the model." }));
    const gD = fmGroup("Default Auto Mesh Options");
    let touchedD = false;
    const optD = optionBlock("fmAmD", m.frame_auto_mesh, () => { touchedD = true; preview(); });
    gD.appendChild(optD.el);
    paneD.appendChild(gD);
    const nOwn = (m.members || []).filter(x => x.auto_mesh != null).length;
    paneD.appendChild(fmH("p", { class: "muted b9-small", text: `${nOwn} of ${(m.members || []).length} frames have their own options and ignore this default.` }));

    /* --- preview --- */
    const gP = fmGroup("Preview — analysis segments", "b9-prev");
    const prevBox = fmH("div", { class: "b9-prev-box", id: "fmAmPreview" });
    const prevTxt = fmH("p", { class: "muted b9-small", id: "fmAmPreviewText" });
    const legend = fmH("div", { class: "b9-legend", html:
      `<span class="b9-lg b9-cut-joint"></span>joint <span class="b9-lg b9-cut-intersection"></span>intersection <span class="b9-lg b9-cut-division"></span>division` });
    gP.append(prevBox, prevTxt, legend);
    body.append(paneF, paneD, gP);

    const err = fmErrorLine();
    body.appendChild(err);

    function draftModel() {
      const d = optD.read(), f = optF.read();
      if (typeof d === "string" || typeof f === "string") return null;
      const dm = { ...m, members: m.members.map(x => ({ ...x })) };
      dm.frame_auto_mesh = touchedD ? (fmActive(d) ? d : null) : m.frame_auto_mesh;
      if (touchedF && mem.length) {
        const ids = new Set(mem.map(x => x.uid));
        for (const x of dm.members) if (ids.has(x.uid)) x.auto_mesh = rDef.checked ? null : f;
      }
      return dm;
    }
    function preview() {
      defNote.textContent = " — " + summary(fmCanon(touchedD ? optD.read() : m.frame_auto_mesh));
      const dm = draftModel();
      if (!dm) { fmShowError(err, (typeof optD.read() === "string" ? optD.read() : optF.read())); return; }
      fmShowError(err, "");
      const tgt = m0 ? dm.members.find(x => x.uid === m0.uid)
        : dm.members.find(x => x.kind === "beam") || dm.members[0];
      if (!tgt) { prevBox.innerHTML = ""; prevTxt.textContent = "No frames in the model."; return; }
      const cuts = (fmPoints(dm).get(tgt.uid) || []);
      const L = fmLen(tgt);
      prevBox.innerHTML = lineSvg(L, cuts, { caption: tgt.uid });
      let nSplit = 0;
      for (const [, v] of fmPoints(dm)) if (v.length) nSplit++;
      prevTxt.textContent = `${tgt.uid}: ${cuts.length + 1} analysis segment${cuts.length ? "s" : ""}` +
        (cuts.length ? ` (cuts at ${cuts.slice(0, 6).map(c => FMU.fmt("length", c.t, 2)).join(", ")}${cuts.length > 6 ? ", …" : ""} ${FMU.label("length")})` : "") +
        ` · ${nSplit} frame${nSplit === 1 ? "" : "s"} divided in the model.`;
    }
    function show() {
      tabs.set(cur);
      paneF.classList.toggle("hidden", cur !== "frames");
      paneD.classList.toggle("hidden", cur !== "default");
    }

    const apply = () => {
      const d = optD.read(), f = optF.read();
      if (touchedD && typeof d === "string") { cur = "default"; show(); fmShowError(err, d); return false; }
      if (touchedF && rCus.checked && typeof f === "string") { cur = "frames"; show(); fmShowError(err, f); return false; }
      let changed = false;
      if (touchedD) {
        const nd = fmActive(d) ? d : null;
        if (!fmSame(nd, m.frame_auto_mesh == null ? null : fmCanon(m.frame_auto_mesh))) {
          if (nd) m.frame_auto_mesh = nd; else delete m.frame_auto_mesh;
          changed = true;
        }
      }
      if (touchedF && mem.length) {
        for (const x of mem) {
          if (rDef.checked) { if ("auto_mesh" in x) { delete x.auto_mesh; changed = true; } }
          else if (!fmSame(fmCanon(x.auto_mesh), f) || x.auto_mesh == null) { x.auto_mesh = { ...f }; changed = true; }
        }
      }
      if (changed) fmAfter(sky, "auto-mesh");
      return true;
    };
    const fb = fmFootBar("Divides the analysis members only — results stay one entry per drawn frame.", [
      fmBtn("Cancel", "", () => dlg.close()),
      fmBtn("Apply", "", () => { if (apply()) { touchedD = touchedF = false; toast("Frame Auto Mesh", "Options applied."); } }),
      fmBtn("OK", "btn-primary", () => { if (apply()) dlg.close(); }),
    ]);
    fb.wrap.querySelector(".btn-primary").id = "fmAmOk";
    const dlg = fmDialog("fmAutoMeshDlg", { title: "Frame Auto Mesh Options", iconId: "tool-beam", body, foot: fb.wrap });
    show(); preview();
    return dlg;
  }

  /* ================= Output Stations ================= */
  function openOutputStations() {
    const m = S.model;
    if (!m) return null;
    const mem = frames();
    if (!mem.length) { toast("Output Stations", "Select one or more frame members first (Select tool).", "error"); return null; }
    const m0 = mem[0];
    const os0 = m0.output_stations || null;
    const mixed = !mem.every(x => fmSame(x.output_stations || null, os0));
    const body = fmH("div", { class: "b9-os" });
    body.appendChild(fmH("p", { class: "muted dlg-intro", html:
      `${mem.length} frame${mem.length > 1 ? "s" : ""} selected (${fmEsc(mem.slice(0, 5).map(x => x.uid).join(", "))}${mem.length > 5 ? ", …" : ""}). ` +
      `Stations where member forces and deflections are reported. With an option set, every analysis segment end and every concentrated load point is added too.` +
      (mixed ? ` <b>Values differ across the selection — showing ${fmEsc(m0.uid)}.</b>` : "") }));
    const g = fmGroup("Output Stations");
    let mode = os0 == null ? "default" : os0.max_spacing != null ? "spacing" : "number";
    const radios = {};
    const sp = fmNum("length", os0 && os0.max_spacing != null ? os0.max_spacing : 0.5, { id: "fmOsSpacing", min: "0" });
    const nn = fmNum("none", os0 && os0.min_number != null ? os0.min_number : FM_NS, { id: "fmOsNumber", min: "2", step: "1" });
    let touched = false;
    const opt = (k, label, f) => {
      const r = fmH("input", { type: "radio", name: "fmOsMode", value: k, id: "fmOsMode_" + k });
      r.checked = mode === k;
      r.addEventListener("change", () => { if (r.checked) { mode = k; touched = true; sync(); preview(); } });
      radios[k] = r;
      const row = fmH("div", { class: "b9-opt-row" }, [fmH("label", { class: "dlg-chk" }, [r, fmH("span", { text: label })])]);
      if (f) { row.append(f.el, fmH("span", { class: "asn-unit", text: f.unit() })); f.el.addEventListener("input", () => { touched = true; preview(); }); }
      g.appendChild(row);
    };
    opt("default", `Default — ${FM_NS} equally spaced stations`, null);
    opt("spacing", "Max station spacing", sp);
    opt("number", "Min number of stations", nn);
    body.appendChild(g);
    const gP = fmGroup("Preview", "b9-prev");
    const prevBox = fmH("div", { class: "b9-prev-box", id: "fmOsPreview" });
    const prevTxt = fmH("p", { class: "muted b9-small", id: "fmOsPreviewText" });
    gP.append(prevBox, prevTxt, fmH("div", { class: "b9-legend", html: `<span class="b9-lg b9-sta-lg"></span>station <span class="b9-lg b9-cut-division"></span>analysis segment end <span class="b9-lg b9-load-lg"></span>concentrated load` }));
    body.appendChild(gP);
    const err = fmErrorLine();
    body.appendChild(err);
    const sync = () => { sp.el.disabled = mode !== "spacing"; nn.el.disabled = mode !== "number"; };
    const read = () => {
      if (mode === "default") return null;
      if (mode === "spacing") {
        const v = sp.get();
        return isFinite(v) && v > 0 ? { max_spacing: v } : "Max station spacing must be a number > 0.";
      }
      const v = nn.get();
      return isFinite(v) && v >= 2 && Math.abs(v - Math.round(v)) < 1e-9 ? { min_number: Math.round(v) } : "Min number of stations must be an integer ≥ 2.";
    };
    function preview() {
      const os = read();
      if (typeof os === "string") { fmShowError(err, os); return; }
      fmShowError(err, "");
      const cuts = fmPoints(m).get(m0.uid) || [];
      const xs = fmStationXs(m, m0, cuts.map(c => c.t), os);
      prevBox.innerHTML = lineSvg(fmLen(m0), cuts.map(c => ({ ...c, why: "division" })), { stations: xs, loads: os ? fmLoadXs(m, m0) : [], caption: m0.uid });
      prevTxt.textContent = `${m0.uid}: ${xs.length} stations` + (cuts.length ? ` · ${cuts.length + 1} analysis segments (auto mesh)` : "") +
        (os ? "" : " (default)") + ". Shell-split / foundation segment ends are added by the engine too.";
    }
    const apply = () => {
      const os = read();
      if (typeof os === "string") { fmShowError(err, os); return false; }
      if (!touched) return true;
      let changed = false;
      for (const x of mem) {
        if (os == null) { if ("output_stations" in x) { delete x.output_stations; changed = true; } }
        else if (!fmSame(x.output_stations || null, os)) { x.output_stations = { ...os }; changed = true; }
      }
      if (changed) fmAfter(sky, "output-stations");
      return true;
    };
    const fb = fmFootBar("Same station list in every case — combos and envelopes superpose station by station.", [
      fmBtn("Cancel", "", () => dlg.close()),
      fmBtn("OK", "btn-primary", () => { if (apply()) dlg.close(); }),
    ]);
    fb.wrap.querySelector(".btn-primary").id = "fmOsOk";
    const dlg = fmDialog("fmOutStationsDlg", { title: "Frame Output Stations", iconId: "tool-beam", body, foot: fb.wrap });
    sync(); preview();
    return dlg;
  }

  /* ================= Check Model quick fix ================= */
  /** {label, title, apply} for FRAME_INTERSECTION / FRAME_JOINT_ON_SPAN, else null. */
  function checkFix(issue) {
    const m = S.model;
    if (!m || !issue) return null;
    const objs = (issue.objects || []).filter(u => (m.members || []).some(x => x.uid === u));
    let targets, key, what;
    if (issue.code === "FRAME_INTERSECTION") {
      targets = objs; key = "at_intersections"; what = "intersections";
    } else if (issue.code === "FRAME_JOINT_ON_SPAN") {
      targets = objs.slice(1); key = "at_intermediate_joints"; what = "intermediate joints";    // objects = [end, span]
    } else return null;
    const mm = targets.map(u => m.members.find(x => x.uid === u))
      .filter(x => x && (x.axial_limit || "both") === "both");
    if (!mm.length) return null;
    return {
      label: "Enable auto-mesh",
      title: `Enable auto-mesh at ${what} for ${mm.map(x => x.uid).join(", ")}`,
      apply: () => {
        for (const x of mm) {
          const base = fmCanon(x.auto_mesh != null ? x.auto_mesh : m.frame_auto_mesh) || { ...OFF };
          base[key] = true;
          x.auto_mesh = base;
        }
        return `Auto-mesh at ${what} enabled for ${mm.map(x => x.uid).join(", ")}`;
      },
    };
  }

  /* ================= properties-panel badge ================= */
  function decorateProps(box, sel) {
    const mem = (sel && sel.members) || [];
    if (!mem.length || !S.model) return;
    const m = S.model;
    const am = mem.map(x => x.auto_mesh != null ? "custom: " + summary(fmCanon(x.auto_mesh)) : "default (" + summary(fmCanon(m.frame_auto_mesh)) + ")");
    const os = mem.map(x => !x.output_stations ? `default (${FM_NS})` : x.output_stations.max_spacing != null
      ? `max spacing ${FMU.fmtU("length", x.output_stations.max_spacing, 2)}` : `min ${x.output_stations.min_number} stations`);
    const one = arr => arr.every(v => v === arr[0]) ? arr[0] : "mixed";
    const wrap = fmH("div", { class: "b9-props", id: "fmPropsBlock" }, [
      fmH("h3", { class: "group-title", html: `Frame mesh &amp; stations <span class="unit">analysis</span>` }),
      fmH("div", { class: "b9-props-row" }, [fmH("span", { class: "muted", text: "Auto mesh" }), fmH("b", { text: one(am) })]),
      fmH("div", { class: "b9-props-row" }, [fmH("span", { class: "muted", text: "Output stations" }), fmH("b", { text: one(os) })]),
      fmH("div", { class: "b9-props-btns" }, [
        fmBtn("Auto Mesh…", "btn-small", () => openAutoMesh("frames"), "Assign > Frame > Frame Auto Mesh Options"),
        fmBtn("Output Stations…", "btn-small", () => openOutputStations(), "Assign > Frame > Output Stations"),
      ]),
    ]);
    const del = box.querySelector("#propDelete");
    const anchor = del ? del.previousElementSibling : null;
    if (anchor && anchor.parentNode === box) box.insertBefore(wrap, anchor); else box.appendChild(wrap);
  }

  sky.openFrameAutoMesh = openAutoMesh;
  sky.openOutputStations = openOutputStations;
  sky.frameMesh = { openAutoMesh, openOutputStations, checkFix, decorateProps, points: fmPoints, stationXs: fmStationXs };
  return sky.frameMesh;
}

