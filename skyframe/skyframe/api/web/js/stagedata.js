/* SkyFrame — ETABS user-defined Staged Construction ("Stage Data") and the
   per-stage results viewer.

   CONTRACT "Groups and user-defined staged construction":
     StagedCase.stages = "per_story"                      (Auto by story)
                       | [{name, duration_days, operations: [
                            {op: "add",    group, age_days},
                            {op: "remove", group},
                            {op: "load",   group, pattern, scale}]}]
     results.staged[name].stages = [{name, duration_days, t_start, t_end,
        active: {members, shells, links}, node_disp, reactions, base,
        member_forces}]   (cumulative state at the end of each stage)

   * loads.js staged card → hook sky.grpStagedCard(): "Auto by story" vs
     "User-defined stages" radio + Stage Data… button + stage summary.
   * Stage Data dialog: stages table (name, duration days) and the selected
     stage's operations table, validated live (group exists, add of active /
     remove of absent objects, pattern exists) — mirrors the backend.
   * Results: a stage slider next to the staged badge (tab bar) and a
     Display > Staged Construction Stages… dialog with displacements /
     reactions / member forces per stage; the 3D deformed shape follows the
     chosen stage (viewer hook grpCaseData). */

import { dialog as stgDialog, btn as stgBtn, footBar as stgFootBar, errorLine as stgErrorLine,
  showError as stgShowError } from "./analysisdlg.js";
import SU from "./units.js";
import * as SGM from "./groups_model.js";

const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const clone = o => JSON.parse(JSON.stringify(o));
function h(tag, attrs = {}, kids = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k === "html") n.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
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
const OP_LABEL = { add: "Add structure", remove: "Remove structure", load: "Load objects" };
const opText = op => op.op === "add" ? `add ${op.group} (age ${+op.age_days} d)`
  : op.op === "remove" ? `remove ${op.group}` : `load ${op.group} · ${op.pattern} × ${+op.scale}`;

export function initStageData(sky) {
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 4500) => sky.toast && sky.toast(t, m, k, ms);
  const stash = new WeakMap();     // staged case → last user stage list (radio round trip)

  /* ================= Stage Data dialog ================= */
  function openStageData(caseName, opts = {}) {
    const m = S.model;
    const sc = m && (m.staged_cases || {})[caseName];
    if (!sc) { toast("Stage Data", "Pick a staged case first.", "error"); return null; }
    const groups = SGM.groupNames(m);
    const pats = Object.keys(m.patterns || {});
    let stages = Array.isArray(sc.stages) ? clone(sc.stages).map(SGM.normStage)
      : (stash.get(sc) ? clone(stash.get(sc)) : []);
    if (!stages.length) stages = [{ name: "Stage 1", duration_days: 0, operations: [] }];
    const initial = JSON.stringify(Array.isArray(sc.stages) ? sc.stages : null);
    let cur = 0;

    const body = h("div", { class: "stg-dlg" });
    body.appendChild(h("p", { class: "muted dlg-intro", html:
      `Staged case <b>${esc(caseName)}</b>. Each stage runs its operations together on the structure built so far: ` +
      `<b>Add structure</b> activates a group (unstressed, at the given concrete age), <b>Remove structure</b> releases a ` +
      `group's accumulated forces onto the remaining structure, <b>Load objects</b> applies pattern × scale to the group's ` +
      `active objects. Durations drive creep/shrinkage when time-dependent is on.` +
      (groups.length ? "" : ` <b>No groups are defined yet — Define > Groups… first.</b>`) }));

    // ---- stages table
    const fsS = fieldset("Stages");
    const stWrap = h("div", { class: "table-scroll stg-wrap" });
    const stTbl = h("table", { class: "data-table stg-table", id: "stgStagesTable" });
    stWrap.appendChild(stTbl);
    const stBtns = h("div", { class: "stg-btns" });
    const mkB = (label, id, fn, title) => Object.assign(stgBtn(label, "btn-small", fn, title), { id });
    stBtns.append(
      mkB("Add Stage", "stgAddStage", () => { stages.splice(cur + 1, 0, { name: uniqueStageName(), duration_days: 0, operations: [] }); cur++; render(); }, "Insert a stage after the selected one"),
      mkB("Delete Stage", "stgDelStage", () => { if (stages.length <= 1) return; stages.splice(cur, 1); cur = Math.max(0, cur - 1); render(); }),
      mkB("Move Up", "stgUp", () => { if (cur > 0) { [stages[cur - 1], stages[cur]] = [stages[cur], stages[cur - 1]]; cur--; render(); } }),
      mkB("Move Down", "stgDown", () => { if (cur < stages.length - 1) { [stages[cur + 1], stages[cur]] = [stages[cur], stages[cur + 1]]; cur++; render(); } }));
    if (groups.length) stBtns.appendChild(mkB("One Stage per Group", "stgAutoGroups", () => {
      stages = groups.map((g, i) => ({ name: `Stage ${i + 1}`, duration_days: 7,
        operations: [{ op: "add", group: g, age_days: 28 },
          ...(pats.length ? [{ op: "load", group: g, pattern: m.patterns.DEAD ? "DEAD" : pats[0], scale: 1 }] : [])] }));
      cur = 0; render();
    }, "Replace the table: stage k adds group k and loads it with DEAD"));
    fsS.append(stWrap, stBtns);
    body.appendChild(fsS);

    // ---- operations table of the selected stage
    const fsO = fieldset("Operations");
    const opWrap = h("div", { class: "table-scroll stg-wrap" });
    const opTbl = h("table", { class: "data-table stg-op-table", id: "stgOpsTable" });
    opWrap.appendChild(opTbl);
    const addOp = mkB("Add Operation", "stgAddOp", () => {
      const prevAdds = new Set(stages.slice(0, cur + 1).flatMap(s => s.operations.filter(o => o.op === "add").map(o => o.group)));
      const g = groups.find(x => !prevAdds.has(x)) || groups[0] || "";
      stages[cur].operations.push({ op: "add", group: g, age_days: SGM.DEFAULT_ADD_AGE_DAYS });
      render();
    });
    fsO.append(opWrap, addOp);
    body.appendChild(fsO);

    const check = h("div", { class: "stg-check", id: "stgCheck" });
    body.appendChild(check);
    const err = stgErrorLine();
    body.appendChild(err);

    function uniqueStageName() {
      let i = stages.length + 1;
      while (stages.some(s => s.name === `Stage ${i}`)) i++;
      return `Stage ${i}`;
    }
    const numIn = (val, onSet, attrs = {}) => {
      const i = h("input", { type: "number", step: "any", value: String(val), ...attrs });
      i.addEventListener("change", () => {
        const v = parseFloat(i.value);
        if (isFinite(v) && (attrs.min == null || v >= +attrs.min)) { onSet(v); renderCheck(); renderStageCounts(); }
        else i.value = String(val);
      });
      return i;
    };
    function renderStageCounts() {
      stTbl.querySelectorAll("tr[data-i]").forEach(tr => {
        const s = stages[+tr.dataset.i];
        const c = tr.querySelector(".stg-ops-sum");
        if (s && c) c.textContent = s.operations.map(opText).join("; ") || "—";
      });
    }
    function render() {
      // stages
      let t0 = 0;
      stTbl.innerHTML = `<thead><tr><th>#</th><th class="txt">Stage name</th><th>Duration (days)</th><th>Start (d)</th><th class="txt">Operations</th></tr></thead>`;
      const tb = h("tbody");
      stages.forEach((s, i) => {
        const tr = h("tr", { class: "stg-row" + (i === cur ? " is-sel" : ""), "data-i": String(i) });
        const nm = h("input", { type: "text", value: s.name, class: "stg-name", spellcheck: "false" });
        nm.addEventListener("change", () => { s.name = nm.value.trim(); renderCheck(); });
        nm.addEventListener("focus", () => { if (cur !== i) { cur = i; renderOps(); markSel(); } });
        const du = numIn(s.duration_days, v => { s.duration_days = v; }, { min: "0", class: "stg-dur" });
        du.addEventListener("focus", () => { if (cur !== i) { cur = i; renderOps(); markSel(); } });
        tr.append(h("td", { text: String(i + 1) }), h("td", { class: "txt" }, nm), h("td", {}, du),
          h("td", { class: "muted", text: String(+t0.toFixed(3)) }),
          h("td", { class: "txt muted stg-ops-sum", text: s.operations.map(opText).join("; ") || "—" }));
        tr.addEventListener("click", () => { if (cur !== i) { cur = i; renderOps(); markSel(); } });
        tb.appendChild(tr);
        t0 += +s.duration_days || 0;
      });
      stTbl.appendChild(tb);
      renderOps();
      renderCheck();
    }
    function markSel() {
      stTbl.querySelectorAll("tr[data-i]").forEach(tr => tr.classList.toggle("is-sel", +tr.dataset.i === cur));
    }
    function renderOps() {
      const s = stages[cur];
      fsO.querySelector("legend").textContent = `Operations — ${s ? s.name || `Stage ${cur + 1}` : ""}`;
      opTbl.innerHTML = `<thead><tr><th class="txt">Operation</th><th class="txt">Group</th><th>Age (days)</th><th class="txt">Load pattern</th><th>Scale</th><th></th></tr></thead>`;
      const tb = h("tbody");
      if (!s.operations.length) tb.innerHTML = `<tr><td colspan="6" class="txt muted">No operations — Add Operation.</td></tr>`;
      s.operations.forEach((op, j) => {
        const tr = h("tr", { class: "stg-op-row", "data-j": String(j) });
        const opSel = h("select", { class: "stg-op-kind" });
        opSel.innerHTML = SGM.STAGE_OPS.map(o => `<option value="${o}">${OP_LABEL[o]}</option>`).join("");
        opSel.value = op.op;
        opSel.addEventListener("change", () => {
          const g = op.group;
          s.operations[j] = opSel.value === "add" ? { op: "add", group: g, age_days: SGM.DEFAULT_ADD_AGE_DAYS }
            : opSel.value === "remove" ? { op: "remove", group: g }
            : { op: "load", group: g, pattern: m.patterns.DEAD ? "DEAD" : (pats[0] || ""), scale: 1 };
          renderOps(); renderCheck(); renderStageCounts();
        });
        const gSel = h("select", { class: "stg-op-group" });
        const gl = op.group && !groups.includes(op.group) ? [...groups, op.group] : groups;
        gSel.innerHTML = gl.map(g => `<option value="${esc(g)}">${esc(g)}${groups.includes(g) ? "" : " (missing)"}</option>`).join("") ||
          `<option value="">(no groups)</option>`;
        gSel.value = op.group || "";
        if (!op.group && gl.length) { op.group = gl[0]; gSel.value = op.group; }
        gSel.addEventListener("change", () => { op.group = gSel.value; renderCheck(); renderStageCounts(); });
        const ageTd = h("td"), patTd = h("td", { class: "txt" }), scTd = h("td");
        if (op.op === "add") ageTd.appendChild(numIn(op.age_days, v => { op.age_days = v; }, { min: "0", class: "stg-op-age" }));
        if (op.op === "load") {
          const pSel = h("select", { class: "stg-op-pat" });
          const pl = op.pattern && !pats.includes(op.pattern) ? [...pats, op.pattern] : pats;
          pSel.innerHTML = pl.map(p => `<option value="${esc(p)}">${esc(p)}</option>`).join("");
          pSel.value = op.pattern;
          pSel.addEventListener("change", () => { op.pattern = pSel.value; renderCheck(); renderStageCounts(); });
          patTd.appendChild(pSel);
          scTd.appendChild(numIn(op.scale, v => { op.scale = v; }, { class: "stg-op-scale" }));
        }
        const del = h("button", { class: "chip-x stg-op-del", title: "Delete operation", text: "✕" });
        del.addEventListener("click", () => { s.operations.splice(j, 1); renderOps(); renderCheck(); renderStageCounts(); });
        tr.append(h("td", { class: "txt" }, opSel), h("td", { class: "txt" }, gSel), ageTd, patTd, scTd, h("td", {}, del));
        tb.appendChild(tr);
      });
      opTbl.appendChild(tb);
    }
    function renderCheck() {
      const { errors, warnings } = SGM.checkStages(m, stages);
      check.innerHTML = (errors.length ? errors.map(e => `<div class="stg-err">✕ ${esc(e)}</div>`).join("") : "") +
        (warnings.length ? warnings.map(w => `<div class="stg-warn">⚠ ${esc(w)}</div>`).join("") : "") +
        (!errors.length && !warnings.length ? `<div class="stg-ok">✓ Stage sequence is valid (${stages.length} stage${stages.length > 1 ? "s" : ""}).</div>` : "");
      return { errors, warnings };
    }
    render();

    const okFn = () => {
      const { errors } = renderCheck();
      if (errors.length) { stgShowError(err, `Fix ${errors.length} error${errors.length > 1 ? "s" : ""} first: ${errors[0]}`); return false; }
      stgShowError(err, "");
      const out = stages.map(SGM.normStage);
      if (JSON.stringify(out) !== initial) {
        sc.stages = out;
        stash.set(sc, clone(out));
        sky.markDirty();
        if (S.mode === "loads" && sky.loadsEditor) sky.loadsEditor.render();
      }
      opts.onOk && opts.onOk();
      return true;
    };
    const fb = stgFootBar("Staged Construction — Stage Data", [
      stgBtn("Cancel", "", () => d.close()),
      Object.assign(stgBtn("OK", "btn-primary", () => { if (okFn()) { okDone = true; d.close(); } }), { id: "stgOk" }),
    ]);
    let okDone = false;
    const d = stgDialog("stgDataDlg", { title: `Stage Data — ${caseName}`, iconId: null, wide: true, body, foot: fb.wrap,
      onClose: () => { if (!okDone && opts.onCancel) opts.onCancel(); } });
    return d;
  }

  /* ================= staged card hook (loads.js) ================= */
  function stagedCard(card, m, sc, mutated) {
    card.dataset.staged = sc.name;
    const user = Array.isArray(sc.stages);
    if (user) stash.set(sc, clone(sc.stages));
    const wrap = h("div", { class: "stg-mode" });
    const rid = "stgMode_" + Math.random().toString(36).slice(2, 8);
    const rAuto = h("input", { type: "radio", name: rid, value: "per_story", class: "stg-mode-auto" });
    const rUser = h("input", { type: "radio", name: rid, value: "user", class: "stg-mode-user" });
    rAuto.checked = !user; rUser.checked = user;
    const dataBtn = stgBtn("Stage Data…", "btn-small stg-data-btn", () => openStageData(sc.name, { onOk: () => mutated() }),
      "ETABS Stage Data — stages and their add / remove / load group operations");
    dataBtn.disabled = !user;
    rAuto.addEventListener("change", () => {
      if (!rAuto.checked) return;
      if (Array.isArray(sc.stages)) stash.set(sc, clone(sc.stages));
      sc.stages = "per_story";
      mutated();
    });
    rUser.addEventListener("change", () => {
      if (!rUser.checked) return;
      const prev = stash.get(sc);
      if (prev && !SGM.checkStages(m, prev).errors.length) { sc.stages = clone(prev); mutated(); return; }
      openStageData(sc.name, { onOk: () => mutated(), onCancel: () => { rAuto.checked = true; rUser.checked = false; } });
    });
    const tag = h("span", { class: "mass-tag", text: "stages =" });
    tag.title = "Auto by story (v0.6 sequential gravity) or ETABS user-defined stages built from groups";
    wrap.append(tag,
      h("label", { class: "stg-mode-opt" }, [rAuto, h("span", { text: "Auto by story" })]),
      h("label", { class: "stg-mode-opt" }, [rUser, h("span", { text: "User-defined stages" })]),
      dataBtn);
    const head = card.querySelector(".rs-head");
    if (head && head.nextSibling) card.insertBefore(wrap, head.nextSibling); else card.appendChild(wrap);
    if (user) {
      // the gravity pattern is not used by a stage list — loads come from "load" operations
      const patSel = head && head.querySelector("select");
      if (patSel) { patSel.disabled = true; patSel.title = "Not used by user-defined stages (loads come from Load objects operations)"; }
      const list = h("ol", { class: "stg-sum" });
      let t = 0;
      for (const s of sc.stages) {
        list.appendChild(h("li", { html: `<b>${esc(s.name)}</b> <span class="muted">t ${+t.toFixed(2)}→${+(t + (+s.duration_days || 0)).toFixed(2)} d</span> · ` +
          esc((s.operations || []).map(opText).join("; ") || "no operations") }));
        t += +s.duration_days || 0;
      }
      wrap.after(list);
      const { errors } = SGM.checkStages(m, sc.stages);
      if (errors.length) list.after(h("p", { class: "cut-warn stg-card-err", text: `⚠ ${errors[0]}` }));
      const note = card.querySelector(".staged-note:not(.staged-td-hint)");
      if (note) note.innerHTML = "User-defined stages (ETABS Stage Data): each stage solves the active structure under that stage's " +
        "increments; results report the cumulative state at the end of every stage (stage slider in the results view).";
    }
  }

  /* ================= results: stage selector ================= */
  let stageIdx = -1;                 // -1 = final state
  let stageCase = null;
  const stagedName = () => (typeof S.caseName === "string" && S.caseName.startsWith("staged:")) ? S.caseName.slice(7) : null;
  const stagedRes = name => (S.results && S.results.staged && S.results.staged[name]) || null;
  const stageList = name => { const r = stagedRes(name); return (r && Array.isArray(r.stages)) ? r.stages : null; };
  /** The state shown for the selected stage (null = final). */
  function currentStage() {
    const n = stagedName();
    const st = n && stageList(n);
    if (!st || stageIdx < 0 || stageIdx >= st.length) return null;
    return st[stageIdx];
  }

  // viewer: deformed shape of a staged case follows the chosen stage
  if (sky.viewer) sky.viewer.grpCaseData = (name, r) => {
    if (typeof name !== "string" || !name.startsWith("staged:") || !r || !r.staged) return null;
    const sd = r.staged[name.slice(7)];
    if (!sd) return null;
    const st = Array.isArray(sd.stages) && stageCase === name.slice(7) && stageIdx >= 0 ? sd.stages[stageIdx] : null;
    return st ? { node_disp: st.node_disp } : sd;
  };

  // tab-bar control next to the staged badge
  const bar = h("span", { class: "stg-bar hidden", id: "stgBar", title: "Staged construction — pick a stage (cumulative state at the end of the stage)" });
  const range = h("input", { type: "range", min: "0", max: "1", step: "1", value: "1", id: "stgRange", "aria-label": "Stage" });
  const lbl = h("b", { class: "stg-bar-lbl", id: "stgBarLabel" });
  const openBtn = h("button", { class: "btn btn-small", id: "stgBarOpen", text: "Stages…", title: "Display per-stage displacements / reactions / member forces" });
  bar.append(h("span", { class: "muted", text: "Stage" }), range, lbl, openBtn);
  const badge = document.getElementById("stagedBadge");
  if (badge && badge.parentNode) badge.parentNode.insertBefore(bar, badge.nextSibling);
  range.addEventListener("input", () => setStage(+range.value >= +range.max ? -1 : +range.value));
  openBtn.addEventListener("click", () => openStageResults());

  function setStage(i) {
    const n = stagedName();
    const st = n && stageList(n);
    stageIdx = st && i >= 0 && i < st.length ? i : -1;
    stageCase = n;
    syncBar();
    if (sky.viewer) sky.viewer._dirty = true;
    if (resDlg) renderRes();
    document.dispatchEvent(new CustomEvent("sky:stage-changed", { detail: { caseName: n, stage: stageIdx } }));
  }
  function syncBar() {
    const n = stagedName();
    const st = n && stageList(n);
    bar.classList.toggle("hidden", !st);
    if (!st) return;
    if (stageCase !== n) { stageCase = n; stageIdx = -1; }
    range.max = String(st.length);
    range.value = String(stageIdx < 0 ? st.length : stageIdx);
    lbl.textContent = stageIdx < 0 ? `Final (${st.length} stages)` : `${stageIdx + 1}/${st.length} · ${st[stageIdx].name}`;
  }
  const cs = document.getElementById("caseSelect");
  if (cs) cs.addEventListener("change", () => { stageIdx = -1; syncBar(); if (resDlg) renderRes(); });
  document.addEventListener("sky:results-changed", () => { stageIdx = -1; stageCase = null; syncBar(); if (resDlg) renderRes(); });

  /* ================= results: per-stage dialog ================= */
  let resDlg = null, resTab = "disp", activeOnly = true;
  let resBody = null;
  function openStageResults(name) {
    if (name && S.results && S.results.staged && S.results.staged[name]) {
      const sel = document.getElementById("caseSelect");
      if (S.caseName !== `staged:${name}` && sel) {
        sel.value = `staged:${name}`;
        sel.dispatchEvent(new Event("change"));
      }
    }
    let n = stagedName();
    if (!n || !stageList(n)) {
      const cand = Object.entries((S.results && S.results.staged) || {}).find(([, v]) => Array.isArray(v.stages));
      if (!cand) { toast("Staged results", "Run an analysis with a user-defined staged case first.", "error"); return null; }
      const sel = document.getElementById("caseSelect");
      if (sel) { sel.value = `staged:${cand[0]}`; sel.dispatchEvent(new Event("change")); }
      n = cand[0];
    }
    resBody = h("div", { class: "stg-res" });
    const fb = stgFootBar("Staged construction — cumulative state at the end of each stage", [
      Object.assign(stgBtn("Show Deformed (3D)", "", () => showDeformed()), { id: "stgResDeformed" }),
      stgBtn("Close", "btn-primary", () => resDlg && resDlg.close()),
    ]);
    resDlg = stgDialog("stgResDlg", { title: `Staged Construction Stages — ${n}`, iconId: null, wide: true, body: resBody, foot: fb.wrap,
      onClose: () => { resDlg = null; } });
    renderRes();
    return resDlg;
  }
  function showDeformed() {
    if (resDlg) resDlg.close();
    if (sky.etabs && sky.etabs.showResult) sky.etabs.showResult("view3d"); else { sky.setMode("analyze"); sky.switchTab("view3d"); }
    const chip = document.getElementById("chipDeformed");
    if (chip && !chip.disabled && !chip.classList.contains("is-on")) chip.click();
    if (sky.viewer) sky.viewer._dirty = true;
  }
  function renderRes() {
    if (!resBody) return;
    const n = stagedName();
    const st = n && stageList(n);
    resBody.textContent = "";
    if (!st) { resBody.appendChild(h("p", { class: "muted", text: "Select a user-defined staged case in the case selector." })); return; }
    const sd = stagedRes(n);
    // stage stepper
    const top = h("div", { class: "stg-res-top" });
    const r2 = h("input", { type: "range", min: "0", max: String(st.length - 1), step: "1", value: String(stageIdx < 0 ? st.length - 1 : stageIdx), id: "stgResRange" });
    r2.addEventListener("input", () => setStage(+r2.value));
    const sel = h("select", { id: "stgResSelect" });
    sel.innerHTML = st.map((s, i) => `<option value="${i}">${i + 1}. ${esc(s.name)}</option>`).join("");
    sel.value = String(stageIdx < 0 ? st.length - 1 : stageIdx);
    sel.addEventListener("change", () => setStage(+sel.value));
    const i = stageIdx < 0 ? st.length - 1 : stageIdx;
    const s = st[i];
    top.append(h("span", { class: "muted", text: "Stage" }), sel, r2,
      h("span", { class: "muted", id: "stgResTime", text: `t ${fmtN(s.t_start)} → ${fmtN(s.t_end)} d · ${s.active.members.length} frames · ${s.active.shells.length} shells · ${s.active.links.length} links active` }));
    resBody.appendChild(top);

    // per-stage trend: max |U| and base FZ
    resBody.appendChild(trendSvg(st, i));

    // base totals
    const b = s.base || {};
    const bt = h("table", { class: "data-table stg-base", id: "stgResBase" });
    bt.innerHTML = `<thead><tr><th class="txt">Base reaction</th>${["FX", "FY", "FZ"].map(k => `<th>${k} ${SU.label("force")}</th>`).join("")}${["MX", "MY", "MZ"].map(k => `<th>${k} ${SU.label("moment")}</th>`).join("")}</tr></thead>` +
      `<tbody><tr><td class="txt">${esc(s.name)}</td>${["FX", "FY", "FZ"].map(k => `<td>${SU.fmt("force", b[k] || 0, 1)}</td>`).join("")}${["MX", "MY", "MZ"].map(k => `<td>${SU.fmt("moment", b[k] || 0, 1)}</td>`).join("")}</tr></tbody>`;
    resBody.appendChild(bt);

    // tabs
    const tabs = h("div", { class: "seg-toggle stg-tabs" });
    for (const [k, t] of [["disp", "Displacements"], ["react", "Reactions"], ["forces", "Member forces"]]) {
      const bb = h("button", { class: "seg-btn" + (resTab === k ? " is-active" : ""), "data-tab": k, text: t });
      bb.addEventListener("click", () => { resTab = k; renderRes(); });
      tabs.appendChild(bb);
    }
    const ao = h("input", { type: "checkbox", id: "stgResActive" });
    ao.checked = activeOnly;
    ao.addEventListener("change", () => { activeOnly = ao.checked; renderRes(); });
    resBody.append(h("div", { class: "stg-tabs-row" }, [tabs, h("label", { class: "dlg-chk" }, [ao, h("span", { text: "Non-zero / active only" })])]));

    const wrap = h("div", { class: "table-scroll stg-res-wrap" });
    const tbl = h("table", { class: "data-table stg-res-table", id: "stgResTable" });
    wrap.appendChild(tbl);
    resBody.appendChild(wrap);
    const nodes = (S.results && S.results.nodes) || {};
    const LIM = 400;
    const nz = arr => arr && arr.some(v => Math.abs(v) > 1e-12);
    if (resTab === "disp") {
      let rows = Object.entries(s.node_disp || {});
      if (activeOnly) rows = rows.filter(([, d]) => nz(d));
      rows.sort((a, b2) => Math.abs(b2[1][2]) - Math.abs(a[1][2]));
      tbl.innerHTML = `<thead><tr><th class="txt">Node</th><th>X ${SU.label("length")}</th><th>Y</th><th>Z</th>` +
        ["UX", "UY", "UZ"].map(k => `<th>${k} ${SU.label("disp")}</th>`).join("") + ["RX", "RY", "RZ"].map(k => `<th>${k} rad</th>`).join("") + `</tr></thead>` +
        `<tbody>${rows.slice(0, LIM).map(([t, d]) => { const p = nodes[t] || [NaN, NaN, NaN];
          return `<tr><td class="txt">${esc(t)}</td>${p.map(v => `<td>${isFinite(v) ? SU.fmt("length", v, 2) : "—"}</td>`).join("")}` +
            d.slice(0, 3).map(v => `<td>${SU.fmt("disp", v, 3)}</td>`).join("") + d.slice(3, 6).map(v => `<td>${(+v).toExponential(2)}</td>`).join("") + `</tr>`; }).join("")}</tbody>`;
      if (rows.length > LIM) wrap.appendChild(h("p", { class: "muted grp-note", text: `… ${rows.length - LIM} more (largest |UZ| first)` }));
    } else if (resTab === "react") {
      let rows = Object.entries(s.reactions || {});
      if (activeOnly) rows = rows.filter(([, d]) => nz(d));
      tbl.innerHTML = `<thead><tr><th class="txt">Node</th>` + ["FX", "FY", "FZ"].map(k => `<th>${k} ${SU.label("force")}</th>`).join("") +
        ["MX", "MY", "MZ"].map(k => `<th>${k} ${SU.label("moment")}</th>`).join("") + `</tr></thead>` +
        `<tbody>${rows.slice(0, LIM).map(([t, d]) => `<tr><td class="txt">${esc(t)}</td>` + d.slice(0, 3).map(v => `<td>${SU.fmt("force", v, 1)}</td>`).join("") +
          d.slice(3, 6).map(v => `<td>${SU.fmt("moment", v, 1)}</td>`).join("") + `</tr>`).join("")}</tbody>`;
    } else {
      const act = new Set(s.active.members || []);
      let rows = Object.entries(s.member_forces || {});
      if (activeOnly) rows = rows.filter(([u, f]) => act.has(u) || nz(f));
      tbl.innerHTML = `<thead><tr><th class="txt">Frame</th><th class="txt">State</th>` +
        ["P i", "V2 i", "V3 i"].map(k => `<th>${k} ${SU.label("force")}</th>`).join("") + ["T i", "M2 i", "M3 i", "M2 j", "M3 j"].map(k => `<th>${k} ${SU.label("moment")}</th>`).join("") + `</tr></thead>` +
        `<tbody>${rows.slice(0, LIM).map(([u, f]) => `<tr><td class="txt">${esc(u)}</td><td class="txt ${act.has(u) ? "stg-on" : "muted"}">${act.has(u) ? "active" : "inactive"}</td>` +
          [0, 1, 2].map(k => `<td>${SU.fmt("force", f[k], 1)}</td>`).join("") + [3, 4, 5, 10, 11].map(k => `<td>${SU.fmt("moment", f[k], 1)}</td>`).join("") + `</tr>`).join("")}</tbody>`;
      if (rows.length > LIM) wrap.appendChild(h("p", { class: "muted grp-note", text: `… ${rows.length - LIM} more` }));
    }
    if (sd && sd.comparison) resBody.appendChild(h("p", { class: "muted grp-note", text: `Final state vs one-shot: Δ column axial ${fmtN(sd.comparison.column_axial_max_diff_pct)} %` }));
  }
  const fmtN = v => (v == null || !isFinite(v)) ? "∞" : String(+(+v).toFixed(2));
  function trendSvg(st, cur) {
    const W = 520, H = 70, pad = 22;
    const umax = st.map(s => Math.max(0, ...Object.values(s.node_disp || {}).map(d => Math.hypot(d[0], d[1], d[2]))));
    const fz = st.map(s => (s.base && s.base.FZ) || 0);
    const mu = Math.max(...umax, 1e-12), mf = Math.max(...fz.map(Math.abs), 1e-12);
    const x = i => pad + (st.length === 1 ? (W - 2 * pad) / 2 : i * (W - 2 * pad) / (st.length - 1));
    const bw = Math.max(6, Math.min(26, (W - 2 * pad) / st.length * 0.4));
    let svg = `<svg viewBox="0 0 ${W} ${H}" class="stg-trend" id="stgTrend" role="img" aria-label="Max displacement and base FZ per stage">`;
    st.forEach((s, i) => {
      const hh = (H - 26) * umax[i] / mu;
      svg += `<rect x="${x(i) - bw / 2}" y="${H - 16 - hh}" width="${bw}" height="${Math.max(hh, 1)}" rx="2" class="stg-bar-u${i === cur ? " is-cur" : ""}" data-i="${i}"><title>${esc(s.name)}: max |U| ${SU.fmt("disp", umax[i], 2)} ${SU.label("disp")} · base FZ ${SU.fmt("force", fz[i], 1)} ${SU.label("force")}</title></rect>`;
      svg += `<text x="${x(i)}" y="${H - 4}" class="stg-tick${i === cur ? " is-cur" : ""}">${i + 1}</text>`;
    });
    const pts = st.map((s, i) => `${x(i)},${H - 16 - (H - 26) * Math.abs(fz[i]) / mf}`).join(" ");
    svg += `<polyline points="${pts}" class="stg-fz"/>`;
    svg += `<text x="${W - pad}" y="10" class="stg-leg" text-anchor="end">bars: max |U| · line: base FZ</text></svg>`;
    const d = h("div", { class: "stg-trend-wrap", html: svg });
    d.querySelectorAll("rect[data-i]").forEach(r => r.addEventListener("click", () => setStage(+r.dataset.i)));
    return d;
  }

  /* ================= styles ================= */
  if (!document.getElementById("stgStyles")) {
    const s = document.createElement("style");
    s.id = "stgStyles";
    s.textContent = `
      .stg-wrap { max-height: 28vh; }
      .stg-table input.stg-name { min-width: 110px; }
      .stg-table input, .stg-op-table input, .stg-op-table select { padding: 2px 5px; min-width: 0; }
      .stg-table input.stg-dur, .stg-op-table input { width: 90px; }
      .stg-table tbody tr { cursor: pointer; }
      .stg-table tbody tr.is-sel td { background: var(--accent-ghost); }
      .stg-ops-sum { font-size: 11px; max-width: 320px; white-space: normal; }
      .stg-btns { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }
      .stg-check { font-size: 11.5px; display: flex; flex-direction: column; gap: 2px; margin-top: 6px; max-height: 16vh; overflow: auto; }
      .stg-err { color: var(--red, #e5534b); } .stg-warn { color: var(--amber, #e5a50a); } .stg-ok { color: var(--green, #3fb950); }
      .stg-mode { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 6px 0 4px; font-size: 12px; }
      .stg-mode-opt { display: inline-flex; align-items: center; gap: 5px; cursor: pointer; }
      .stg-mode-opt input { width: auto; }
      .stg-sum { margin: 2px 0 4px 18px; padding: 0; font-size: 11.5px; display: flex; flex-direction: column; gap: 2px; }
      .stg-card-err { font-size: 11.5px; margin: 2px 0; }
      .stg-bar { display: inline-flex; align-items: center; gap: 6px; margin: 0 8px; font-size: 11.5px; }
      .stg-bar.hidden { display: none; }
      .stg-bar input[type=range] { width: 110px; }
      .stg-bar-lbl { font-weight: 600; white-space: nowrap; max-width: 180px; overflow: hidden; text-overflow: ellipsis; }
      .stg-res-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 12px; margin-bottom: 6px; }
      .stg-res-top select { width: auto; }
      .stg-res-top input[type=range] { flex: 1 1 140px; }
      .stg-trend { width: 100%; max-width: 520px; height: 70px; display: block; }
      .stg-bar-u { fill: rgba(53,181,229,.45); cursor: pointer; } .stg-bar-u.is-cur { fill: var(--accent, #35b5e5); }
      .stg-fz { fill: none; stroke: #f5be3c; stroke-width: 1.5; }
      .stg-tick { font-size: 9px; fill: var(--text-3, #66727f); text-anchor: middle; } .stg-tick.is-cur { fill: var(--text-1, #e8edf3); font-weight: 700; }
      .stg-leg { font-size: 9px; fill: var(--text-3, #66727f); }
      .stg-tabs-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin: 8px 0 4px; flex-wrap: wrap; }
      .stg-res-wrap { max-height: 38vh; }
      .stg-base { margin-top: 6px; }
      .stg-on { color: var(--green, #3fb950); }
    `;
    document.head.appendChild(s);
  }

  sky.openStageData = openStageData;
  sky.openStageResults = openStageResults;
  sky.grpStagedCard = stagedCard;
  sky.stages = { openStageData, openStageResults, setStage, currentStage, stage: () => stageIdx, syncBar };
  return sky.stages;
}
