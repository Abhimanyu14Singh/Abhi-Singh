/* SkyFrame — ETABS Define > Frame Hinge Properties…, Assign > Frame >
   Hinges… / Hinge Overwrites…, the Properties-panel hinge badge and the
   hinge-location dots on the plan / elevation / 3D views (CONTRACT
   "User-defined hinges and hinge overwrites", B10).

   Every dialog edits a draft and writes the model only on OK / Apply, and
   only what the user changed: OK with defaults leaves the model
   byte-identical.  Forces / deformations / stiffnesses go through
   units.js when they are absolute; scale-factor multiples are unitless.
   Hinge results (Display > Hinge Results…) live in js/uhresults.js. */

import { dialog as uhDialog, btn as uhBtn, footBar as uhFootBar, errorLine as uhErrorLine,
  showError as uhShowError } from "./analysisdlg.js";
import UHU from "./units.js";
import { b9h as uhH, b9group as uhGroup, b9num as uhNum, b9tabs as uhTabs, b9after as uhAfter,
  b9css as uhB9Css, b9esc as uhEsc, b9same as uhSame } from "./b9common.js";
import { UH_TYPES, UH_HYST, UH_DROPS, UH_HYST_DEFAULTS, UH_HP_KEYS, UH_POINT_LABELS, UH_MAX_POINTS,
  UH_STATE_COLORS, uhNewProperty, uhSides, uhValidateProperty, uhValidateMemberHinges, uhKinds,
  uhClone, isUserList, isNum, uhLen, uhLocations, uhNice } from "./uhcore.js";
import { initHingeResults as uhInitResults } from "./uhresults.js";

let uhCssDone = false;
export function uhCss() {
  uhB9Css();
  if (uhCssDone || document.querySelector("link[data-uh-css]")) { uhCssDone = true; return; }
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = "/static/uhinge.css"; l.setAttribute("data-uh-css", "1");
  document.head.appendChild(l);
  uhCssDone = true;
}

const TYPE_LABEL = Object.fromEntries(UH_TYPES);
const fmtSF = v => (v == null || !isFinite(v)) ? "—" : String(+(+v).toPrecision(4));

/* ================================================================
   Backbone preview SVG (plastic deformation vs force, A–E + IO/LS/CP)
   ================================================================ */
/** p = property draft. Returns an SVG string. */
export function uhBackboneSvg(p, { w = 340, hgt = 240, id = "uhPropPlot" } = {}) {
  const k = uhKinds(p.type);
  const sc = p.scale || {};
  const yv = sc.yield_value == null ? 1 : sc.yield_value;
  const yd = sc.yield_deformation;
  const sfD = yd == null || yd === "auto" ? 1 : yd;
  const xLab = yd === "auto" ? "× member yield def." : (UHU.label(k.d) || "rad");
  const yLab = UHU.label(k.f);
  const [pos, neg] = uhSides(p);
  const M = { l: 50, r: 12, t: 16, b: 30 };
  const pw = w - M.l - M.r, ph = hgt - M.t - M.b;
  let s = `<svg viewBox="0 0 ${w} ${hgt}" class="uh-plot" id="${id}" role="img" aria-label="Hinge backbone">`;
  if (!pos || !pos.every(q => q.every(isNum)) || !neg.every(q => q.every(isNum))) {
    s += `<text x="${w / 2}" y="${hgt / 2}" class="uh-tick" text-anchor="middle">${p.type === "PMM_fiber" ? "Fiber hinge — backbone from the section fibers" : "Enter finite backbone values"}</text>`;
    return s + "</svg>";
  }
  const side = (raw, sg) => {
    const b0 = Math.abs(raw[1][0]);
    const pts = [[0, 0], ...raw.slice(1).map(q => [sg * Math.max(0, Math.abs(q[0]) - b0) * sfD, sg * Math.abs(q[1]) * yv])];
    return pts;
  };
  const P = side(pos, 1), N = side(neg, -1);
  // tail
  const tail = (pts, sg) => {
    const last = pts[pts.length - 1];
    const span = Math.max(Math.abs(last[0]), 1e-12);
    return (p.drop_strength || "drops") === "drops" ? [last, [last[0] + sg * 0.06 * span, 0], [last[0] + sg * 0.15 * span, 0]]
      : [last, [last[0] + sg * 0.15 * span, last[1]]];
  };
  const TP = tail(P, 1), TN = tail(N, -1);
  const acc = p.acceptance || {};
  const accX = ["IO", "LS", "CP"].filter(key => isNum(acc[key])).map(key => [key, acc[key] * sfD]);
  const all = [...P, ...N, ...TP, ...TN];
  const xm = uhNice(Math.max(1e-12, ...all.map(q => Math.abs(q[0])), ...accX.map(a => a[1])) * 1.02);
  const ym = uhNice(Math.max(1e-12, ...all.map(q => Math.abs(q[1]))) * 1.08);
  const X = d => M.l + (d + xm) / (2 * xm) * pw, Y = f => M.t + (ym - f) / (2 * ym) * ph;
  const fx = v => yd === "auto" ? fmtSF(v) : UHU.fmt(k.d, v, k.d === "rotation" ? 4 : 2);
  const fy = v => UHU.fmt(k.f, v, 1);                 // forces are plotted scaled (f × yield value) = absolute
  s += `<rect x="${M.l}" y="${M.t}" width="${pw}" height="${ph}" class="uh-plot-bg"/>`;
  for (const t of [-1, -0.5, 0.5, 1]) {
    s += `<line x1="${X(t * xm)}" y1="${M.t}" x2="${X(t * xm)}" y2="${M.t + ph}" class="uh-grid"/>`;
    s += `<line x1="${M.l}" y1="${Y(t * ym)}" x2="${M.l + pw}" y2="${Y(t * ym)}" class="uh-grid"/>`;
  }
  s += `<line x1="${M.l}" y1="${Y(0)}" x2="${M.l + pw}" y2="${Y(0)}" class="uh-axis"/><line x1="${X(0)}" y1="${M.t}" x2="${X(0)}" y2="${M.t + ph}" class="uh-axis"/>`;
  // acceptance markers (both sides)
  for (const [key, x] of accX) for (const sg of [1, -1]) {
    s += `<line x1="${X(sg * x)}" y1="${M.t}" x2="${X(sg * x)}" y2="${M.t + ph}" class="uh-acc uh-acc-${key}"/>`;
    if (sg > 0) s += `<text x="${X(x) + 2}" y="${M.t + 10}" class="uh-tick uh-acc-lbl">${key}</text>`;
  }
  const path = pts => pts.map((q, i) => `${i ? "L" : "M"}${X(q[0]).toFixed(1)},${Y(q[1]).toFixed(1)}`).join(" ");
  s += `<path d="${path(TP)}" class="uh-bb-tail"/><path d="${path(TN)}" class="uh-bb-tail"/>`;
  s += `<path d="${path(N.slice().reverse().concat(P.slice(1)))}" class="uh-bb"/>`;
  P.forEach((q, i) => {
    s += `<circle cx="${X(q[0]).toFixed(1)}" cy="${Y(q[1]).toFixed(1)}" r="3.4" class="uh-bb-pt"><title>${UH_POINT_LABELS[i]}: ${uhEsc(fx(q[0]))}, ${uhEsc(fy(q[1]))}</title></circle>`;
    if (i) s += `<text x="${X(q[0]).toFixed(1)}" y="${(Y(q[1]) - 6).toFixed(1)}" class="uh-tick uh-pt-lbl" text-anchor="middle">${UH_POINT_LABELS[i]}</text>`;
  });
  N.forEach((q, i) => { if (i) s += `<circle cx="${X(q[0]).toFixed(1)}" cy="${Y(q[1]).toFixed(1)}" r="2.6" class="uh-bb-pt uh-neg"/>`; });
  s += `<text x="${M.l + pw}" y="${hgt - 8}" class="uh-tick" text-anchor="end">${uhEsc(fx(xm))} ${uhEsc(yd === "auto" ? "" : xLab)}</text>`;
  s += `<text x="${M.l}" y="${hgt - 8}" class="uh-tick">${uhEsc(fx(-xm))}</text>`;
  s += `<text x="${X(0)}" y="${hgt - 8}" class="uh-tick" text-anchor="middle">plastic ${uhEsc(yd === "auto" ? xLab : "deformation")}</text>`;
  s += `<text x="${M.l - 4}" y="${M.t + 8}" class="uh-tick" text-anchor="end">${uhEsc(fy(ym))}</text>`;
  s += `<text x="${M.l - 4}" y="${M.t + ph}" class="uh-tick" text-anchor="end">${uhEsc(fy(-ym))}</text>`;
  s += `<text x="${M.l - 4}" y="${Y(0) + 3}" class="uh-tick" text-anchor="end">${uhEsc(yLab)}</text>`;
  return s + "</svg>";
}

/* ================================================================ */
export function initUserHinges(sky) {
  uhCss();
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 4500) => sky.toast && sky.toast(t, m, k, ms);
  const frames = () => {
    const m = S.model;
    if (!m) return [];
    return (S.selection || []).filter(r => r.type === "member")
      .map(r => m.members.find(x => x.uid === r.uid)).filter(Boolean);
  };
  const usesOf = (m, name) => (m.members || []).filter(mm => isUserList(mm) && mm.hinges.some(h => h && h.property === name)).length;

  /* ================= Define > Frame Hinge Properties ================= */
  function openHingeProperties(pick, onDone) {
    const m = S.model;
    if (!m) return null;
    const orig = m.hinge_properties || {};
    const draft = uhClone(orig);
    const renames = {};                       // new name -> original name
    let cur = pick && draft[pick] ? pick : Object.keys(draft)[0] || null;
    const body = uhH("div", { class: "uh-props" });
    body.appendChild(uhH("p", { class: "muted dlg-intro", text:
      "User-defined plastic hinge properties (ETABS Hinge Property Data). The backbone A-B-C-D-E is in PLASTIC deformation (rigid up to B). Assign them with Assign > Frame > Hinges…; they act in pushover, nonlinear static and nonlinear time-history cases." }));
    const grid = uhH("div", { class: "b9-sp-grid uh-grid-2" });
    const left = uhH("div", { class: "b9-sp-list-wrap" });
    const list = uhH("div", { class: "b9-sp-list", id: "uhPropList", role: "listbox" });
    const nameOf = base => { let i = 1, n = base; while (draft[n]) n = base + (++i); return n; };
    const addType = uhH("select", { id: "uhPropAddType", title: "Type of the new hinge property" });
    addType.innerHTML = UH_TYPES.map(([t]) => `<option value="${t}">${t === "PMM_fiber" ? "PMM" : t}</option>`).join("");
    const addBtn = uhBtn("Add", "btn-small", () => {
      const t = addType.value;
      const n = nameOf("HINGE_" + (t === "PMM_fiber" ? "PMM" : t));
      draft[n] = uhNewProperty(t);
      cur = n; renderAll();
    }, "Add a new hinge property of the chosen type");
    addBtn.id = "uhPropAdd";
    const copyBtn = uhBtn("Copy", "btn-small", () => {
      if (!cur) return;
      const n = nameOf(cur + "_COPY");
      draft[n] = uhClone(draft[cur]); cur = n; renderAll();
    });
    copyBtn.id = "uhPropCopy";
    const delBtn = uhBtn("Delete", "btn-small", () => {
      if (!cur) return;
      const on = usesOf(m, renames[cur] || cur);
      if (on) { uhShowError(err, `"${cur}" is assigned to ${on} frame${on > 1 ? "s" : ""} — remove it there first (Assign > Frame > Hinges…).`); return; }
      delete draft[cur]; delete renames[cur];
      cur = Object.keys(draft)[0] || null; renderAll();
    });
    delBtn.id = "uhPropDelete";
    left.append(list, uhH("div", { class: "b9-sp-list-btns" }, [addType, addBtn, copyBtn, delBtn]));
    const right = uhH("div", { class: "uh-edit", id: "uhPropEditor" });
    grid.append(left, right);
    body.appendChild(grid);
    const err = uhErrorLine();
    body.appendChild(err);

    function renderList() {
      list.textContent = "";
      const names = Object.keys(draft);
      if (!names.length) list.appendChild(uhH("p", { class: "muted b9-small", text: "No hinge properties. Pick a type and click Add." }));
      for (const n of names) {
        const used = usesOf(m, renames[n] || n);
        const b = uhH("button", { type: "button", class: "b9-sp-item" + (n === cur ? " is-sel" : ""), "data-name": n, role: "option" }, [
          uhH("b", { text: n }), uhH("span", { class: "muted", text: (draft[n].type === "PMM_fiber" ? "PMM fiber" : draft[n].type) + " · " + (draft[n].hysteresis || "kinematic") + (used ? ` · ${used}×` : "") })]);
        b.addEventListener("click", () => { cur = n; renderAll(); });
        list.appendChild(b);
      }
    }

    let plotBox = null;
    const replot = () => {
      if (!plotBox || !cur) return;
      plotBox.innerHTML = uhBackboneSvg(draft[cur]);
      const e = uhValidateProperty(cur, draft[cur]);
      uhShowError(err, e || "");
    };

    function renderEditor() {
      right.textContent = "";
      plotBox = null;
      if (!cur) { right.appendChild(uhH("p", { class: "muted", text: "Select or add a hinge property." })); return; }
      const p = draft[cur];
      const kd = uhKinds(p.type);
      /* --- name + type --- */
      const nameIn = uhH("input", { type: "text", id: "uhPropName", value: cur, spellcheck: "false", autocomplete: "off" });
      nameIn.addEventListener("change", () => {
        const nn = nameIn.value.trim();
        if (!nn || nn === cur) { nameIn.value = cur; return; }
        if (draft[nn]) { uhShowError(err, `A property named "${nn}" already exists.`); nameIn.value = cur; return; }
        const rebuilt = {};
        for (const [k, v] of Object.entries(draft)) rebuilt[k === cur ? nn : k] = v;
        for (const k of Object.keys(draft)) delete draft[k];
        Object.assign(draft, rebuilt);
        renames[nn] = renames[cur] || cur; delete renames[cur];
        cur = nn; renderAll();
      });
      const typeSel = uhH("select", { id: "uhPropType" });
      typeSel.innerHTML = UH_TYPES.map(([t, l]) => `<option value="${t}"${t === p.type ? " selected" : ""}>${uhEsc(l)}</option>`).join("");
      typeSel.addEventListener("change", () => {
        const t = typeSel.value;
        if (t === "PMM_fiber") { delete p.backbone; }
        else if (!p.backbone) p.backbone = uhNewProperty(t).backbone;
        p.type = t;
        renderAll();
      });
      right.appendChild(uhH("div", { class: "uh-top" }, [
        uhH("label", { class: "uh-fld" }, [uhH("span", { text: "Property name" }), nameIn]),
        uhH("label", { class: "uh-fld" }, [uhH("span", { text: "Degree of freedom" }), typeSel]),
      ]));
      const cols = uhH("div", { class: "uh-cols" });
      const colL = uhH("div", { class: "uh-col" }), colR = uhH("div", { class: "uh-col" });
      cols.append(colL, colR);
      right.appendChild(cols);
      const fiber = p.type === "PMM_fiber";
      p.scale = p.scale || { yield_value: 1.0, yield_deformation: null };
      const sc = p.scale;
      const absF = (sc.yield_value == null ? 1 : sc.yield_value) === 1;
      const yd = sc.yield_deformation;
      const dKind = yd == null ? kd.d : "none";
      const fKind = absF ? kd.f : "none";

      /* --- backbone table(s) --- */
      if (!fiber) {
        const gB = uhGroup("Force – Deformation (backbone)");
        const isSym = Array.isArray(p.backbone);
        const symChk = uhH("input", { type: "checkbox", id: "uhPropSym" });
        symChk.checked = isSym;
        symChk.addEventListener("change", () => {
          if (symChk.checked) p.backbone = uhClone(uhSides(p)[0]);
          else p.backbone = { positive: uhClone(p.backbone), negative: uhClone(p.backbone) };
          renderAll();
        });
        gB.appendChild(uhH("label", { class: "dlg-chk" }, [symChk, uhH("span", { text: "Symmetric (negative = positive)" })]));
        const table = (arr, sg, idp) => {
          const t = uhH("div", { class: "uh-bbt", id: idp });
          t.appendChild(uhH("div", { class: "uh-bbt-row head" }, [uhH("span", { text: "Pt" }),
            uhH("span", { text: `${sg < 0 ? "−" : ""}Plastic def${dKind === "none" ? " / SF" : " (" + UHU.label(dKind) + ")"}` }),
            uhH("span", { text: `${sg < 0 ? "−" : ""}Force${fKind === "none" ? " / SF" : " (" + UHU.label(fKind) + ")"}` })]));
          arr.forEach((q, i) => {
            const row = uhH("div", { class: "uh-bbt-row" }, [uhH("b", { text: UH_POINT_LABELS[i] })]);
            if (i === 0) {
              row.append(uhH("span", { class: "muted", text: "0" }), uhH("span", { class: "muted", text: "0" }));
            } else {
              for (const j of [0, 1]) {
                const kind = j === 0 ? dKind : fKind;
                const f = uhNum(kind, sg * Math.abs(q[j]), { id: `${idp}_${i}_${j}`, "aria-label": `${UH_POINT_LABELS[i]} ${j ? "force" : "deformation"}` });
                f.el.addEventListener("input", () => { const v = f.get(); q[j] = (v == null) ? NaN : Math.abs(v); replot(); });
                row.appendChild(f.el);
              }
            }
            t.appendChild(row);
          });
          const addP = uhBtn("+ Point", "btn-small", () => {
            if (arr.length >= UH_MAX_POINTS) return;
            const l = arr[arr.length - 1];
            arr.push([+(Math.abs(l[0]) + 0.01).toPrecision(6), Math.abs(l[1])]); renderAll();
          }, "Add a backbone point after the last one");
          const delP = uhBtn("− Point", "btn-small", () => { if (arr.length > 2) { arr.pop(); renderAll(); } }, "Remove the last backbone point");
          addP.id = idp + "Add"; delP.id = idp + "Del";
          if (arr.length >= UH_MAX_POINTS) addP.disabled = true;
          if (arr.length <= 2) delP.disabled = true;
          t.appendChild(uhH("div", { class: "b9-sp-list-btns" }, [addP, delP]));
          return t;
        };
        if (isSym) gB.appendChild(table(p.backbone, 1, "uhBbPos"));
        else {
          gB.appendChild(uhH("p", { class: "muted b9-small", text: "Positive side" }));
          gB.appendChild(table(p.backbone.positive, 1, "uhBbPos"));
          gB.appendChild(uhH("p", { class: "muted b9-small", text: "Negative side (stored as magnitudes)" }));
          gB.appendChild(table(p.backbone.negative, -1, "uhBbNeg"));
        }
        colL.appendChild(gB);
      } else {
        colL.appendChild(uhH("p", { class: "muted b9-small", text:
          "P-M2-M3 fiber hinge: the section fibers give the backbone (HingeRadau forceBeamColumn, hinges at both member ends). Only the acceptance criteria and the yield-value fallback (used when the section cannot be sized) apply. Hinge length: Assign > Frame > Hinge Overwrites (relative length; default 0.5 h)." }));
      }

      /* --- scale factors --- */
      const gS = uhGroup("Scale Factors");
      const rad = (name, val, label, checked, onPick, id) => {
        const r = uhH("input", { type: "radio", name, value: val, id });
        r.checked = checked;
        r.addEventListener("change", () => { if (r.checked) onPick(); });
        return uhH("label", { class: "dlg-chk" }, [r, uhH("span", { text: label })]);
      };
      const yvF = uhNum(kd.f, absF ? null : sc.yield_value, { id: "uhPropYv", min: "0", placeholder: "—" });
      yvF.el.disabled = absF;
      yvF.el.addEventListener("input", () => { const v = yvF.get(); sc.yield_value = v == null ? NaN : v; replot(); });
      gS.append(uhH("div", { class: "uh-sf-row" }, [uhH("span", { class: "uh-sf-h", text: fiber ? "Yield value (My fallback)" : "Force" }),
        rad("uhSfF", "abs", "Absolute values", absF, () => { sc.yield_value = 1.0; renderAll(); }, "uhSfFAbs"),
        rad("uhSfF", "yv", "Yield value", !absF, () => { sc.yield_value = UHU.parse(kd.f, "100"); renderAll(); }, "uhSfFYv"),
        yvF.el, uhH("span", { class: "asn-unit", text: yvF.unit() })]));
      const ydF = uhNum(kd.d, isNum(yd) ? yd : (kd.d === "rotation" ? 0.01 : 0.005), { id: "uhPropYd", min: "0" });
      ydF.el.disabled = !isNum(yd);
      ydF.el.addEventListener("input", () => { const v = ydF.get(); sc.yield_deformation = v == null ? NaN : v; replot(); });
      gS.append(uhH("div", { class: "uh-sf-row" }, [uhH("span", { class: "uh-sf-h", text: "Deformation" }),
        rad("uhSfD", "abs", "Absolute", yd == null, () => { sc.yield_deformation = null; renderAll(); }, "uhSfDAbs"),
        rad("uhSfD", "auto", "Auto (member yield)", yd === "auto", () => { sc.yield_deformation = "auto"; renderAll(); }, "uhSfDAuto"),
        rad("uhSfD", "val", "Yield deformation", isNum(yd) || (yd != null && yd !== "auto"), () => { sc.yield_deformation = kd.d === "rotation" ? 0.01 : 0.005; renderAll(); }, "uhSfDVal"),
        ydF.el, uhH("span", { class: "asn-unit", text: ydF.unit() })]));
      if (yd === "auto") gS.appendChild(uhH("p", { class: "muted b9-small", text: "Auto: deformations are multiples of F_B / k_member (k_member = 6EI/L for M3/M2, EA/L for P, 12EI/L³ for V2)." }));
      colL.appendChild(gS);

      /* --- acceptance --- */
      const gA = uhGroup("Acceptance Criteria (plastic deformation" + (dKind === "none" ? " / SF)" : ")"));
      const accOn = uhH("input", { type: "checkbox", id: "uhPropAccOn" });
      accOn.checked = p.acceptance != null;
      accOn.addEventListener("change", () => {
        p.acceptance = accOn.checked ? { IO: 0.005, LS: 0.015, CP: 0.02 } : null;
        renderAll();
      });
      gA.appendChild(uhH("label", { class: "dlg-chk" }, [accOn, uhH("span", { text: "Use acceptance criteria (blank level = never reached)" })]));
      if (p.acceptance) {
        const row = uhH("div", { class: "uh-acc-row" });
        for (const key of ["IO", "LS", "CP"]) {
          const f = uhNum(dKind, p.acceptance[key] ?? null, { id: "uhPropAcc" + key, min: "0", placeholder: "—" });
          f.el.addEventListener("input", () => {
            const v = f.get();
            if (v == null) delete p.acceptance[key]; else p.acceptance[key] = v;
            replot();
          });
          row.appendChild(uhH("label", { class: "uh-fld uh-acc-" + key }, [uhH("span", { text: key === "IO" ? "Immediate Occupancy" : key === "LS" ? "Life Safety" : "Collapse Prevention" }), f.el]));
        }
        gA.appendChild(row);
      }
      (fiber ? colL : colR).appendChild(gA);

      if (!fiber) {
        /* --- plot --- */
        const gP = uhGroup("Backbone (scaled)", "uh-plot-grp");
        plotBox = uhH("div", { class: "uh-plot-box" });
        gP.append(plotBox, uhH("div", { class: "uh-legend", html:
          `<span><i class="uh-sw uh-sw-bb"></i>backbone A–E</span><span><i class="uh-sw uh-sw-tail"></i>after last point</span>` +
          `<span><i class="uh-sw" style="background:${UH_STATE_COLORS["B-IO"]}"></i>IO</span><span><i class="uh-sw" style="background:${UH_STATE_COLORS["IO-LS"]}"></i>LS</span><span><i class="uh-sw" style="background:${UH_STATE_COLORS[">CP"]}"></i>CP</span>` }));
        colR.prepend(gP);

        /* --- hysteresis / drop / k_e --- */
        const gH = uhGroup("Hysteresis & Behaviour");
        const hySel = uhH("select", { id: "uhPropHyst" });
        hySel.innerHTML = UH_HYST.map(([h, l]) => `<option value="${h}"${h === (p.hysteresis || "kinematic") ? " selected" : ""}>${l}</option>`).join("");
        hySel.addEventListener("change", () => {
          p.hysteresis = hySel.value;
          if (!UH_HYST_DEFAULTS[p.hysteresis]) p.hysteresis_params = null;
          renderAll();
        });
        const dsSel = uhH("select", { id: "uhPropDrop" });
        dsSel.innerHTML = UH_DROPS.map(([d, l]) => `<option value="${d}"${d === (p.drop_strength || "drops") ? " selected" : ""}>${l}</option>`).join("");
        dsSel.addEventListener("change", () => { p.drop_strength = dsSel.value; replot(); });
        gH.append(uhH("label", { class: "uh-fld" }, [uhH("span", { text: "Hysteresis type" }), hySel]),
          uhH("label", { class: "uh-fld" }, [uhH("span", { text: "Load carrying capacity beyond the last point" }), dsSel]));
        if (p.hysteresis === "isotropic") gH.appendChild(uhH("p", { class: "muted b9-small", text: "Isotropic is approximated by the kinematic multi-linear rule (exact monotonic envelope)." }));
        const defs = UH_HYST_DEFAULTS[p.hysteresis];
        if (defs) {
          const hpOn = uhH("input", { type: "checkbox", id: "uhPropHpOn" });
          hpOn.checked = p.hysteresis_params != null;
          hpOn.addEventListener("change", () => { p.hysteresis_params = hpOn.checked ? { ...defs } : null; renderAll(); });
          gH.appendChild(uhH("label", { class: "dlg-chk" }, [hpOn, uhH("span", { text: "Custom hysteresis parameters (else defaults " + UH_HP_KEYS.map(k => defs[k]).join(" / ") + ")" })]));
          if (p.hysteresis_params) {
            const row = uhH("div", { class: "uh-hp-row" });
            for (const key of UH_HP_KEYS) {
              const f = uhNum("none", p.hysteresis_params[key] ?? defs[key], { id: "uhPropHp_" + key });
              f.el.addEventListener("input", () => { const v = f.get(); p.hysteresis_params[key] = v == null ? NaN : v; replot(); });
              row.appendChild(uhH("label", { class: "uh-fld" }, [uhH("span", { text: key.replace("_", " ") }), f.el]));
            }
            gH.appendChild(row);
          }
        }
        const keOn = uhH("input", { type: "checkbox", id: "uhPropKeDef" });
        keOn.checked = p.k_elastic == null;
        const keF = uhNum(kd.k, p.k_elastic ?? null, { id: "uhPropKe", min: "0", placeholder: "10 × k_member" });
        keF.el.disabled = keOn.checked;
        keOn.addEventListener("change", () => {
          if (keOn.checked) p.k_elastic = null;
          else p.k_elastic = UHU.parse(kd.k, "100000");
          renderAll();
        });
        keF.el.addEventListener("input", () => { const v = keF.get(); p.k_elastic = v == null ? NaN : v; replot(); });
        gH.append(uhH("label", { class: "dlg-chk" }, [keOn, uhH("span", { text: "Default hinge elastic stiffness (10 × member stiffness)" })]),
          uhH("div", { class: "uh-sf-row" }, [uhH("span", { class: "uh-sf-h", text: "k elastic" }), keF.el, uhH("span", { class: "asn-unit", text: keF.unit() })]));
        colR.appendChild(gH);
      }
      replot();
    }
    function renderAll() { renderList(); renderEditor(); }

    const apply = () => {
      for (const [n, p] of Object.entries(draft)) {
        const e = uhValidateProperty(n, p);
        if (e) { cur = n; renderAll(); uhShowError(err, e); err.scrollIntoView({ block: "nearest" }); return false; }
      }
      // canonicalise edited properties (magnitudes; floats)
      const out = {};
      let changed = false;
      for (const [n, p] of Object.entries(draft)) {
        const on = renames[n] || n;
        if (orig[on] && uhSame(orig[on], p)) { out[n] = orig[on]; if (on !== n) changed = true; continue; }
        const q = uhClone(p);
        const mag = a => a.map(r => [Math.abs(r[0]), Math.abs(r[1])]);
        if (q.backbone) q.backbone = Array.isArray(q.backbone) ? mag(q.backbone) : { positive: mag(q.backbone.positive), negative: mag(q.backbone.negative) };
        out[n] = q; changed = true;
      }
      for (const on of Object.keys(orig)) if (!Object.entries(renames).some(([, o]) => o === on) && !(on in draft)) changed = true;
      if (!changed && Object.keys(out).length === Object.keys(orig).length) return true;
      // renames → member hinge lists
      const rmap = {};
      for (const [nn, on] of Object.entries(renames)) if (nn !== on) rmap[on] = nn;
      if (Object.keys(rmap).length)
        for (const mm of m.members || []) if (isUserList(mm)) for (const h of mm.hinges) if (h && rmap[h.property]) h.property = rmap[h.property];
      if (Object.keys(out).length) m.hinge_properties = out; else delete m.hinge_properties;
      uhAfter(sky, "hinge-properties");
      return true;
    };
    const fb = uhFootBar("Hinges act only in hinged (pushover / nonlinear static / nonlinear TH) cases.", [
      uhBtn("Cancel", "", () => dlg.close()),
      uhBtn("OK", "btn-primary", () => { if (apply()) { dlg.close(); onDone && onDone(); } }),
    ]);
    fb.wrap.querySelector(".btn-primary").id = "uhPropOk";
    const dlg = uhDialog("uhPropsDlg", { title: "Frame Hinge Properties", iconId: "tool-beam", wide: true, body, foot: fb.wrap });
    renderAll();
    return dlg;
  }

  /* ================= Assign > Frame > Hinges / Hinge Overwrites ================= */
  function lineSvg(mm, rows, props) {
    const W = 460, H = 70, x0 = 22, x1 = W - 22, y = 32;
    const X = t => x0 + t * (x1 - x0);
    const L = uhLen(mm);
    let s = `<svg viewBox="0 0 ${W} ${H}" class="b9-line-svg" id="uhAsnPreview" role="img" aria-label="hinge locations">`;
    s += `<line x1="${x0}" y1="${y}" x2="${x1}" y2="${y}" class="b9-mem"/>`;
    s += `<circle cx="${x0}" cy="${y}" r="4.5" class="b9-end"/><circle cx="${x1}" cy="${y}" r="4.5" class="b9-end"/>`;
    for (const r of rows) {
      if (!isNum(r.relative_distance)) continue;
      const t = Math.min(1, Math.max(0, r.relative_distance));
      const pr = props[r.property];
      s += `<g class="uh-asn-dot"><title>${uhEsc(r.property)} @ ${t} (${uhEsc(UHU.fmtU("length", t * L, 2))})</title>` +
        `<circle cx="${X(t).toFixed(1)}" cy="${y}" r="5.5"/><text x="${X(t).toFixed(1)}" y="${y - 10}" class="b9-tick" text-anchor="middle">${uhEsc(pr ? (pr.type === "PMM_fiber" ? "PMM" : pr.type) : "?")}</text></g>`;
    }
    s += `<text x="${x0}" y="${H - 6}" class="b9-tick">i (0)</text><text x="${x1}" y="${H - 6}" class="b9-tick" text-anchor="end">j (1) · L = ${uhEsc(UHU.fmtU("length", L, 2))}</text>`;
    return s + "</svg>";
  }

  function openFrameHinges(tab) {
    const m = S.model;
    if (!m) return null;
    const mem = frames();
    if (!mem.length) { toast("Assign Frame Hinges", "Select one or more frame members first (Select tool).", "error"); return null; }
    const m0 = mem[0];
    let cur = tab === "overwrites" ? "overwrites" : "hinges";
    const body = uhH("div", { class: "uh-asn" });
    const sameH = mem.every(x => uhSame(isUserList(x) ? x.hinges : null, isUserList(m0) ? m0.hinges : null));
    const sameO = mem.every(x => uhSame(x.hinge_overwrites ?? null, m0.hinge_overwrites ?? null));
    body.appendChild(uhH("p", { class: "muted dlg-intro", html:
      `${mem.length} frame${mem.length > 1 ? "s" : ""} selected (${uhEsc(mem.slice(0, 5).map(x => x.uid).join(", "))}${mem.length > 5 ? ", …" : ""}).` +
      ((!sameH || !sameO) ? ` <b>Assignments differ across the selection — showing ${uhEsc(m0.uid)}; only the tab you change is written.</b>` : "") }));
    const tabs = uhTabs([["hinges", "Frame Hinges"], ["overwrites", "Hinge Overwrites"]], k => { cur = k; show(); }, "uhAsnTabs");
    body.appendChild(uhH("div", { class: "b9-top" }, [tabs.el]));
    const err = uhErrorLine();

    /* --- hinges tab --- */
    const paneH = uhH("div", { class: "b9-pane", id: "uhAsnPaneHinges" });
    let touchedH = false;
    let userMode = isUserList(m0);
    let rows = isUserList(m0) ? uhClone(m0.hinges) : [];
    const props = () => m.hinge_properties || {};
    const gH = uhGroup("Frame Hinge Assignment");
    const rAuto = uhH("input", { type: "radio", name: "uhAsnMode", id: "uhAsnAuto", value: "auto" });
    const rUser = uhH("input", { type: "radio", name: "uhAsnMode", id: "uhAsnUser", value: "user" });
    rAuto.checked = !userMode; rUser.checked = userMode;
    const legacy = typeof m0.hinges === "string" && m0.hinges !== "none" ? ` — member keeps "${m0.hinges}"` : "";
    gH.append(uhH("label", { class: "dlg-chk" }, [rAuto, uhH("span", { text: "Auto (case default) — the case's hinge option decides" + legacy })]),
      uhH("label", { class: "dlg-chk" }, [rUser, uhH("span", { text: "User-defined hinges (replace the case's automatic hinges; an empty list = no hinges)" })]));
    for (const r of [rAuto, rUser]) r.addEventListener("change", () => { touchedH = true; userMode = rUser.checked; renderRows(); });
    const tbl = uhH("div", { class: "uh-asn-table", id: "uhAsnRows" });
    const btns = uhH("div", { class: "b9-sp-list-btns uh-asn-btns" });
    const firstProp = () => Object.keys(props())[0] || "";
    const addRow = d => { rows.push({ property: (rows[rows.length - 1] || {}).property || firstProp(), relative_distance: d }); };
    const mkB = (label, id, fn, title) => { const b = uhBtn(label, "btn-small", () => { touchedH = true; userMode = true; rUser.checked = true; fn(); renderRows(); }, title); b.id = id; return b; };
    btns.append(
      mkB("Add", "uhAsnAdd", () => addRow(0.5), "Add a hinge row"),
      mkB("0.05 + 0.95", "uhAsnPreset", () => { rows = rows.filter(r => r.relative_distance !== 0.05 && r.relative_distance !== 0.95); addRow(0.05); addRow(0.95); }, "ETABS default beam hinge locations"),
      mkB("Ends 0 + 1", "uhAsnEnds", () => { rows = rows.filter(r => r.relative_distance !== 0 && r.relative_distance !== 1); addRow(0); addRow(1); }, "Hinges at both joints"),
      mkB("Clear list", "uhAsnClear", () => { rows = []; }, "Empty list = no hinges on these frames"),
      uhBtn("Hinge Properties…", "btn-small", () => openHingeProperties(rows.length ? rows[0].property : null, () => renderRows()), "Define > Frame Hinge Properties"),
    );
    gH.append(tbl, btns);
    const prevBox = uhH("div", { class: "b9-prev-box" });
    paneH.append(gH, prevBox);

    function renderRows() {
      tbl.textContent = "";
      tbl.classList.toggle("is-off", !userMode);
      const names = Object.keys(props());
      if (!names.length) tbl.appendChild(uhH("p", { class: "muted b9-small", text: "No hinge properties defined yet — click Hinge Properties… to add one." }));
      tbl.appendChild(uhH("div", { class: "uh-asn-row head" }, [uhH("span", { text: "Hinge property" }), uhH("span", { text: "Relative distance (0–1)" }), uhH("span", { text: "Distance" }), uhH("span")]));
      rows.forEach((r, i) => {
        const sel = uhH("select", { id: `uhAsnProp_${i}` });
        const opts = names.includes(r.property) ? names : [r.property, ...names];
        sel.innerHTML = opts.map(n => `<option value="${uhEsc(n)}"${n === r.property ? " selected" : ""}>${uhEsc(n)}${props()[n] ? " (" + (props()[n].type === "PMM_fiber" ? "PMM" : props()[n].type) + ")" : " (missing)"}</option>`).join("");
        sel.disabled = !userMode;
        sel.addEventListener("change", () => { r.property = sel.value; touchedH = true; preview(); });
        const d = uhH("input", { type: "number", step: "0.05", min: "0", max: "1", id: `uhAsnDist_${i}`, value: String(r.relative_distance) });
        d.disabled = !userMode;
        const abs = uhH("span", { class: "muted b9-small", text: UHU.fmtU("length", (isNum(r.relative_distance) ? r.relative_distance : 0) * uhLen(m0), 2) });
        d.addEventListener("input", () => {
          const v = d.value.trim() === "" ? NaN : +d.value;
          r.relative_distance = v; touchedH = true;
          abs.textContent = isFinite(v) ? UHU.fmtU("length", v * uhLen(m0), 2) : "—";
          preview();
        });
        const x = uhH("button", { type: "button", class: "chip-x", title: "Remove hinge", text: "✕", id: `uhAsnDel_${i}` });
        x.disabled = !userMode;
        x.addEventListener("click", () => { rows.splice(i, 1); touchedH = true; renderRows(); });
        tbl.appendChild(uhH("div", { class: "uh-asn-row" }, [sel, d, abs, x]));
      });
      if (userMode && !rows.length) tbl.appendChild(uhH("p", { class: "muted b9-small", text: "Empty list — these frames get NO hinges (not even the case's automatic ones)." }));
      preview();
    }
    function preview() {
      prevBox.innerHTML = lineSvg(m0, userMode ? rows : [], props());
      uhShowError(err, "");
    }

    /* --- overwrites tab --- */
    const paneO = uhH("div", { class: "b9-pane", id: "uhAsnPaneOver" });
    let touchedO = false;
    const ov0 = m0.hinge_overwrites || null;
    const gO = uhGroup("Hinge Overwrites");
    const oOn = uhH("input", { type: "checkbox", id: "uhOvOn" });
    oOn.checked = ov0 != null;
    const sub = uhH("input", { type: "checkbox", id: "uhOvSub" });
    sub.checked = !!(ov0 && ov0.auto_subdivide);
    const rlOn = uhH("input", { type: "checkbox", id: "uhOvRlOn" });
    rlOn.checked = !!(ov0 && ov0.relative_length != null);
    const rl = uhH("input", { type: "number", step: "0.01", min: "0", max: "0.5", id: "uhOvRl", value: ov0 && ov0.relative_length != null ? String(ov0.relative_length) : "0.1" });
    const sync = () => { sub.disabled = rlOn.disabled = !oOn.checked; rl.disabled = !oOn.checked || !rlOn.checked; };
    for (const c of [oOn, sub, rlOn]) c.addEventListener("change", () => { touchedO = true; sync(); });
    rl.addEventListener("input", () => { touchedO = true; });
    gO.append(uhH("label", { class: "dlg-chk" }, [oOn, uhH("span", { text: "Overwrite hinge settings for these frames" })]),
      uhH("div", { class: "uh-ov-body" }, [
        uhH("label", { class: "dlg-chk" }, [sub, uhH("span", { text: "Auto subdivide the member at hinges (stored; interior lumped hinges always split the member)" })]),
        uhH("div", { class: "b9-opt-row" }, [uhH("label", { class: "dlg-chk" }, [rlOn, uhH("span", { text: "Relative hinge length (fiber PMM: lp = x · L)" })]), rl, uhH("span", { class: "asn-unit", text: "× L" })]),
      ]));
    paneO.append(gO, uhH("p", { class: "muted b9-small", text: "The relative length sets the HingeRadau plastic-hinge length of PMM fiber hinges (default 0.5 h). Lumped M3/M2/P/V2 hinges are zero-length, so it has no effect on them." }));
    sync();
    body.append(paneH, paneO, err);

    function show() {
      tabs.set(cur);
      paneH.classList.toggle("hidden", cur !== "hinges");
      paneO.classList.toggle("hidden", cur !== "overwrites");
    }
    const readOv = () => {
      if (!oOn.checked) return null;
      const o = { auto_subdivide: sub.checked };
      if (rlOn.checked) {
        const v = rl.value.trim() === "" ? NaN : +rl.value;
        if (!(v > 0 && v <= 0.5)) return "Relative hinge length must be in (0, 0.5].";
        o.relative_length = v;
      }
      return o;
    };
    const apply = () => {
      const ov = readOv();
      if (touchedO && typeof ov === "string") { cur = "overwrites"; show(); uhShowError(err, ov); return false; }
      // validate on a scratch copy first
      const trial = mem.map(x => {
        const c = { ...x };
        if (touchedH) c.hinges = userMode ? uhClone(rows) : (isUserList(x) ? "none" : x.hinges);
        if (touchedO) { if (ov) c.hinge_overwrites = ov; else delete c.hinge_overwrites; }
        return c;
      });
      for (const c of trial) {
        const e = uhValidateMemberHinges(m, c);
        if (e) { cur = touchedH ? "hinges" : "overwrites"; show(); uhShowError(err, e); return false; }
      }
      let changed = false;
      mem.forEach((x, i) => {
        const c = trial[i];
        if (touchedH && !uhSame(x.hinges, c.hinges)) { x.hinges = c.hinges; changed = true; }
        if (touchedO) {
          if (!ov) { if ("hinge_overwrites" in x) { delete x.hinge_overwrites; changed = true; } }
          else if (!uhSame(x.hinge_overwrites, ov)) { x.hinge_overwrites = { ...ov }; changed = true; }
        }
      });
      if (changed) { S.modelEdited = true; uhAfter(sky, "frame-hinges"); }
      return true;
    };
    const fb = uhFootBar("Relative distance is measured along the joint-to-joint length (i = 0, j = 1).", [
      uhBtn("Cancel", "", () => dlg.close()),
      uhBtn("Apply", "", () => { if (apply()) { touchedH = touchedO = false; toast("Frame Hinges", "Assignment applied."); } }),
      uhBtn("OK", "btn-primary", () => { if (apply()) dlg.close(); }),
    ]);
    fb.wrap.querySelector(".btn-primary").id = "uhAsnOk";
    const dlg = uhDialog("uhAssignDlg", { title: "Assign Frame Hinges", iconId: "tool-beam", body, foot: fb.wrap });
    show(); renderRows();
    return dlg;
  }

  /* ================= Properties panel badge ================= */
  function decorateProps(box, sel) {
    const mem = (sel && sel.members) || [];
    if (!mem.length || !S.model) return;
    const users = mem.filter(isUserList);
    const selEl = box.querySelector("#propHinges");
    if (selEl && users.length) {
      const o = document.createElement("option");
      o.value = "__uh_user"; o.textContent = users.length === mem.length ? `User-defined (${users[0].hinges.length} hinge${users[0].hinges.length === 1 ? "" : "s"})` : "User-defined (some members)";
      selEl.prepend(o);
      if (users.length === mem.length) selEl.value = "__uh_user";
      selEl.addEventListener("change", e => { if (e.target.value === "__uh_user") openFrameHinges("hinges"); });
    }
    const txt = users.length
      ? (users.length === mem.length && users.every(x => uhSame(x.hinges, users[0].hinges))
        ? (users[0].hinges.length ? users[0].hinges.map(h => `${h.property} @ ${h.relative_distance}`).join(", ") : "none (empty list)")
        : "mixed")
      : "auto (case default)";
    const ov = mem.map(x => x.hinge_overwrites ? `rel. length ${x.hinge_overwrites.relative_length ?? "—"}${x.hinge_overwrites.auto_subdivide ? " · subdivide" : ""}` : "none");
    const wrap = uhH("div", { class: "b9-props", id: "uhPropsBlock" }, [
      uhH("h3", { class: "group-title", html: `User hinges <span class="unit">B10</span>` }),
      uhH("div", { class: "b9-props-row" }, [uhH("span", { class: "muted", text: "Hinges" }), uhH("b", { text: txt, title: txt })]),
      uhH("div", { class: "b9-props-row" }, [uhH("span", { class: "muted", text: "Overwrites" }), uhH("b", { text: ov.every(v => v === ov[0]) ? ov[0] : "mixed" })]),
      uhH("div", { class: "b9-props-btns" }, [
        uhBtn("Hinges…", "btn-small", () => openFrameHinges("hinges"), "Assign > Frame > Hinges"),
        uhBtn("Overwrites…", "btn-small", () => openFrameHinges("overwrites"), "Assign > Frame > Hinge Overwrites"),
      ]),
    ]);
    const del = box.querySelector("#propDelete");
    const anchor = del ? del.previousElementSibling : null;
    if (anchor && anchor.parentNode === box) box.insertBefore(wrap, anchor); else box.appendChild(wrap);
  }

  /* ================= hinge dots: plan / elevation (SVG) ================= */
  const NS = "http://www.w3.org/2000/svg";
  const R = () => sky.uhResultsState && sky.uhResultsState();   // {stateOf(uid, d) → state|null} when the results display is on
  function dotColor(loc) {
    const rs = R();
    const st = rs && rs.active ? rs.stateOf(loc.mm.uid, loc.d) : null;
    return st ? UH_STATE_COLORS[st] : null;
  }
  function svgOverlay(editor, kind) {
    const m = S.model;
    const locs = uhLocations(m);
    if (!locs.length || !editor.gElems) return;
    const layer = document.createElementNS(NS, "g");
    layer.setAttribute("class", "uh-dot-layer");
    layer.setAttribute("pointer-events", "none");
    const r = 5.5 / (editor.scale || 40);
    let st = null;
    if (kind === "plan") st = (m.stories || []).find(s => s.name === editor.opts.getStory());
    for (const loc of locs) {
      let xy = null;
      if (kind === "plan") {
        if (!st || loc.mm.story !== st.name) continue;
        if (loc.mm.kind === "column") continue;              // column hinges: elevation / 3D
        xy = [loc.p[0], loc.p[1]];
      } else {
        if (!editor.inPlane || !editor.inPlane(loc.mm.pi) || !editor.inPlane(loc.mm.pj)) continue;
        xy = [editor.sOf(loc.p), loc.p[2]];
      }
      const col = dotColor(loc);
      const c = document.createElementNS(NS, "circle");
      c.setAttribute("cx", xy[0]); c.setAttribute("cy", xy[1]); c.setAttribute("r", r);
      c.setAttribute("class", "uh-dot" + (col ? " uh-dot-state" : ""));
      c.setAttribute("fill", col || "#d946ef");
      c.setAttribute("stroke", "#0b0f14"); c.setAttribute("stroke-width", "1.2");
      c.setAttribute("vector-effect", "non-scaling-stroke");
      c.setAttribute("data-uh", `${loc.mm.uid}@${loc.d}`);
      const t = document.createElementNS(NS, "title");
      t.textContent = `${loc.h.property} @ ${loc.d} (${loc.mm.uid})`;
      c.appendChild(t);
      layer.appendChild(c);
    }
    // plan: column hinges as a ring around the column
    if (kind === "plan" && st) {
      const seen = new Set();
      for (const loc of locs) {
        if (loc.mm.kind !== "column" || loc.mm.story !== st.name || seen.has(loc.mm.uid)) continue;
        seen.add(loc.mm.uid);
        const c = document.createElementNS(NS, "circle");
        c.setAttribute("cx", loc.mm.pi[0]); c.setAttribute("cy", loc.mm.pi[1]); c.setAttribute("r", 3 * r);
        c.setAttribute("fill", "none"); c.setAttribute("stroke", dotColor(loc) || "#d946ef"); c.setAttribute("stroke-width", "1.6");
        c.setAttribute("stroke-dasharray", "3 2"); c.setAttribute("vector-effect", "non-scaling-stroke");
        c.setAttribute("class", "uh-dot-ring"); c.setAttribute("data-uh", loc.mm.uid);
        layer.appendChild(c);
      }
    }
    editor.gElems.appendChild(layer);
  }
  if (sky.planEditor) sky.planEditor.uhOverlay = ed => svgOverlay(ed, "plan");
  if (sky.elevEditor) sky.elevEditor.uhOverlay = ed => svgOverlay(ed, "elev");

  /* ================= hinge dots: 3D (canvas) ================= */
  function draw3d(ctx, P, viewer) {
    const m = S.model;
    const locs = uhLocations(m);
    if (!locs.length) return;
    const rs = R();
    if (rs && rs.active && rs.draw3d) { rs.draw3d(ctx, P, viewer, locs); return; }
    ctx.save();
    for (const loc of locs) {
      const pc = P.toCam(loc.p);
      if (pc[2] < P.near) continue;
      const q = P.proj(pc);
      ctx.beginPath(); ctx.arc(q.x, q.y, 4.2, 0, Math.PI * 2);
      ctx.fillStyle = "#d946ef"; ctx.fill();
      ctx.lineWidth = 1.2; ctx.strokeStyle = "#0b0f14"; ctx.stroke();
    }
    ctx.restore();
  }
  if (sky.viewer) sky.viewer.uhOverlay = draw3d;

  const redraw = () => {
    try { if (sky.planEditor) sky.planEditor.renderStatic(); } catch (e) { console.error(e); }
    try { if (sky.elevEditor && sky.elevEditor.refresh) sky.elevEditor.refresh(); } catch (e) { console.error(e); }
    if (sky.viewer) sky.viewer._dirty = true;
  };

  sky.openHingeProperties = openHingeProperties;
  sky.openFrameHinges = openFrameHinges;
  sky.uhDecorateProps = (box, sel) => { try { decorateProps(box, sel); } catch (e) { console.error("user hinge props decoration failed", e); } };
  sky.userHinges = { openHingeProperties, openFrameHinges, decorateProps, redraw, locations: () => uhLocations(S.model) };
  try { uhInitResults(sky, { redraw }); } catch (e) { console.error("hinge results init failed", e); }
  return sky.userHinges;
}
