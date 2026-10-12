/* SkyFrame — ?mock=1 stand-in for the CSI / steel interop endpoints
   (POST /api/interop/export|import/<fmt>, CONTRACT "SAP2000 / SAFE / steel
   interop").

   Exports are small but real files built from the store model: s2k / f2k
   CSI tables (PROGRAM CONTROL, JOINT COORDINATES, CONNECTIVITY - FRAME …),
   SDNF Packet 00 / 10, an IDEA OpenModel XML (sent as plain text in mock),
   and a minimal IFC4 header for the Revit export. Every mock file carries
   the exact model JSON in a comment line ("$SF-MOCK <b64>" / "# SF-MOCK" /
   "<!-- SF-MOCK -->" / STEP comment) so mock export -> import round-trips
   byte-identically; foreign files need the live server. */

const imN = v => { const s = String(+(+v).toPrecision(15)); return s === "-0" ? "0" : s; };
const imB64 = str => btoa(unescape(encodeURIComponent(str)));
const imUnB64 = b64 => decodeURIComponent(escape(atob(b64)));
const imQ = s => /[\s"=]/.test(String(s)) || s === "" ? `"${String(s).replace(/"/g, "'")}"` : String(s);
const IM_UNITS = { "KN,M": 1, "KN,MM": 0.001, "N,MM": 0.001, "KIP,IN": 0.0254, "KIP,FT": 0.3048 };

// same spelling as the live writer (skyframe/io/csi_tables.units_field)
function imUnitsField(units) {
  const [f, l] = (units || ["KN", "M"]).map(v => String(v).toUpperCase());
  const F = { KN: "KN", N: "N", KIP: "Kip", LB: "lb", KGF: "Kgf", TONF: "Tonf" }[f] || f;
  const L = { M: "m", MM: "mm", CM: "cm", FT: "ft", IN: "in" }[l] || l;
  return `${F}, ${L}, ${l === "FT" || l === "IN" ? "F" : "C"}`;
}

function imJoints(m) {
  const pts = new Map();
  const key = p => p.map(v => imN(v)).join(",");
  for (const mb of m.members || []) for (const p of [mb.pi, mb.pj]) if (!pts.has(key(p))) pts.set(key(p), [String(pts.size + 1), p]);
  return { pts, id: p => pts.get(key(p))[0] };
}

function imTables(prog, m, units) {
  const L = IM_UNITS[(units || ["KN", "M"]).join(",").toUpperCase()] || 1;
  const J = imJoints(m);
  const t = [`File ${m.name || "model"} exported by SkyFrame (mock ${prog})`, "",
    'TABLE:  "PROGRAM CONTROL"', `   ProgramName=${prog}   CurrUnits="${imUnitsField(units)}"`, " ",
    'TABLE:  "JOINT COORDINATES"'];
  for (const [, [n, p]] of J.pts) t.push(`   Joint=${n}   XorR=${imN(p[0] / L)}   Y=${imN(p[1] / L)}   Z=${imN(p[2] / L)}`);
  t.push(" ", 'TABLE:  "CONNECTIVITY - FRAME"');
  for (const mb of m.members || []) t.push(`   Frame=${imQ(mb.uid)}   JointI=${J.id(mb.pi)}   JointJ=${J.id(mb.pj)}`);
  t.push(" ", 'TABLE:  "FRAME SECTION ASSIGNMENTS"');
  for (const mb of m.members || []) t.push(`   Frame=${imQ(mb.uid)}   AnalSect=${imQ(mb.section)}`);
  t.push(" ", 'TABLE:  "LOAD PATTERN DEFINITIONS"');
  for (const p of Object.values(m.patterns || {})) t.push(`   LoadPat=${imQ(p.name)}   DesignType=${String(p.kind || "other").toUpperCase()}   SelfWtMult=${imN(p.self_weight_factor || 0)}`);
  t.push(" ", "END TABLE DATA", `$SF-MOCK ${imB64(JSON.stringify(m))}`);
  return { text: t.join("\n") + "\n", joints: J.pts.size };
}

function imCounts(m) {
  return { stories: (m.stories || []).length, materials: Object.keys(m.materials || {}).length,
    frame_sections: Object.keys(m.sections || {}).length, frames: (m.members || []).length,
    areas: (m.shells || []).length, load_patterns: Object.keys(m.patterns || {}).length,
    load_cases: Object.keys(m.cases || {}).length, load_combinations: Object.keys(m.combos || {}).length };
}

export function mockInteropExport(fmt, model, opts = {}) {
  const m = model || {};
  const name = (m.name || "model").replace(/\//g, "_");
  const ok = (ext, mime, content, report) => ({ ok: true, filename: `${name}.${ext}`, mime, content, report: { format: fmt, mock: true, ...report } });
  if (fmt === "s2k") {
    const r = imTables("SAP2000", m, opts.units);
    return ok("s2k", "text/plain", r.text, { written: { ...imCounts(m), joints: r.joints }, skipped: [] });
  }
  if (fmt === "f2k") {
    const st = (m.stories || []).find(s => s.name === opts.story) || (m.stories || [])[0];
    if (!st) return { ok: false, error: "the model has no stories" };
    const at = z => Math.abs(z - st.elevation) < 1e-9;
    const fm = { ...m, members: (m.members || []).filter(x => (at(x.pi[2]) && at(x.pj[2])) || (x.kind === "column" && (at(x.pi[2]) || at(x.pj[2])))) };
    const r = imTables("SAFE", fm, opts.units);
    const cols = fm.members.filter(x => x.kind === "column").length;
    return { ...ok("f2k", "text/plain", r.text, { story: st.name, elevation: st.elevation,
      written: { points: r.joints, slabs: (m.shells || []).filter(s => s.kind === "slab").length, beams: fm.members.length - cols, columns: cols,
        column_reactions: opts.loads_from_above === false ? 0 : cols }, skipped: [] }), filename: `${name}-${st.name}.f2k` };
  }
  if (fmt === "sdnf") {
    const f = { m: 1, mm: 0.001, in: 0.0254, ft: 0.3048 }[opts.units || "m"] || 1;
    const lines = ["Packet 00", `"SkyFrame" "${name}" "None" "01/01/2000" "00:00:00" 1 "${{ m: "Meters", mm: "Millimeters", in: "Inches", ft: "Feet" }[opts.units || "m"]}" "Kilonewtons"`,
      "Packet 10", String((m.members || []).length)];
    (m.members || []).forEach((x, i) => {
      lines.push(`${i + 1} "${x.kind === "column" ? "Column" : x.kind === "beam" ? "Beam" : "Vertical Brace"}" "${x.uid}" "${x.section}" "${(m.sections[x.section] || {}).material || ""}" ${imN(x.angle || 0)} 0 0 ${x.cardinal_point || 10}`,
        [...x.pi, ...x.pj].map(v => imN(v / f)).join(" "), "0 0 0 0 0 0", '"000000" "000000"');
    });
    lines.push(`# SF-MOCK ${imB64(JSON.stringify(m))}`);
    return ok("sdnf", "text/plain", lines.join("\n") + "\n", { version: "3.0", written: { members: (m.members || []).length }, skipped: [] });
  }
  if (fmt === "idea") {
    const xml = ['<?xml version="1.0" encoding="utf-8"?>', "<OpenModel>", "<Point3D/>",
      `<Member1D>${(m.members || []).map((x, i) => `<Member1D><Id>${i + 1}</Id><Name>${x.uid}</Name></Member1D>`).join("")}</Member1D>`, "</OpenModel>"].join("\n");
    return ok("xml", "application/xml", xml, { schema: "IdeaRS.OpenModel (IOM)", written: { members: (m.members || []).length, internal_force_results: 0 },
      skipped: ["mock: load effects need the live server"] });
  }
  if (fmt === "ifc_revit") {
    const txt = ["ISO-10303-21;", "HEADER;", "FILE_DESCRIPTION(('ViewDefinition [ReferenceView_V1.2, StructuralAnalysisView]'),'2;1');",
      "FILE_SCHEMA(('IFC4'));", "ENDSEC;", "DATA;", `/* SF-MOCK ${imB64(JSON.stringify(m))} */`, "ENDSEC;", "END-ISO-10303-21;"].join("\n");
    return ok("ifc", "application/x-step", txt, { schema: "IFC4", written: { frames: (m.members || []).length, revit_types: new Set((m.members || []).map(x => x.kind + x.section)).size }, skipped: [] });
  }
  return { ok: false, error: `unknown export format ${fmt}` };
}

export function mockInteropImport(fmt, body) {
  const text = String(body.text || "");
  const mm = text.match(/(?:\$SF-MOCK|# SF-MOCK) ([A-Za-z0-9+/=]+)/);
  if (!mm) return { ok: false, error: `${fmt.toUpperCase()} import of foreign files needs the live server (mock mode)` };
  let model;
  try { model = JSON.parse(imUnB64(mm[1])); } catch { return { ok: false, error: "corrupt mock file" }; }
  return { ok: true, model, warnings: [], report: { format: fmt, mock: true, read: imCounts(model) } };
}
