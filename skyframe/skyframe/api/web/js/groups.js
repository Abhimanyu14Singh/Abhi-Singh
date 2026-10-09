/* SkyFrame — ETABS Groups UI.

     Define  > Groups…                       define / rename / delete / colour
     Assign  > Assign Objects to Group…      current selection → a group
     Select  > Select by Group…              group → selection (also deselect)
     Display > Show Group…                   colour a group's objects in
                                             plan / elevation / 3D
     Model Explorer "Groups" node            every group with its colour swatch
     Section-cut card "defined by group"     SectionCut.group (loads.js hook)

   CONTRACT "Groups and user-defined staged construction" — model.groups =
   {name: {members, shells, links, points: [[x,y,z]], color}}. Every dialog
   edits a DRAFT and writes the model only on OK (Cancel / Esc discard), then
   calls sky.markDirty(). Coordinates go through units.js (store stays SI).
   Pure helpers live in groups_model.js (also used by modeledit.js so erasing
   an object keeps the groups consistent). */

import { dialog as grpDialog, btn as grpBtn, footBar as grpFootBar, errorLine as grpErrorLine,
  showError as grpShowError } from "./analysisdlg.js";
import GU from "./units.js";
import * as GM from "./groups_model.js";

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
const keyOfPoint = p => p.map(v => +(+v).toFixed(4)).join(",");   // == modeledit.springKey
const countStr = g => {
  const n = GM.normGroup(g);
  const parts = [];
  if (n.members.length) parts.push(`${n.members.length} frame${n.members.length > 1 ? "s" : ""}`);
  if (n.shells.length) parts.push(`${n.shells.length} shell${n.shells.length > 1 ? "s" : ""}`);
  if (n.links.length) parts.push(`${n.links.length} link${n.links.length > 1 ? "s" : ""}`);
  if (n.points.length) parts.push(`${n.points.length} joint${n.points.length > 1 ? "s" : ""}`);
  return parts.join(" · ") || "empty";
};

export function initGroups(sky) {
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 4500) => sky.toast && sky.toast(t, m, k, ms);
  const model = () => S.model;
  const shown = new Set();                 // groups highlighted (Show Group)

  /* ---------------- refresh after a model change ---------------- */
  const after = (what) => {
    sky.markDirty();
    try { sky.renderProps && sky.renderProps(); } catch (e) { console.error(e); }
    if (S.mode === "loads" && sky.loadsEditor) sky.loadsEditor.render();
    for (const n of [...shown]) if (!GM.groupsOf(model())[n]) shown.delete(n);
    redraw();
    refreshExplorer();
    document.dispatchEvent(new CustomEvent("sky:groups-changed", { detail: { what } }));
  };
  function redraw() {
    try { sky.planEditor && sky.planEditor.renderStatic(); } catch (e) { console.error(e); }
    try { sky.elevEditor && sky.elevEditor.renderStatic(); } catch (e) { console.error(e); }
    if (sky.viewer) sky.viewer._dirty = true;
    syncLegend();
  }

  /* ---------------- selection <-> group ---------------- */
  /** Group contents of the current selection. joints: also object end points. */
  function selectionContents(withJoints) {
    const m = model();
    const out = { members: [], shells: [], links: [], points: [] };
    const addPt = p => { if (!out.points.some(q => GM.samePoint(q, p))) out.points.push([+p[0], +p[1], +p[2]]); };
    for (const r of (S.selection || [])) {
      const k = GM.REF_KIND[r.type];
      if (k) {
        if (!out[k].includes(r.uid)) out[k].push(r.uid);
        if (withJoints) {
          if (k === "members") { const x = m.members.find(o => o.uid === r.uid); if (x) { addPt(x.pi); addPt(x.pj); } }
          if (k === "links") { const x = (m.links || []).find(o => o.uid === r.uid); if (x) { addPt(x.pi); addPt(x.pj); } }
          if (k === "shells") { const x = (m.shells || []).find(o => o.uid === r.uid); if (x) x.corners.forEach(addPt); }
        }
      } else if (r.type === "spring") {
        const sp = (m.spring_supports || []).find(s => keyOfPoint(s.point) === r.uid);
        if (sp) addPt(sp.point);
      }
    }
    return out;
  }
  /** Selection refs of a group's objects (points → spring supports there). */
  function groupRefs(name) {
    const m = model();
    const g = GM.groupsOf(m)[name];
    if (!g) return [];
    const refs = [];
    for (const k of GM.GROUP_KINDS) for (const uid of (g[k] || [])) refs.push({ type: GM.KIND_REF[k], uid });
    for (const p of (g.points || [])) {
      const sp = (m.spring_supports || []).find(s => GM.samePoint(s.point, p));
      if (sp) refs.push({ type: "spring", uid: keyOfPoint(sp.point) });
    }
    return refs;
  }
  function selectGroups(names, deselect = false) {
    const refs = names.flatMap(groupRefs);
    const key = r => `${r.type}:${r.uid}`;
    if (deselect) {
      const drop = new Set(refs.map(key));
      sky.handleSelect((S.selection || []).filter(r => !drop.has(key(r))), false);
    } else {
      const cur = new Map((S.selection || []).map(r => [key(r), r]));
      for (const r of refs) cur.set(key(r), r);
      sky.handleSelect([...cur.values()], false);
    }
    return refs.length;
  }

  /* ================= Define > Groups… ================= */
  function openGroups(focus) {
    const m = model();
    if (!m) return null;
    // draft: the group dict + the two places that reference groups
    const draft = { groups: clone(GM.groupsOf(m)), section_cuts: clone(m.section_cuts || []),
      staged_cases: clone(m.staged_cases || {}), patterns: m.patterns, members: m.members, shells: m.shells, links: m.links };
    const initial = JSON.stringify([draft.groups, draft.section_cuts, draft.staged_cases]);
    let cur = focus && draft.groups[focus] ? focus : (Object.keys(draft.groups)[0] || null);
    const dShown = new Set(shown);
    let delArmed = null;

    const body = h("div", { class: "grp-def" });
    body.appendChild(h("p", { class: "muted dlg-intro", html:
      "A group is a named set of frames, shells, links and joints. Groups drive " +
      "<b>user-defined staged construction</b> (add / remove / load a group per stage) and " +
      "<b>section cuts defined by group</b>; on their own they never change results." }));
    const cols = h("div", { class: "grp-cols" });
    const left = h("div", { class: "grp-left" });
    const listWrap = h("div", { class: "table-scroll grp-list-wrap" });
    const table = h("table", { class: "data-table grp-table", id: "grpDefTable" });
    listWrap.appendChild(table);
    left.appendChild(listWrap);
    const lbtns = h("div", { class: "grp-btn-col" });
    const bAdd = grpBtn("Add New Group", "btn-small", () => {
      const n = GM.uniqueGroupName(draft);
      draft.groups[n] = GM.normGroup({ color: GM.nextGroupColor(draft) });
      cur = n; render();
    }, "Create an empty group");
    bAdd.id = "grpDefAdd";
    const bAddSel = grpBtn("New from Selection", "btn-small", () => {
      const c = selectionContents(false);
      if (!GM.GROUP_KINDS.some(k => c[k].length) && !c.points.length) { grpShowError(err, "Nothing selected — select objects in plan / elevation first."); return; }
      const n = GM.uniqueGroupName(draft);
      draft.groups[n] = GM.normGroup({ ...c, color: GM.nextGroupColor(draft) });
      cur = n; render();
    }, "Create a group holding the current selection");
    bAddSel.id = "grpDefAddSel";
    const bDel = grpBtn("Delete Group", "btn-small", () => {
      if (!cur) return;
      const refs = GM.groupReferences(draft, cur);
      if (refs.length && delArmed !== cur) {
        delArmed = cur;
        bDel.textContent = "Delete anyway";
        grpShowError(err, `${cur} is used by ${refs.join(", ")}. Click "Delete anyway" to delete it and clear those references.`);
        return;
      }
      GM.deleteGroup(draft, cur, true);
      if (!draft.groups) draft.groups = {};
      dShown.delete(cur);
      cur = Object.keys(draft.groups)[0] || null;
      render();
    }, "Delete the selected group");
    bDel.id = "grpDefDelete";
    lbtns.append(bAdd, bAddSel, bDel);
    left.appendChild(lbtns);

    const right = h("div", { class: "grp-right", id: "grpDefDetail" });
    cols.append(left, right);
    body.appendChild(cols);
    const err = grpErrorLine();
    body.appendChild(err);

    function render() {
      if (!draft.groups) draft.groups = {};
      delArmed = delArmed === cur ? delArmed : null;
      bDel.textContent = delArmed ? "Delete anyway" : "Delete Group";
      if (!delArmed) grpShowError(err, "");
      const names = Object.keys(draft.groups);
      table.innerHTML = `<thead><tr><th class="txt">Group</th><th class="txt">Contents</th><th class="txt" title="Show Group highlight">Show</th></tr></thead>`;
      const tb = h("tbody");
      if (!names.length) tb.innerHTML = `<tr><td colspan="3" class="txt muted">No groups yet — Add New Group or New from Selection.</td></tr>`;
      for (const n of names) {
        const g = draft.groups[n];
        const tr = h("tr", { class: "grp-row" + (n === cur ? " is-sel" : ""), "data-group": n });
        const sw = h("span", { class: "grp-swatch", style: `background:${g.color || "#888"}` });
        const shw = h("input", { type: "checkbox", class: "grp-show-cb", title: "Show Group (highlight in plan / elevation / 3D)" });
        shw.checked = dShown.has(n);
        shw.addEventListener("click", e => e.stopPropagation());
        shw.addEventListener("change", () => { shw.checked ? dShown.add(n) : dShown.delete(n); });
        tr.append(h("td", { class: "txt" }, [sw, h("b", { text: n })]), h("td", { class: "txt muted", text: countStr(g) }), h("td", { class: "txt" }, shw));
        tr.addEventListener("click", () => { cur = n; render(); });
        tb.appendChild(tr);
      }
      table.appendChild(tb);
      renderDetail();
    }

    function renderDetail() {
      right.textContent = "";
      if (!cur || !draft.groups[cur]) { right.appendChild(h("p", { class: "muted", text: "Select or add a group." })); return; }
      const g = draft.groups[cur];
      const fs = fieldset("Group Data");
      const nameIn = h("input", { type: "text", id: "grpDefName", value: cur, spellcheck: "false" });
      nameIn.addEventListener("change", () => {
        const nu = nameIn.value.trim();
        if (nu === cur) return;
        if (!nu) { grpShowError(err, "Group name cannot be empty."); nameIn.value = cur; return; }
        if (draft.groups[nu]) { grpShowError(err, `A group named ${nu} already exists.`); nameIn.value = cur; return; }
        const wasShown = dShown.delete(cur);
        GM.renameGroup(draft, cur, nu);
        if (wasShown) dShown.add(nu);
        cur = nu; render();
      });
      const colIn = h("input", { type: "color", id: "grpDefColor", value: /^#[0-9a-f]{6}$/i.test(g.color) ? g.color : "#888888" });
      colIn.addEventListener("input", () => { g.color = colIn.value; render(); });
      fs.append(
        h("label", { class: "grp-field" }, [h("span", { text: "Name" }), nameIn]),
        h("label", { class: "grp-field" }, [h("span", { text: "Color" }), colIn]),
        h("div", { class: "grp-field" }, [h("span", { text: "Contents" }), h("b", { id: "grpDefCount", text: countStr(g) })]));
      right.appendChild(fs);

      const fsSel = fieldset("Objects");
      const joints = h("input", { type: "checkbox", id: "grpDefJoints" });
      const apply = mode => {
        const c = selectionContents(joints.checked);
        const any = GM.GROUP_KINDS.some(k => c[k].length) || c.points.length;
        if (!any) { grpShowError(err, "Nothing selected — select objects in plan / elevation first (Select tool)."); return; }
        grpShowError(err, "");
        for (const k of GM.GROUP_KINDS) {
          if (mode === "replace") g[k] = [...c[k]];
          else if (mode === "add") g[k] = [...g[k], ...c[k].filter(u => !g[k].includes(u))];
          else g[k] = g[k].filter(u => !c[k].includes(u));
        }
        if (mode === "replace") g.points = c.points.map(p => [...p]);
        else if (mode === "add") g.points = [...g.points, ...c.points.filter(p => !g.points.some(q => GM.samePoint(p, q)))];
        else g.points = g.points.filter(p => !c.points.some(q => GM.samePoint(p, q)));
        render();
      };
      const nSel = (S.selection || []).length;
      fsSel.append(
        h("p", { class: "muted grp-note", text: `${nSel} object${nSel === 1 ? "" : "s"} currently selected.` }),
        h("div", { class: "grp-btn-row" }, [
          Object.assign(grpBtn("Add Selection", "btn-small", () => apply("add")), { id: "grpDefSelAdd" }),
          Object.assign(grpBtn("Replace with Selection", "btn-small", () => apply("replace")), { id: "grpDefSelReplace" }),
          Object.assign(grpBtn("Remove Selection", "btn-small", () => apply("remove")), { id: "grpDefSelRemove" }),
          Object.assign(grpBtn("Clear", "btn-small", () => { for (const k of GM.GROUP_KINDS) g[k] = []; g.points = []; render(); }), { id: "grpDefClear" }),
        ]),
        h("label", { class: "dlg-chk" }, [joints, h("span", { text: "Also add / remove the joints of selected objects (end points, corners)" })]));
      right.appendChild(fsSel);

      // joints (points) — editable coordinates in display units
      const fsPt = fieldset(`Joints (${GU.label("length")})`);
      const ptTbl = h("div", { class: "grp-pts", id: "grpDefPoints" });
      ptTbl.appendChild(h("div", { class: "grp-pt-row head" }, ["X", "Y", "Z", ""].map(t => h("span", { text: t }))));
      g.points.forEach((p, i) => {
        const row = h("div", { class: "grp-pt-row" });
        for (let k = 0; k < 3; k++) {
          const shownV = GU.inputValue("length", p[k]);
          const inp = h("input", { type: "number", step: "any", value: shownV, "data-k": String(k) });
          inp.addEventListener("change", () => {
            const v = GU.parse("length", inp.value);
            if (isFinite(v)) p[k] = v; else inp.value = GU.inputValue("length", p[k]);
          });
          row.appendChild(inp);
        }
        row.appendChild(h("button", { class: "chip-x", title: "Remove joint", text: "✕", onclick: () => { g.points.splice(i, 1); render(); } }));
        ptTbl.appendChild(row);
      });
      if (!g.points.length) ptTbl.appendChild(h("p", { class: "muted grp-note", text: "No joints. Joints control support / joint-load activity in staged construction." }));
      const addPt = grpBtn("+ Joint", "btn-small", () => { g.points.push([0, 0, 0]); render(); }, "Add a joint by coordinates");
      addPt.id = "grpDefAddPoint";
      fsPt.append(ptTbl, addPt);
      right.appendChild(fsPt);

      const refs = GM.groupReferences(draft, cur);
      if (refs.length) right.appendChild(h("p", { class: "muted grp-note", html: `Used by ${refs.map(esc).join(", ")}.` }));
    }
    render();

    const okFn = () => {
      // validate (same rules as the backend)
      for (const n of Object.keys(draft.groups || {})) {
        if (!n.trim()) { grpShowError(err, "Group names cannot be empty."); return false; }
        for (const p of draft.groups[n].points)
          if (!p.every(v => isFinite(v))) { grpShowError(err, `Group ${n}: joint coordinates must be numbers.`); return false; }
      }
      const now = JSON.stringify([draft.groups || {}, draft.section_cuts, draft.staged_cases]);
      if (now !== initial) {
        const gs = Object.fromEntries(Object.entries(draft.groups || {}).map(([k, v]) => [k, GM.normGroup(v)]));
        if (Object.keys(gs).length) m.groups = gs; else delete m.groups;
        if (m.section_cuts || draft.section_cuts.length) m.section_cuts = draft.section_cuts;
        if (m.staged_cases || Object.keys(draft.staged_cases).length) m.staged_cases = draft.staged_cases;
      }
      shown.clear();
      for (const n of dShown) if (GM.groupsOf(m)[n]) shown.add(n);
      if (now !== initial) after("define"); else { redraw(); refreshExplorer(); }
      return true;
    };
    const fb = grpFootBar("Define Groups", [
      grpBtn("Cancel", "", () => d.close()),
      Object.assign(grpBtn("OK", "btn-primary", () => { if (okFn()) d.close(); }), { id: "grpDefOk" }),
    ]);
    const d = grpDialog("grpDefineDlg", { title: "Define Groups", iconId: null, wide: true, body, foot: fb.wrap });
    return d;
  }

  /* ================= Assign > Assign Objects to Group… ================= */
  function openAssign() {
    const m = model();
    if (!m) return null;
    const c0 = selectionContents(false);
    const nSel = (S.selection || []).length;
    if (!nSel) { toast("Assign to Group", "Select objects first (Select tool, V), then assign them to a group.", "error"); return null; }
    const names = GM.groupNames(m);
    const body = h("div", { class: "grp-asn" });
    body.appendChild(h("p", { class: "muted dlg-intro", html:
      `${nSel} object${nSel > 1 ? "s" : ""} selected (${countStr(c0)}).` }));
    const fs = fieldset("Group");
    const sel = h("select", { id: "grpAsnGroup" });
    sel.innerHTML = names.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join("") +
      `<option value="__new">+ New group…</option>`;
    const newName = h("input", { type: "text", id: "grpAsnNewName", value: GM.uniqueGroupName(m), spellcheck: "false" });
    const newWrap = h("label", { class: "grp-field" }, [h("span", { text: "New name" }), newName]);
    const syncNew = () => newWrap.classList.toggle("hidden", sel.value !== "__new");
    sel.addEventListener("change", syncNew);
    fs.append(h("label", { class: "grp-field" }, [h("span", { text: "Group" }), sel]), newWrap);
    body.appendChild(fs);
    const fsM = fieldset("Options");
    let mode = "add";
    for (const [v, t] of [["add", "Add to group"], ["replace", "Replace group contents"], ["remove", "Remove from group"]]) {
      const r = h("input", { type: "radio", name: "grpAsnMode", value: v, id: "grpAsnMode_" + v });
      r.checked = v === mode;
      r.addEventListener("change", () => { if (r.checked) mode = v; });
      fsM.appendChild(h("label", { class: "dlg-chk" }, [r, h("span", { text: t })]));
    }
    const joints = h("input", { type: "checkbox", id: "grpAsnJoints" });
    fsM.appendChild(h("label", { class: "dlg-chk" }, [joints, h("span", { text: "Include the joints of selected objects (end points, corners, springs are always included)" })]));
    body.appendChild(fsM);
    const err = grpErrorLine();
    body.appendChild(err);
    syncNew();

    const apply = () => {
      let name = sel.value;
      if (name === "__new") {
        name = newName.value.trim();
        if (!name) { grpShowError(err, "Enter a name for the new group."); return false; }
        if (GM.groupsOf(m)[name]) { grpShowError(err, `A group named ${name} already exists.`); return false; }
      }
      if (!name) { grpShowError(err, "Choose a group."); return false; }
      const c = selectionContents(joints.checked);
      if (!m.groups) m.groups = {};
      const g = GM.normGroup(m.groups[name] || { color: GM.nextGroupColor(m) });
      for (const k of GM.GROUP_KINDS) {
        if (mode === "replace") g[k] = [...c[k]];
        else if (mode === "add") g[k] = [...g[k], ...c[k].filter(u => !g[k].includes(u))];
        else g[k] = g[k].filter(u => !c[k].includes(u));
      }
      if (mode === "replace") g.points = c.points;
      else if (mode === "add") g.points = [...g.points, ...c.points.filter(p => !g.points.some(q => GM.samePoint(p, q)))];
      else g.points = g.points.filter(p => !c.points.some(q => GM.samePoint(p, q)));
      m.groups[name] = g;
      after("assign");
      toast("Assign to Group", `${name}: ${countStr(g)}`, "info", 3500);
      return true;
    };
    const fb = grpFootBar("Assign Objects to Group", [
      grpBtn("Cancel", "", () => d.close()),
      Object.assign(grpBtn("OK", "btn-primary", () => { if (apply()) d.close(); }), { id: "grpAsnOk" }),
    ]);
    const d = grpDialog("grpAssignDlg", { title: "Assign Objects to Group", iconId: null, narrow: true, body, foot: fb.wrap });
    return d;
  }

  /* ================= Select > Select by Group… / Display > Show Group… ================= */
  function pickGroupsDialog(id, title, intro, buttons, preset) {
    const m = model();
    if (!m) return null;
    const names = GM.groupNames(m);
    if (!names.length) { toast(title, "No groups defined — Define > Groups… first.", "error"); return null; }
    const chosen = new Set(preset || []);
    const body = h("div", { class: "grp-pick" });
    body.appendChild(h("p", { class: "muted dlg-intro", html: intro }));
    const list = h("div", { class: "grp-pick-list", id: id + "List" });
    for (const n of names) {
      const g = GM.groupsOf(m)[n];
      const cb = h("input", { type: "checkbox", value: n });
      cb.checked = chosen.has(n);
      cb.addEventListener("change", () => { cb.checked ? chosen.add(n) : chosen.delete(n); });
      list.appendChild(h("label", { class: "dlg-chk grp-pick-row" }, [cb,
        h("span", { class: "grp-swatch", style: `background:${g.color || "#888"}` }),
        h("b", { text: n }), h("span", { class: "muted", text: countStr(g) })]));
    }
    body.appendChild(list);
    const fb = grpFootBar(title, [
      ...buttons.map(([label, cls, fn, bid]) => Object.assign(grpBtn(label, cls, () => { if (fn([...chosen]) !== false) d.close(); }), { id: bid })),
      grpBtn("Cancel", "", () => d.close()),
    ]);
    const d = grpDialog(id, { title, iconId: null, narrow: true, body, foot: fb.wrap });
    return d;
  }
  function openSelectByGroup() {
    return pickGroupsDialog("grpSelectDlg", "Select by Group",
      "Select (or deselect) every object of the checked groups. Group joints select the point springs there.",
      [["Deselect", "", names => { const n = selectGroups(names, true); toast("Deselect by Group", `${n} object(s)`, "info", 2500); }, "grpSelDeselect"],
       ["Select", "btn-primary", names => {
         if (!names.length) return false;
         if (S.mode !== "model") sky.setMode("model");
         const n = selectGroups(names, false);
         toast("Select by Group", `${n} object(s) selected from ${names.join(", ")}`, "info", 3000);
       }, "grpSelSelect"]]);
  }
  function openShowGroup() {
    return pickGroupsDialog("grpShowDlg", "Show Group",
      "Colour the objects of the checked groups (group colour) in the plan, elevation and 3D views.",
      [["Clear All", "", () => { shown.clear(); redraw(); refreshExplorer(); }, "grpShowClear"],
       ["Show", "btn-primary", names => { shown.clear(); names.forEach(n => shown.add(n)); redraw(); refreshExplorer(); }, "grpShowOk"]],
      [...shown]);
  }
  function setShown(names) {
    shown.clear();
    for (const n of names || []) if (GM.groupsOf(model())[n]) shown.add(n);
    redraw(); refreshExplorer();
  }
  function toggleShown(n) { shown.has(n) ? shown.delete(n) : shown.add(n); redraw(); refreshExplorer(); }

  /* ================= Show Group highlight — plan / elevation (SVG) ================= */
  const shownItems = () => {
    const m = model();
    if (!m || !shown.size) return [];
    return [...shown].map(n => ({ name: n, g: GM.groupsOf(m)[n] })).filter(x => x.g);
  };
  function svgOverlay(editor, kind) {
    const items = shownItems();
    const host = editor.gElems;
    if (!items.length || !host) return;
    const NS = "http://www.w3.org/2000/svg";
    const layer = document.createElementNS(NS, "g");
    layer.setAttribute("class", "grp-hl-layer");
    layer.setAttribute("pointer-events", "none");
    for (const { name, g } of items) {
      const col = g.color || "#f5be3c";
      for (const k of GM.GROUP_KINDS)
        for (const uid of (g[k] || [])) {
          const sel = `[data-ref="${GM.KIND_REF[k]}:${CSS.escape(uid)}"]`;
          for (const node of host.querySelectorAll(sel)) {
            const c = node.cloneNode(false);
            c.removeAttribute("data-ref");
            c.setAttribute("data-grp", name);
            c.setAttribute("stroke", col);
            const fill = c.getAttribute("fill");
            if (fill && fill !== "none" && !fill.startsWith("url(")) { c.setAttribute("fill", col); c.setAttribute("fill-opacity", "0.32"); }
            else c.setAttribute("fill", "none");
            if (c.getAttribute("vector-effect") === "non-scaling-stroke")
              c.setAttribute("stroke-width", String(Math.max(+c.getAttribute("stroke-width") || 1, 1.5) + 2));
            else if (c.getAttribute("stroke-width")) c.setAttribute("stroke-width", String(+c.getAttribute("stroke-width") * 1.35));
            c.setAttribute("opacity", "0.95");
            layer.appendChild(c);
          }
        }
      // joints
      const r = 5 / (editor.scale || 40);
      for (const p of (g.points || [])) {
        let xy = null;
        if (kind === "plan") {
          const st = (model().stories || []).find(s => s.name === editor.opts.getStory());
          if (st && Math.abs(st.elevation - p[2]) < 1e-6) xy = [p[0], p[1]];
          else if (!st && Math.abs(p[2]) < 1e-6) xy = [p[0], p[1]];
        } else if (editor.inPlane && editor.inPlane(p)) xy = [editor.sOf(p), p[2]];
        if (!xy) continue;
        const c = document.createElementNS(NS, "circle");
        c.setAttribute("cx", xy[0]); c.setAttribute("cy", xy[1]); c.setAttribute("r", r);
        c.setAttribute("fill", col); c.setAttribute("stroke", "#000"); c.setAttribute("stroke-width", "1");
        c.setAttribute("vector-effect", "non-scaling-stroke"); c.setAttribute("data-grp", name);
        layer.appendChild(c);
      }
    }
    host.appendChild(layer);
  }
  if (sky.planEditor) sky.planEditor.grpOverlay = ed => svgOverlay(ed, "plan");
  if (sky.elevEditor) sky.elevEditor.grpOverlay = ed => svgOverlay(ed, "elev");

  /* ================= Show Group highlight — 3D (canvas) ================= */
  function draw3d(ctx, P, viewer) {
    const items = shownItems();
    if (!items.length || !viewer.model) return;
    const m = viewer.model;
    const byUid = (arr) => new Map((arr || []).map(o => [o.uid, o]));
    const mem = byUid(m.members), shl = byUid(m.shells), lnk = byUid(m.links);
    const proj = p => { const pc = P.toCam(p); return pc[2] < P.near ? null : P.proj(pc); };
    ctx.save();
    for (const { g } of items) {
      const col = g.color || "#f5be3c";
      ctx.fillStyle = col; ctx.strokeStyle = col;
      // shells — translucent fill
      ctx.globalAlpha = 0.32;
      for (const uid of (g.shells || [])) {
        const s = shl.get(uid);
        if (!s) continue;
        const pts = s.corners.map(proj);
        if (pts.some(x => !x)) continue;
        ctx.beginPath();
        pts.forEach((q, i) => i ? ctx.lineTo(q.x, q.y) : ctx.moveTo(q.x, q.y));
        ctx.closePath(); ctx.fill();
      }
      ctx.globalAlpha = 0.95;
      ctx.lineWidth = 3.2; ctx.lineCap = "round";
      ctx.beginPath();
      for (const [lst, mp] of [[g.members, mem], [g.links, lnk]])
        for (const uid of (lst || [])) {
          const o = mp.get(uid);
          if (!o) continue;
          const sg = viewer._projSeg(P, o.pi, o.pj);
          if (sg) { ctx.moveTo(sg.a.x, sg.a.y); ctx.lineTo(sg.b.x, sg.b.y); }
        }
      for (const uid of (g.shells || [])) {
        const s = shl.get(uid);
        if (!s) continue;
        s.corners.forEach((c, i) => {
          const sg = viewer._projSeg(P, c, s.corners[(i + 1) % s.corners.length]);
          if (sg) { ctx.moveTo(sg.a.x, sg.a.y); ctx.lineTo(sg.b.x, sg.b.y); }
        });
      }
      ctx.stroke();
      for (const p of (g.points || [])) {
        const q = proj(p);
        if (!q) continue;
        ctx.beginPath(); ctx.arc(q.x, q.y, 4.5, 0, Math.PI * 2); ctx.fill();
      }
    }
    ctx.restore();
  }
  if (sky.viewer) sky.viewer.grpOverlay = draw3d;

  /* ---- small legend over the plan / 3D when groups are shown ---- */
  let legend = null;
  function syncLegend() {
    const items = shownItems();
    if (!legend) {
      legend = h("div", { class: "grp-legend hidden", id: "grpLegend" });
      document.body.appendChild(legend);
    }
    legend.classList.toggle("hidden", !items.length);
    legend.innerHTML = items.length ? `<span class="muted">Show Group</span>` + items.map(({ name, g }) =>
      `<span class="grp-legend-item"><i class="grp-swatch" style="background:${esc(g.color || "#888")}"></i>${esc(name)}</span>`).join("") +
      `<button class="chip-x" title="Hide groups" id="grpLegendClear">✕</button>` : "";
    const x = legend.querySelector("#grpLegendClear");
    if (x) x.addEventListener("click", () => setShown([]));
  }

  /* ================= Model Explorer "Groups" node ================= */
  let exGroup = null, exList = null, exCount = null;
  function mountExplorer() {
    const body = document.querySelector("#etabsExplorer .ex-body");
    if (!body || exGroup) return;
    exGroup = h("div", { class: "ex-group ex-groups open", id: "exGroups" });
    const head = h("button", { class: "ex-group-head" }, [
      h("span", { class: "ex-caret", html: "&#9656;" }),
      h("span", { class: "ex-ico", html: `<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="1.5" y="1.5" width="5" height="5" rx="1"/><rect x="9.5" y="1.5" width="5" height="5" rx="1"/><rect x="5.5" y="9.5" width="5" height="5" rx="1"/></svg>` }),
      h("span", { text: "Groups" }),
    ]);
    exCount = h("span", { class: "ex-count", id: "exGroupsCount" });
    head.appendChild(exCount);
    head.addEventListener("click", () => exGroup.classList.toggle("open"));
    exList = h("div", { class: "ex-leaves", id: "exGroupsList" });
    exGroup.append(head, exList);
    const cases = body.querySelector(".ex-cases");
    if (cases && cases.nextSibling) body.insertBefore(exGroup, cases.nextSibling);
    else body.appendChild(exGroup);
    refreshExplorer();
  }
  function refreshExplorer() {
    if (!exList) return;
    const m = model();
    const names = GM.groupNames(m);
    exCount.textContent = names.length ? String(names.length) : "";
    exList.textContent = "";
    for (const n of names) {
      const g = GM.groupsOf(m)[n];
      const leaf = h("button", { class: "ex-leaf ex-grp" + (shown.has(n) ? " is-shown" : ""), "data-group": n,
        title: `${n} · ${countStr(g)}\nClick: Show Group on/off · Double-click: Define Groups…` }, [
        h("span", { class: "grp-swatch", style: `background:${g.color || "#888"}` }),
        h("span", { class: "ex-leaf-lbl", text: n }),
        h("span", { class: "ex-grp-n muted", text: String(GM.GROUP_KINDS.reduce((a, k) => a + (g[k] || []).length, 0) + (g.points || []).length) }),
      ]);
      leaf.addEventListener("click", () => toggleShown(n));
      leaf.addEventListener("dblclick", () => openGroups(n));
      exList.appendChild(leaf);
    }
    const def = h("button", { class: "ex-leaf ex-grp-def", title: "Define > Groups…" }, [
      h("span", { class: "ex-leaf-lbl", text: names.length ? "Define Groups…" : "No groups — Define Groups…" })]);
    def.addEventListener("click", () => openGroups());
    exList.appendChild(def);
  }
  mountExplorer();
  document.addEventListener("sky:model-changed", () => refreshExplorer());
  document.addEventListener("sky:results-changed", () => { if (!exGroup) mountExplorer(); });

  /* ================= Section cut — "defined by group" (loads.js hook) ================= */
  function cutCard(card, m, cut, mutated) {
    card.dataset.cut = cut.name;
    const names = GM.groupNames(m);
    const row = h("div", { class: "cut-ranges grp-cut-row" });
    row.appendChild(h("span", { class: "mass-tag", text: "defined by:", title: "ETABS: section cut defined by a plane (all crossing objects) or by a group" }));
    const rid = "grpCut_" + Math.random().toString(36).slice(2, 8);
    const rPlane = h("input", { type: "radio", name: rid, value: "plane", class: "grp-cut-plane" });
    const rGroup = h("input", { type: "radio", name: rid, value: "group", class: "grp-cut-group" });
    const sel = h("select", { class: "grp-cut-sel" });
    const keep = cut.group && !names.includes(cut.group) ? [cut.group] : [];
    sel.innerHTML = [...names, ...keep].map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
    rPlane.checked = !cut.group; rGroup.checked = !!cut.group;
    if (cut.group) sel.value = cut.group;
    sel.disabled = !cut.group;
    if (!names.length && !cut.group) { rGroup.disabled = true; sel.innerHTML = `<option value="">(no groups)</option>`; }
    rPlane.addEventListener("change", () => { if (rPlane.checked) { delete cut.group; mutated(); } });
    rGroup.addEventListener("change", () => { if (rGroup.checked && sel.value) { cut.group = sel.value; mutated(); } });
    sel.addEventListener("change", () => { if (rGroup.checked && sel.value) { cut.group = sel.value; mutated(); } });
    row.append(
      h("label", { class: "cut-range-field" }, [rPlane, h("span", { text: "Plane — all crossing objects" })]),
      h("label", { class: "cut-range-field" }, [rGroup, h("span", { text: "Group" }), sel]));
    if (cut.group && !names.includes(cut.group))
      row.appendChild(h("span", { class: "cut-warn", text: `⚠ group ${cut.group} does not exist` }));
    const note = card.querySelector(".staged-note");
    card.insertBefore(row, note || null);
    if (note && cut.group) note.insertAdjacentHTML("beforeend", ` Only objects of group <b>${esc(cut.group)}</b> are integrated.`);
  }

  /* ================= styles ================= */
  if (!document.getElementById("grpStyles")) {
    const s = document.createElement("style");
    s.id = "grpStyles";
    s.textContent = `
      .grp-cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.1fr); gap: 14px; }
      @media (max-width: 760px) { .grp-cols { grid-template-columns: 1fr; } }
      .grp-list-wrap { max-height: 46vh; }
      .grp-table tbody tr { cursor: pointer; user-select: none; }
      .grp-table tbody tr.is-sel td { background: var(--accent-ghost); }
      .grp-table td b { margin-left: 6px; }
      .grp-swatch { display: inline-block; width: 11px; height: 11px; border-radius: 3px; border: 1px solid rgba(0,0,0,.35); vertical-align: -1px; flex: none; }
      .grp-btn-col { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
      .grp-btn-row { display: flex; gap: 6px; flex-wrap: wrap; margin: 4px 0 8px; }
      .grp-field { display: grid; grid-template-columns: 90px minmax(0, 1fr); gap: 8px; align-items: center; font-size: 12px; margin-bottom: 6px; }
      .grp-field input[type=color] { width: 48px; height: 24px; padding: 0 2px; }
      .grp-note { font-size: 11px; margin: 2px 0 6px; }
      .grp-pts { display: flex; flex-direction: column; gap: 4px; margin-bottom: 6px; max-height: 24vh; overflow: auto; }
      .grp-pt-row { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)) 24px; gap: 4px; align-items: center; }
      .grp-pt-row.head span { font-size: 10.5px; color: var(--text-3); }
      .grp-pt-row input { min-width: 0; padding: 3px 5px; }
      .grp-pick-list { display: flex; flex-direction: column; gap: 6px; max-height: 50vh; overflow: auto; }
      .grp-pick-row { gap: 8px; }
      .grp-pick-row .muted { font-size: 11px; margin-left: auto; }
      .ex-grp { display: flex; align-items: center; gap: 7px; }
      .ex-grp .ex-grp-n { margin-left: auto; font-size: 10.5px; }
      .ex-grp.is-shown { color: var(--text-1); background: var(--accent-ghost); }
      .ex-grp-def .ex-leaf-lbl { color: var(--text-3); font-style: italic; }
      .grp-legend { position: fixed; right: 16px; bottom: 44px; z-index: 30; display: flex; gap: 10px; align-items: center;
        flex-wrap: wrap; max-width: min(520px, calc(100vw - 32px)); padding: 5px 10px; font-size: 11.5px;
        background: var(--surface-2, #161b22); border: 1px solid var(--border, #232b36); border-radius: 8px; box-shadow: 0 4px 14px rgba(0,0,0,.35); }
      .grp-legend.hidden { display: none; }
      .grp-legend-item { display: inline-flex; gap: 5px; align-items: center; }
      .grp-cut-row { flex-wrap: wrap; }
      .grp-cut-row select { width: auto; min-width: 110px; }
    `;
    document.head.appendChild(s);
  }

  sky.openGroups = openGroups;
  sky.openAssignGroup = openAssign;
  sky.openSelectByGroup = openSelectByGroup;
  sky.openShowGroup = openShowGroup;
  sky.grpCutCard = cutCard;
  sky.groups = { openGroups, openAssign, openSelectByGroup, openShowGroup, selectGroups, groupRefs,
    selectionContents, setShown, toggleShown, shown: () => [...shown], refreshExplorer, model: GM };
  return sky.groups;
}
