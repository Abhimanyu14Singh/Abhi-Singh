/* SkyFrame Wave B — report templates, .rtf export and saved table sets
   (builds on the A7/A8 report: js/reportx.js, js/reportplus.js,
   js/a8results.js, io/docx.py).

   File › Create Report… gains
     · a "Report Template" group: pick a saved template (sections = figures,
       result tables, story-plot cases, snapshot cases, paper / layout
       options) → Apply; Save As… stores the dialog's current choices under a
       name in the model ("report_templates"); Delete; "Tables from set"
       ticks the tables of a saved Show-Tables set;
     · "Download .rtf": the finished report converted to the .docx block
       document (reportPlus.htmlToDoc) and written by POST /api/report/rtf
       (skyframe/io/rtf.py; ?mock=1 js/mock_wb.js in the browser).
   Display › Show Tables… gains a "Table set" filter (named lists of tables
   saved in the model, "table_sets") with Save Set… / Delete.
   Both model keys are written only when used. */

import { el as rEl, esc as rEsc, dkForm as rForm, dkGroup as rGroup, dkDialog as rDialog, dkNote as rNote } from "./dlgkit.js";
import { mockRtf as rMockRtf } from "./mock_wbi.js";

export function initWbReport(sky) {
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 4000) => sky.toast && sky.toast(t, m, k, ms);
  const tpls = () => (S.model && S.model.report_templates) || [];
  const sets = () => (S.model && S.model.table_sets) || [];
  const put = (key, list) => { if (list.length) S.model[key] = list; else delete S.model[key]; sky.markDirty(); };
  const $ = id => document.getElementById(id);
  const RS = { last: null };

  /* ================================================================ Create Report */
  const sel = node => (node ? [...node.selectedOptions].map(o => o.value) : []);
  const setSel = (node, vals) => { if (node) for (const o of node.options) o.selected = vals.includes(o.value); };
  /** The dialog's current choices (same keys as reportx opts() + A8). */
  function readDialog() {
    const sections = [...document.querySelectorAll('#rxReportDlg input[id^="rxSec-"]')].filter(i => i.checked).map(i => i.id.slice(6));
    const tables = [...document.querySelectorAll("#rpTableList input[data-key]")].filter(i => i.checked).map(i => i.dataset.key);
    const o = { sections, topN: Math.max(1, Math.min(500, parseInt(($("rxTopN") || {}).value, 10) || 25)),
      paper: ($("rxPaper") || {}).value || "A4", orientation: ($("rxOrient") || {}).value || "portrait",
      storyCases: sel($("rxStoryCases")).slice(0, 6), planStory: ($("rxPlanStory") || {}).value || null,
      rpTables: tables, rpMaxRows: Math.max(10, Math.min(5000, parseInt(($("rpMaxRows") || {}).value, 10) || 200)),
      rpSnapCases: sel($("rpSnapCases")).slice(0, 12) };
    return o;
  }
  const toTemplate = (name, o) => ({ name, tables: o.rpTables, figures: o.sections, cases: o.storyCases,
    options: { snapCases: o.rpSnapCases, topN: o.topN, paper: o.paper, orientation: o.orientation, planStory: o.planStory, maxRows: o.rpMaxRows } });
  async function applyTemplate(t) {
    if (!t) return false;
    document.querySelectorAll('#rxReportDlg input[id^="rxSec-"]').forEach(i => { i.checked = (t.figures || []).includes(i.id.slice(6)); });
    const op = t.options || {};
    if ($("rxTopN") && op.topN) $("rxTopN").value = String(op.topN);
    if ($("rxPaper") && op.paper) $("rxPaper").value = op.paper;
    if ($("rxOrient") && op.orientation) $("rxOrient").value = op.orientation;
    if ($("rxPlanStory") && op.planStory) $("rxPlanStory").value = op.planStory;
    if ($("rpMaxRows") && op.maxRows) $("rpMaxRows").value = String(op.maxRows);
    setSel($("rxStoryCases"), t.cases || []);
    setSel($("rpSnapCases"), op.snapCases || []);
    await tickTables(t.tables || []);
    return true;
  }
  /** Tick the result-table boxes (the list loads asynchronously). */
  async function tickTables(keys, add = false) {
    for (let k = 0; k < 40; k++) {
      const boxes = [...document.querySelectorAll("#rpTableList input[data-key]")];
      if (boxes.length || !S.results) {
        for (const b of boxes) b.checked = add ? (b.checked || keys.includes(b.dataset.key)) : keys.includes(b.dataset.key);
        return boxes.filter(b => b.checked).length;
      }
      await new Promise(r => setTimeout(r, 75));
    }
    return 0;
  }
  function saveAs(name, o) {
    name = String(name || "").trim();
    if (!name) throw new Error("Enter a template name.");
    const list = tpls().filter(t => t.name !== name);
    const i = tpls().findIndex(t => t.name === name);
    const t = toTemplate(name, o || readDialog());
    if (i >= 0) list.splice(i, 0, t); else list.push(t);
    put("report_templates", list);
    return t;
  }
  const b64 = s => { const bytes = new TextEncoder().encode(s); let bin = ""; for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000)); return btoa(bin); };
  function download(rec) {
    const bin = atob(rec.content_b64), u8 = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([u8], { type: rec.mime }));
    a.download = rec.filename;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }
  /** Build the report with the given options and save it as .rtf. */
  async function exportRtf(o) {
    o = o || readDialog();
    if (!o.sections.length) throw new Error("Select at least one section.");
    if (!sky.buildEtabsReport || !sky.reportPlus) throw new Error("report builder unavailable");
    const html = await sky.buildEtabsReport(o);
    const doc = await sky.reportPlus.htmlToDoc(html, o);
    const name = String((S.model && S.model.name) || "model").replace(/[^\w.-]+/g, "_");
    let rec;
    if (S.mock) rec = { filename: `${name}_report.rtf`, mime: "application/rtf", content_b64: b64(rMockRtf(doc)) };
    else {
      const res = await fetch("/api/report/rtf", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(doc) });
      const d = await res.json().catch(() => null);
      if (!res.ok || !d) throw new Error((d && d.error) || `${res.status} ${res.statusText}`);
      rec = { filename: d.filename, mime: d.mime, content_b64: d.content_b64 };
    }
    rec.blocks = doc.blocks.length;
    rec.images = doc.blocks.filter(b => b.type === "image").length;
    rec.text = atob(rec.content_b64).slice(0, 4000);
    RS.last = rec;
    download(rec);
    return rec;
  }

  function enhance() {
    const dlg = $("rxReportDlg");
    if (!dlg || dlg.querySelector("#wbRptGroup")) return;
    const body = dlg.querySelector(".rx-create") || dlg.querySelector(".modal-body");
    const tSel = rEl("select", { id: "wbRptTpl", "aria-label": "Report template" });
    const setSel2 = rEl("select", { id: "wbRptSet", "aria-label": "Tables from set" });
    const fill = () => {
      tSel.innerHTML = `<option value="">(current choices)</option>` + tpls().map(t => `<option value="${rEsc(t.name)}">${rEsc(t.name)}</option>`).join("");
      setSel2.innerHTML = `<option value="">—</option>` + sets().map(t => `<option value="${rEsc(t.name)}">${rEsc(t.name)} (${t.tables.length})</option>`).join("");
    };
    fill();
    const btn = (txt, id, fn, title) => { const b = rEl("button", { type: "button", class: "btn btn-small", id, title: title || null }, [txt]); b.addEventListener("click", fn); return b; };
    const status = rEl("span", { class: "muted", id: "wbRptStatus" });
    const g = rEl("fieldset", { class: "dlg-group wb-rpt-group", id: "wbRptGroup" }, [rEl("legend", { text: "Report template" }),
      rEl("div", { class: "rx-row wb-rpt-row" }, [rEl("span", { text: "Template" }), tSel,
        btn("Apply", "wbRptApply", async () => { const t = tpls().find(x => x.name === tSel.value); if (t) { await applyTemplate(t); status.textContent = `“${t.name}” applied`; } }),
        btn("Save As…", "wbRptSave", () => openSaveTemplate(fill, status)),
        btn("Delete", "wbRptDel", () => { if (!tSel.value) return; put("report_templates", tpls().filter(t => t.name !== tSel.value)); fill(); status.textContent = "Template deleted"; })]),
      rEl("div", { class: "rx-row wb-rpt-row" }, [rEl("span", { text: "Tables from set" }), setSel2,
        btn("Add Tables", "wbRptSetAdd", async () => { const s = sets().find(x => x.name === setSel2.value); if (s) { const n = await tickTables(s.tables, true); status.textContent = `${n} table(s) ticked`; } },
          "Tick every table of the saved Show-Tables set")]), status]);
    body.insertBefore(g, body.firstChild && body.firstChild.nextSibling ? body.firstChild.nextSibling : null);
    const docx = dlg.querySelector("#rxDocxBtn");
    if (docx && !dlg.querySelector("#wbRtfBtn")) {
      const b = rEl("button", { type: "button", class: "btn", id: "wbRtfBtn", title: "Rich Text Format (Word, LibreOffice, WordPad)" }, ["Download .rtf"]);
      b.addEventListener("click", async () => {
        const st = $("rxStatus");
        try { if (st) st.textContent = "Building .rtf…"; const r = await exportRtf(); if (st) st.textContent = `${r.filename} · ${r.blocks} blocks · ${r.images} images`; }
        catch (e) { if (st) st.textContent = ""; toast("RTF export failed", e.message, "error", 7000); }
      });
      docx.parentNode.insertBefore(b, docx);
    }
  }
  function openSaveTemplate(onDone, status) {
    const V = { name: (tpls()[0] && $("wbRptTpl") && $("wbRptTpl").value) || `Report ${tpls().length + 1}` };
    const F = rForm();
    const o = readDialog();
    return rDialog({ id: "wbRptSaveDlg", title: "Save Report Template", size: "sm", form: F,
      body: [rGroup("Template", [F.text(V, "name", { label: "Template name", id: "wbRptName", hint: "An existing name is replaced." })]),
        rNote(`${o.sections.length} section(s) · ${o.rpTables.length} result table(s) · ${o.storyCases.length} story-plot case(s) · ${o.rpSnapCases.length} snapshot(s) · ${rEsc(o.paper)} ${rEsc(o.orientation)}`)],
      onOk: () => { try { const t = saveAs(V.name, o); onDone && onDone(); if (status) status.textContent = `“${t.name}” saved in the model`; } catch (e) { return e.message; } } });
  }
  const origOpen = sky.openCreateReport;
  if (typeof origOpen === "function") {
    sky.openCreateReport = (...a) => { const d = origOpen(...a); try { enhance(); } catch (e) { console.error("report template group", e); } return d; };
  }

  /* ================================================================ Show Tables sets */
  let curSet = "";
  function filterTree(root) {
    const tree = root.querySelector("#tblTree");
    if (!tree) return;
    const s = sets().find(x => x.name === curSet);
    tree.querySelectorAll(".tbl-leaf").forEach(b => { b.style.display = !s || s.tables.includes(b.dataset.key) ? "" : "none"; });
    tree.querySelectorAll(".tbl-node").forEach(n => { const any = [...n.querySelectorAll(".tbl-leaf")].some(b => b.style.display !== "none"); n.style.display = any ? "" : "none"; });
    if (s && s.tables.length && sky.tables && !s.tables.includes(sky.tables.current())) sky.tables.select(s.tables[0]);
  }
  function openSaveSet(root, refill) {
    const all = (sky.tables && sky.tables.list()) || [];
    const cur = sky.tables && sky.tables.current();
    const V = { name: curSet || `Set ${sets().length + 1}` };
    const pre = new Set((sets().find(s => s.name === curSet) || { tables: cur ? [cur] : [] }).tables);
    const F = rForm();
    const box = rEl("div", { class: "wb-set-list", id: "wbSetList" });
    for (const t of all) {
      const cb = rEl("input", { type: "checkbox", "data-key": t.key, checked: pre.has(t.key) || null });
      box.appendChild(rEl("label", { class: "dk-check", title: t.group }, [cb, rEl("span", { text: t.title })]));
    }
    return rDialog({ id: "wbSetSaveDlg", title: "Save Table Set", size: "md", form: F,
      body: [rGroup("Table Set", [F.text(V, "name", { label: "Set name", id: "wbSetName", hint: "An existing name is replaced." }), box])],
      onOk: () => {
        const keys = [...box.querySelectorAll("input:checked")].map(i => i.dataset.key);
        if (!keys.length) return "Tick at least one table.";
        const name = V.name.trim();
        if (!name) return "Enter a set name.";
        const list = sets().filter(s => s.name !== name);
        const i = sets().findIndex(s => s.name === name);
        const s = { name, tables: keys };
        if (i >= 0) list.splice(i, 0, s); else list.push(s);
        put("table_sets", list);
        curSet = name;
        refill(); filterTree(root);
      } });
  }
  document.addEventListener("sky:tables-open", ev => {
    const root = ev.detail && ev.detail.el;
    const tools = root && root.querySelector(".tbl-tools");
    if (!tools || tools.querySelector("#wbTblSet")) return;
    const sel2 = rEl("select", { id: "wbTblSet", class: "mini-select", "aria-label": "Table set", title: "Show only the tables of a saved set" });
    const refill = () => {
      if (curSet && !sets().some(s => s.name === curSet)) curSet = "";
      sel2.innerHTML = `<option value="">All tables</option>` + sets().map(s => `<option value="${rEsc(s.name)}"${s.name === curSet ? " selected" : ""}>${rEsc(s.name)} (${s.tables.length})</option>`).join("");
    };
    refill();
    sel2.addEventListener("change", () => { curSet = sel2.value; filterTree(root); });
    const save = rEl("button", { type: "button", class: "chip", id: "wbTblSetSave", title: "Save a named set of tables in the model" }, ["Save Set…"]);
    save.addEventListener("click", () => openSaveSet(root, refill));
    const del = rEl("button", { type: "button", class: "chip", id: "wbTblSetDel", title: "Delete the selected table set" }, ["Delete Set"]);
    del.addEventListener("click", () => { if (!curSet) return; put("table_sets", sets().filter(s => s.name !== curSet)); curSet = ""; refill(); filterTree(root); });
    tools.insertBefore(rEl("span", { class: "wb-tblset" }, [sel2, save, del]), tools.firstChild);
    const tree = root.querySelector("#tblTree");
    if (tree) new MutationObserver(() => { if (curSet) filterTree(root); }).observe(tree, { childList: true });
    filterTree(root);
  });

  sky.wb.report = { readDialog, applyTemplate, saveAs, exportRtf, templates: tpls, sets, get last() { return RS.last; },
    setTableSet: name => { curSet = name || ""; const root = $("showTablesModal"); if (root) { const s2 = root.querySelector("#wbTblSet"); if (s2) s2.value = curSet; filterTree(root); } },
    saveTableSet: (name, tables) => { const list = sets().filter(s => s.name !== name); list.push({ name, tables }); put("table_sets", list); } };
}
