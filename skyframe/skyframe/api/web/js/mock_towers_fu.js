/* SkyFrame — ?mock=1 support for the multiple-towers follow-ups (CONTRACT
   "Multiple towers — follow-ups").

   fuMockAugment(model, r)        analysis results: per-tower TH story shear
                                  peaks + towers block + story_shear_history
                                  (when requested), pushover / NLS
                                  control_tower monitored joint + height
   fuMockValidate(model)          POST /api/model pre-check (control_tower,
                                  "" = ok)
   fuMockDbEdit(model, path, body, inner)
                                  Interactive Database Editing router wrapper:
                                  the Story Definitions "tower" column (only
                                  with towers) — every other request goes to
                                  the inner mock untouched

   Every function is a no-op for a model without towers (and without the new
   keys), so ?mock=1 output of other models is unchanged. */

import { hasTowers as fuHas, towersOf as fuOf, towerStories as fuStories,
  storyTowerMap as fuMap, recomputeTowerElevations as fuRecompute } from "./towers.js";
import { towerTopStory as fuTop, validateControlTowers as fuValidateCtl,
  controlHeight as fuCtlH } from "./towers_fu.js";
import { parseCsv as fuParseCsv } from "./mock_dbedit.js";

const peakOf = a => (a || []).reduce((x, v) => Math.max(x, Math.abs(v)), 0);
const r6 = v => +(+v).toPrecision(10);

export function fuMockValidate(model) {
  return model ? fuValidateCtl(model) : "";
}

/* ----------------------------------------------- TH per-tower shears */
function shareProfile(model) {
  // per story: fraction of the total base shear carried at that level
  // (inverted-triangle cumulative profile within each tower, towers by
  // their story count) — plausible mock numbers only
  const out = {};
  const tws = fuOf(model);
  const total = tws.reduce((a, t) => a + fuStories(model, t.name).length, 0) || 1;
  for (const t of tws) {
    const sts = fuStories(model, t.name);
    const n = sts.length;
    const w = sts.map((_, i) => i + 1);
    const W = w.reduce((a, b) => a + b, 0) || 1;
    sts.forEach((s, i) => { out[s.name] = (n / total) * w.slice(i).reduce((a, b) => a + b, 0) / W; });
  }
  return out;
}

function legacyProfile(model) {
  const sts = model.stories || [];
  const w = sts.map((_, i) => i + 1);
  const W = w.reduce((a, b) => a + b, 0) || 1;
  const out = {};
  sts.forEach((s, i) => { out[s.name] = w.slice(i).reduce((a, b) => a + b, 0) / W; });
  return out;
}

function augmentTh(model, r) {
  const towers = fuHas(model);
  for (const [name, rec] of Object.entries(r.th_cases || {})) {
    const tc = (model.th_cases || {})[name] || {};
    const req = !!(tc.output_requests && tc.output_requests.story_shears);
    if (!towers && !req) continue;
    const prof = towers ? shareProfile(model) : legacyProfile(model);
    const bx = rec.base_FX || [], by = rec.base_FY || [];
    const VX = {}, VY = {};
    for (const s of model.stories || []) {
      const f = prof[s.name] || 0;
      VX[s.name] = bx.map(v => r6(-v * f));
      VY[s.name] = by.map(v => r6(-v * f));
    }
    if (towers) {
      rec.peaks = rec.peaks || { story: {}, base: {} };
      rec.peaks.story = rec.peaks.story || {};
      for (const s of model.stories || []) {
        const p = rec.peaks.story[s.name] = rec.peaks.story[s.name] || {};
        p.shear_x = peakOf(VX[s.name]);
        p.shear_y = peakOf(VY[s.name]);
      }
      const tw = {};
      for (const t of fuOf(model)) {
        const sts = fuStories(model, t.name).map(s => s.name);
        const p = (sts.length && rec.peaks.story[sts[0]]) || {};
        tw[t.name] = { stories: sts, base_shear: { FX: p.shear_x || 0, FY: p.shear_y || 0 } };
      }
      rec.towers = tw;
      rec.story_shear_method = "element_cut";
    }
    if (req) {
      rec.story_shear_history = { method: towers ? "element_cut" : "inertia", VX, VY };
      if (towers) rec.story_shear_history.story_tower = fuMap(model);
    }
  }
}

/* ------------------------------------------ pushover / NLS control */
function towerRoofNode(model, r, tower) {
  const top = fuTop(model, tower);
  if (!top) return null;
  const z = +top.elevation || 0;
  const pts = [];
  for (const mm of model.members || []) {
    if (mm.story !== top.name) continue;
    for (const p of [mm.pi, mm.pj]) if (Math.abs(p[2] - z) < 1e-6) pts.push(p);
  }
  const cx = pts.length ? pts.reduce((a, p) => a + p[0], 0) / pts.length : 0;
  const cy = pts.length ? pts.reduce((a, p) => a + p[1], 0) / pts.length : 0;
  let best = null, bd = Infinity;
  for (const [t, p] of Object.entries(r.nodes || {})) {
    if (Math.abs(p[2] - z) > 1e-6) continue;
    const d = Math.hypot(p[0] - cx, p[1] - cy);
    if (d < bd) { bd = d; best = t; }
  }
  return { story: top.name, z, node: best != null ? +best : null, point: best != null ? r.nodes[best].map(Number) : [cx, cy, z] };
}

function augmentControl(model, r) {
  const H = Math.max(1e-9, ...(model.stories || []).map(s => +s.elevation || 0));
  for (const [name, po] of Object.entries(r.pushover || {})) {
    const pc = (model.pushover_cases || {})[name] || {};
    if (!po) continue;
    const hasTarget = isFinite(pc.target_disp) && pc.target_disp;
    if (!pc.control_tower) {
      // v1.20: the default roof of a towers model — H_ctrl = its tower's height
      const cc0 = po.capacity_curve;
      if (cc0 && pc.control_story == null && pc.control_point == null) {
        const hc = fuCtlH(model, pc, "roof");
        if (!hasTarget && cc0.height) cc0.disp = (cc0.disp || []).map(u => +(u * hc / cc0.height).toFixed(6));
        cc0.height = +hc.toFixed(6);
      }
      continue;
    }
    const roof = towerRoofNode(model, r, pc.control_tower);
    if (!roof) continue;
    const cc = po.capacity_curve;
    if (cc) {
      const hc = fuCtlH(model, pc, "tower");          // v1.20: tower height (above its base)
      if (!hasTarget) cc.disp = (cc.disp || []).map(u => +(u * hc / H).toFixed(6));
      cc.height = +hc.toFixed(6);
      cc.node = roof.node;
    }
    if (po.distribution && (pc.load_distribution || "roof_point") === "roof_point")
      po.distribution.story_forces = { [roof.story]: 1.0 };
  }
  for (const [name, res] of Object.entries(r.nonlinear_static || {})) {
    const c = (model.nonlinear_static_cases || {})[name] || {};
    const hist = res && res.nonlinear && res.nonlinear.history;
    if (!c.control_tower || !hist || !hist.monitored) continue;
    const roof = towerRoofNode(model, r, c.control_tower);
    if (!roof) continue;
    const z0 = (hist.monitored.point || [0, 0, H])[2] || H;
    if (c.load_application !== "displacement_control") hist.disp = (hist.disp || []).map(u => u * roof.z / z0);
    hist.monitored = { node: roof.node, dof: hist.monitored.dof, point: roof.point };
  }
}

export function fuMockAugment(model, r) {
  if (!model || !r) return r;
  augmentTh(model, r);
  if (fuHas(model)) augmentControl(model, r);
  return r;
}

/* ------------------------------------------ database editing wrapper */
const towerCol = m => ({ key: "tower", label: "Tower", quantity: "none", unit: "", type: "enum",
  editable: true, enum: fuOf(m).map(t => t.name) });

function storyRowsWithTower(m, rows) {
  const map = fuMap(m);
  return (rows || []).map(r => Object.assign({}, r, { tower: map[r.name] || "" }));
}

function dbError(table, row, col, message) {
  const e = new Error(`1 error: ${message} [${table}${row != null ? ` row ${row + 1}` : ""}${col ? `, ${col}` : ""}]`);
  e.errors = [{ table, row, col, message }];
  return e;
}

/** Strip the tower cells of story rows (inner mock does not know them);
    returns [rows, towers-by-row-index]. */
function splitTower(m, rows) {
  const names = fuOf(m).map(t => t.name);
  const tw = [];
  const out = (rows || []).map((r, i) => {
    if (!r || typeof r !== "object" || !("tower" in r)) { tw.push(undefined); return r; }
    const v = r.tower == null ? "" : String(r.tower).trim();
    if (!names.includes(v)) throw dbError("stories", i, "tower", `Tower: '${v}' is not one of ${JSON.stringify(names)}`);
    tw.push(v);
    const o = Object.assign({}, r);
    delete o.tower;
    return o;
  });
  return [out, tw];
}

function applyTowerTags(model, tw) {
  if (!model || !fuHas(model)) return;
  const def = fuOf(model)[0].name;
  (model.stories || []).forEach((s, i) => {
    const v = tw[i];
    if (v === undefined || v === (s.tower || def)) return;     // unchanged cell: keep the stored tag
    if (!v || v === def) delete s.tower; else s.tower = v;
  });
  fuRecompute(model);                 // per-tower elevations + tower story lists
}

export async function fuMockDbEdit(model, path, body = {}, inner) {
  const m = body.model || model;
  if (!fuHas(m)) return inner(model, path, body);
  const p = path.replace(/^\/api\/modeltables\/?/, "");
  if (p === "apply" && body.tables && Array.isArray(body.tables.stories)) {
    const [rows, tw] = splitTower(m, body.tables.stories);
    const res = await inner(model, path, Object.assign({}, body, { tables: Object.assign({}, body.tables, { stories: rows }) }));
    applyTowerTags(res.model, tw);
    return res;
  }
  const [key, op] = p.split("/");
  if (decodeURIComponent(key || "") !== "stories") return inner(model, path, body);
  if (!op) {
    const out = await inner(model, path, body);
    out.columns = [...(out.columns || []), towerCol(m)];
    out.rows = storyRowsWithTower(m, out.rows);
    return out;
  }
  if (op === "apply") {
    const [rows, tw] = splitTower(m, body.rows || []);
    const res = await inner(model, path, Object.assign({}, body, { rows }));
    applyTowerTags(res.model, tw);
    return res;
  }
  if (op === "csv") {
    const out = await inner(model, path, body);
    const map = fuMap(m);
    const recs = fuParseCsv(out.csv || "");
    const lines = (out.csv || "").replace(/\n$/, "").split("\n");
    const ni = recs.length ? recs[0].indexOf("name") : -1;
    out.csv = lines.map((ln, i) => i === 0 ? `${ln},tower` : `${ln},${(ni >= 0 && recs[i] ? map[recs[i][ni]] : "") || ""}`).join("\n") + "\n";
    return out;
  }
  if (op === "csv_import") {
    const recs = fuParseCsv(body.csv || "");
    const ti = recs.length ? recs[0].findIndex(h => /^tower(\s*\[.*\])?$/i.test(h.trim())) : -1;
    const parsed = await inner(model, path, Object.assign({}, body, { apply: false }));
    let rows = parsed.rows || [];
    if (ti >= 0) {
      const data = recs.slice(1).filter(rec => rec.some(s => s.trim()));
      rows = rows.map((r, i) => (data[i] ? Object.assign({}, r, { tower: data[i][ti].trim() }) : r));
    }
    if (!body.apply) return { rows };
    return fuMockDbEdit(model, `/api/modeltables/stories/apply`, Object.assign({}, body, { rows }), inner);
  }
  return inner(model, path, body);
}
