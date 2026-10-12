/* SkyFrame — Rhino / Grasshopper exchange UI (CONTRACT "Rhino / Grasshopper
   exchange", docs/RHINO_LINK.md).

     File › Import › Rhino (.3dm)…   pick a .3dm (or a skyframe.exchange/1
                                     .json) → POST /api/rhino/import → layer
                                     mapping dialog → Preview → Apply
     File › Link Rhino File…         a .3dm path watched by the server
                                     (POST /api/rhino/link); the status bar
                                     shows "Rhino: linked ✓" or "Rhino:
                                     changed — Update" (polls GET
                                     /api/rhino/link); Update = re-import +
                                     the same mapping → Preview → Apply
     File › Exchange Sessions…       sessions in the model with object counts
                                     and "Remove session objects"

   Mapping dialog: one row per layer (colour swatch, object counts) → role /
   frame section (model + steel database) / shell section / material / story
   rule; mapping profiles are saved IN the model (exchange_mappings) and can
   be loaded again.  Preview = POST /api/exchange/preview (create / update /
   delete counts + warnings + per-object rows, model untouched).  Apply =
   POST /api/exchange/apply with the working model; the echoed model is
   adopted as ONE labelled undo step ("Rhino import").  ?mock=1 runs
   js/mock_rhinolink.js on canned payloads.  The store stays SI; the only
   numeric input (story tolerance) goes through js/units.js.
   Installs window.__sky.openRhinoImport / openRhinoLink /
   openExchangeSessions / rhinoLink. */

import xU from "./units.js";
import { normalizeModel as xNormalizeModel } from "./modeledit.js";
import { dialog as xDialog, btn as xBtn, footBar as xFootBar, errorLine as xErrorLine,
  showError as xShowError, closeDialog as xCloseDialog } from "./analysisdlg.js";
import { mockRhinoImport as xMockImport, mockPreview as xMockPreview, mockApply as xMockApply,
  mockSessions as xSessions, mockRemoveSession as xMockRemove, mockLinkState as xMockLinkState,
  mockLink as xMockLink, mockUnlink as xMockUnlink, mockLinkTouch as xMockLinkTouch } from "./mock_rhinolink.js";
import { mockSdbShapes as xMockSdbShapes } from "./mock_steeldb.js";

const ROLE_OPTS = [["", "auto (geometry)"], ["column", "Column"], ["beam", "Beam"], ["brace", "Brace"],
  ["frame", "Frame (auto kind)"], ["slab", "Slab"], ["wall", "Wall"], ["shell", "Shell"],
  ["support", "Support"], ["joint", "Joint"], ["link", "Link"], ["ignore", "Ignore"]];
const POLL_MS = 2000;

function el(tag, attrs = {}, kids = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
    else if (v != null && v !== false) n.setAttribute(k, v === true ? "" : v);
  }
  (Array.isArray(kids) ? kids : [kids]).forEach(c =>
    c != null && n.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
  return n;
}
const select = (opts, value, attrs = {}) => {
  const s = el("select", attrs);
  for (const [v, l] of opts) s.appendChild(el("option", { value: v, text: l }));
  s.value = value || "";
  return s;
};

export function initRhinoLink(sky) {
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 4500) => sky.toast && sky.toast(t, m, k, ms);
  if (!document.querySelector("link[data-rhinolink-css]")) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/static/rhinolink.css"; l.setAttribute("data-rhinolink-css", "1");
    document.head.appendChild(l);
  }
  const st = { last: null, rules: {}, link: { linked: false }, sdbNames: null, timer: null };

  /* ------------------------------------------------------------ backend */
  async function call(method, path, body, form) {
    const init = { method };
    if (form) init.body = form;
    else if (body !== undefined) { init.headers = { "Content-Type": "application/json" }; init.body = JSON.stringify(body); }
    const res = await fetch(path, init);
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
    return data;
  }
  const workingModel = () => { const p = JSON.parse(JSON.stringify(S.model)); delete p._mock_params; return p; };
  const mockOr = r => { if (r && r.error) throw new Error(r.error); return r; };

  async function importFile(file) {
    if (/\.json$/i.test(file.name)) {                       // a GH / hand-written payload
      const pl = JSON.parse(await file.text());
      if (!pl || pl.schema !== "skyframe.exchange/1") throw new Error("not a skyframe.exchange/1 payload");
      return { exchange: pl, layers: layersOf(pl), warnings: pl.warnings || [] };
    }
    if (S.mock) return mockOr(xMockImport({ fileName: file.name }));
    const fd = new FormData();
    fd.append("file", file, file.name);
    return call("POST", "/api/rhino/import", undefined, fd);
  }
  async function importPath(path) {
    if (S.mock) return mockOr(xMockImport({ path }));
    return call("POST", "/api/rhino/import", { path });
  }
  async function preview(exchange, mapping, mode) {
    if (S.mock) return mockOr(xMockPreview(S.model, { exchange, mapping, mode }));
    return call("POST", "/api/exchange/preview", { exchange, mapping, mode, model: workingModel() });
  }
  async function applyRaw(exchange, mapping, mode) {
    if (S.mock) return mockOr(xMockApply(S.model, { exchange, mapping, mode }));
    return call("POST", "/api/exchange/apply", { exchange, mapping, mode, model: workingModel() });
  }
  async function linkState() {
    if (S.mock) return xMockLinkState();
    return call("GET", "/api/rhino/link");
  }
  async function linkSet(path, autoApply) {
    if (S.mock) return mockOr(xMockLink(path));
    return call("POST", "/api/rhino/link", { path, auto_apply: !!autoApply });
  }
  async function unlink() {
    const r = S.mock ? xMockUnlink() : await call("DELETE", "/api/rhino/link");
    st.link = r; syncIndicator();
    return r;
  }
  async function sectionNames() {
    if (st.sdbNames) return st.sdbNames;
    try {
      const r = S.mock ? xMockSdbShapes({ standard: "aisc" })
        : await call("GET", "/api/steel_db/shapes?standard=aisc");
      st.sdbNames = (r.shapes || []).map(s => s.name);
    } catch (e) { st.sdbNames = []; }
    return st.sdbNames;
  }
  function layersOf(pl) {
    const rows = new Map((pl.layers || []).map(l => [l.path, { path: l.path, color: l.color, count: 0, kinds: {} }]));
    for (const o of pl.objects || []) {
      if (!rows.has(o.layer)) rows.set(o.layer, { path: o.layer, color: "#808080", count: 0, kinds: {} });
      const r = rows.get(o.layer); r.count++; r.kinds[o.kind] = (r.kinds[o.kind] || 0) + 1;
    }
    return [...rows.values()];
  }

  /* ------------------------------------------------------------ adopt */
  function refreshViews() {
    S.modelEdited = true;
    try { sky.rebuildStorySelect && sky.rebuildStorySelect(); } catch (e) { console.error(e); }
    try { sky.planEditor && sky.planEditor.refresh(); } catch (e) { console.error(e); }
    try { sky.elevEditor && sky.elevEditor.refresh(); } catch (e) { console.error(e); }
    try { sky.renderProps && sky.renderProps(); } catch (e) { console.error(e); }
    try { if (S.mode === "analyze" && sky.viewer) { sky.viewer.setModel(S.model); S.modelEdited = false; } } catch (e) { console.error(e); }
    try { if (S.mode === "loads" && sky.loadsEditor) sky.loadsEditor.render(); } catch (e) { console.error(e); }
  }
  /** Adopt a model echoed by the exchange as ONE labelled undo step. */
  function adopt(model, label) {
    const md = S.mock ? model : (sky.keepSetup ? sky.keepSetup(model) : model);
    if (S.model && S.model._mock_params !== undefined && md._mock_params === undefined) md._mock_params = S.model._mock_params;
    if (sky.history && sky.history.label) sky.history.label(label);
    S.model = xNormalizeModel(md);
    const live = {};
    for (const k of ["member", "shell", "link"]) live[k] = new Set((S.model[k + "s"] || []).map(o => o.uid));
    S.selection = (S.selection || []).filter(r => !live[r.type] || live[r.type].has(r.uid));
    S.results = S.results || null;
    sky.markDirty();
    refreshViews();
  }
  async function apply(exchange, mapping, mode = "merge", { run = false } = {}) {
    const res = await applyRaw(exchange, mapping, mode);
    adopt(res.model, "Rhino import");
    st.last = res;
    const c = res.report.summary;
    const n = k => c.create[k] + c.update[k] + c.delete[k];
    toast("Rhino import", `${c.create.members} member(s), ${c.create.shells} shell(s) created · `
      + `${c.update.members + c.update.shells} updated · ${c.delete.members + c.delete.shells + c.delete.joints} deleted`
      + (n("members") + n("shells") + n("joints") ? " — Edit › Undo reverts it." : ""));
    await pollLink();
    if (run && sky.doRun) await sky.doRun();
    return res;
  }

  /* ------------------------------------------------------------ mapping dialog */
  function rulesFromRows(rows) {
    const rules = [];
    for (const r of rows) {
      const rule = { layer: r.path };
      for (const k of ["role", "section", "shell_section", "material", "story_rule"])
        if (r[k]) rule[k] = r[k];
      if (Object.keys(rule).length > 1) rules.push(rule);
    }
    return rules;
  }
  function rowsFromRules(layers, rules) {
    return layers.map(ly => {
      const r = (rules || []).find(x => x.layer === ly.path) || {};
      return { path: ly.path, color: ly.color, count: ly.count, kinds: ly.kinds,
        role: r.role || "", section: r.section || "", shell_section: r.shell_section || "",
        material: r.material || "", story_rule: r.story_rule || "" };
    });
  }

  async function openMapping(imp, { title = "Import Rhino Model", linkPath = null } = {}) {
    if (!S.model) { toast("Rhino import", "Load a model first."); return null; }
    const pl = imp.exchange;
    const ses = pl.session;
    const profiles = () => (S.model.exchange_mappings || {});
    const prev = st.rules[ses];
    const rows = rowsFromRules(imp.layers || layersOf(pl), prev ? prev.rules : []);
    const o = { mode: "merge", run: false, story_tol: prev && prev.story_tolerance ? prev.story_tolerance : 0.05 };
    const names = await sectionNames();
    const body = el("div", { class: "dlg-check rhx-dlg" });
    const src = pl.source || {};
    body.appendChild(el("p", { class: "dlg-intro muted", text:
      `${src.doc || "payload"} · ${pl.objects.length} object(s) · units ${src.units} · tolerance ${src.tolerance} ${src.units}. `
      + "Each layer maps to a role and properties; empty = automatic (UserText sf_* keys and the geometry decide). "
      + "Preview shows what changes; Apply makes it one step (Edit › Undo reverts it)." }));
    if ((imp.warnings || []).length) {
      const w = el("div", { class: "rhx-warn" }, [el("div", { class: "rhx-head", text: "Import warnings" })]);
      for (const t of imp.warnings.slice(0, 20)) w.appendChild(el("div", { class: "muted rhx-wrow", text: t }));
      body.appendChild(w);
    }
    // profile bar
    const profSel = select([["", "— mapping profile —"], ...Object.keys(profiles()).map(n => [n, n])], "", { id: "rhxProfileSel", "aria-label": "Mapping profile" });
    const profName = el("input", { type: "text", id: "rhxProfileName", placeholder: "profile name", maxlength: "80", "aria-label": "Profile name" });
    const profBar = el("div", { class: "rhx-prof" }, [
      el("span", { class: "muted", text: "Profile" }), profSel,
      xBtn("Load", "btn-small", () => loadProfile()),
      profName,
      xBtn("Save Profile", "btn-small", () => saveProfile(), "Store the mapping in the model (exchange_mappings)"),
    ]);
    body.appendChild(profBar);
    // layer table
    const secOpts = [["", "auto"], ...Object.keys(S.model.sections || {}).map(n => [n, n])];
    const shOpts = [["", "auto"], ...Object.keys(S.model.shell_sections || {}).map(n => [n, n])];
    const matOpts = [["", "auto"], ...Object.keys(S.model.materials || {}).map(n => [n, n])];
    const stOpts = [["", "auto (cluster levels)"], ...(S.model.stories || []).map(s => [s.name, s.name])];
    const dl = el("datalist", { id: "rhxSecList" });
    for (const n of [...Object.keys(S.model.sections || {}), ...names.slice(0, 4000)])
      dl.appendChild(el("option", { value: n }));
    body.appendChild(dl);
    const table = el("table", { class: "rhx-table", id: "rhxLayerTable" });
    table.appendChild(el("thead", {}, el("tr", {}, ["Layer", "Objects", "Role", "Frame section", "Shell section", "Material", "Story"]
      .map(h => el("th", { text: h })))));
    const tb = el("tbody");
    const inputs = [];
    rows.forEach((r, i) => {
      const kinds = Object.entries(r.kinds || {}).map(([k, v]) => `${v} ${k}`).join(", ");
      const role = select(ROLE_OPTS, r.role, { "data-k": "role", "aria-label": `Role of ${r.path}` });
      const sec = el("input", { type: "text", list: "rhxSecList", value: r.section, placeholder: "auto", "data-k": "section", "aria-label": `Section of ${r.path}` });
      const sh = select(shOpts, r.shell_section, { "data-k": "shell_section", "aria-label": `Shell section of ${r.path}` });
      const mat = select(matOpts, r.material, { "data-k": "material", "aria-label": `Material of ${r.path}` });
      const sto = select(stOpts, r.story_rule, { "data-k": "story_rule", "aria-label": `Story of ${r.path}` });
      for (const c of [role, sec, sh, mat, sto]) {
        c.addEventListener("change", () => { r[c.dataset.k] = c.value.trim(); });
        c.addEventListener("input", () => { r[c.dataset.k] = c.value.trim(); });
      }
      inputs.push({ r, role, sec, sh, mat, sto });
      tb.appendChild(el("tr", { "data-layer": r.path, class: r.count ? "" : "rhx-empty" }, [
        el("td", {}, [el("span", { class: "rhx-swatch", style: `background:${/^#[0-9a-f]{6}$/i.test(r.color) ? r.color : "#808080"}` }),
          el("span", { class: "rhx-path", text: r.path || "(no layer)" })]),
        el("td", { class: "rhx-num", title: kinds, text: String(r.count) }),
        el("td", {}, role), el("td", {}, sec), el("td", {}, sh), el("td", {}, mat), el("td", {}, sto),
      ]));
    });
    table.appendChild(tb);
    body.appendChild(el("div", { class: "rhx-scroll" }, table));
    // options
    const tolIn = el("input", { type: "number", id: "rhxStoryTol", min: "0", step: xU.step("length", 0.01), value: xU.inputValue("length", o.story_tol) });
    const modeSel = select([["merge", "Merge (keep SkyFrame assignments)"], ["replace", "Replace the session's objects"]], o.mode, { id: "rhxMode" });
    const runChk = el("input", { type: "checkbox", id: "rhxRun" });
    body.appendChild(el("div", { class: "rhx-opts" }, [
      el("label", {}, [el("span", { text: "Story tolerance " }), tolIn, el("span", { class: "muted", text: " " + xU.label("length") })]),
      el("label", {}, [el("span", { text: "Mode " }), modeSel]),
      el("label", { class: "dlg-chk" }, [runChk, el("span", { text: "Run the analysis after Apply" })]),
    ]));
    const prevBox = el("div", { class: "rhx-preview", id: "rhxPreview" },
      el("div", { class: "muted", text: "Click Preview to see what will be created, updated and deleted (the model is not touched)." }));
    body.appendChild(prevBox);
    const err = xErrorLine();
    body.appendChild(err);

    const syncInputs = () => inputs.forEach(({ r, role, sec, sh, mat, sto }) => {
      role.value = r.role; sec.value = r.section; sh.value = r.shell_section; mat.value = r.material; sto.value = r.story_rule;
    });
    function loadProfile() {
      const p = profiles()[profSel.value];
      if (!p) { xShowError(err, "Pick a saved profile first."); return; }
      xShowError(err, "");
      const fresh = rowsFromRules(rows, p.rules || []);
      fresh.forEach((f, i) => Object.assign(rows[i], f));
      if (p.story_tolerance) tolIn.value = xU.inputValue("length", p.story_tolerance);
      profName.value = profSel.value;
      syncInputs();
    }
    function saveProfile() {
      const name = profName.value.trim();
      if (!name) { xShowError(err, "Enter a profile name."); return; }
      const m = readMapping();
      if (!m) return;
      if (sky.history && sky.history.label) sky.history.label("Save mapping profile");
      S.model.exchange_mappings = Object.assign({}, S.model.exchange_mappings || {}, { [name]: m });
      sky.markDirty();
      if (![...profSel.options].some(op => op.value === name)) profSel.appendChild(el("option", { value: name, text: name }));
      profSel.value = name;
      toast("Mapping profile", `Saved “${name}” in the model.`);
    }
    function readMapping() {
      const tol = xU.parse("length", tolIn.value);
      if (!(tol > 0 && tol <= 10)) { xShowError(err, `Story tolerance must be > 0 and ≤ ${xU.fmtU("length", 10, 2)}.`); return null; }
      xShowError(err, "");
      const m = { rules: rulesFromRows(rows) };
      if (Math.abs(tol - 0.05) > 1e-12) m.story_tolerance = tol;
      return m;
    }
    let busy = false;
    const doPreview = async () => {
      const mapping = readMapping();
      if (!mapping || busy) return null;
      busy = true; prevBtn.disabled = true; prevBox.classList.add("is-busy");
      try {
        const pv = await preview(pl, mapping, modeSel.value);
        renderPreview(prevBox, pv);
        applyBtn.disabled = false;
        return pv;
      } catch (e) { xShowError(err, e.message); return null; }
      finally { busy = false; prevBtn.disabled = false; prevBox.classList.remove("is-busy"); }
    };
    const doApply = async () => {
      const mapping = readMapping();
      if (!mapping || busy) return null;
      busy = true; applyBtn.disabled = true;
      try {
        st.rules[ses] = mapping;
        const res = await apply(pl, mapping, modeSel.value, { run: false });
        d.close();
        if (runChk.checked && sky.doRun) await sky.doRun();
        return res;
      } catch (e) { xShowError(err, e.message); return null; }
      finally { busy = false; applyBtn.disabled = false; }
    };
    const prevBtn = xBtn("Preview", "", doPreview, "List the creates / updates / deletes (dry run)");
    const applyBtn = xBtn("Apply", "btn-run", doApply, "Apply the import (one undo step)");
    prevBtn.id = "rhxPreviewBtn"; applyBtn.id = "rhxApplyBtn";
    const fb = xFootBar(linkPath ? `Linked: ${linkPath}` : "File › Import › Rhino (.3dm)", [xBtn("Cancel", "", () => d.close()), prevBtn, applyBtn]);
    const d = xDialog("rhxMapModal", { title, wide: true, body, foot: fb.wrap });
    return d;
  }

  function renderPreview(box, pv) {
    box.textContent = "";
    const s = pv.summary;
    const grid = el("table", { class: "rhx-sum", id: "rhxSummary" });
    grid.appendChild(el("tr", {}, ["", "Members", "Shells", "Joints"].map(h => el("th", { text: h }))));
    for (const k of ["create", "update", "delete", "unchanged"]) {
      if (!s[k]) continue;
      grid.appendChild(el("tr", { "data-row": k }, [el("th", { text: k[0].toUpperCase() + k.slice(1) }),
        ...["members", "shells", "joints"].map(c => el("td", { class: "rhx-num" + (s[k][c] ? " is-on rhx-" + k : ""), text: String(s[k][c]) }))]));
    }
    box.appendChild(grid);
    if ((pv.stories_created || []).length) box.appendChild(el("div", { class: "muted", text: `New stories: ${pv.stories_created.join(", ")}` }));
    if (pv.valid === false) box.appendChild(el("div", { class: "rhx-bad", text: "The converted model does not validate — see the warnings." }));
    if ((pv.warnings || []).length) {
      const w = el("div", { class: "rhx-warn", id: "rhxWarnings" }, [el("div", { class: "rhx-head", text: `Warnings (${pv.warnings.length})` })]);
      for (const t of pv.warnings.slice(0, 60)) w.appendChild(el("div", { class: "muted rhx-wrow", text: t }));
      box.appendChild(w);
    }
    const objs = pv.objects || [];
    const t = el("table", { class: "rhx-objs" });
    t.appendChild(el("tr", {}, ["Object", "Layer", "Role", "Section", "Story", "Status"].map(h => el("th", { text: h }))));
    for (const r of objs.slice(0, 300)) t.appendChild(el("tr", { class: "rhx-st-" + r.status }, [
      el("td", { class: "rhx-id", title: r.id, text: r.id.length > 22 ? r.id.slice(0, 8) + "…" + r.id.slice(-10) : r.id }),
      el("td", { text: r.layer || "" }), el("td", { text: r.role || "" }), el("td", { text: r.section || "" }),
      el("td", { text: r.story || "" }), el("td", { text: r.status })]));
    box.appendChild(el("details", { class: "rhx-details" }, [el("summary", { text: `Objects (${objs.length})` }), t]));
  }

  /* ------------------------------------------------------------ entry points */
  const fileIn = el("input", { type: "file", accept: ".3dm,.json", id: "rhxFileInput", class: "hidden", "aria-hidden": "true" });
  fileIn.addEventListener("change", async () => {
    const f = fileIn.files && fileIn.files[0];
    fileIn.value = "";
    if (!f) return;
    try { await openMapping(await importFile(f)); }
    catch (e) { toast("Rhino import", e.message, "error", 7000); }
  });
  document.body.appendChild(fileIn);
  function openRhinoImport() { fileIn.click(); }

  function openRhinoLink() {
    const body = el("div", { class: "dlg-check rhx-dlg" });
    body.appendChild(el("p", { class: "dlg-intro muted", text:
      "Link a .3dm file on this computer: SkyFrame watches it and the status bar shows “Rhino: changed — Update” after each save in Rhino. "
      + "Update re-imports it with the same layer mapping (merge: your SkyFrame sections, loads and releases are kept)." }));
    const pathIn = el("input", { type: "text", id: "rhxLinkPath", placeholder: "C:\\Projects\\tower.3dm", value: st.link.path || "", size: "48", "aria-label": ".3dm path" });
    const auto = el("input", { type: "checkbox", id: "rhxLinkAuto" });
    body.appendChild(el("label", { class: "dlg-sub-row" }, [el("span", { text: "File" }), pathIn]));
    body.appendChild(el("label", { class: "dlg-chk" }, [auto, el("span", { text: "Apply changes automatically (no preview)" })]));
    if (st.link.linked) body.appendChild(el("div", { class: "muted", text: `Linked now: ${st.link.path}` }));
    const err = xErrorLine();
    body.appendChild(err);
    const go = async () => {
      const p = pathIn.value.trim();
      if (!p) { xShowError(err, "Enter the path of a .3dm file."); return; }
      try {
        st.link = await linkSet(p, auto.checked);
        syncIndicator();
        d.close();
        if (!auto.checked) await updateFromLink();
      } catch (e) { xShowError(err, e.message); }
    };
    const btns = [xBtn("Cancel", "", () => d.close())];
    if (st.link.linked) btns.push(xBtn("Unlink", "", async () => { await unlink(); d.close(); toast("Rhino link", "Unlinked."); }));
    const linkBtn = xBtn("Link & Import", "btn-run", go);
    linkBtn.id = "rhxLinkBtn";
    btns.push(linkBtn);
    const d = xDialog("rhxLinkModal", { title: "Link Rhino File", body, foot: xFootBar("File › Link Rhino File", btns).wrap });
    pathIn.focus();
    return d;
  }
  async function updateFromLink() {
    if (!st.link.linked) { openRhinoLink(); return null; }
    try { return await openMapping(await importPath(st.link.path), { title: "Update from Rhino", linkPath: st.link.path }); }
    catch (e) { toast("Rhino link", e.message, "error", 7000); return null; }
  }

  function openExchangeSessions() {
    const body = el("div", { class: "dlg-check rhx-dlg" });
    const render = () => {
      body.textContent = "";
      if (!S.mock) body.appendChild(el("p", { class: "muted", id: "rhxConnectAddr", text:
        `Grasshopper: point the SkyFrame Connect component at ${location.origin}` }));
      const ses = xSessions(S.model);
      if (!ses.length) { body.appendChild(el("p", { class: "muted", id: "rhxNoSessions", text: "No exchange sessions in this model — import a Rhino file or send from Grasshopper." })); return; }
      const t = el("table", { class: "rhx-table", id: "rhxSessionTable" });
      t.appendChild(el("tr", {}, ["Session", "Source", "Objects", "Members", "Shells", "Supports", "Joints", "Last update", ""].map(h => el("th", { text: h }))));
      for (const s of ses) {
        const rm = xBtn("Remove session objects", "btn-small", async () => {
          try {
            const r = S.mock ? mockOr(xMockRemove(S.model, s.session))
              : await call("POST", "/api/exchange/sessions/remove", { session: s.session, model: workingModel() });
            adopt(r.model, "Remove exchange session");
            toast("Exchange session", `Removed ${r.report.deleted.members.length} member(s), ${r.report.deleted.shells.length} shell(s) — Edit › Undo reverts it.`);
            await pollLink();
            render();
          } catch (e) { toast("Exchange session", e.message, "error"); }
        });
        rm.setAttribute("data-session", s.session);
        t.appendChild(el("tr", { "data-session": s.session }, [
          el("td", { class: "rhx-id", title: s.session, text: s.session.length > 28 ? s.session.slice(0, 12) + "…" + s.session.slice(-12) : s.session }),
          el("td", { text: [s.app, s.doc].filter(Boolean).join(" · ") }),
          ...[s.objects, s.members, s.shells, s.supports, s.joints].map(v => el("td", { class: "rhx-num", text: String(v) })),
          el("td", { class: "muted", text: s.last_update || "" }), el("td", {}, rm)]));
      }
      body.appendChild(el("div", { class: "rhx-scroll" }, t));
    };
    render();
    return xDialog("rhxSessionsModal", { title: "Exchange Sessions", wide: true, body,
      foot: xFootBar("Objects keep their Rhino / Grasshopper id (source_ref)", [xBtn("Close", "", () => xCloseDialog("rhxSessionsModal"))]).wrap });
  }

  /* ------------------------------------------------------------ status bar */
  const ind = el("button", { class: "sb-chip rhx-ind hidden", id: "sbRhinoLink", type: "button" });
  ind.addEventListener("click", () => (st.link.changed ? updateFromLink() : openRhinoLink()));
  const place = () => {
    const spacer = document.querySelector(".sb-spacer");
    if (spacer && !ind.isConnected) spacer.parentNode.insertBefore(ind, spacer);
  };
  function syncIndicator() {
    place();
    const L = st.link || {};
    ind.classList.toggle("hidden", !L.linked);
    ind.classList.toggle("is-changed", !!L.changed);
    ind.textContent = !L.linked ? "" : L.changed ? "Rhino: changed — Update" : (L.exists === false ? "Rhino: file missing" : "Rhino: linked ✓");
    ind.title = L.linked ? `${L.path}${L.changed ? " — saved in Rhino since the last import: click to update" : ""}` : "";
  }
  let lastRev = null;
  async function pollLink() {
    try {
      const L = await linkState();
      const rev = L.revision;
      st.link = L;
      syncIndicator();
      // an auto-applied change on the server: reload the model
      if (!S.mock && rev != null && lastRev != null && rev !== lastRev && L.auto_apply) {
        const m = await call("GET", "/api/model");
        adopt(m, "Rhino link update");
        toast("Rhino link", "The linked file changed — model updated.");
      }
      lastRev = rev;
    } catch (e) { /* server busy: next tick */ }
    return st.link;
  }
  st.timer = setInterval(pollLink, POLL_MS);
  setTimeout(pollLink, 50);

  // menus via the runtime registry: File › Import › Rhino (.3dm)… (the
  // registry puts it under File › More when there is no Import submenu)
  if (sky.menu && sky.menu.add) {
    sky.menu.add("File/Import", { label: "Rhino (.3dm)…", act: "file-rhx-import", hint: "layers → roles · merge", fn: () => openRhinoImport() });
    sky.menu.add("File", { label: "Link Rhino File…", act: "file-rhx-link", hint: "watch a .3dm · Update", fn: () => openRhinoLink() });
    sky.menu.add("File", { label: "Exchange Sessions…", act: "file-rhx-sessions", hint: "Rhino · Grasshopper", fn: () => openExchangeSessions() });
  }

  sky.openRhinoImport = openRhinoImport;
  sky.openRhinoLink = openRhinoLink;
  sky.openExchangeSessions = openExchangeSessions;
  sky.rhinoLink = {
    openImport: openRhinoImport, openLink: openRhinoLink, openSessions: openExchangeSessions,
    importFile, importPath, openMapping, preview, apply, adopt, update: updateFromLink,
    link: linkSet, unlink, poll: pollLink, linkState: () => st.link, sessions: () => xSessions(S.model),
    last: () => st.last, mockTouch: () => { const r = xMockLinkTouch(); return pollLink().then(() => r); },
  };
}

export default initRhinoLink;
