/* SkyFrame — Wave C: Tools › Compare Results… (CONTRACT "Results comparison
   (Compare Results)").

   Compares two analysis runs or saved result files value by value and lists
   the differences above a relative tolerance (core/results_diff.py through
   POST /api/results/compare; ?mock=1: js/mock_wavec.js wcMockDiff).

     runs     Current results · earlier runs of this session (the last 5
              results are kept automatically when a new analysis finishes)
              · result files loaded from disk (.json: a results object or
              {"results": …}, e.g. saved with "Save Current Results…")
     filter   case / combo · quantity (node_disp, member_forces, base,
              story, member_stations, shell_forces, piers, section_cuts, …)
     table    Case · Quantity · Item · A · B · Δ · relative difference, every
              value in the current display units (js/units.js)

   Read-only: the model and the results are never modified. */

import cmU from "./units.js";
import { el as cmEl, esc as cmEsc, dkForm as cmForm, dkGroup as cmGroup, dkDialog as cmDialog, dkNote as cmNote } from "./dlgkit.js";
import { wcMockDiff as cmMockDiff, wcMockCatalogue as cmMockCatalogue } from "./mock_wavec.js";

const MAX_HISTORY = 5;
const runs = { history: [], files: [], last: null, seq: 0 };
export const wcCompareState = { a: "", b: "current", case: "", quantity: "", rtol: 1e-6, lastResult: null };

const CSS = `
.dk-dlg .wcc-out { margin-top: 8px; }
.dk-dlg .wcc-sum { font-size: 12px; margin: 4px 0 6px; }
.dk-dlg .wcc-sum.is-eq { color: var(--ok, #34c384); }
.dk-dlg .wcc-sum.is-diff { color: var(--warn, #e0b050); }
.dk-dlg .wcc-wrap { max-height: 300px; overflow: auto; border: 1px solid var(--border-soft, #333); border-radius: 4px; }
.dk-dlg .wcc-table { width: 100%; border-collapse: collapse; font-size: 11.5px; font-variant-numeric: tabular-nums; }
.dk-dlg .wcc-table th, .dk-dlg .wcc-table td { padding: 3px 6px; border-bottom: 1px solid rgba(128,128,128,.18); text-align: right; white-space: nowrap; }
.dk-dlg .wcc-table th { position: sticky; top: 0; background: var(--panel, #1b2129); text-align: right; }
.dk-dlg .wcc-table .txt { text-align: left; }
.dk-dlg .wcc-btns { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; }
`;
function ensureCss() {
  if (document.getElementById("wccCss")) return;
  const s = document.createElement("style"); s.id = "wccCss"; s.textContent = CSS; document.head.appendChild(s);
}

/* ---------------------------------------------------------------- runs */
function stamp() { const d = new Date(); return d.toTimeString().slice(0, 8); }
function track(sky) {
  const r = sky.store.results;
  if (r === runs.last) return;
  if (runs.last) {
    runs.seq += 1;
    runs.history.unshift({ id: `run${runs.seq}`, label: `Previous run ${runs.seq} · ${runs.lastStamp || ""}`, results: runs.last });
    runs.history.length = Math.min(runs.history.length, MAX_HISTORY);
  }
  runs.last = r;
  runs.lastStamp = stamp();
}
function runOptions(sky) {
  const out = [];
  if (sky.store.results) out.push(["current", "Current results"]);
  for (const h of runs.history) out.push([h.id, h.label]);
  for (const f of runs.files) out.push([f.id, `File: ${f.label}`]);
  return out;
}
function runResults(sky, id) {
  if (id === "current") return sky.store.results;
  const h = runs.history.find(x => x.id === id) || runs.files.find(x => x.id === id);
  return h ? h.results : null;
}
export function wcAddResultFile(name, obj) {
  const id = `file${runs.files.length + 1}`;
  runs.files.push({ id, label: name, results: obj });
  return id;
}

/* ---------------------------------------------------------------- units */
export function wcValueKind(row) {
  const q = String(row.quantity || "").replace(/^min\./, ""), it = String(row.item || "").split(".");
  const last = it[it.length - 1], idx = +last;
  const fm = k => /^(F|P|V|N)/.test(k) ? "force" : /^(M|T)/.test(k) ? "moment" : "none";
  switch (q) {
    case "node_disp": return idx % 6 < 3 ? "disp" : "rotation";
    case "reactions": case "member_forces": return idx % 6 < 3 ? "force" : "moment";
    case "base": case "base_FX": case "base_FY": return q.startsWith("base_") ? "force" : fm(last);
    case "story": return /^u[xy]$/.test(last) ? "disp" : /^shear/.test(last) ? "force" : "none";
    case "story_ux": case "story_uy": return "disp";
    case "member_stations": { const k = it[it.length - 2] || ""; return k === "x" ? "length" : fm(k); }
    case "member_deflections": return it[it.length - 2] === "x" ? "length" : "disp";
    case "shell_forces": return (idx % 8) >= 3 && (idx % 8) <= 5 ? "line_moment" : "line_force";
    case "section_cuts": return fm(it.find(k => /^[FM][XYZ]$/.test(k)) || "");
    case "piers": { const k = it.find(x => ["P", "V", "M"].includes(x)) || ""; return k === "M" ? "moment" : k ? "force" : "none"; }
    case "spandrels": { const k = it.find(x => ["P", "V2", "V3", "T", "M2", "M3", "length"].includes(x)) || ""; return k === "length" ? "length" : fm(k); }
    case "modal": return /periods/.test(row.item) ? "period" : "none";
    default: return "none";
  }
}
function fmtVal(kind, v) {
  if (v == null) return "—";
  if (typeof v !== "number") return cmEsc(String(v));
  return cmEsc(kind === "none" ? (+v.toPrecision(6)).toString() : cmU.fmt(kind, v, 3));
}

/* ---------------------------------------------------------------- compare */
export async function wcCompare(sky, o) {
  const a = runResults(sky, o.a), b = runResults(sky, o.b);
  if (!a || !b) throw new Error("Pick two runs (run the analysis, or load a result file).");
  const body = { case: o.case || "", quantity: o.quantity || "", rtol: +o.rtol, max_rows: 500 };
  if (sky.store.mock) return cmMockDiff(a, b, body);
  const res = await fetch("/api/results/compare", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, a, b }) });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
  return data;
}

function renderOut(out, res) {
  out.textContent = "";
  const n = res.n_flagged;
  out.appendChild(cmEl("div", { class: "wcc-sum " + (res.equal ? "is-eq" : "is-diff"), id: "wcCmpSummary",
    text: res.equal ? `Identical within tolerance — ${res.n_compared} values compared.` :
      `${n} difference${n === 1 ? "" : "s"} in ${res.n_compared} values compared · largest relative ${(+res.worst_rel).toExponential(2)}${res.truncated ? " · first 500 shown" : ""}` }));
  if (!res.rows.length) return;
  const head = `<thead><tr><th class="txt">Case</th><th class="txt">Quantity</th><th class="txt">Item</th>
    <th>A</th><th>B</th><th>Δ</th><th>Rel.</th><th class="txt">Unit</th></tr></thead>`;
  const body = res.rows.map(r => {
    const k = wcValueKind(r);
    const status = r.status === "changed" ? "" : ` (${r.status.replace("_", " ")})`;
    const diff = r.status === "changed" && typeof r.a === "number" ? fmtVal(k, r.b - r.a) : "—";
    return `<tr data-path="${cmEsc(r.path)}"><td class="txt">${cmEsc(r.case || r.block)}</td><td class="txt">${cmEsc(r.quantity)}${cmEsc(status)}</td>
      <td class="txt">${cmEsc(r.item)}</td><td>${r.status === "changed" ? fmtVal(k, r.a) : cmEsc(String(r.a ?? "—"))}</td>
      <td>${r.status === "changed" ? fmtVal(k, r.b) : cmEsc(String(r.b ?? "—"))}</td><td>${diff}</td>
      <td>${r.rel != null && r.status === "changed" ? (+r.rel).toExponential(2) : "—"}</td><td class="txt">${cmEsc(cmU.label(k) || "–")}</td></tr>`;
  }).join("");
  const wrap = cmEl("div", { class: "wcc-wrap" });
  wrap.innerHTML = `<table class="wcc-table" id="wcCmpTable">${head}<tbody>${body}</tbody></table>`;
  out.appendChild(wrap);
}

export function wcOpenCompare(sky) {
  ensureCss();
  track(sky);
  const st = wcCompareState;
  const opts = () => runOptions(sky);
  const ids = opts().map(x => x[0]);
  if (!ids.includes(st.b)) st.b = ids[0] || "";
  if (!ids.includes(st.a)) st.a = ids.find(x => x !== st.b) || st.b;
  const cat = () => {
    const a = runResults(sky, st.a), b = runResults(sky, st.b);
    return cmMockCatalogue(a || {}, b || {});
  };
  const F = cmForm();
  const out = cmEl("div", { class: "wcc-out", id: "wcCmpOut" });
  const file = cmEl("input", { type: "file", accept: ".json,application/json", id: "wcCmpFile", style: "display:none" });
  const loadBtn = cmEl("button", { type: "button", class: "btn", id: "wcCmpLoad" }, ["Load Result File…"]);
  const saveBtn = cmEl("button", { type: "button", class: "btn", id: "wcCmpSave" }, ["Save Current Results…"]);
  loadBtn.addEventListener("click", () => file.click());
  file.addEventListener("change", async () => {
    const f = file.files && file.files[0];
    if (!f) return;
    try {
      const obj = JSON.parse(await f.text());
      if (!obj || typeof obj !== "object") throw new Error("not a JSON object");
      const id = wcAddResultFile(f.name, obj);
      st.a = id;
      F.refresh();
      d.setError("");
    } catch (e) { d.setError(`Could not read ${f.name}: ${e.message}`); }
    file.value = "";
  });
  saveBtn.addEventListener("click", () => {
    const r = sky.store.results;
    if (!r) { d.setError("No current results — run the analysis first."); return; }
    const name = String((sky.store.model && sky.store.model.name) || "model").replace(/[^\w.-]+/g, "_");
    const blob = new Blob([JSON.stringify({ model_name: name, saved: new Date().toISOString(), results: r })], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = `${name}_results.json`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  });
  const body = [
    cmGroup("Runs", [
      F.select(st, "a", opts, { label: "Run A (reference)", id: "wcCmpA", onChange: () => F.refresh() }),
      F.select(st, "b", opts, { label: "Run B", id: "wcCmpB", onChange: () => F.refresh() }),
      cmEl("div", { class: "wcc-btns" }, [loadBtn, saveBtn, file]),
    ]),
    cmGroup("Compare", [
      F.select(st, "case", () => [["", "(all cases and combos)"], ...cat().cases.map(n => [n, n])], { label: "Case / combo", id: "wcCmpCase" }),
      F.select(st, "quantity", () => [["", "(all quantities)"], ...cat().quantities.map(n => [n, n])], { label: "Quantity", id: "wcCmpQty" }),
      F.num(st, "rtol", { label: "Relative tolerance", min: 0, id: "wcCmpRtol", hint: "|A − B| ≤ tol × the largest value of that quantity" }),
    ]),
    out,
    cmNote("Run A is the reference. Earlier runs of this session are kept automatically (last 5)."),
  ];
  const d = cmDialog({
    id: "wcCompareDlg", title: "Compare Results", size: "lg", form: F, body, okLabel: "Compare", cancelLabel: "Close",
    intro: "Compare two analysis runs or saved result files, value by value.",
    onOk: async () => {
      try {
        const res = await wcCompare(sky, st);
        st.lastResult = res;
        renderOut(out, res);
      } catch (e) { return e.message || String(e); }
      return false;                                     // stay open
    },
  });
  return d;
}

export function initWcCompare(sky) {
  track(sky);
  document.addEventListener("sky:results-changed", () => track(sky));
  sky.wcCompare = { open: () => wcOpenCompare(sky), compare: o => wcCompare(sky, o), addFile: wcAddResultFile,
    runs, state: wcCompareState, valueKind: wcValueKind };
}
