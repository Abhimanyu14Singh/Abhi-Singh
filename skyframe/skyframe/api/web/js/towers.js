/* SkyFrame — ETABS Define → Towers (multiple towers, gap item C9).
   CONTRACT "Multiple towers (Define > Towers)".

     Define → Towers…           model.towers [{name, grid_system, base_elevation,
                                stories: [names]}] + Story.tower tags
     Draw toolbar               "Tower" combo next to the Story combo — filters
                                the story list / ▲▼ stepping to one tower
     Story tab / tables         a Tower column whenever the results carry
                                results.story_tower

   Keys are written ONLY when towers are defined (the backend emits them only
   then), so a model without towers — or the dialog closed with OK and no edit
   — round-trips byte-identically through POST /api/model. The first tower is
   the DEFAULT tower: its stories stay untagged (exactly as the backend writes
   them); stories of every other tower carry "tower". Lengths go through
   js/units.js; the store stays SI (m). */

import U from "./units.js";
import {
  esc, clone, isNum, dialog, closeDialog as twCloseDialog, btn, footBar, errorLine, showError,
  group, el, numInput, select, checkbox, makeCtx, ensureCss,
} from "./nls_ui.js";

const EPS = 1e-6;
const NAME_MAX = 60;

/* ---------------------------------------------------------------- model */
export const towersOf = m => (m && Array.isArray(m.towers) ? m.towers : []);
export const hasTowers = m => towersOf(m).length > 0;
export const defaultTower = m => (hasTowers(m) ? m.towers[0].name : "");
export const storyTower = (m, s) => (s && s.tower) || defaultTower(m);
export const towerStories = (m, name) => (m.stories || []).filter(s => storyTower(m, s) === name);
export function storyTowerMap(m) {
  const out = {};
  for (const s of m.stories || []) out[s.name] = storyTower(m, s);
  return out;
}

/** Refresh every tower's derived `stories` name list (model order). */
export function syncTowerLists(m) {
  if (!hasTowers(m)) return;
  for (const t of m.towers) t.stories = towerStories(m, t.name).map(s => s.name);
}

/** modeledit.js hook: per-tower elevations (base + cumulative heights).
    Returns false without towers (legacy single chain applies). */
export function recomputeTowerElevations(m) {
  if (!hasTowers(m)) return false;
  for (const t of m.towers) {
    let z = +t.base_elevation || 0;
    for (const s of towerStories(m, t.name)) { z += s.height; s.elevation = +z.toFixed(6); }
  }
  syncTowerLists(m);
  return true;
}

/** modeledit.js hook: a story inserted next to `ref` joins ref's tower. */
export function inheritTower(m, ref, st) {
  if (hasTowers(m) && ref && ref.tower) st.tower = ref.tower;
}

/** modeledit.js hook: keep tower story lists in step with a rename. */
export function renameTowerStory(m) { syncTowerLists(m); }

const objTags = m => [...(m.members || []), ...(m.shells || []), ...(m.links || [])];

/** Client mirror of core.towers.validate (mock + pre-check). "" = ok. */
export function validateTowers(m) {
  const tw = towersOf(m);
  const tagged = [...(m.stories || []), ...objTags(m)].filter(o => o && o.tower);
  if (!tw.length) {
    if (tagged.length || (m.joint_towers || []).length) return "Tower tags need towers (Define → Towers); none are defined.";
    return "";
  }
  const names = tw.map(t => t.name);
  if (names.some(n => typeof n !== "string" || !n.trim() || n.length > NAME_MAX)) return `Tower names must be non-empty (max ${NAME_MAX} chars).`;
  if (new Set(names).size !== names.length) return "Tower names must be unique.";
  const grids = new Set((m.grid_systems || (m.grid ? [m.grid] : [])).map(g => g.name || "G1"));
  for (const t of tw) {
    if (t.grid_system && !grids.has(t.grid_system)) return `Tower ${t.name}: unknown grid system ${t.grid_system}.`;
    if (t.base_elevation != null && !isNum(+t.base_elevation)) return `Tower ${t.name}: base elevation must be a number.`;
  }
  const set = new Set(names);
  for (const o of tagged) if (!set.has(o.tower)) return `${o.uid || o.name}: unknown tower ${o.tower}.`;
  for (const j of m.joint_towers || []) if (!set.has(j.tower)) return `Joint tower tag: unknown tower ${j.tower}.`;
  const seen = new Set();
  for (const s of m.stories || []) {
    if (seen.has(s.name)) return `Duplicate story name ${s.name} (story names are unique across towers).`;
    seen.add(s.name);
  }
  for (const t of tw) {
    const sts = towerStories(m, t.name);
    if (!sts.length) return `Tower ${t.name} has no stories.`;
    for (let i = 1; i < sts.length; i++)
      if (!(sts[i].elevation > sts[i - 1].elevation + EPS)) return `Tower ${t.name}: story elevations must increase bottom → top.`;
  }
  const st = storyTowerMap(m);
  for (const o of [...(m.members || []), ...(m.shells || [])])
    if (o.tower && o.story && st[o.story] && st[o.story] !== o.tower) return `${o.uid}: tower ${o.tower} contradicts its story ${o.story}.`;
  return "";
}

const towerObjects = (m, name) => {
  const st = storyTowerMap(m);
  return objTags(m).filter(o => (o.tower || st[o.story] || "") === name).length;
};

/* ---------------------------------------------------- Define → Towers… */
export function openTowers(ctx, sky) {
  const m = ctx.store.model;
  if (!m) return null;
  ensureTwCss();
  const had = hasTowers(m);
  let allow = had;
  const grids = (m.grid_systems && m.grid_systems.length ? m.grid_systems : (m.grid ? [m.grid] : [])).map(g => g.name || "G1");
  const mkRows = () => towersOf(m).map(t => ({
    orig: t.name, name: t.name, grid_system: t.grid_system || null,
    base_elevation: +t.base_elevation || 0, add: null,
  }));
  let rows = mkRows();
  let sel = 0;
  const body = el("div", "tw-def");
  const err = errorLine("twError");
  const draft = { n: 3, h: 3.0, base: 0.0, grid: grids[0] || null };

  const storiesOf = r => (r.orig ? towerStories(m, r.orig) : []);
  const objCount = r => (r.orig ? towerObjects(m, r.orig) : 0);

  const draw = () => {
    body.textContent = "";
    body.appendChild(el("p", "muted dlg-intro",
      "ETABS multiple towers. Each tower has its own stories, grid system and base elevation; " +
      "diaphragms, story forces, auto-lateral loads (ELF / wind), drifts, story shears and CM / CR are computed " +
      "<b>per tower</b>. Towers may share a podium (a tower whose base elevation is the podium roof) and may be " +
      "joined by link beams. The first tower is the default: untagged stories and objects belong to it."));
    body.appendChild(checkbox("Allow multiple towers", allow, v => {
      allow = v;
      if (allow && !rows.length)
        rows = [{ orig: null, name: "T1", grid_system: grids[0] || null, base_elevation: 0, add: null, wrapExisting: true }];
      showError(err, "");
      draw();
    }, "twAllow", "Off = one implicit tower (the legacy single-tower model)"));
    if (!allow) { body.appendChild(err); return; }

    const g = group("Towers");
    const tbl = el("table", "data-table tw-table");
    tbl.id = "twTable";
    const L = esc(U.label("length"));
    tbl.innerHTML = `<thead><tr><th class="txt">Tower</th><th class="txt">Grid system</th><th>Base elev ${L}</th>` +
      `<th>Stories</th><th class="txt">Range ${L}</th><th>Objects</th><th></th></tr></thead>`;
    const tb = document.createElement("tbody");
    rows.forEach((r, i) => {
      const tr = document.createElement("tr");
      tr.dataset.tower = r.name;
      if (i === sel) tr.className = "is-sel";
      tr.addEventListener("click", ev => { if (ev.target.closest("input,select,button")) return; sel = i; draw(); });
      const nameIn = document.createElement("input");
      nameIn.type = "text"; nameIn.value = r.name; nameIn.id = `twName${i}`; nameIn.spellcheck = false; nameIn.maxLength = NAME_MAX;
      nameIn.addEventListener("change", () => { r.name = nameIn.value.trim(); showError(err, ""); });
      const td1 = el("td", "txt"); td1.appendChild(nameIn);
      if (i === 0) td1.appendChild(el("span", "tw-def-badge", "default"));
      const td2 = el("td", "txt");
      td2.appendChild(select([["", "(none)"], ...grids.map(n => [n, n])], r.grid_system || "", v => { r.grid_system = v || null; }, `twGrid${i}`));
      const sts = r.wrapExisting ? (m.stories || []) : storiesOf(r);
      const nobj = r.wrapExisting ? 0 : objCount(r);
      const td3 = el("td");
      const baseLocked = r.wrapExisting || nobj > 0;
      td3.appendChild(numInput("length", r.base_elevation, {
        id: `twBase${i}`, disabled: baseLocked,
        title: baseLocked ? "Base elevation of a tower with objects is fixed (move the objects first)" : "",
        onSet: v => { r.base_elevation = v; },
      }));
      const nSt = sts.length + (r.add ? r.add.n : 0);
      const zs = sts.map(s => s.elevation);
      let lo = sts.length ? sts[0].elevation - sts[0].height : r.base_elevation, hi = zs.length ? Math.max(...zs) : r.base_elevation;
      if (r.add) { hi = (sts.length ? hi : r.base_elevation) + r.add.n * r.add.h; }
      const td4 = el("td", "", String(nSt) + (r.add ? ` <span class="dim">(+${r.add.n} new)</span>` : ""));
      const td5 = el("td", "txt dim", `${U.fmt("length", lo, 2)} – ${U.fmt("length", hi, 2)}`);
      const td6 = el("td", "", nobj ? String(nobj) : `<span class="dim">0</span>`);
      const td7 = document.createElement("td");
      if (i > 0) {
        const x = el("button", "chip-x"); x.textContent = "✕"; x.id = `twDel${i}`;
        x.title = nobj ? `Tower has ${nobj} object(s) — delete or move them first` : "Delete tower (and its empty stories)";
        x.addEventListener("click", () => {
          if (nobj) { showError(err, `Can't delete ${r.orig}: ${nobj} object(s) belong to it.`); return; }
          rows.splice(i, 1); sel = Math.min(sel, rows.length - 1); draw();
        });
        td7.appendChild(x);
      }
      tr.append(td1, td2, td3, td4, td5, td6, td7);
      tb.appendChild(tr);
    });
    tbl.appendChild(tb);
    g.appendChild(el("div", "table-scroll", "")).appendChild(tbl);

    // ---- add tower (story-height table: n stories × typical height)
    const add = el("div", "cd-inline tw-add");
    add.append(
      el("span", "", "New tower:"),
      el("label", "tw-lab", "stories"),
      numInput("none", draft.n, { id: "twAddN", int: true, min: 1, max: 200, onSet: v => { draft.n = v; } }),
      el("label", "tw-lab", `height ${esc(U.label("length"))}`),
      numInput("length", draft.h, { id: "twAddH", gt: 0, onSet: v => { draft.h = v; } }),
      el("label", "tw-lab", `base ${esc(U.label("length"))}`),
      numInput("length", draft.base, { id: "twAddBase", onSet: v => { draft.base = v; } }),
      btn("+ Add Tower", "btn-small cd-add", () => {
        if (!(draft.n >= 1 && draft.h > 0)) { showError(err, "Enter a story count ≥ 1 and a height > 0."); return; }
        const taken = new Set(rows.map(r => r.name));
        let k = rows.length + 1;
        while (taken.has(`T${k}`)) k++;
        rows.push({ orig: null, name: `T${k}`, grid_system: draft.grid, base_elevation: draft.base,
          add: { n: Math.round(draft.n), h: draft.h } });
        sel = rows.length - 1;
        showError(err, "");
        draw();
      }, "Add a tower with its own story-height table", "twAdd"));
    g.appendChild(add);
    body.appendChild(g);

    // ---- stories of the selected tower (tower column = this table's key)
    const r = rows[sel];
    if (r) {
      const g2 = group(`Stories of ${r.name}`);
      const st = el("table", "data-table tw-stories");
      st.id = "twStories";
      const L2 = esc(U.label("length"));
      st.innerHTML = `<thead><tr><th class="txt">Story</th><th class="txt">Tower</th><th>Height ${L2}</th><th>Elevation ${L2}</th></tr></thead>`;
      const sb = document.createElement("tbody");
      const list = (r.wrapExisting ? (m.stories || []) : storiesOf(r)).map(s => ({ name: s.name, h: s.height, z: s.elevation, isNew: false }));
      if (r.add) {
        let z = list.length ? list[list.length - 1].z : r.base_elevation;
        for (let j = 0; j < r.add.n; j++) { z += r.add.h; list.push({ name: newStoryName(m, r.name, j, list), h: r.add.h, z, isNew: true }); }
      }
      for (const s of [...list].reverse())
        sb.insertAdjacentHTML("beforeend", `<tr${s.isNew ? ' class="tw-new"' : ""}><td class="txt">${esc(s.name)}</td><td class="txt">${esc(r.name)}</td>` +
          `<td>${U.fmt("length", s.h, 2)}</td><td>${U.fmt("length", s.z, 2)}</td></tr>`);
      st.appendChild(sb);
      g2.appendChild(el("div", "table-scroll tw-st-scroll", "")).appendChild(st);
      g2.appendChild(el("p", "muted", "Edit story names / heights in <b>Edit Grid/Stories…</b>; inserted stories join the tower of the story they are inserted next to."));
      body.appendChild(g2);
    }
    body.appendChild(err);
  };

  const commit = () => {
    if (!allow) {
      if (!had) return true;                                  // untouched
      if (rows.length > 1 || towersOf(m).length > 1) {
        showError(err, "Delete the extra towers first (only one tower can be merged back into the single-tower model).");
        return false;
      }
      delete m.towers; delete m.joint_towers;
      for (const o of [...(m.stories || []), ...objTags(m)]) if (o && o.tower) delete o.tower;
      ctx.markDirty(); ctx.onChange && ctx.onChange("towers");
      sky && sky.rebuildStorySelect && sky.rebuildStorySelect();
      return true;
    }
    const names = rows.map(r => r.name);
    if (!rows.length) { showError(err, "Define at least one tower (or untick Allow multiple towers)."); return false; }
    if (names.some(n => !n || n.length > NAME_MAX)) { showError(err, "Every tower needs a name."); return false; }
    if (new Set(names).size !== names.length) { showError(err, "Tower names must be unique."); return false; }
    if (rows.some(r => !isNum(+r.base_elevation))) { showError(err, "Base elevations must be numbers."); return false; }
    const prevJson = JSON.stringify({ t: m.towers || null });
    const work = clone(m);
    const ren = Object.fromEntries(rows.filter(r => r.orig && r.orig !== r.name).map(r => [r.orig, r.name]));
    const keep = new Set(rows.filter(r => r.orig).map(r => r.orig));
    // deleted towers (object-free): drop their stories + story-keyed refs
    const gone = new Set(towersOf(work).map(t => t.name).filter(n => !keep.has(n)));
    if (gone.size) {
      const dead = new Set(work.stories.filter(s => gone.has(storyTower(work, s))).map(s => s.name));
      work.stories = work.stories.filter(s => !dead.has(s.name));
      for (const p of Object.values(work.patterns || {})) p.story_forces = (p.story_forces || []).filter(f => !dead.has(f.story));
      for (const k of ["story_diaphragm", "explicit_story_masses", "story_masses"])
        if (work[k]) for (const n of dead) delete work[k][n];
      if (work.joint_towers) work.joint_towers = work.joint_towers.filter(j => !gone.has(j.tower));
    }
    // renames → retag stories / objects / joints
    for (const o of [...work.stories, ...objTags(work)]) if (o && o.tower && ren[o.tower]) o.tower = ren[o.tower];
    for (const j of work.joint_towers || []) if (ren[j.tower]) j.tower = ren[j.tower];
    const first = rows[0].name;
    // a story tagged with the (new) default tower stays untagged — backend form
    work.towers = rows.map(r => ({ name: r.name, grid_system: r.grid_system || null,
      base_elevation: +r.base_elevation || 0, stories: [] }));
    for (const s of work.stories) if (s.tower === first) delete s.tower;
    // new towers: materialise the story-height table
    for (const r of rows) {
      if (!r.add) continue;
      const sts = towerStories(work, r.name);
      let z = sts.length ? sts[sts.length - 1].elevation : (+r.base_elevation || 0);
      for (let j = 0; j < r.add.n; j++) {
        z += r.add.h;
        const st = { name: newStoryName(work, r.name, j, []), height: r.add.h, elevation: +z.toFixed(6) };
        if (r.name !== first) st.tower = r.name;
        work.stories.push(st);
        if (work.story_masses) work.story_masses[st.name] = 0;
      }
    }
    // base elevation edits (object-free towers only): re-level that tower
    for (const r of rows) {
      if (!r.orig || r.add || towerObjects(work, r.name)) continue;
      const prev = towersOf(m).find(t => t.name === r.orig);
      if (prev && Math.abs((+prev.base_elevation || 0) - (+r.base_elevation || 0)) > EPS) {
        let z = +r.base_elevation || 0;
        for (const s of towerStories(work, r.name)) { z += s.height; s.elevation = +z.toFixed(6); }
      }
    }
    syncTowerLists(work);
    const bad = validateTowers(work);
    if (bad) { showError(err, bad); return false; }
    if (JSON.stringify({ t: work.towers }) === prevJson && JSON.stringify(work.stories) === JSON.stringify(m.stories)
        && JSON.stringify(objTags(work)) === JSON.stringify(objTags(m))) return true;   // OK with no edit
    for (const k of Object.keys(m)) if (!(k in work)) delete m[k];
    Object.assign(m, work);
    ctx.markDirty(); ctx.onChange && ctx.onChange("towers");
    if (sky) {
      sky.store.c9Tower = null;
      sky.rebuildStorySelect && sky.rebuildStorySelect();
      sky.renderGridEditor && sky.renderGridEditor();
    }
    return true;
  };

  const fb = footBar("Define → Towers", [
    btn("Cancel", "", () => dlg.close(), "", "twCancel"),
    btn("OK", "btn-run", () => { if (commit()) dlg.close(); }, "", "twOk"),
  ]);
  const dlg = dialog("twModal", { title: "Towers", glyph: "diaph", wide: true, body, foot: fb.wrap, onUnits: draw });
  draw();
  return dlg;
}

function newStoryName(m, tower, j, pending) {
  const taken = new Set([...(m.stories || []).map(s => s.name), ...pending.map(s => s.name)]);
  let k = j + 1, n = `${tower}-S${k}`;
  while (taken.has(n)) { k++; n = `${tower}-S${k}`; }
  return n;
}

/* ------------------------------------------- Draw toolbar: Tower combo */
function ensureTowerSelect() {
  let wrap = document.getElementById("towerWrap");
  if (wrap) return wrap;
  const storyWrap = document.getElementById("storyWrap");
  if (!storyWrap) return null;
  wrap = document.createElement("label");
  wrap.className = "story-wrap hidden";
  wrap.id = "towerWrap";
  wrap.title = "Tower shown in the plan view / story list (Define → Towers)";
  wrap.innerHTML = `<span>Tower</span><select id="towerSelect" class="mini-select"></select>`;
  storyWrap.before(wrap);
  return wrap;
}

/** app.js hook (rebuildStorySelect): show the Tower combo and restrict the
    Story combo to the chosen tower. */
export function syncTowerSelect(sel, store) {
  const wrap = ensureTowerSelect();
  const m = store && store.model;
  if (!wrap || !m) return;
  const on = hasTowers(m);
  wrap.classList.toggle("hidden", !on);
  if (!on) { store.c9Tower = null; return; }
  const ts = document.getElementById("towerSelect");
  const names = m.towers.map(t => t.name);
  if (store.c9Tower && !names.includes(store.c9Tower)) store.c9Tower = null;
  ts.innerHTML = `<option value="">All towers</option>` + names.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
  ts.value = store.c9Tower || "";
  const st = storyTowerMap(m);
  if (store.c9Tower) {
    for (const o of [...sel.options]) if (st[o.value] !== store.c9Tower) o.remove();
    if (!sel.options.length) return;
    if (st[store.story] !== store.c9Tower) store.story = sel.options[0].value;
  }
  for (const o of sel.options) o.textContent = `${o.value} · ${st[o.value] || ""}`;
  sel.value = store.story;
}

/** app.js hook (stepStory): stories reachable by ▲▼ (null = all). */
export function towerStepNames(m, store) {
  if (!hasTowers(m) || !store.c9Tower) return null;
  return towerStories(m, store.c9Tower).map(s => s.name);
}

/* ------------------------------------- results: Tower column decoration */
/** app.js hook (renderStoryTab): add a Tower column after Story. */
export function decorateStoryTable(table, r) {
  const map = r && r.story_tower;
  try { if (window.__sky && window.__sky.store) renderTowerCard(window.__sky); } catch (e) { console.warn("tower card", e); }
  if (!table || !map) return;
  const hr = table.querySelector("thead tr");
  if (!hr || hr.querySelector(".tw-col")) return;
  const th = document.createElement("th");
  th.className = "txt tw-col"; th.textContent = "Tower";
  hr.children[0].after(th);
  for (const tr of table.querySelectorAll("tbody tr")) {
    const c0 = tr.children[0];
    if (!c0) continue;
    const td = document.createElement("td");
    td.className = "txt tw-col"; td.textContent = map[c0.textContent.trim()] || "";
    c0.after(td);
  }
}

/** app.js hook (Edit Grid/Stories rows): prefix the tower to the range. */
export function decorateStoryRows(box, m) {
  if (!box || !hasTowers(m)) return;
  const st = storyTowerMap(m);
  for (const row of box.querySelectorAll(".ge-story:not(.head)")) {
    const inp = row.querySelector("input[type=text]");
    const ev = row.querySelector(".ge-elev");
    if (!inp || !ev || ev.dataset.tw) continue;
    ev.dataset.tw = "1";
    ev.textContent = `${st[inp.value] || ""} · ${ev.textContent}`;
    ev.title = `Tower ${st[inp.value] || ""}`;
  }
}

/** Results summary card (Story tab): per-tower base shear of the case. */
export function renderTowerCard(sky) {
  const host = document.getElementById("content-story");
  if (!host) return;
  let card = document.getElementById("twResCard");
  const r = sky.store.results;
  if (!r || !r.towers) { if (card) card.remove(); return; }
  if (!card) {
    card = el("div", "diag-block tw-card");
    card.id = "twResCard";
    const t = document.getElementById("storyTable");
    const anchor = t && t.closest(".table-scroll");
    if (anchor && anchor.parentNode === host) anchor.after(card); else host.appendChild(card);
  }
  const cname = (sky.store.caseName || "").replace(/^rs:/, "");
  const F = esc(U.label("force")), L = esc(U.label("length"));
  const rows = Object.entries(r.towers).map(([n, t]) => {
    const bs = (t.base_shear || {})[cname];
    return `<tr><td class="txt"><b>${esc(n)}</b></td><td class="txt">${esc((t.stories || []).join(", "))}</td>` +
      `<td>${U.fmt("length", t.base_elevation || 0, 2)}</td><td class="txt dim">${esc(t.grid_system || "—")}</td>` +
      `<td>${bs ? U.fmt("force", bs.FX, 1) : "—"}</td><td>${bs ? U.fmt("force", bs.FY, 1) : "—"}</td></tr>`;
  }).join("");
  card.innerHTML = `<div class="diag-head"><div><h3>Towers <span class="muted">per tower</span></h3>` +
    `<p class="muted">story shear = cut through each tower's own elements; base shear = its lowest story` +
    (cname ? ` · case <b>${esc(cname)}</b>` : "") + `</p></div></div>` +
    `<div class="table-scroll"><table class="data-table" id="twResTable"><thead><tr><th class="txt">Tower</th><th class="txt">Stories</th>` +
    `<th>Base elev ${L}</th><th class="txt">Grid</th><th>Base Vx ${F}</th><th>Base Vy ${F}</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

/* ------------------------------------------------------------------ css */
let twCss = false;
function ensureTwCss() {
  ensureCss();
  if (twCss || document.getElementById("towersCss")) return;
  twCss = true;
  const link = document.createElement("link");
  link.rel = "stylesheet"; link.id = "towersCss"; link.href = "/static/towers.css";
  document.head.appendChild(link);
}

/* -------------------------------------------------------------- install */
export function installTowers(sky) {
  if (!sky) return;
  ensureTwCss();
  Object.assign(sky, {
    openTowers: () => openTowers(makeCtx(sky), sky),
    closeTowersDialog: () => twCloseDialog("twModal"),
    validateTowers, towersOf, storyTowerMap, recomputeTowerElevations,
    renderTowerCard: () => renderTowerCard(sky),
  });
  const wrap = ensureTowerSelect();
  if (wrap) {
    document.getElementById("towerSelect").addEventListener("change", e => {
      sky.store.c9Tower = e.target.value || null;
      const prev = sky.store.story;
      sky.rebuildStorySelect && sky.rebuildStorySelect();
      if (sky.store.story !== prev) {
        const s = sky.store.story;
        sky.store.story = prev;               // let setStory run its refresh
        sky.setStory && sky.setStory(s);
      }
    });
    syncTowerSelect(document.getElementById("storySelect"), sky.store);
  }
  const upd = () => { try { renderTowerCard(sky); } catch (e) { console.warn("tower card", e); } };
  document.addEventListener("sky:results-changed", upd);
  document.addEventListener("sky:units-changed", upd);
}
