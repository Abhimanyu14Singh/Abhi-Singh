/* SkyFrame — multiple towers follow-ups (CONTRACT "Multiple towers —
   follow-ups").

     TH results tab        "Story shear per tower" block: per-tower peak
                           |Vx| / |Vy| (element cut), tower base shear, the
                           story_shear_history chart of the selected story
                           and a "Record story-shear history" check
                           (th_cases[c].output_requests.story_shears)
     Pushover options /    "Tower" choice of the monitored displacement
     NLS case dialogs      (control_tower) — helpers used by casedlg.js /
                           nlsdlg.js hook lines

   Nothing renders / is written without towers, so a model without towers
   round-trips byte-identically. Forces go through js/units.js; the store
   stays SI. */

import U from "./units.js";
import { timeSeriesChart as fuTimeSeriesChart } from "./charts.js";
import { esc as fuEsc, el as fuEl, row as fuRow, select as fuSelect, radio as fuRadio,
  checkbox as fuCheckbox } from "./nls_ui.js";
import { hasTowers as fuHasTowers, towersOf as fuTowersOf, towerStories as fuTowerStories,
  storyTowerMap as fuStoryTowerMap } from "./towers.js";

/* ------------------------------------------------------------- model */
export const towerNames = m => fuTowersOf(m).map(t => t.name);

/** Top story (highest elevation) of a tower. */
export function towerTopStory(m, name) {
  const sts = fuTowerStories(m, name);
  let best = null;
  for (const s of sts) if (!best || (+s.elevation || 0) > (+best.elevation || 0)) best = s;
  return best;
}

/** Client mirror of the backend control_tower check ("" = ok). */
export function controlTowerError(m, c, at) {
  const ct = c && c.control_tower;
  if (ct == null) return "";
  if (typeof ct !== "string" || !ct) return `${at}: control tower must be a tower name.`;
  if (!fuHasTowers(m)) return `${at}: control tower ${ct} needs towers (Define → Towers).`;
  if (!towerNames(m).includes(ct)) return `${at}: unknown control tower ${ct}.`;
  if (c.control_story != null || c.control_point != null) return `${at}: give a control tower OR a control story / joint, not both.`;
  return "";
}

/** Mock / pre-check validator for every pushover + NLS case. */
export function validateControlTowers(m) {
  if (!m) return "";
  for (const [n, c] of Object.entries(m.pushover_cases || {})) {
    const e = controlTowerError(m, c, `Pushover case ${n}`);
    if (e) return e;
  }
  for (const [n, c] of Object.entries(m.nonlinear_static_cases || {})) {
    const e = controlTowerError(m, c, `Nonlinear static case ${n}`);
    if (e) return e;
  }
  return "";
}

/* -------------------------------------------- dialog control helpers */
/** casedlg / nlsdlg hook: append a "Tower" radio (towers only) to the
    monitored-displacement radio row ``rr``. */
export function towerRadio(rr, m, d, ctrl, onPick, prefix) {
  if (!rr || !fuHasTowers(m)) return;
  rr.appendChild(fuRadio(`${prefix}Ctrl`, "tower", ctrl === "tower", "Tower roof", () => {
    if (!d.control_tower) d.control_tower = towerNames(m)[0] || null;
    onPick("tower");
  }, `${prefix}CtrlTower`));
}

/** casedlg / nlsdlg hook: the tower selector row when "Tower" is picked. */
export function towerRow(g, m, d, ctrl, prefix) {
  if (!g || ctrl !== "tower" || !fuHasTowers(m)) return;
  const sel = fuSelect(towerNames(m).map(n => {
    const top = towerTopStory(m, n);
    return [n, top ? `${n} — roof ${top.name} (z ${U.fmt("length", top.elevation, 2)} ${U.label("length")})` : n];
  }), d.control_tower || "", v => { d.control_tower = v || null; }, `${prefix}Tower`);
  g.appendChild(fuRow("Control tower", sel, null, "the tower's top-story diaphragm master"));
}

/** Initial radio state of a case. */
export const initialCtrl = d => (d.control_tower ? "tower" : d.control_point ? "point" : d.control_story ? "story" : "roof");

/* ------------------------------------------------- TH results block */
const ID = "c9fThTowers";

function ensureBlock() {
  let box = document.getElementById(ID);
  if (box) return box;
  const host = document.getElementById("content-th");
  if (!host) return null;
  box = fuEl("div", "diag-block c9f-th");
  box.id = ID;
  const peaks = document.getElementById("thPeaksTable");
  const anchor = peaks && peaks.closest(".table-scroll");
  if (anchor && anchor.parentNode === host) anchor.after(box); else host.appendChild(box);
  return box;
}

/** app.js renderThTab hook: (re)render the per-tower block for record ``td``. */
export function renderThTowers(sky, td) {
  const store = sky && sky.store;
  const m = store && store.model;
  const old = document.getElementById(ID);
  if (!m || !fuHasTowers(m) || !td) { if (old) old.remove(); return; }
  const box = ensureBlock();
  if (!box) return;
  box.textContent = "";
  const caseName = td._case || store.thCase;
  const tc = (m.th_cases || {})[caseName];
  const F = fuEsc(U.label("force")), L = fuEsc(U.label("length"));
  const head = fuEl("div", "diag-head");
  head.innerHTML = `<div><h3>Story shear per tower <span class="muted">${fuEsc(td.story_shear_method === "element_cut" ? "element cut" : "—")}</span></h3>` +
    `<p class="muted">Horizontal cut through each tower's own frames, shells and links just below the level ` +
    `(restoring forces, increment past gravity) · peak |V| over the record${td._fna ? " · FNA" : ""}</p></div>`;
  box.appendChild(head);
  if (tc) {
    const req = tc.output_requests || {};
    box.appendChild(fuCheckbox("Record story-shear history (next run)", !!req.story_shears, on => {
      const cur = Object.assign({}, tc.output_requests || {});
      if (on) cur.story_shears = true; else delete cur.story_shears;
      if (Object.keys(cur).length) tc.output_requests = cur; else delete tc.output_requests;
      sky.markDirty && sky.markDirty();
    }, "c9fThRecord", "Sets output_requests.story_shears on this time-history case"));
  }
  const peaks = (td.peaks && td.peaks.story) || {};
  const tw = td.towers || null;
  const elev = {};
  for (const s of m.stories || []) elev[s.name] = s.elevation;
  const rows = [];
  for (const t of fuTowersOf(m)) {
    const sts = fuTowerStories(m, t.name).map(s => s.name).reverse();     // roof first
    sts.forEach((s, i) => {
      const p = peaks[s] || {};
      const act = s === store.thStory ? ` class="th-active"` : "";
      rows.push(`<tr${act}><td class="txt">${i === 0 ? `<b>${fuEsc(t.name)}</b>` : ""}</td><td class="txt">${fuEsc(s)}</td>` +
        `<td class="dim">${U.fmt("length", elev[s] || 0, 2)}</td>` +
        `<td>${p.shear_x != null ? U.fmt("force", p.shear_x, 1) : "—"}</td><td>${p.shear_y != null ? U.fmt("force", p.shear_y, 1) : "—"}</td></tr>`);
    });
    const bs = tw && tw[t.name] && tw[t.name].base_shear;
    rows.push(`<tr class="totals"><td class="txt">${fuEsc(t.name)}</td><td class="txt">peak base shear</td><td></td>` +
      `<td>${bs ? U.fmt("force", bs.FX, 1) : "—"}</td><td>${bs ? U.fmt("force", bs.FY, 1) : "—"}</td></tr>`);
  }
  const wrap = fuEl("div", "table-scroll");
  wrap.innerHTML = `<table class="data-table" id="c9fThTable"><thead><tr><th class="txt">Tower</th><th class="txt">Story</th>` +
    `<th>Elev ${L}</th><th>peak |Vx| ${F}</th><th>peak |Vy| ${F}</th></tr></thead><tbody>${rows.join("")}</tbody></table>`;
  box.appendChild(wrap);
  if (!tw) box.appendChild(fuEl("p", "muted", "Run the analysis to get per-tower story shears (element cut)."));
  const hist = td.story_shear_history;
  const s = store.thStory;
  if (hist && s && hist.VX && hist.VX[s]) {
    const dF = v => U.toDisplay("force", v);
    const towerOf = (hist.story_tower || fuStoryTowerMap(m))[s] || "";
    const chart = fuTimeSeriesChart(td.t || [], [
      { label: `${s} Vx`, values: (hist.VX[s] || []).map(dF), color: "#1e9ad4" },
      { label: `${s} Vy`, values: ((hist.VY || {})[s] || []).map(dF), color: "#d55181" },
    ], { title: `Story shear — ${s}${towerOf ? ` (tower ${towerOf})` : ""}`, unit: U.label("force"), dec: U.dec("force", 1) });
    chart.id = "c9fThChart";
    box.appendChild(chart);
  } else if (tc && (tc.output_requests || {}).story_shears) {
    box.appendChild(fuEl("p", "muted", "Story-shear history is requested — re-run the analysis to chart it."));
  }
}

/* ------------------------------------------------------------ install */
export function installTowersFu(sky) {
  if (!sky) return;
  Object.assign(sky, {
    c9fRenderTh: td => renderThTowers(sky, td),
    c9fValidateControlTowers: validateControlTowers,
  });
}
