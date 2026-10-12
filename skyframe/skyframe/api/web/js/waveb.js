/* SkyFrame — Wave B results UI (CONTRACT "Irregularity checks, grouped
   ASCE 7 combinations, E2K releases / joint axes, joint-local reactions,
   RS shell forces in combos, …").

     Display › Story Response › Irregularity Checks…   [dis-wb-irregularity]
        ASCE 7 Table 12.3-1 / 12.3-2, EN 1998-1 §4.2.3 and IS 1893 Tables
        5 / 6: summary per code, every check row (value / limit / status),
        discontinuities and per-story quantities; options = EC8 band and
        story shear-strength overrides (force units).  POST /api/irregularity.
     Display › Force Diagrams › Joint Reactions (Local Axes)…  [dis-wb-jlreact]
        F1..M3 of the joints with joint local axes, per case / combo.
        POST /api/results/joint-local-reactions.

   Read-only dialogs: the model is never written (OK/Close leaves the saved
   model byte-identical).  Every number goes through js/units.js.  ?mock=1
   uses js/mock_waveb.js.  One init line in app.js. */

import wbU from "./units.js";
import { el as wbEl, esc as wbEsc, dkGroup as wbGroup, dkTabs as wbTabs, dkNote as wbNote,
  dkDialog as wbDialog } from "./dlgkit.js";
import { wbMockIrregularity, wbMockJointLocal } from "./mock_waveb.js";

const WB_CODES = ["ASCE 7", "EN 1998-1", "IS 1893"];
const WB_STATE = { opts: { ec8_tol: 0.25, story_strength: {} }, lastCase: null };

function wbCss() {
  if (document.getElementById("wbCss")) return;
  const s = document.createElement("style");
  s.id = "wbCss";
  s.textContent = `
.wb-st { display: inline-block; padding: 1px 7px; border-radius: 9px; font-size: 11px; font-weight: 650; border: 1px solid var(--border); }
.wb-st.is-ok { color: var(--green); border-color: rgba(52,195,132,.4); }
.wb-st.is-irregular { color: var(--amber); background: var(--amber-ghost); border-color: rgba(229,165,10,.4); }
.wb-st.is-extreme { color: var(--red); background: rgba(230,103,103,.12); border-color: rgba(230,103,103,.4); }
.wb-st.is-na { color: var(--text-3); }
.wb-dlg .dk-table td.num { text-align: right; font-family: var(--mono); }
.wb-dlg .dk-table td.note { color: var(--text-3); max-width: 280px; }
.wb-dlg .wb-sum { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
@media (max-width: 760px) { .wb-dlg .wb-sum { grid-template-columns: 1fr; } }
.wb-dlg .wb-strength input { width: 90px; }
`;
  document.head.appendChild(s);
}

/* ------------------------------------------------------------ data access */
async function wbApi(sky, path, body) {
  const S = sky.store;
  if (S.mock) {
    await new Promise(r => setTimeout(r, 20));
    if (path === "/api/irregularity") return wbMockIrregularity(S.model, S.results || null, body.options || {});
    if (path === "/api/results/joint-local-reactions") return wbMockJointLocal(S.model, S.results || null, body.case ?? null);
    throw new Error(`no mock for ${path}`);
  }
  const payload = { ...(body || {}) };
  if (S.results) payload.results = S.results;
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
  return data;
}

const stChip = st => `<span class="wb-st is-${st === "n/a" ? "na" : wbEsc(st)}">${wbEsc(st)}</span>`;
const fmtRatio = v => v == null || !isFinite(v) ? "—" : (+v).toFixed(3);

function table(cols, rows, id) {
  const head = `<thead><tr>${cols.map(c => `<th>${c[0]}</th>`).join("")}</tr></thead>`;
  const body = rows.map(r => `<tr>${cols.map(c => c[1](r)).join("")}</tr>`).join("");
  const wrap = wbEl("div", { class: "dk-table-wrap" });
  const sc = wbEl("div", { class: "dk-table-scroll" });
  sc.innerHTML = `<table class="dk-table" id="${id}">${head}<tbody>${body || `<tr><td colspan="${cols.length}" class="muted">No rows.</td></tr>`}</tbody></table>`;
  wrap.appendChild(sc);
  return wrap;
}

/* --------------------------------------------------------- irregularity */
function checksTable(rows, code, id) {
  return table([
    ["Item", r => `<td>${wbEsc(r.item)}</td>`],
    ["Check", r => `<td>${wbEsc(r.check)}</td>`],
    ["Story", r => `<td>${wbEsc(r.story)}</td>`],
    ["Case", r => `<td>${wbEsc(r.case)}</td>`],
    ["Dir", r => `<td>${wbEsc(r.direction)}</td>`],
    ["Value", r => `<td class="num">${fmtRatio(r.value)}</td>`],
    ["Limit", r => `<td>${wbEsc(r.limit)}</td>`],
    ["Status", r => `<td>${stChip(r.status)}</td>`],
    ["Note", r => `<td class="note">${wbEsc(r.note)}</td>`],
  ], rows.filter(r => r.code === code), id);
}

function summaryBlock(rep) {
  const box = wbEl("div", { class: "wb-sum" });
  for (const code of WB_CODES) {
    const items = Object.entries((rep.summary || {})[code] || {});
    const t = table([["Item", ([k]) => `<td>${wbEsc(k)}</td>`], ["Worst", ([, s]) => `<td>${stChip(s)}</td>`]],
      items, "wbSum-" + code.replace(/[^A-Za-z0-9]/g, ""));
    box.appendChild(wbGroup(code, [t]));
  }
  return box;
}

function storiesBlock(rep) {
  const L = k => wbU.label(k);
  const t = table([
    ["Story", r => `<td>${wbEsc(r.story)}</td>`],
    [`Mass [${L("mass")}]`, r => `<td class="num">${wbU.fmt("mass", r.mass, 2)}</td>`],
    [`Dx [${L("length")}]`, r => `<td class="num">${r.Dx == null ? "—" : wbU.fmt("length", r.Dx, 2)}</td>`],
    [`Dy [${L("length")}]`, r => `<td class="num">${r.Dy == null ? "—" : wbU.fmt("length", r.Dy, 2)}</td>`],
    ["Cols / Braces / Walls", r => `<td class="num">${r.n_columns} / ${r.n_braces} / ${r.n_walls}</td>`],
    [`Vn,x [${L("force")}]`, r => `<td class="num">${r.Vn_x == null ? "n/a" : wbU.fmt("force", r.Vn_x, 0)}</td>`],
    [`Vn,y [${L("force")}]`, r => `<td class="num">${r.Vn_y == null ? "n/a" : wbU.fmt("force", r.Vn_y, 0)}</td>`],
    ["Strength", r => `<td>${wbEsc(r.strength_source)}</td>`],
  ], (rep.stories || []).slice().reverse(), "wbStories");
  const d = table([
    ["Story", r => `<td>${wbEsc(r.story)}</td>`], ["Element", r => `<td>${wbEsc(r.element)}</td>`],
    ["Type", r => `<td>${wbEsc(r.type)}</td>`], ["Kind", r => `<td>${wbEsc(r.kind.replace(/_/g, " "))}</td>`],
    [`Offset [${L("length")}]`, r => `<td class="num">${r.offset == null ? "—" : wbU.fmt("length", r.offset, 2)}</td>`],
    ["Offset / L", r => `<td class="num">${fmtRatio(r.ratio)}</td>`],
  ], rep.discontinuities || [], "wbDisc");
  return [wbGroup("Story quantities", [t]), wbGroup("Discontinuous vertical elements", [d])];
}

function optionsBlock(sky) {
  const o = WB_STATE.opts;
  const tol = wbEl("input", { type: "text", inputmode: "decimal", class: "dk-num", id: "wbEc8Tol", value: String(o.ec8_tol) });
  tol.addEventListener("change", () => { const v = parseFloat(tol.value); if (v > 0 && v < 1) o.ec8_tol = v; else tol.value = String(o.ec8_tol); });
  const tolRow = wbEl("div", { class: "dk-row" }, [wbEl("label", { class: "dk-label", for: "wbEc8Tol" }, ["EC8 4.2.3.3(3) band ±"]),
    wbEl("div", { class: "dk-ctl" }, [tol]), wbEl("span", { class: "dk-unit", text: "–" }),
    wbEl("div", { class: "dk-hint muted", html: "Upper/lower mass and stiffness ratio band (EC8 gives no number). Default 0.25" })]);
  const st = sky.store.model.stories || [];
  const fu = wbU.label("force");
  const tb = wbEl("table", { class: "dk-table wb-strength", id: "wbStrength" });
  tb.innerHTML = `<thead><tr><th>Story</th><th>Vn,x override [${wbEsc(fu)}]</th><th>Vn,y override [${wbEsc(fu)}]</th></tr></thead>`;
  const body = wbEl("tbody");
  for (const s of st.slice().reverse()) {
    const tr = wbEl("tr");
    tr.appendChild(wbEl("td", { text: s.name }));
    for (const d of ["x", "y"]) {
      const cur = ((o.story_strength || {})[s.name] || {})[d];
      const inp = wbEl("input", { type: "text", inputmode: "decimal", class: "dk-num", "data-story": s.name, "data-dir": d,
        placeholder: "estimated", value: cur == null ? "" : wbU.inputValue("force", cur) });
      inp.addEventListener("change", () => {
        const txt = inp.value.trim();
        const ss = o.story_strength[s.name] = o.story_strength[s.name] || {};
        if (txt === "") { delete ss[d]; inp.classList.remove("is-invalid"); }
        else {
          const v = wbU.parse("force", txt);
          if (isFinite(v) && v > 0) { ss[d] = v; inp.classList.remove("is-invalid"); } else inp.classList.add("is-invalid");
        }
        if (!Object.keys(ss).length) delete o.story_strength[s.name];
      });
      const td = wbEl("td"); td.appendChild(inp); tr.appendChild(td);
    }
    body.appendChild(tr);
  }
  tb.appendChild(body);
  const sc = wbEl("div", { class: "dk-table-scroll" }); sc.appendChild(tb);
  return wbGroup("Options", [tolRow, wbNote("Story shear strength: blank = estimate from the vertical elements (nominal, no reinforcement; n/a when f′c / Fy is not set)."), sc]);
}

/** Options as sent to the backend: ec8_tol + only the complete overrides. */
function wbOptions() {
  const o = WB_STATE.opts, ss = {};
  for (const [k, v] of Object.entries(o.story_strength || {})) if (v.x != null || v.y != null) ss[k] = { ...v };
  return Object.keys(ss).length ? { ec8_tol: o.ec8_tol, story_strength: ss } : { ec8_tol: o.ec8_tol };
}

export async function openIrregularity(sky) {
  wbCss();
  const out = wbEl("div", { id: "wbIrrOut" });
  const run = async () => {
    out.innerHTML = `<p class="muted">Computing…</p>`;
    try {
      const rep = await wbApi(sky, "/api/irregularity", { options: wbOptions() });
      out.innerHTML = "";
      if (!rep.has_results) out.appendChild(wbNote("No analysis results: only the model-based checks are evaluated (run the analysis for soft story, torsion and modal checks).", "wb-nores"));
      const tabs = wbTabs([
        { id: "sum", label: "Summary", body: summaryBlock(rep) },
        ...WB_CODES.map(c => ({ id: c.replace(/[^A-Za-z0-9]/g, ""), label: c, body: checksTable(rep.checks || [], c, "wbChecks-" + c.replace(/[^A-Za-z0-9]/g, "")) })),
        { id: "stories", label: "Stories", body: wbEl("div", {}, storiesBlock(rep)) },
      ], { id: "wbIrrTabs" });
      out.appendChild(tabs);
      for (const w of rep.warnings || []) out.appendChild(wbNote("⚠ " + wbEsc(w)));
      sky.wbLastIrregularity = rep;
    } catch (e) {
      out.innerHTML = "";
      out.appendChild(wbNote("Could not compute the checks: " + wbEsc(e.message), "dk-err-note"));
    }
  };
  const btn = wbEl("button", { type: "button", class: "btn", id: "wbIrrCompute", title: "Recompute with the options above" }, ["Compute"]);
  btn.addEventListener("click", run);
  const d = wbDialog({
    id: "wbIrrDlg", title: "Irregularity Checks", size: "lg", noCancel: true,
    intro: "ASCE 7 Tables 12.3-1 / 12.3-2, EN 1998-1 §4.2.3 and IS 1893 Tables 5 / 6. Pure post-processing: the model is not changed.",
    body: [optionsBlock(sky), out], extra: [btn],
    footNote: "Response checks use the lateral (quake / wind) load cases.",
  });
  d.el.classList.add("wb-dlg");
  await run();
  return d;
}

/* ------------------------------------------------- joint-local reactions */
export async function openJointLocalReactions(sky) {
  wbCss();
  const out = wbEl("div", { id: "wbJlrOut" });
  const sel = wbEl("select", { id: "wbJlrCase" });
  const render = data => {
    const per = (data.cases || {})[sel.value] || {};
    const L = k => wbEsc(wbU.label(k));
    const rows = Object.entries(per).map(([t, e]) => ({ t, ...e }));
    out.innerHTML = "";
    out.appendChild(table([
      ["Joint", r => `<td>${wbEsc(r.t)}</td>`],
      [`X [${L("length")}]`, r => `<td class="num">${wbU.fmt("length", r.point[0], 2)}</td>`],
      [`Y [${L("length")}]`, r => `<td class="num">${wbU.fmt("length", r.point[1], 2)}</td>`],
      [`Z [${L("length")}]`, r => `<td class="num">${wbU.fmt("length", r.point[2], 2)}</td>`],
      ["Angles A / B / C [deg]", r => `<td class="num">${(r.angles || []).map(v => (+v).toFixed(1)).join(" / ")}</td>`],
      ...["F1", "F2", "F3", "M1", "M2", "M3"].map((k, i) => [`${k} [${L(i < 3 ? "force" : "moment")}]`,
        r => `<td class="num">${wbU.fmt(i < 3 ? "force" : "moment", r.local[i], 2)}</td>`]),
      ...["FX", "FY", "FZ"].map((k, i) => [`${k} [${L("force")}]`, r => `<td class="num muted">${wbU.fmt("force", r.global[i], 2)}</td>`]),
    ], rows, "wbJlrTable"));
    if (!rows.length) out.appendChild(wbNote("No reactions at joints with local axes in this case."));
  };
  let data = { cases: {} };
  try { data = await wbApi(sky, "/api/results/joint-local-reactions", {}); }
  catch (e) { out.appendChild(wbNote("Could not read the reactions: " + wbEsc(e.message))); }
  const names = Object.keys(data.cases || {});
  for (const n of names) sel.appendChild(wbEl("option", { value: n, text: n }));
  if (WB_STATE.lastCase && names.includes(WB_STATE.lastCase)) sel.value = WB_STATE.lastCase;
  sel.addEventListener("change", () => { WB_STATE.lastCase = sel.value; render(data); });
  const pick = wbEl("div", { class: "dk-row" }, [wbEl("label", { class: "dk-label", for: "wbJlrCase" }, ["Load case / combo"]),
    wbEl("div", { class: "dk-ctl" }, [sel]), wbEl("span", { class: "dk-unit", text: "" })]);
  const d = wbDialog({
    id: "wbJlrDlg", title: "Joint Reactions — Joint Local Axes", size: "lg", noCancel: true,
    intro: "Support reactions resolved on the joint local axes 1 / 2 / 3 (Assign › Joint › Local Axes): F_local = Rᵀ·F_global. Static cases and single-valued combinations.",
    body: names.length ? [wbGroup("Output", [pick]), out]
      : [wbNote((sky.store.model.joint_local_axes || []).length ? "No reactions at joints with local axes — run the analysis first (F5)." : "No joint has local axes: Assign › Joint › Local Axes…"), out],
  });
  d.el.classList.add("wb-dlg");
  if (names.length) render(data);
  return d;
}

/* ------------------------------------------------------------- installer */
export function initWaveB(sky) {
  if (!sky) return;
  sky.wbOpenIrregularity = () => openIrregularity(sky);
  sky.wbOpenJointLocalReactions = () => openJointLocalReactions(sky);
  if (!sky.menu || !sky.menu.add) return;
  try {
    sky.menu.add("Display/Story Response", { label: "Irregularity Checks…", act: "dis-wb-irregularity",
      fn: () => openIrregularity(sky),
      hint: "ASCE 7 Tables 12.3-1/-2, EN 1998-1 §4.2.3 and IS 1893 Tables 5/6 regularity checks" });
  } catch (e) { console.warn("menu add dis-wb-irregularity", e); }
  try {
    sky.menu.add("Display/Force Diagrams", { label: "Joint Reactions (Local Axes)…", act: "dis-wb-jlreact", needs: "results",
      fn: () => openJointLocalReactions(sky),
      hint: "Reactions F1..M3 in the joint local axes of joints with Assign › Joint › Local Axes" });
  } catch (e) { console.warn("menu add dis-wb-jlreact", e); }
}
