/* SkyFrame — Tools › Explain Result… (load-path explainer, beyond ETABS;
   CONTRACT "Load-path explainer").

   Pick a result (support reaction, joint displacement, frame force at a
   station, story response, base reaction, pier force) and a load case or
   combination; the backend (core/load_path.py via POST /api/explain;
   ?mock=1: js/mock_explain.js) returns the contributions:

     linear static case   per load pattern (stored or re-solved by
                          superposition; nonlinear cases get a message)
     combination          per member case × factor (patterns nested
                          below), with the governing member of envelope /
                          max-min combinations
     response spectrum    per mode: signed modal value, share of R², cross
                          terms of the CQC sum
     time history         the governing step and the next-largest steps

   Shown as a ranked table and a diverging bar chart (positive blue,
   negative orange, values labelled); clicking a row deep-links: a case /
   pattern shows its deformed shape, a mode its mode shape, a step the TH
   results, with the target object highlighted in 3D.

   API: window.__sky.explain = { open(target?, case?), explain(target, case),
   targetFromSelection() } — target = {kind, component, joint | member +
   station | story | pier + story} (Wave G's member detail window / result
   probes call open(target)). */

import exU from "./units.js";
import { el as exEl, esc as exEsc, dkForm as exForm, dkGroup as exGroup, dkDialog as exDialog, dkNote as exNote } from "./dlgkit.js";
import { mockExplain as exMock, EX_COMPONENTS as EX_COMP } from "./mock_explain.js";

const KIND_LABEL = [["reaction", "Support reaction"], ["member_force", "Frame force / moment"], ["joint_disp", "Joint displacement"],
  ["story", "Story response"], ["base", "Base reaction (total)"], ["pier", "Pier force"]];
const COMP_LABEL = { P: "P (axial)", V2: "V2", V3: "V3", T: "T (torsion)", M2: "M2", M3: "M3", ux: "UX displacement", uy: "UY displacement",
  drift_x: "Drift X", drift_y: "Drift Y", shear_x: "Shear X", shear_y: "Shear Y", V: "V (shear)", M: "M (moment)" };
const POS = "#3d7fc8", NEG = "#d0661e";   // diverging pair, validated (dataviz validator, light + dark)

const CSS = `
.dk-dlg .ex-tgt { display: grid; grid-template-columns: 1fr 1fr; gap: 0 14px; }
.dk-dlg .ex-head { font-size: 12.5px; margin: 6px 0 4px; }
.dk-dlg .ex-head b { font-weight: 600; }
.dk-dlg .ex-ok { color: var(--ok, #34c384); font-size: 11.5px; }
.dk-dlg .ex-bad { color: var(--warn, #e0b050); font-size: 11.5px; }
.dk-dlg .ex-chart { margin: 4px 0 8px; }
.dk-dlg .ex-chart text { font-size: 11px; fill: var(--text-2, #b8c0cc); font-variant-numeric: tabular-nums; }
.dk-dlg .ex-chart .ex-axis { stroke: var(--text-3, #6b7480); stroke-width: 1; }
.dk-dlg .ex-chart rect.ex-bar:hover { opacity: .8; }
.dk-dlg .ex-wrap { max-height: 250px; overflow: auto; border: 1px solid var(--border-soft, #333); border-radius: 4px; }
.dk-dlg .ex-table { width: 100%; border-collapse: collapse; font-size: 11.5px; font-variant-numeric: tabular-nums; }
.dk-dlg .ex-table th, .dk-dlg .ex-table td { padding: 3px 6px; border-bottom: 1px solid rgba(128,128,128,.18); text-align: right; white-space: nowrap; }
.dk-dlg .ex-table th { position: sticky; top: 0; background: var(--panel, #1b2129); }
.dk-dlg .ex-table .txt { text-align: left; }
.dk-dlg .ex-table tr.ex-child td.txt:nth-child(2) { padding-left: 22px; color: var(--text-2, #b8c0cc); }
.dk-dlg .ex-table tr[data-link] { cursor: pointer; }
.dk-dlg .ex-table tr[data-link]:hover td { background: var(--surface-3, #2a313b); }
.dk-dlg .ex-gov { font-size: 10px; padding: 0 5px; border-radius: 6px; border: 1px solid currentColor; margin-left: 4px; }
.dk-dlg .ex-notes { margin: 6px 0 0 16px; padding: 0; font-size: 11.5px; color: var(--text-2, #b8c0cc); }
.dk-dlg .ex-msg { font-size: 12px; margin: 6px 0; padding: 6px 8px; border-left: 3px solid var(--warn, #e0b050); background: rgba(224,176,80,.08); }
@media (max-width: 640px) { .dk-dlg .ex-tgt { grid-template-columns: 1fr; } }
`;
function ensureCss() {
  if (document.getElementById("exCss")) return;
  const s = document.createElement("style"); s.id = "exCss"; s.textContent = CSS; document.head.appendChild(s);
}

export function initExplain(sky) {
  const S = sky.store;
  const st = { kind: "reaction", component: "FZ", joint: "", member: "", station: 0, story: "", pier: "", case: "", expand: true, last: null };

  async function explain(target, caseName) {
    const body = { target, case: caseName, expand: st.expand };
    if (S.mock) return exMock(S.model, S.results, body);
    const res = await fetch("/api/explain", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let data = null;
    try { data = await res.json(); } catch { /* non-JSON */ }
    if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
    return data;
  }

  /* ---------------------------------------------------------------- choices */
  const R = () => S.results || {};
  const caseOptions = () => {
    const r = R(), out = [];
    for (const n of Object.keys(r.cases || {})) if (!n.startsWith("__")) out.push([n, n]);
    for (const n of Object.keys(r.combos || {})) out.push([n, `Combo: ${n}`]);
    for (const n of Object.keys(r.rs_cases || {})) out.push([n, `RS: ${n}`]);
    for (const n of Object.keys(r.th_cases || {})) out.push([n, `TH: ${n}`]);
    return out;
  };
  const nodeLabel = tag => { const c = (R().nodes || {})[tag]; return c ? `${tag}  (${c.map(v => exU.fmt("length", v, 2)).join(", ")})` : String(tag); };
  const supportOptions = () => (R().supports || []).map(t => [String(t), nodeLabel(t)]);
  const storyOptions = () => ((S.model && S.model.stories) || []).map(s => [s.name, s.name]).reverse();
  const pierNames = () => { const p = R().piers || {}; const set = new Set(); for (const c of Object.values(p)) for (const n of Object.keys(c || {})) set.add(n); return [...set].sort(); };

  function targetFromSelection() {
    const sel = (S.selection || []).find(r => r.type === "member");
    if (sel) return { kind: "member_force", member: sel.uid, component: "M3", station: 0 };
    return null;
  }
  function currentTarget() {
    const t = { kind: st.kind, component: st.component };
    if (st.kind === "reaction" || st.kind === "joint_disp") t.joint = String(st.joint || "").trim();
    if (st.kind === "member_force") { t.member = String(st.member || "").trim(); t.station = +st.station; }
    if (st.kind === "story") t.story = st.story;
    if (st.kind === "pier") { t.pier = st.pier; t.story = st.story; }
    return t;
  }
  function applyTarget(t) {
    if (!t) return;
    if (t.kind) st.kind = t.kind;
    const comps = EX_COMP[st.kind] || [];
    const c = comps.find(x => x.toLowerCase() === String(t.component || "").toLowerCase());
    st.component = c || comps[0];
    for (const k of ["joint", "member", "story", "pier"]) if (t[k] != null) st[k] = String(t[k]);
    if (t.station != null) st.station = t.station === "i" ? 0 : t.station === "j" ? 1 : +t.station;
  }
  function fixDefaults() {
    const comps = EX_COMP[st.kind];
    if (!comps.includes(st.component)) st.component = comps[st.kind === "story" ? 4 : st.kind === "reaction" ? 2 : 0];
    if (!st.joint && (R().supports || []).length) st.joint = String(R().supports[0]);
    if (st.kind === "reaction" && !(R().supports || []).map(String).includes(String(st.joint)) && (R().supports || []).length) st.joint = String(R().supports[0]);
    if (!st.member) { const ts = targetFromSelection(); st.member = ts ? ts.member : (((S.model && S.model.members) || [])[0] || {}).uid || ""; }
    if (!st.story || !storyOptions().some(([n]) => n === st.story)) st.story = (storyOptions()[storyOptions().length - 1] || [""])[0];
    if (st.kind === "story" && st.story === (storyOptions()[storyOptions().length - 1] || [""])[0] && storyOptions().length) st.story = storyOptions()[storyOptions().length - 1][0];
    const pn = pierNames();
    if (!pn.includes(st.pier)) st.pier = pn[0] || "";
    const cs = caseOptions().map(x => x[0]);
    if (!cs.includes(st.case)) st.case = (S.caseName && cs.includes(S.caseName)) ? S.caseName : (cs.find(n => (R().combos || {})[n]) || cs[0] || "");
  }

  /* ---------------------------------------------------------------- render */
  const kindOf = res => (res.target && res.target.unit) || "none";
  const fmtV = (kind, v) => v == null || !isFinite(v) ? "—" : (kind === "none" ? (+(+v).toPrecision(5)).toString() : exU.fmt(kind, v, 3));
  const pct = v => v == null || !isFinite(v) ? "—" : `${(v * 100).toFixed(1)} %`;

  function chart(res) {
    const kind = kindOf(res);
    const rows = (res.rows || []).filter(r => r.contribution != null).slice(0, 12);
    if (!rows.length) return null;
    const W = 640, rowH = 20, labW = 150, valW = 92, pad = 6;
    const H = rows.length * rowH + 22;
    const mx = Math.max(...rows.map(r => Math.abs(r.contribution)), 1e-30);
    const anyNeg = rows.some(r => r.contribution < 0), anyPos = rows.some(r => r.contribution > 0);
    const plotW = W - labW - valW - 2 * pad;
    const zero = labW + pad + (anyNeg && anyPos ? plotW / 2 : anyNeg ? plotW : 0);
    const scale = (anyNeg && anyPos ? plotW / 2 : plotW) / mx;
    const unit = res.case_kind === "response_spectrum" ? "share of R²" : res.case_kind === "time_history" ? exU.label(kind) : exU.label(kind);
    let s = `<svg class="ex-chart" id="exChart" viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Contributions to ${exEsc(res.target.label)}">`;
    rows.forEach((r, i) => {
      const y = 4 + i * rowH, v = r.contribution, w = Math.max(Math.abs(v) * scale, 1);
      const x = v >= 0 ? zero : zero - w;
      const lbl = r.name.length > 22 ? r.name.slice(0, 21) + "…" : r.name;
      const shown = res.case_kind === "response_spectrum" ? pct(r.share) : `${fmtV(kind, v)}`;
      s += `<text x="${labW}" y="${y + 13}" text-anchor="end">${exEsc(lbl)}</text>`;
      s += `<rect class="ex-bar" x="${x.toFixed(1)}" y="${y + 3}" width="${w.toFixed(1)}" height="${rowH - 7}" rx="3" fill="${v >= 0 ? POS : NEG}" data-i="${i}"><title>${exEsc(r.name)}: ${exEsc(shown)}${r.factor != null && r.kind !== "mode" && r.kind !== "step" ? ` (factor ${+r.factor.toPrecision(6)})` : ""}</title></rect>`;
      const tx = v >= 0 ? x + w + 4 : x - 4;
      s += `<text x="${tx.toFixed(1)}" y="${y + 13}" text-anchor="${v >= 0 ? "start" : "end"}">${exEsc(shown)}</text>`;
    });
    s += `<line class="ex-axis" x1="${zero}" y1="2" x2="${zero}" y2="${H - 18}"/>`;
    s += `<text x="${W - pad}" y="${H - 4}" text-anchor="end">${exEsc(res.case_kind === "response_spectrum" ? "c_i = r_i Σ ρ_ij r_j  (share of R²)" : `contribution (${unit || "–"})`)}</text>`;
    s += "</svg>";
    const box = exEl("div");
    box.innerHTML = s;
    return box.firstChild;
  }

  function render(out, res) {
    out.textContent = "";
    const kind = kindOf(res);
    const u = exU.label(kind);
    const head = exEl("div", { class: "ex-head", id: "exHead" });
    let val = `<b>${exEsc(res.target.label)}</b> in <b>${exEsc(res.case)}</b>`;
    if (res.value != null) val += ` = <b>${exEsc(fmtV(kind, res.value))}</b> ${exEsc(u || "")}`;
    if (res.value_min != null) val += ` (min ${exEsc(fmtV(kind, res.value_min))})`;
    if (res.target.x != null) val += ` <span class="muted">· x = ${exEsc(exU.fmt("length", res.target.x, 2))} ${exEsc(exU.label("length"))}</span>`;
    if (res.check && res.check.rel_err != null)
      val += res.check.ok ? ` <span class="ex-ok" id="exCheck" title="sum of contributions vs stored result">✓ sum matches (rel. ${(+res.check.rel_err).toExponential(1)})</span>`
        : res.check.ok === false ? ` <span class="ex-bad" id="exCheck">sum differs (rel. ${(+res.check.rel_err).toExponential(1)})</span>` : "";
    head.innerHTML = val;
    out.appendChild(head);
    if (res.supported === false) {
      out.appendChild(exEl("div", { class: "ex-msg", id: "exMsg", text: res.message || "No breakdown available for this case." }));
      return;
    }
    const ch = chart(res);
    if (ch) out.appendChild(ch);
    const rows = [];
    for (const r of res.rows || []) { rows.push([r, false]); for (const c of r.children || []) rows.push([c, true]); }
    const isRs = res.case_kind === "response_spectrum", isTh = res.case_kind === "time_history";
    const cols = isRs ? ["#", "Mode", "Period (s)", `Modal value (${u})`, "Share of R²", "SRSS share"]
      : isTh ? ["#", "Step", "Time (s)", `Value (${u})`, ""]
        : ["#", "Contributor", "Factor", `Unit value (${u})`, `Contribution (${u})`, "Share"];
    const t = exEl("table", { class: "ex-table", id: "exTable" });
    t.innerHTML = `<thead><tr>${cols.map((c, i) => `<th class="${i === 1 ? "txt" : ""}">${exEsc(c)}</th>`).join("")}</tr></thead>`;
    const tb = exEl("tbody");
    rows.forEach(([r, child], i) => {
      const tr = exEl("tr", { class: child ? "ex-child" : "", "data-link": r.link ? JSON.stringify(r.link) : null, title: r.link ? "Click to show it in the 3D view" : null });
      const gov = (r.governs_max ? `<span class="ex-gov" title="governs the maximum">max</span>` : "") + (r.governs_min ? `<span class="ex-gov" title="governs the minimum">min</span>` : "") + (r.governs ? `<span class="ex-gov">governs</span>` : "");
      const src = r.source ? ` <span class="muted" title="${exEsc(r.source)}">${r.source === "re-solved" ? "· re-solved" : "· stored"}</span>` : "";
      const na = r.available === false ? ` <span class="muted">· n/a</span>` : "";
      if (isRs) tr.innerHTML = `<td>${r.rank || ""}</td><td class="txt">${exEsc(r.name)}${gov}</td><td>${(+r.period).toFixed(4)}</td><td>${fmtV(kind, r.modal_value)}</td><td>${pct(r.share)}</td><td>${pct(r.srss_share)}</td>`;
      else if (isTh) tr.innerHTML = `<td>${r.rank || ""}</td><td class="txt">${exEsc(r.name)}${gov}</td><td>${r.time != null ? (+r.time).toFixed(3) : "—"}</td><td>${fmtV(kind, r.unit_value)}</td><td></td>`;
      else {
        const contrib = r.contribution_min != null && r.contribution_min !== r.contribution ? `${fmtV(kind, r.contribution)} / ${fmtV(kind, r.contribution_min)}` : fmtV(kind, r.contribution);
        tr.innerHTML = `<td>${child ? "" : (r.rank || "")}</td><td class="txt">${exEsc(r.name)}${gov}${src}${na}${r.signless ? ' <span class="muted">· ±</span>' : ""}</td><td>${r.factor != null ? +(+r.factor).toPrecision(6) : "—"}</td><td>${fmtV(kind, r.unit_value)}</td><td>${contrib}</td><td>${child ? "" : pct(r.share)}</td>`;
      }
      if (r.link) tr.addEventListener("click", () => deepLink(r.link, res));
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    out.appendChild(exEl("div", { class: "ex-wrap" }, [t]));
    const notes = (res.notes || []).filter(Boolean);
    if (res.cross_terms != null && isRs) notes.push(`R² = Σ r_i² (${(+res.sum_squares).toPrecision(6)}) + cross terms (${(+res.cross_terms).toPrecision(6)}) in SI units.`);
    if (notes.length) out.appendChild(exEl("ul", { class: "ex-notes", id: "exNotes" }, notes.map(n => exEl("li", { text: n }))));
  }

  /* ---------------------------------------------------------------- deep links */
  function highlightTarget(res) {
    const t = res.target || {};
    try {
      if (sky.viewer && sky.viewer.setHighlight) sky.viewer.setHighlight(t.kind === "member_force" ? [t.member] : null, "#ffd166");
    } catch { /* ignore */ }
  }
  function deepLink(link, res) {
    const toAnalyze = () => { try { if (S.mode !== "analyze" && sky.setMode) sky.setMode("analyze"); if (sky.switchTab) sky.switchTab("view3d"); } catch (e) { console.warn(e); } };
    const sel = document.getElementById("caseSelect");
    const pickCase = name => {
      const r = R();
      const v = (r.rs_cases || {})[name] && !(r.cases || {})[name] && !(r.combos || {})[name] ? `rs:${name}` : name;
      if (!sel || ![...sel.options].some(o => o.value === v)) return false;
      sel.value = v; sel.dispatchEvent(new Event("change"));
      return true;
    };
    if (link.type === "case" || link.type === "pattern") {
      const name = link.case || (link.type === "pattern" ? null : link.case) || res.case;
      toAnalyze();
      const ok = pickCase(name) || pickCase(res.case);
      S.overlay.deformed = true; S.overlay.modal = false; S.overlay.buckling = false;
      try { sky.syncOverlayUI && sky.syncOverlayUI(); } catch { /* ignore */ }
      highlightTarget(res);
      if (!ok && sky.toast) sky.toast("Explain Result", `No stored results for ${name}; showing ${res.case}.`, "info", 3000);
      if (link.type === "pattern" && !link.case && sky.toast) sky.toast("Explain Result", `Pattern ${link.pattern} has no case of its own — showing ${res.case}.`, "info", 3000);
    } else if (link.type === "mode") {
      toAnalyze();
      S.overlay.modal = true; S.overlay.deformed = false; S.overlay.buckling = false; S.overlay.modeIndex = link.mode - 1;
      const ms = document.getElementById("modeSelect");
      if (ms) ms.value = String(link.mode - 1);
      try { sky.syncOverlayUI && sky.syncOverlayUI(); } catch { /* ignore */ }
      highlightTarget(res);
    } else if (link.type === "step") {
      try { sky.etabs && sky.etabs.clickItem && sky.etabs.clickItem("dis-th"); } catch (e) { console.warn(e); }
      if (sky.toast) sky.toast("Explain Result", `Governing step ${link.step} of ${link.case}.`, "info", 3000);
    }
    document.dispatchEvent(new CustomEvent("sky:explain-link", { detail: link }));
  }

  /* ---------------------------------------------------------------- dialog */
  function open(target, caseName) {
    ensureCss();
    if (!S.results) { sky.toast && sky.toast("Explain Result", "Run the analysis first (F5).", "warn", 4000); return null; }
    if (target) applyTarget(target);
    else if (!st.last) { const ts = targetFromSelection(); if (ts) applyTarget(ts); }
    if (caseName) st.case = caseName;
    fixDefaults();
    const F = exForm();
    const out = exEl("div", { id: "exOut" });
    const rowsFor = {};
    const vis = () => {
      const k = st.kind;
      const show = (n, on) => { if (rowsFor[n]) rowsFor[n].hidden = !on; };
      show("joint", k === "reaction"); show("jointT", k === "joint_disp"); show("member", k === "member_force"); show("station", k === "member_force");
      show("story", k === "story" || k === "pier"); show("pier", k === "pier");
    };
    rowsFor.joint = F.select(st, "joint", supportOptions, { label: "Support joint", id: "exJoint" });
    rowsFor.jointT = F.text(st, "joint", { label: "Joint (node tag)", id: "exJointTag", required: false,
      validate: v => v && !(R().nodes || {})[v] ? "No joint with this tag in the results." : "" });
    rowsFor.member = F.text(st, "member", { label: "Frame object", id: "exMember", required: false,
      validate: v => v && !((S.model && S.model.members) || []).some(m => m.uid === v) ? "No frame with this label." : "" });
    rowsFor.station = F.num(st, "station", { label: "Station (fraction of length)", min: 0, max: 1, id: "exStation", hint: "0 = end I · 1 = end J · nearest output station" });
    rowsFor.story = F.select(st, "story", storyOptions, { label: "Story", id: "exStory" });
    rowsFor.pier = F.select(st, "pier", () => pierNames().map(n => [n, n]), { label: "Pier", id: "exPier" });
    const body = [
      exGroup("Result", [
        F.select(st, "kind", KIND_LABEL, { label: "Result type", id: "exKind", onChange: () => { fixDefaults(); F.refresh(); vis(); } }),
        F.select(st, "component", () => (EX_COMP[st.kind] || []).map(c => [c, COMP_LABEL[c] || c]), { label: "Component", id: "exComp" }),
        rowsFor.joint, rowsFor.jointT, rowsFor.member, rowsFor.station, rowsFor.story, rowsFor.pier,
      ], { cls: "ex-tgt" }),
      exGroup("Load case", [
        F.select(st, "case", caseOptions, { label: "Case / combination", id: "exCase" }),
        F.check(st, "expand", { label: "Break combination cases down to load patterns", id: "exExpand" }),
      ]),
      out,
    ];
    const d = exDialog({ id: "exDlg", title: "Explain Result", size: "lg", form: F, body, okLabel: "Explain", cancelLabel: "Close",
      intro: "Which load patterns, cases, modes or time steps make up a result, and by how much.",
      footNote: "Linear cases are split by superposition; nonlinear cases are not.",
      onOk: async () => {
        const t = currentTarget();
        out.textContent = "Explaining…";
        try {
          const res = await explain(t, st.case);
          st.last = res;
          render(out, res);
        } catch (e) { out.textContent = ""; return e.message || String(e); }
        return false;                                   // stay open
      },
      onUnits: () => { if (st.last) render(out, st.last); } });
    vis();
    if (target && caseName) d.ok.click();
    return d;
  }

  const api = { open, explain, targetFromSelection, state: st, deepLink };
  sky.explain = api;
  return api;
}
