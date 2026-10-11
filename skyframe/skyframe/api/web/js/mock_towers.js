/* SkyFrame — offline mock for multiple towers (CONTRACT "Multiple towers").

   mockValidateTowers(model)      POST /api/model pre-check (mirrors the
                                  backend validation, "" = ok)
   mockTowersAugment(model, r)    analysis results: per-tower drift chains,
                                  results.towers / results.story_tower
   mockTowersTable(model, tbl)    results tables: Tower column after Story

   Every function is a no-op for a model without towers, so ?mock=1 output
   of single-tower models is unchanged. */

import {
  validateTowers as twValidate, hasTowers as twHas, towersOf as twOf,
  storyTowerMap as twMap, towerStories as twStories,
} from "./towers.js";

export function mockValidateTowers(model) {
  return model ? twValidate(model) : "";
}

export function mockTowersAugment(model, r) {
  if (!r || !twHas(model)) return r;
  const map = twMap(model);
  const rechain = cd => {
    const st = cd && cd.story;
    if (!st) return;
    for (const t of twOf(model)) {
      let px = 0, py = 0;
      for (const s of twStories(model, t.name)) {
        const e = st[s.name];
        if (!e) continue;
        const h = s.height > 0 ? s.height : 1;
        e.drift_x = ((e.ux || 0) - px) / h;
        e.drift_y = ((e.uy || 0) - py) / h;
        px = e.ux || 0; py = e.uy || 0;
      }
    }
  };
  for (const grp of [r.cases, r.combos, r.rs_cases])
    for (const cd of Object.values(grp || {})) rechain(cd);
  const towers = {};
  for (const t of twOf(model)) {
    const stories = twStories(model, t.name).map(s => s.name);
    const base_shear = {};
    for (const grp of [r.cases, r.combos, r.rs_cases])
      for (const [n, cd] of Object.entries(grp || {})) {
        const e = stories.length && cd && cd.story ? cd.story[stories[0]] : null;
        if (e) base_shear[n] = { FX: +e.shear_x || 0, FY: +e.shear_y || 0 };
      }
    towers[t.name] = { stories, base_elevation: +t.base_elevation || 0,
      grid_system: t.grid_system || null, base_shear };
  }
  r.towers = towers;
  r.story_tower = map;
  return r;
}

export function mockTowersTable(model, tbl) {
  if (!tbl || !twHas(model) || !(tbl.columns || []).some(c => c.key === "story")) return tbl;
  const map = twMap(model);
  const cols = [];
  for (const c of tbl.columns) {
    cols.push(c);
    if (c.key === "story") cols.push({ key: "tower", label: "Tower", quantity: c.quantity || "text" });
  }
  const rows = (tbl.rows || []).map(row => {
    const o = {};
    for (const [k, v] of Object.entries(row)) { o[k] = v; if (k === "story") o.tower = map[v] || ""; }
    return o;
  });
  return Object.assign(tbl, { columns: cols, rows });
}
