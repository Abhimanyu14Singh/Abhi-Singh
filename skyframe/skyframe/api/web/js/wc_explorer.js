/* SkyFrame Wave C — Model Explorer context menu (ETABS: right-click a node
   to edit, delete, rename, select or show).

   Definition categories (Materials, Frame / Shell Sections, Load Patterns,
   Combinations, Stories, Grid Systems) get collapsible child nodes, one per
   item (▸ toggle left of the category). A right-click (or Shift+F10 / the
   context-menu key) on any explorer node opens a menu for its node type:

     category     Define <X>… · Add New… · Expand / Collapse ·
                  Select All / Show Only (object categories)
     definition   Modify/Show… · Rename… · Delete · Select Objects Using It ·
                  Show Objects Using It
     load case    Modify/Show… · Rename… · Delete · Run / Do Not Run ·
                  Show Deformed Shape (after a run)
     combination  Modify/Show… · Rename… · Delete · Show Deformed Shape
     story        Modify/Show… · Rename… · Delete · Select Objects on Story ·
                  Show Plan View
     grid system  Modify/Show… · Set as Primary · Rename… · Delete ·
                  Show Plan View / Elevation (general lines)
     group        Modify/Show… · Rename… · Delete · Select · Show

   Every entry reuses an existing handler: Modify/Show runs the menu act
   (the standard dialog) and opens the item's Modify/Show sub-dialog;
   rename / delete use the js/modeledit.js / js/groups_model.js helpers (so
   references follow and used items are refused, naming their users);
   select uses __sky.handleSelect, show uses View › Show Selection Only.
   Each change is one undo step (dkCommit). CONTRACT "Wave C interface …"
   § Model Explorer. */

import { el as xEl, esc as xEsc, dkForm as xForm, dkGroup as xGroup, dkDialog as xDialog, dkDraft as xDraft,
  dkCommit as xCommit, dkConfirm as xConfirm } from "./dlgkit.js";
import * as XME from "./modeledit.js";
import { renameGroup as xRenameGroup, deleteGroup as xDeleteGroup } from "./groups_model.js";

const CATS = {
  "Materials": { kind: "material", act: "def-materials", dlg: "dsMaterials", items: m => Object.keys(m.materials || {}) },
  "Frame Sections": { kind: "frame_section", act: "def-frame", dlg: "dsFrameSections", items: m => Object.keys(m.sections || {}) },
  "Shell Sections": { kind: "shell_section", act: "def-shell", dlg: "dsShellSections", items: m => Object.keys(m.shell_sections || {}) },
  "Load Patterns": { kind: "pattern", act: "def-patterns", dlg: "dsLoadPatterns", items: m => Object.keys(m.patterns || {}) },
  "Combinations": { kind: "combo", act: "def-combos", dlg: "dcCombos", items: m => Object.keys(m.combos || {}) },
  "Stories": { kind: "story", act: "def-stories", items: m => (m.stories || []).map(s => s.name) },
  "Grid Systems": { kind: "grid", act: "def-grid", items: m => XME.gridSystems(m).map(g => g.name) },
};
const OBJ_CATS = { "Frame Members": "member", "Shells (walls / slabs)": "shell", "Links": "link", "Supports & Springs": "spring" };
const KIND_LABEL = { material: "Material", frame_section: "Frame Section", shell_section: "Slab / Wall Section", pattern: "Load Pattern",
  combo: "Load Combination", story: "Story", grid: "Grid System", case: "Load Case", group: "Group" };
const CASE_RENAME = { static: XME.renameCase, rs: XME.renameRsCase, th: XME.renameThCase, buckling: XME.renameBucklingCase,
  pushover: XME.renamePushoverCase, staged: XME.renameStagedCase };
const CASE_DELETE = { static: XME.deleteCase, rs: XME.deleteRsCase, th: XME.deleteThCase, buckling: XME.deleteBucklingCase,
  pushover: XME.deletePushoverCase, staged: XME.deleteStagedCase };

export function initWcExplorer(sky) {
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 4500) => sky.toast && sky.toast(t, m, k, ms);
  const ex = document.getElementById("etabsExplorer");
  if (!ex) return;
  const open = new Set();                                  // expanded categories
  const clickAct = act => (sky.etabs && sky.etabs.clickItem ? sky.etabs.clickItem(act) : false);

  /* ---------------- users of a definition (refs for selection) */
  function users(kind, name) {
    const m = S.model, out = [];
    if (kind === "material") {
      const fs = new Set(Object.values(m.sections || {}).filter(s => s.material === name).map(s => s.name));
      const ss = new Set(Object.values(m.shell_sections || {}).filter(s => s.material === name).map(s => s.name));
      for (const x of m.members || []) if (fs.has(x.section)) out.push({ type: "member", uid: x.uid });
      for (const x of m.shells || []) if (ss.has(x.section)) out.push({ type: "shell", uid: x.uid });
    } else if (kind === "frame_section") {
      for (const x of m.members || []) if (x.section === name) out.push({ type: "member", uid: x.uid });
    } else if (kind === "shell_section") {
      for (const x of m.shells || []) if (x.section === name) out.push({ type: "shell", uid: x.uid });
    } else if (kind === "pattern") {
      const p = (m.patterns || {})[name] || {};
      const mem = new Set([...(p.member_loads || []), ...(p.thermal_loads || []), ...(p.frame_thermal_loads || [])].map(l => l.member_uid).filter(Boolean));
      const sh = new Set([...(p.area_loads || []), ...(p.shell_thermal_loads || [])].map(l => l.region_uid).filter(Boolean));
      for (const x of m.members || []) if (mem.has(x.uid)) out.push({ type: "member", uid: x.uid });
      for (const x of m.shells || []) if (sh.has(x.uid)) out.push({ type: "shell", uid: x.uid });
    } else if (kind === "story") {
      for (const x of m.members || []) if (x.story === name) out.push({ type: "member", uid: x.uid });
      for (const x of m.shells || []) if (x.story === name) out.push({ type: "shell", uid: x.uid });
    } else if (kind === "group" && sky.groups) return sky.groups.groupRefs(name);
    return out;
  }
  const allOf = type => {
    const m = S.model;
    if (type === "member") return (m.members || []).map(x => ({ type, uid: x.uid }));
    if (type === "shell") return (m.shells || []).map(x => ({ type, uid: x.uid }));
    if (type === "link") return (m.links || []).map(x => ({ type, uid: x.uid }));
    return (m.spring_supports || []).map(x => ({ type: "spring", uid: XME.springKey(x.point) }));
  };
  function select(refs, label) {
    if (S.mode !== "model") sky.setMode("model");
    sky.handleSelect(refs, false);
    if (sky.etabs && sky.etabs.refresh) sky.etabs.refresh();
    toast("Select", `${refs.length} object(s) selected${label ? " — " + label : ""}.`, "info", 2500);
    return refs.length;
  }
  function showOnly(refs, label) {
    select(refs, label);
    const it = sky.menu && sky.menu.item && sky.menu.item("vx-selonly");
    if (it && refs.length && !(it.check && it.check())) clickAct("vx-selonly");
  }

  /* ---------------- edit (Modify/Show) — the menu act + the item's sub-dialog */
  function modify(kind, name) {
    const cat = Object.values(CATS).find(c => c.kind === kind);
    if (kind === "grid") {
      const g = XME.gridSystems(S.model).find(x => x.name === name);
      if (g && g.kind === "general" && sky.wb && sky.wb.grid) { const D = xDraft(sky); const s = D.grid_systems.find(x => x.name === name); return sky.wb.grid.openData(D, s, () => xCommit(sky, D)); }
      return clickAct("def-grid");
    }
    if (kind === "case") return caseLeafClick(name);
    if (kind === "group") return clickAct("def-groups");
    if (!cat) return false;
    clickAct(cat.act);
    if (cat.dlg) requestAnimationFrame(() => {
      const tr = document.querySelector(`#${cat.dlg} tr[data-key="${CSS.escape(name)}"]`);
      if (tr) { tr.click(); tr.dispatchEvent(new MouseEvent("dblclick", { bubbles: true })); }
    });
    return true;
  }
  const caseLeafClick = name => { const leaf = ex.querySelector(`.ex-case[data-case="${CSS.escape(name)}"]`); if (leaf) leaf.click(); return !!leaf; };

  /* ---------------- rename / delete (one undo step each) */
  function askName(title, cur, taken) {
    return new Promise(resolve => {
      const V = { name: cur };
      const F = xForm();
      let done = false;
      xDialog({ id: "wcExRename", title, iconId: "menu-edit", size: "sm", form: F,
        body: [xGroup("Name", [F.text(V, "name", { label: "New name", id: "wcExRenameInp",
          validate: v => (!String(v || "").trim() ? "Enter a name." : (v !== cur && taken(String(v).trim())) ? `“${v}” is already used — pick another name.` : "") })])],
        onOk: () => { done = true; resolve(String(V.name).trim()); }, onClose: () => { if (!done) resolve(null); } });
    });
  }
  const caseKind = name => (XME.allAnalysisCases(S.model).find(c => c.name === name) || {}).kind;
  const exists = (kind, n) => {
    const m = S.model;
    if (kind === "material") return !!(m.materials || {})[n];
    if (kind === "frame_section") return !!(m.sections || {})[n];
    if (kind === "shell_section") return !!(m.shell_sections || {})[n];
    if (kind === "pattern") return !!(m.patterns || {})[n];
    if (kind === "combo") return !!(m.combos || {})[n] || XME.allAnalysisCases(m).some(c => c.name === n);
    if (kind === "case") return XME.allAnalysisCases(m).some(c => c.name === n) || !!(m.combos || {})[n];
    if (kind === "story") return (m.stories || []).some(s => s.name === n);
    if (kind === "grid") return XME.gridSystems(m).some(g => g.name === n);
    if (kind === "group") return !!(m.groups || {})[n];
    return false;
  };
  /** Rename in a draft; returns an error string or "" (commits on success). */
  function renameIn(kind, oldName, newName) {
    if (!newName || newName === oldName) return "";
    const D = xDraft(sky);
    let ok = false;
    if (kind === "material") ok = XME.renameMaterial(D, oldName, newName);
    else if (kind === "frame_section") ok = XME.renameFrameSection(D, oldName, newName);
    else if (kind === "shell_section") ok = XME.renameShellSection(D, oldName, newName);
    else if (kind === "pattern") ok = XME.renamePattern(D, oldName, newName);
    else if (kind === "combo") ok = XME.renameCombo(D, oldName, newName);
    else if (kind === "story") ok = XME.renameStory(D, oldName, newName);
    else if (kind === "group") ok = xRenameGroup(D, oldName, newName) !== false;
    else if (kind === "case") { const f = CASE_RENAME[caseKind(oldName)]; if (!f) return "This case type is renamed in its own editor (Modify/Show…)."; ok = f(D, oldName, newName); }
    else if (kind === "grid") {
      const sys = XME.gridSystems(D).find(g => g.name === oldName);
      if (sys && !XME.gridSystems(D).some(g => g.name === newName)) { sys.name = newName; if (D.grid && D.grid.name === oldName) D.grid = XME.gridSystems(D)[0]; ok = true; }
    }
    if (!ok) return `Could not rename “${oldName}” to “${newName}”.`;
    xCommit(sky, D);
    refresh();
    return "";
  }
  async function rename(kind, name) {
    const nn = await askName(`Rename ${KIND_LABEL[kind] || kind}`, name, n => exists(kind, n));
    if (nn == null) return false;
    const err = renameIn(kind, name, nn);
    if (err) { toast("Rename", err, "warn", 6000); return false; }
    if (nn !== name) toast("Rename", `${KIND_LABEL[kind]} “${name}” → “${nn}”.`, "info", 2500);
    return true;
  }
  /** Delete in a draft; returns an error string or "" (commits on success). */
  function deleteIn(kind, name) {
    const D = xDraft(sky);
    const uses = n => `“${name}” is used by ${n} — reassign first.`;
    if (kind === "material") {
      if (XME.materialInUse(D, name)) return uses(`${users("material", name).length} object(s) / sections`);
      delete D.materials[name];
    } else if (kind === "frame_section") {
      if (XME.sectionInUse(D, name)) return uses(`${users("frame_section", name).length} frame(s)`);
      delete D.sections[name];
    } else if (kind === "shell_section") {
      if (XME.shellSectionInUse(D, name)) return uses(`${users("shell_section", name).length} shell(s)`);
      delete D.shell_sections[name];
    } else if (kind === "pattern") {
      const r = XME.patternRefs(D, name);
      if (r.length) return uses(r.slice(0, 4).join(", ") + (r.length > 4 ? "…" : ""));
      XME.deletePattern(D, name);
    } else if (kind === "combo") {
      if (!XME.deleteCombo(D, name)) return `“${name}” is used by another combination — remove it there first.`;
    } else if (kind === "case") {
      const f = CASE_DELETE[caseKind(name)];
      if (!f) return "This case type is deleted in its own editor (Modify/Show…).";
      if (!f(D, name)) { const r = XME.caseRefs(D, name); return uses(r.length ? r.join(", ") : "another case or combination"); }
    } else if (kind === "story") {
      if (!XME.deleteStory(D, name)) return "A model keeps at least one story.";
    } else if (kind === "grid") {
      const list = XME.gridSystems(D), i = list.findIndex(g => g.name === name);
      if (list.length <= 1) return "A model keeps at least one grid system.";
      if (!Array.isArray(D.grid_systems) || !D.grid_systems.length) D.grid_systems = list;
      D.grid_systems.splice(i, 1);
      D.grid = D.grid_systems[0];
    } else if (kind === "group") {
      const r = xDeleteGroup(D, name);
      if (r === false || (r && r.ok === false)) return (r && r.error) || `“${name}” is used — remove its references first.`;
    }
    xCommit(sky, D);
    refresh();
    return "";
  }
  async function del(kind, name) {
    if (kind === "story") {
      const n = users("story", name).length;
      if (!(await xConfirm({ id: "wcExDelConfirm", title: "Delete Story", html: `Delete story <b>${xEsc(name)}</b>${n ? ` and its <b>${n}</b> object(s)` : ""}? Undo restores it.`, okLabel: "Delete" }))) return false;
    }
    const err = deleteIn(kind, name);
    if (err) { toast("Delete", err, "warn", 6500); return false; }
    toast("Delete", `${KIND_LABEL[kind]} “${name}” deleted.`, "info", 2500);
    return true;
  }
  function setPrimary(name) {
    const D = xDraft(sky);
    if (!Array.isArray(D.grid_systems) || !D.grid_systems.length) D.grid_systems = D.grid ? [D.grid] : [];
    const i = D.grid_systems.findIndex(g => g.name === name);
    if (i <= 0) return false;
    D.grid_systems.unshift(D.grid_systems.splice(i, 1)[0]);
    D.grid = D.grid_systems[0];
    xCommit(sky, D);
    if (sky.rebuildElevSelect) sky.rebuildElevSelect();
    refresh();
    return true;
  }
  function toggleRun(name) {
    const D = xDraft(sky);
    const set = new Set(D.cases_not_run || []);
    if (set.has(name)) set.delete(name); else set.add(name);
    if (set.size) D.cases_not_run = [...set]; else delete D.cases_not_run;
    xCommit(sky, D);
  }
  function showCase(name) {
    if (!S.results) { toast("Show", "Run the analysis first (F5).", "warn"); return false; }
    const sel = document.getElementById("caseSelect");
    if (sel && [...sel.options].some(o => o.value === name)) { sel.value = name; sel.dispatchEvent(new Event("change")); }
    clickAct("dis-deformed");
    return true;
  }
  function showPlan(story) { sky.setMode("model"); sky.setView("plan"); if (story) sky.setStory(story); }

  /* ---------------- menu items per node */
  function itemsFor(node) {
    const { type, kind, name, label } = node;
    if (type === "category") {
      const cat = CATS[label], obj = OBJ_CATS[label];
      const out = [{ head: label }, { label: "Open…", fn: () => sky.etabs.explorerClick(label) }];
      if (cat) {
        out.push({ label: `Add New ${KIND_LABEL[cat.kind]}…`, fn: () => clickAct(cat.kind === "grid" ? "def-grid" : cat.act) });
        out.push({ label: open.has(label) ? "Collapse" : "Expand", fn: () => { if (open.has(label)) open.delete(label); else open.add(label); refresh(); } });
      }
      if (obj) { out.push({ sep: true }, { label: `Select All ${label}`, fn: () => select(allOf(obj), label) }, { label: `Show Only ${label}`, fn: () => showOnly(allOf(obj), label) }); }
      return out;
    }
    const head = `${KIND_LABEL[kind]} ${name}`;
    const out = [{ head }, { label: "Modify/Show…", fn: () => modify(kind, name) }];
    if (kind === "grid") out.push({ label: "Set as Primary", fn: () => setPrimary(name), disabled: XME.gridSystems(S.model)[0]?.name === name });
    out.push({ label: "Rename…", fn: () => rename(kind, name) }, { label: "Delete", fn: () => del(kind, name), danger: true });
    if (["material", "frame_section", "shell_section", "pattern", "group"].includes(kind)) {
      const n = users(kind, name).length;
      out.push({ sep: true }, { label: kind === "pattern" ? `Select Loaded Objects (${n})` : `Select Objects Using It (${n})`, fn: () => select(users(kind, name), `${KIND_LABEL[kind]} ${name}`), disabled: !n },
        { label: kind === "pattern" ? "Show Loaded Objects" : "Show Objects Using It", fn: () => showOnly(users(kind, name), `${KIND_LABEL[kind]} ${name}`), disabled: !n });
    }
    if (kind === "story") out.push({ sep: true }, { label: `Select Objects on Story (${users("story", name).length})`, fn: () => select(users("story", name), `story ${name}`) },
      { label: "Show Plan View", fn: () => showPlan(name) });
    if (kind === "grid") {
      const g = XME.gridSystems(S.model).find(x => x.name === name);
      out.push({ sep: true }, { label: "Show Plan View", fn: () => showPlan(S.story) });
      if (g && g.kind === "general" && (g.lines || []).length && sky.wc.grid) out.push({ label: `Show Elevation ${g.lines[0].label}`, fn: () => sky.wc.grid.showElevation(name, g.lines[0].label) });
    }
    if (kind === "case") out.push({ sep: true }, { label: XME.caseNotRun(S.model, name) ? "Set to Run" : "Set to Do Not Run", fn: () => toggleRun(name) },
      { label: "Show Deformed Shape", fn: () => showCase(name), disabled: !S.results });
    if (kind === "combo") out.push({ sep: true }, { label: "Show Deformed Shape", fn: () => showCase(name), disabled: !S.results });
    return out;
  }

  /* ---------------- popup */
  let menu = null;
  const closeMenu = () => { if (menu) { menu.remove(); menu = null; } };
  function openMenu(x, y, node) {
    closeMenu();
    menu = xEl("div", { class: "wf-ctx wc-ex-ctx", role: "menu", id: "wcExCtx", "aria-label": "Model Explorer context menu" });
    for (const it of itemsFor(node)) {
      if (it.head) { menu.appendChild(xEl("div", { class: "wf-ctx-head", text: it.head })); continue; }
      if (it.sep) { menu.appendChild(xEl("div", { class: "wf-ctx-sep", role: "separator" })); continue; }
      const b = xEl("button", { type: "button", class: "wf-ctx-item" + (it.danger ? " is-danger" : ""), role: "menuitem", "data-wc-ex": it.label }, [xEl("span", { text: it.label })]);
      if (it.disabled) { b.disabled = true; b.setAttribute("aria-disabled", "true"); }
      b.addEventListener("click", () => { closeMenu(); try { const r = it.fn(); if (r && r.catch) r.catch(e => toast("Model Explorer", e.message, "error")); } catch (e) { toast("Model Explorer", e.message, "error"); } });
      menu.appendChild(b);
    }
    document.body.appendChild(menu);
    const r = menu.getBoundingClientRect();
    menu.style.left = Math.max(4, Math.min(x, window.innerWidth - r.width - 6)) + "px";
    menu.style.top = Math.max(4, Math.min(y, window.innerHeight - r.height - 6)) + "px";
    const first = menu.querySelector(".wf-ctx-item:not([disabled])");
    if (first) first.focus({ preventScroll: true });
  }
  document.addEventListener("mousedown", e => { if (menu && !menu.contains(e.target)) closeMenu(); }, true);
  document.addEventListener("keydown", e => {
    if (!menu) return;
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeMenu(); return; }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const its = [...menu.querySelectorAll(".wf-ctx-item:not([disabled])")], i = its.indexOf(document.activeElement);
      const j = (i + (e.key === "ArrowDown" ? 1 : -1) + its.length) % its.length;
      if (its[j]) its[j].focus();
    }
  }, true);
  window.addEventListener("resize", closeMenu);

  /** Node descriptor of an explorer element (or null). */
  function nodeOf(t) {
    const it = t.closest(".ex-item");
    if (it) return { type: "item", kind: it.dataset.kind, name: it.dataset.name };
    const cs = t.closest(".ex-case");
    if (cs) { const n = cs.dataset.case; return { type: "item", kind: (S.model.combos || {})[n] ? "combo" : "case", name: n }; }
    const gr = t.closest(".ex-grp");
    if (gr) return { type: "item", kind: "group", name: gr.dataset.group };
    const leaf = t.closest(".ex-leaf");
    if (leaf) return { type: "category", label: (leaf.querySelector(".ex-leaf-lbl") || leaf).textContent.trim() };
    return null;
  }
  ex.addEventListener("contextmenu", e => {
    const node = nodeOf(e.target);
    if (!node) return;
    e.preventDefault(); e.stopPropagation();
    openMenu(e.clientX + 2, e.clientY + 2, node);
  });
  ex.addEventListener("keydown", e => {
    if (!((e.key === "F10" && e.shiftKey) || e.key === "ContextMenu")) return;
    const node = nodeOf(e.target);
    if (!node) return;
    e.preventDefault();
    const r = e.target.getBoundingClientRect();
    openMenu(r.left + 24, r.bottom, node);
  });

  /* ---------------- child nodes under the definition categories */
  function refresh() {
    const m = S.model;
    if (!m) return;
    ex.querySelectorAll(".wc-ex-sub, .wc-ex-tog").forEach(n => n.remove());
    for (const leaf of ex.querySelectorAll(".ex-body .ex-leaf:not(.ex-case):not(.ex-grp):not(.ex-item)")) {
      const label = (leaf.querySelector(".ex-leaf-lbl") || leaf).textContent.trim();
      const cat = CATS[label];
      if (!cat) continue;
      const names = cat.items(m);
      const isOpen = open.has(label);
      const tog = xEl("span", { class: "wc-ex-tog" + (isOpen ? " is-open" : ""), title: `${isOpen ? "Collapse" : "Expand"} ${label} (${names.length})`,
        role: "button", "aria-label": `${isOpen ? "Collapse" : "Expand"} ${label}`, "aria-expanded": isOpen ? "true" : "false", "data-wc-tog": label, html: "&#9656;" });
      leaf.prepend(tog);                       // clicks are taken at window capture (below)
      leaf.classList.add("wc-has-tog");
      if (!isOpen) continue;
      const sub = xEl("div", { class: "wc-ex-sub", role: "group", "data-wc-cat": label });
      for (const n of names) {
        const b = xEl("button", { type: "button", class: "wc-ex-item ex-item", "data-kind": cat.kind, "data-name": n, title: `${KIND_LABEL[cat.kind]} ${n} — double-click: Modify/Show · right-click: more` },
          [xEl("span", { class: "wc-ex-lbl", text: n })]);
        b.addEventListener("dblclick", () => modify(cat.kind, n));
        sub.appendChild(b);
      }
      if (!names.length) sub.appendChild(xEl("div", { class: "muted wc-ex-empty", text: "none defined" }));
      leaf.after(sub);
    }
  }
  /* the ▸ toggle sits inside the category leaf: take its clicks before the
     leaf / dialog-routing handlers (window capture, registered first) */
  window.addEventListener("click", e => {
    const t = e.target && e.target.closest && e.target.closest("#etabsExplorer .wc-ex-tog");
    if (!t) return;
    e.stopImmediatePropagation(); e.preventDefault();
    const label = t.dataset.wcTog;
    if (open.has(label)) open.delete(label); else open.add(label);
    refresh();
  }, true);
  refresh();
  for (const ev of ["sky:model-changed", "sky:model-replaced", "sky:results-changed"]) document.addEventListener(ev, () => refresh());

  sky.wc.explorer = {
    itemsFor: node => itemsFor(node).map(i => (i.head ? { head: i.head } : i.sep ? { sep: true } : { label: i.label, disabled: !!i.disabled })),
    open: (node, x = 200, y = 200) => openMenu(x, y, node), close: closeMenu, run: (node, label) => { const it = itemsFor(node).find(i => i.label === label); if (!it || it.disabled) return false; return it.fn(); },
    expand: (label, on = true) => { if (on) open.add(label); else open.delete(label); refresh(); },
    users, renameIn, deleteIn, refresh, CATS: Object.keys(CATS),
  };
}
