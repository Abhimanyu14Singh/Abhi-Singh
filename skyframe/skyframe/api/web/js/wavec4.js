/* SkyFrame — Wave C response-spectrum tools (CONTRACT "Wave C: regional
   seismic / wind generators, code spectra, spectrum file import, USGS lookup
   and regional material / steel libraries").

     Define › Functions › Response Spectrum From File…   (dsRsFnFile)
         paste or open a period–value file; period in s or a frequency in Hz,
         values in g or an acceleration unit; header lines and a damping line
         are recognised.  Live: POST /api/function/spectrum-file/preview;
         ?mock=1: the JS port in js/mock_wavec_spectra.js.
     Define › Functions › USGS Seismic Site Lookup…      (dsRsFnUsgs)
         optional online lookup (POST /api/usgs/designmaps).  When the
         service is unreachable the dialog says so and the manual SDS / SD1
         entry stays available; either way it writes an ASCE 7-22 spectrum
         function (multi-period from the USGS ordinates, else two-period).

   Both are also buttons in Define › Functions (js/dlgstd_defs.js hook).
   Every dialog edits a draft; OK with nothing changed writes nothing. */

import {
  el as wvcEl, dkForm as wvcForm, dkGroup as wvcGroup, dkNote as wvcNote, dkDialog as wvcDialog,
  dkDraft as wvcDraft, dkCommit as wvcCommit, uniqueName as wvcUnique,
} from "./dlgkit.js";
import { wvcParseSpectrum as wvcParse, wvcMockUsgs } from "./mock_wavec_spectra.js";
import { lcSpectrum as wvcLcSpectrum } from "./loadcodes.js";

const isMock = sky => !!(sky && sky.store && sky.store.mock);
async function wvcPost(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) { const e = new Error((data && data.error) || `${res.status} ${res.statusText}`); e.status = res.status; e.manual = !!(data && data.manual); throw e; }
  return data;
}
const fmt = (v, d = 4) => v == null || !isFinite(v) ? "—" : String(+(+v).toPrecision(d));

function spark(points, id) {
  const W = 420, H = 140, P = 24;
  const box = wvcEl("div", { class: "dk-chart", id });
  if (!points || !points.length) { box.appendChild(wvcEl("p", { class: "muted dk-empty", text: "No points" })); return box; }
  const x1 = Math.max(...points.map(p => p[0])) || 1, y1 = Math.max(...points.map(p => p[1])) || 1;
  const sx = x => P + x / x1 * (W - 2 * P), sy = y => H - P + 6 - y / y1 * (H - 2 * P);
  const d = points.map((p, i) => `${i ? "L" : "M"}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join("");
  box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Spectrum plot"><line x1="${P}" y1="${H - P + 6}" x2="${W - P}" y2="${H - P + 6}" stroke="var(--border)"/>
    <line x1="${P}" y1="8" x2="${P}" y2="${H - P + 6}" stroke="var(--border)"/><path d="${d}" fill="none" stroke="var(--accent)" stroke-width="1.6"/>
    <text x="${W - P}" y="${H - 4}" text-anchor="end" font-size="10" fill="var(--text-3)">T (s) ${+x1.toPrecision(3)}</text>
    <text x="${P + 4}" y="16" font-size="10" fill="var(--text-3)">Sa (g) ${+y1.toPrecision(3)}</text></svg>`;
  return box;
}

/** Draft handling: inside Define › Functions the caller's draft is used;
    from the menu a fresh draft is committed on OK. */
function useDraft(sky, D, done) {
  const own = !D;
  const draft = D || wvcDraft(sky);
  draft.spectrum_functions = draft.spectrum_functions || {};
  return { D: draft, finish: nm => { if (own) wvcCommit(sky, draft); done && done(nm); } };
}

/* ================================================================ file import */
export function wvcOpenSpectrumFile(sky, D0, { done } = {}) {
  const { D, finish } = useDraft(sky, D0, done);
  const st = { name: wvcUnique(D.spectrum_functions, "SPEC-FILE"), text: "", period_units: "s", value_units: "g", skip_lines: 0, scale: 1, damping: null };
  const F = wvcForm();
  let last = null, seq = 0;
  const err = wvcEl("div", { class: "dk-err", role: "alert", id: "dsRsFnFileErr" });
  const summary = wvcEl("div", { class: "muted dk-note", id: "dsRsFnFileSummary" });
  const chartHost = wvcEl("div");
  const ta = wvcEl("textarea", { id: "dsRsFnFileText", rows: "8", spellcheck: "false", class: "dk-textarea",
    placeholder: "Period  Value\n0.0     0.40\n0.2     1.00\n1.0     0.60\n…", style: "width:100%;font-family:var(--mono,monospace)" });
  const fileIn = wvcEl("input", { type: "file", id: "dsRsFnFileInput", accept: ".txt,.csv,.dat,.tsv,text/plain" });
  const preview = async () => {
    const my = ++seq;
    st.text = ta.value;
    if (!st.text.trim()) { last = null; err.textContent = ""; summary.textContent = "Paste or open a file."; chartHost.textContent = ""; return; }
    const args = { period_units: st.period_units, value_units: st.value_units, skip_lines: st.skip_lines | 0, scale: st.scale };
    try {
      const r = isMock(sky) ? wvcParse(st.text, args) : await wvcPost("/api/function/spectrum-file/preview", { text: st.text, ...args });
      if (my !== seq) return;
      last = r; err.textContent = "";
      chartHost.textContent = ""; chartHost.appendChild(spark(r.points, "dsRsFnFileChart"));
      summary.textContent = `${r.n} points · T ${fmt(r.points[0][0])}–${fmt(r.points[r.n - 1][0])} s · peak Sa ${fmt(Math.max(...r.points.map(p => p[1])))} g` +
        ` · ${r.header_lines} header line(s)` + (r.damping != null ? ` · damping in file ${fmt(100 * r.damping)} %` : "");
    } catch (e) { if (my !== seq) return; last = null; err.textContent = e.message || String(e); }
  };
  ta.addEventListener("input", () => { F.touched = true; preview(); });
  fileIn.addEventListener("change", async () => {
    const f = fileIn.files && fileIn.files[0];
    if (!f) return;
    ta.value = await f.text(); F.touched = true;
    if (/\.(txt|csv|dat|tsv)$/i.test(f.name) && st.name.startsWith("SPEC-FILE")) {
      const nm = f.name.replace(/\.[^.]+$/, "").replace(/["\\]/g, "").slice(0, 40) || st.name;
      if (!D.spectrum_functions[nm]) { st.name = nm; F.refresh(); }
    }
    preview();
  });
  const body = [
    wvcGroup("Function", [
      F.text(st, "name", { label: "Function Name", id: "dsRsFnFileName",
        validate: v => Object.prototype.hasOwnProperty.call(D.spectrum_functions, v) ? `“${v}” is already used — pick another name.` : (/["\\]/.test(v) ? "Names cannot contain quotes or backslashes." : "") }),
      F.num(st, "damping", { label: "Function damping ratio", min: 0, lt: 1, allowNull: true, nullText: "from file / 0.05", id: "dsRsFnFileDamp" }),
    ]),
    wvcGroup("File", [
      F.row("Open file", fileIn, { id: "dsRsFnFileInput", unit: "" }),
      ta,
      F.select(st, "period_units", [["s", "Period (s)"], ["Hz", "Frequency (Hz)"]], { label: "First column", id: "dsRsFnFilePer", onChange: () => preview() }),
      F.select(st, "value_units", [["g", "g"], ["m/s2", "m/s²"], ["cm/s2", "cm/s² (gal)"], ["mm/s2", "mm/s²"], ["ft/s2", "ft/s²"], ["in/s2", "in/s²"]],
        { label: "Values in", id: "dsRsFnFileVal", onChange: () => preview() }),
      F.num(st, "skip_lines", { label: "Header lines to skip", min: 0, int: true, id: "dsRsFnFileSkip", onChange: () => preview() }),
      F.num(st, "scale", { label: "Scale factor", gt: 0, id: "dsRsFnFileScale", onChange: () => preview() }),
    ]),
    wvcGroup("Spectrum", [chartHost, summary, err]),
  ];
  return wvcDialog({
    id: "dsRsFnFile", title: "Response Spectrum Function — From File", iconId: "exleaf-functions", size: "lg", form: F, body,
    intro: "Reads period–value pairs (comma, tab, space or semicolon separated). Lines before the data are treated as a header; a “damping 5%” line sets the damping.",
    onOk: async () => {
      if (!F.touched) { done && done(null); return; }
      await preview();
      if (!last) return err.textContent || "Paste or open a spectrum file first.";
      const nm = String(st.name).trim();
      const damping = st.damping != null ? st.damping : (last.damping || 0.05);
      D.spectrum_functions[nm] = { name: nm, points: last.points, damping };
      finish(nm);
    },
  });
}

/* ================================================================ USGS lookup */
const SITE22 = ["A", "B", "BC", "C", "CD", "D", "DE", "E", "Default"], SITE16 = ["A", "B", "C", "D", "E", "Default"];
export function wvcOpenUsgs(sky, D0, { done } = {}) {
  const { D, finish } = useDraft(sky, D0, done);
  const st = { name: wvcUnique(D.spectrum_functions, "SPEC-USGS"), latitude: 37.7749, longitude: -122.4194, edition: "asce7-22",
    site_class: "D", risk_category: "II", SDS: 1.0, SD1: 0.6, TL: 8 };
  const F = wvcForm();
  let res = null;
  const status = wvcEl("div", { class: "muted dk-note", id: "dsRsFnUsgsStatus", text: "Enter the site and press Look Up, or type SDS / SD1 below." });
  const out = wvcEl("div", { id: "dsRsFnUsgsOut" });
  const btn = wvcEl("button", { type: "button", class: "btn btn-small", id: "dsRsFnUsgsGo" }, ["Look Up"]);
  btn.addEventListener("click", async () => {
    const ae = document.activeElement; if (ae && ae.blur) ae.blur();
    btn.disabled = true; status.textContent = "Contacting the USGS design-maps service…"; out.textContent = "";
    try {
      const body = { latitude: st.latitude, longitude: st.longitude, edition: st.edition, site_class: st.site_class, risk_category: st.risk_category };
      res = isMock(sky) ? await wvcMockUsgs(body) : await wvcPost("/api/usgs/designmaps", body);
      if (res && res.ok === false) throw new Error(res.error || "USGS lookup failed");
      const v = res.values || {};
      status.textContent = `USGS ${res.edition}: ` + ["SS", "S1", "SDS", "SD1", "TL"].filter(k => v[k] != null).map(k => `${k} ${fmt(v[k])}`).join(" · ") +
        (res.mprs_pairs ? ` · ${res.mprs_pairs.length} multi-period ordinates` : "");
      if (v.SDS != null) st.SDS = v.SDS;
      if (v.SD1 != null) st.SD1 = v.SD1;
      if (v.TL != null) st.TL = v.TL;
      F.touched = true; F.refresh();
      if (res.mprs_pairs) out.appendChild(spark(res.mprs_pairs, "dsRsFnUsgsChart"));
    } catch (e) {
      res = null;
      status.innerHTML = "";
      status.appendChild(wvcEl("span", { class: "dk-err", role: "alert", id: "dsRsFnUsgsErr", text: (e.message || String(e)) }));
      status.appendChild(wvcEl("span", { text: " — the manual SDS / SD1 entry below still works." }));
    } finally { btn.disabled = false; }
  });
  const siteSel = () => (st.edition === "asce7-22" ? SITE22 : SITE16).map(s => [s, s]);
  const body = [
    wvcGroup("Function", [
      F.text(st, "name", { label: "Function Name", id: "dsRsFnUsgsName",
        validate: v => Object.prototype.hasOwnProperty.call(D.spectrum_functions, v) ? `“${v}” is already used — pick another name.` : "" }),
    ]),
    wvcGroup("Site (online lookup — optional)", [
      F.num(st, "latitude", { label: "Latitude (°)", min: -90, max: 90, id: "dsRsFnUsgsLat" }),
      F.num(st, "longitude", { label: "Longitude (°)", min: -180, max: 180, id: "dsRsFnUsgsLon" }),
      F.select(st, "edition", [["asce7-22", "ASCE 7-22"], ["asce7-16", "ASCE 7-16"]], { label: "Edition", id: "dsRsFnUsgsEd" }),
      F.select(st, "site_class", siteSel(), { label: "Site class", id: "dsRsFnUsgsSite" }),
      F.select(st, "risk_category", [["I", "I"], ["II", "II"], ["III", "III"], ["IV", "IV"]], { label: "Risk category", id: "dsRsFnUsgsRisk" }),
      btn, status, out,
    ]),
    wvcGroup("Design values (manual entry or filled by the lookup)", [
      wvcNote("Without the multi-period ordinates the function is the ASCE 7-22 §11.4.5.2 two-period spectrum from SDS / SD1."),
      F.num(st, "SDS", { label: "SDS", unit: "g", gt: 0, id: "dsRsFnUsgsSDS" }),
      F.num(st, "SD1", { label: "SD1", unit: "g", min: 0, id: "dsRsFnUsgsSD1" }),
      F.num(st, "TL", { label: "Long-period TL", kind: "period", gt: 0, id: "dsRsFnUsgsTL" }),
    ]),
  ];
  return wvcDialog({
    id: "dsRsFnUsgs", title: "USGS Seismic Site Lookup", iconId: "exleaf-functions", size: "md", form: F, body,
    intro: "Optionally queries the USGS Seismic Design Web Services for the site. Offline or blocked networks are reported and the values can be entered by hand.",
    onOk: async () => {
      if (!F.touched) { done && done(null); return; }
      const mprs = res && res.mprs_pairs && st.edition === "asce7-22";
      const code = mprs ? "asce7_22_mprs" : "asce7_22_two_period";
      const params = mprs ? { ordinates: res.mprs_pairs, basis: "design", TL: st.TL } : { SDS: st.SDS, SD1: st.SD1, TL: st.TL };
      let r;
      try { r = await wvcLcSpectrum(sky, code, params); } catch (e) { return e.message || String(e); }
      const nm = String(st.name).trim();
      D.spectrum_functions[nm] = { name: nm, points: r.points, damping: 0.05, source: { code, params: r.params } };
      finish(nm);
    },
  });
}

export function initWaveC(sky) {
  if (!sky) return;
  sky.wvcOpenSpectrumFile = (D, o) => wvcOpenSpectrumFile(sky, D, o);
  sky.wvcOpenUsgs = (D, o) => wvcOpenUsgs(sky, D, o);
  if (!sky.menu || !sky.menu.add) return;
  try {
    sky.menu.add("Define/Functions", { label: "Response Spectrum From File…", act: "def-wvc-rsfile", fn: () => wvcOpenSpectrumFile(sky, null),
      hint: "Period–value pairs from a text / CSV file (s or Hz; g or acceleration units)" });
    sky.menu.add("Define/Functions", { label: "USGS Seismic Site Lookup…", act: "def-wvc-usgs", fn: () => wvcOpenUsgs(sky, null),
      hint: "Optional online USGS design-maps lookup → ASCE 7-22 spectrum function (manual entry when offline)" });
  } catch (e) { console.warn("menu add def-wvc", e); }
}
