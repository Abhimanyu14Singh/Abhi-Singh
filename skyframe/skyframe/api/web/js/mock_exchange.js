/* SkyFrame — ?mock=1 stand-in for the model exchange endpoints
   (POST /api/exchange/export|import/<fmt>, CONTRACT "Model exchange: E2K /
   IFC / DXF / Excel").

   Exports are small but real files built from the store model:
     · e2k  — UNITS / STORY / MATERIAL / FRAMESECTION / POINT / LINE /
              LINEASSIGN / LOADPATTERN / LOADCASE / COMBO records;
     · ifc  — IFC4 STEP header + storeys + one IfcColumn/IfcBeam/IfcMember
              per frame (no geometry solids);
     · dxf  — 3D LINEs on SF-<kind>-<section> layers, 3DFACEs for shells;
     · xlsx — a stored (uncompressed) OOXML workbook with a frame_objects
              sheet.
   Every mock file also carries the exact model JSON (an e2k "$SF-MOCK"
   comment, a STEP comment, a DXF 999 comment, a workbook part) so a mock
   export -> import round-trips byte-identically. Foreign e2k / DXF files
   get a basic parse (stories, points, frames on SF layers); foreign IFC /
   xlsx need the live server (friendly error, no console noise). */

const xmEnc = new TextEncoder();
const xmNum = v => { const s = String(+(+v).toPrecision(15)); return s === "-0" ? "0" : s; };
const xmQ = s => '"' + String(s).replace(/"/g, "''") + '"';
const xmB64 = str => btoa(unescape(encodeURIComponent(str)));
const xmUnB64 = b64 => decodeURIComponent(escape(atob(b64)));

const XM_UNITS = { "kN-m": ["KN", "M", 1, 1], "kN-mm": ["KN", "MM", 1, 0.001], "N-mm": ["N", "MM", 0.001, 0.001],
  "kip-ft": ["KIP", "FT", 4.4482216152605, 0.3048], "kip-in": ["KIP", "IN", 4.4482216152605, 0.0254],
  "tonf-m": ["TONF", "M", 9.80665, 1], "kgf-m": ["KGF", "M", 0.00980665, 1] };

function xmCounts(m) {
  return { stories: (m.stories || []).length, materials: Object.keys(m.materials || {}).length,
    frame_sections: Object.keys(m.sections || {}).length, frames: (m.members || []).length,
    areas: (m.shells || []).length, load_patterns: Object.keys(m.patterns || {}).length,
    load_cases: Object.keys(m.cases || {}).length, load_combinations: Object.keys(m.combos || {}).length };
}

/* ---------------------------------------------------------------- e2k */
function xmE2k(m, opts) {
  const u = XM_UNITS[opts.units] || XM_UNITS["kN-m"];
  const L = v => xmNum(v / u[3]), S = v => xmNum(v / (u[2] / u[3] ** 2));
  const out = [`$ File ${m.name || "model"}.e2k exported by SkyFrame (mock)`, "", "$ PROGRAM INFORMATION",
    '  PROGRAM  "ETABS"  VERSION "20.0.0"', "", "$ CONTROLS", `  UNITS  "${u[0]}"  "${u[1]}"  "C"`, "",
    "$ STORIES - IN SEQUENCE FROM TOP"];
  const st = m.stories || [];
  [...st].reverse().forEach(s => out.push(`  STORY ${xmQ(s.name)}  HEIGHT ${L(s.height)}  MASTERSTORY "Yes"`));
  const base = st.length ? st[0].elevation - st[0].height : 0;
  out.push(`  STORY "Base"  ELEV ${L(base)}`, "", "$ MATERIAL PROPERTIES");
  for (const mt of Object.values(m.materials || {})) {
    out.push(`  MATERIAL  ${xmQ(mt.name)}    TYPE "${(mt.material_type || "concrete").replace(/^./, c => c.toUpperCase())}"`);
    out.push(`  MATERIAL  ${xmQ(mt.name)}    SYMTYPE "Isotropic"  E ${S(mt.E)}  U ${xmNum(mt.nu ?? 0.2)}`);
  }
  out.push("", "$ FRAME SECTIONS");
  for (const s of Object.values(m.sections || {})) {
    if (s.b > 0 && s.h > 0 && !s.shape) out.push(`  FRAMESECTION  ${xmQ(s.name)}  MATERIAL ${xmQ(s.material)}  SHAPE "Concrete Rectangular"  D ${L(s.h)}  B ${L(s.b)}`);
    else out.push(`  FRAMESECTION  ${xmQ(s.name)}  MATERIAL ${xmQ(s.material)}  SHAPE "General"  D ${L(s.h || 0)}  B ${L(s.b || 0)}  AREA ${xmNum(s.A / u[3] ** 2)}  I33 ${xmNum(s.I33 / u[3] ** 4)}  I22 ${xmNum(s.I22 / u[3] ** 4)}  J ${xmNum(s.J / u[3] ** 4)}`);
  }
  const levels = [["Base", base], ...st.map(s => [s.name, s.elevation])];
  const pts = new Map(), lines = [], assigns = [];
  const pt = (x, y) => { const k = `${x},${y}`; if (!pts.has(k)) pts.set(k, [String(pts.size + 1), x, y]); return pts.get(k)[0]; };
  const lev = z => { let k = levels.findIndex(l => Math.abs(l[1] - z) < 1e-9); return k; };
  for (const mb of m.members || []) {
    const ki = lev(mb.pi[2]), kj = lev(mb.pj[2]);
    if (ki < 0 || kj < 0) continue;
    const kind = mb.kind === "column" ? "COLUMN" : mb.kind === "beam" ? "BEAM" : "BRACE";
    lines.push(`  LINE  ${xmQ(mb.uid)}  ${kind}  ${xmQ(pt(mb.pi[0], mb.pi[1]))}  ${xmQ(pt(mb.pj[0], mb.pj[1]))}  ${kj - ki}`);
    assigns.push(`  LINEASSIGN  ${xmQ(mb.uid)}  ${xmQ(levels[Math.max(ki, kj)][0])}  SECTION ${xmQ(mb.section)}${mb.angle ? "  ANGLE " + xmNum(mb.angle) : ""}`);
  }
  out.push("", "$ POINT COORDINATES", ...[...pts.values()].map(p => `  POINT ${xmQ(p[0])}  ${L(p[1])}  ${L(p[2])}`));
  out.push("", "$ LINE CONNECTIVITIES", ...lines, "", "$ LINE ASSIGNS", ...assigns);
  if (opts.include.loads !== false) {
    out.push("", "$ LOAD PATTERNS");
    for (const p of Object.values(m.patterns || {})) out.push(`  LOADPATTERN ${xmQ(p.name)}  TYPE  "${({ dead: "Dead", live: "Live", quake: "Seismic", wind: "Wind" })[p.kind] || "Other"}"  SELFWEIGHT  ${xmNum(p.self_weight_factor || 0)}`);
  }
  if (opts.include.cases !== false) {
    out.push("", "$ LOAD CASES");
    for (const c of Object.values(m.cases || {})) {
      out.push(`  LOADCASE ${xmQ(c.name)}  TYPE  "Linear Static"  INITCOND  "PRESET"`);
      for (const [p, f] of Object.entries(c.patterns || {})) out.push(`  LOADCASE ${xmQ(c.name)}  LOADPAT  ${xmQ(p)}  SF  ${xmNum(f)}`);
    }
  }
  if (opts.include.combos !== false && Object.keys(m.combos || {}).length) {
    out.push("", "$ LOAD COMBINATIONS");
    for (const c of Object.values(m.combos)) {
      out.push(`  COMBO ${xmQ(c.name)}  TYPE "Linear Add"`);
      for (const [k, f] of Object.entries(c.cases || {})) out.push(`  COMBO ${xmQ(c.name)}  LOADCASE ${xmQ(k)}  SF ${xmNum(f)}`);
    }
  }
  const skipped = ["mock mode: shells, loads, links and dynamic cases are carried only in the SkyFrame extension comment"];
  if (opts.extensions !== false) out.push("", "$ SKYFRAME EXTENSIONS", "$SF-MOCK " + xmB64(JSON.stringify(m)));
  out.push("", "  END", "$ END OF MODEL FILE", "");
  return { content: out.join("\n"), report: { format: "e2k", units: [u[0], u[1]], written: xmCounts(m),
    skipped, extensions: opts.extensions !== false ? { model: 1 } : {}, exact: opts.extensions !== false, mock: true } };
}

function xmE2kRead(text) {
  const mk = text.match(/^\$SF-MOCK (\S+)/m);
  if (mk) return { model: JSON.parse(xmUnB64(mk[1])), warnings: [] };
  const warnings = [];
  const tok = line => [...line.matchAll(/"([^"]*)"|(\S+)/g)].map(x => x[1] !== undefined ? x[1] : x[2]);
  const kv = (t, i) => { const o = {}; for (; i + 1 < t.length; i += 2) o[t[i].toUpperCase()] = t[i + 1]; return o; };
  let F = 1, Lf = 1;
  const stories = [], pts = {}, lines = {}, secs = {}, mats = {}, members = [];
  let base = 0;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("$")) continue;
    const t = tok(line), head = t[0].toUpperCase();
    if (head === "UNITS") { const u = Object.values(XM_UNITS).find(x => x[0] === t[1].toUpperCase() && x[1] === t[2].toUpperCase()); if (u) { F = u[2]; Lf = u[3]; } }
    else if (head === "STORY") { const o = kv(t, 2); if (o.ELEV !== undefined) base = +o.ELEV * Lf; else stories.push([t[1], +o.HEIGHT * Lf]); }
    else if (head === "MATERIAL") { const o = kv(t, 2); const m = mats[t[1]] = mats[t[1]] || { name: t[1], E: 25e6, nu: 0.2, unit_weight: 24 }; if (o.E) m.E = +o.E * F / Lf ** 2; if (o.U) m.nu = +o.U; }
    else if (head === "FRAMESECTION") {
      const o = kv(t, 2); if (!o.SHAPE) continue;
      const b = +(o.B || 0.3) * Lf, h = +(o.D || 0.6) * Lf;
      secs[t[1]] = { name: t[1], material: o.MATERIAL || "CONC", A: b * h, I33: b * h ** 3 / 12, I22: h * b ** 3 / 12, J: 0.141 * Math.max(b, h) * Math.min(b, h) ** 3, b, h };
      if (!/RECTANGULAR/i.test(o.SHAPE)) warnings.push(`mock: shape ${o.SHAPE} of ${t[1]} approximated as a ${b}x${h} rectangle`);
    }
    else if (head === "POINT") pts[t[1]] = [+t[2] * Lf, +t[3] * Lf];
    else if (head === "LINE") lines[t[1]] = [t[2].toUpperCase(), t[3], t[4], t[5] !== undefined ? +t[5] : (t[2].toUpperCase() === "COLUMN" ? 1 : 0)];
    else if (head === "LINEASSIGN") members.push([t[1], t[2], kv(t, 3)]);
    else if (!["PROGRAM", "TITLE2", "END", "LOADPATTERN", "LOADCASE", "COMBO"].includes(head)) warnings.push(`mock: ${head} record ignored`);
  }
  const st = [...stories].reverse();
  let e = base;
  const levels = [["Base", base]];
  const sts = st.map(([n, h]) => { e += h; levels.push([n, e]); return { name: n, height: h, elevation: e }; });
  const mem = [];
  for (const [lab, story, o] of members) {
    const ln = lines[lab]; const k = levels.findIndex(l => l[0] === story);
    if (!ln || k < 0 || !pts[ln[1]] || !pts[ln[2]]) { warnings.push(`mock: line ${lab} skipped`); continue; }
    const ki = ln[3] >= 0 ? k - ln[3] : k, kj = ln[3] >= 0 ? k : k + ln[3];
    mem.push({ uid: lab, kind: ln[0] === "COLUMN" ? "column" : ln[0] === "BEAM" ? "beam" : "brace", section: o.SECTION,
      pi: [...pts[ln[1]], levels[ki][1]], pj: [...pts[ln[2]], levels[kj][1]], story, releases: "", angle: +(o.ANGLE || 0) });
  }
  const xs = [...new Set(Object.values(pts).map(p => p[0]))].sort((a, b) => a - b);
  const ys = [...new Set(Object.values(pts).map(p => p[1]))].sort((a, b) => a - b);
  return { warnings, model: { name: "E2K Import", frame_axis_convention: "etabs", materials: mats, sections: secs,
    shell_sections: {}, stories: sts, members: mem, shells: [], grid: { x_lines: xs, y_lines: ys }, base_fixity: "fixed",
    patterns: {}, cases: {}, combos: {}, rs_cases: {}, th_cases: {} } };
}

/* ---------------------------------------------------------------- ifc */
function xmIfc(m, opts) {
  let n = 0;
  const L = [];
  const add = s => { n += 1; L.push(`#${n}=${s};`); return `#${n}`; };
  const R = v => { const s = String(+v); return s.includes(".") || s.includes("e") ? s.replace("e", "E") : s + "."; };
  add(`IFCPROJECT('0SkyFrameMockProject00',$,'${(m.name || "model").replace(/'/g, "''")}',$,$,$,$,$,$)`);
  const st = m.stories || [];
  if (st.length) add(`IFCBUILDINGSTOREY('0SkyFrameMockBase00000',$,'Base',$,$,$,$,$,.ELEMENT.,${R(st[0].elevation - st[0].height)})`);
  st.forEach((s, i) => add(`IFCBUILDINGSTOREY('0SkyFrameMockStorey${String(i).padStart(3, "0")}',$,'${s.name}',$,$,$,$,$,.ELEMENT.,${R(s.elevation)})`));
  if (opts.include.physical !== false)
    for (const mb of m.members || []) add(`${mb.kind === "column" ? "IFCCOLUMN" : mb.kind === "beam" ? "IFCBEAM" : "IFCMEMBER"}('${mb.uid}',$,'${mb.uid}',$,'${mb.section}',$,$,'${mb.story || ""}',$)`);
  const text = ["ISO-10303-21;", "HEADER;", "FILE_DESCRIPTION(('ViewDefinition [ReferenceView_V1.2]'),'2;1');",
    `FILE_NAME('${(m.name || "model")}.ifc','',(''),(''),'SkyFrame (mock)','SkyFrame','');`, "FILE_SCHEMA(('IFC4'));", "ENDSEC;",
    `/* SF-MOCK ${xmB64(JSON.stringify(m))} */`, "DATA;", ...L, "ENDSEC;", "END-ISO-10303-21;", ""].join("\n");
  return { content: text, report: { format: "ifc", schema: "IFC4", written: { storeys: st.length + 1, frames: (m.members || []).length },
    skipped: ["mock mode: profiles, solids and the analysis model are written by the live server"], entities: n, mock: true } };
}

/* ---------------------------------------------------------------- dxf */
const xmLayer = (kind, sec) => ("SF-" + kind + (sec ? "-" + sec : "")).replace(/[<>/\\":;?*|=,`\s]/g, "_");
function xmDxf(m, opts) {
  const k = opts.unit_scale || 1, E = [], layers = {};
  const P = (c, v) => E.push(String(c), String(v));
  const lay = (kind, sec) => { const l = xmLayer(kind, sec); layers[l] = sec || ""; return l; };
  if (opts.include.frames !== false)
    for (const mb of m.members || []) {
      P(0, "LINE"); P(8, lay(mb.kind === "column" ? "COL" : mb.kind === "beam" ? "BEAM" : "BRACE", mb.section));
      [10, 20, 30].forEach((c, i) => P(c, xmNum(mb.pi[i] * k))); [11, 21, 31].forEach((c, i) => P(c, xmNum(mb.pj[i] * k)));
    }
  if (opts.include.shells !== false)
    for (const r of m.shells || []) {
      if (r.corners.length > 4) continue;
      P(0, "3DFACE"); P(8, lay(r.kind === "wall" ? "WALL" : r.behavior === "membrane" ? "DECK" : "SLAB", r.section));
      const q = [...r.corners]; while (q.length < 4) q.push(q[q.length - 1]);
      q.forEach((c, i) => { P(10 + i, xmNum(c[0] * k)); P(20 + i, xmNum(c[1] * k)); P(30 + i, xmNum(c[2] * k)); });
    }
  const head = ["999", `SF-MOCK ${xmB64(JSON.stringify(m))}`, ...Object.entries(layers).flatMap(([l, s]) => ["999", `SF-MAP ${l}=${s}`]),
    "0", "SECTION", "2", "ENTITIES"];
  return { content: [...head, ...E, "0", "ENDSEC", "0", "EOF", ""].join("\n"),
    report: { format: "dxf", written: { frames: (m.members || []).length, areas: (m.shells || []).length, layers: Object.keys(layers).length },
      layers, skipped: ["loads, supports, properties and analysis data are not written (DXF carries geometry and layer = section only)"], mock: true } };
}
function xmDxfRead(text, opts, current) {
  const lines = text.split(/\r?\n/).map(s => s.trim());
  for (let i = 0; i + 1 < lines.length; i += 2)
    if (lines[i] === "999" && lines[i + 1].startsWith("SF-MOCK ")) return { model: JSON.parse(xmUnB64(lines[i + 1].slice(8))), warnings: [] };
  const s = 1 / (opts.unit_scale || 1), warnings = [], map = {}, frames = [];
  for (let i = 0; i + 1 < lines.length; i += 2) if (lines[i] === "999" && lines[i + 1].startsWith("SF-MAP ")) { const [l, sec] = lines[i + 1].slice(7).split("="); map[l] = sec; }
  let cur = null;
  const flush = () => { if (cur && cur.type === "LINE" && /^SF-(COL|BEAM|BRACE)/i.test(cur[8] || "")) frames.push(cur); };
  for (let i = 0; i + 1 < lines.length; i += 2) {
    const c = +lines[i], v = lines[i + 1];
    if (c === 0) { flush(); cur = { type: v.toUpperCase() }; } else if (cur) cur[c] = v;
  }
  flush();
  const zs = [...new Set(frames.flatMap(f => [+f[30] * s, +f[31] * s]))].sort((a, b) => a - b);
  const stories = zs.slice(1).map((z, i) => ({ name: `Story${i + 1}`, height: z - zs[i], elevation: z }));
  const secs = {}, mats = {};
  const cm = (current && current.materials) || {};
  Object.assign(mats, Object.keys(cm).length ? cm : { CONC: { name: "CONC", E: 25e6, nu: 0.2, unit_weight: 24 } });
  const members = frames.map((f, i) => {
    const layer = f[8], kind = /^SF-COL/i.test(layer) ? "column" : /^SF-BEAM/i.test(layer) ? "beam" : "brace";
    const sec = map[layer] || layer.split("-").slice(2).join("-") || "DXF-FRAME";
    if (!secs[sec]) {
      if (current && current.sections && current.sections[sec]) secs[sec] = current.sections[sec];
      else { secs[sec] = { name: sec, material: Object.keys(mats)[0], A: 0.18, I33: 0.0054, I22: 0.00135, J: 0.0037, b: 0.3, h: 0.6 }; warnings.push(`frame section ${sec} not defined — 0.3x0.6 placeholder rectangle`); }
    }
    const pi = [+f[10] * s, +f[20] * s, +f[30] * s], pj = [+f[11] * s, +f[21] * s, +f[31] * s];
    const top = Math.max(pi[2], pj[2]);
    const st = stories.find(x => Math.abs(x.elevation - top) < 1e-9);
    return { uid: (kind === "column" ? "C" : kind === "beam" ? "B" : "D") + (i + 1), kind, section: sec, pi, pj, story: st ? st.name : "", releases: "", angle: 0 };
  });
  return { warnings, model: { name: "DXF Import", frame_axis_convention: "etabs", materials: mats, sections: secs, shell_sections: {},
    stories, members, shells: [], base_fixity: "fixed", patterns: {}, cases: {}, combos: {}, rs_cases: {}, th_cases: {} } };
}

/* ---------------------------------------------------------------- xlsx */
const XM_CRC = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
const xmCrc = b => { let c = 0xFFFFFFFF; for (const x of b) c = XM_CRC[(c ^ x) & 0xFF] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; };
/** Stored (method 0) zip of {name: string}. */
export function xmZip(files) {
  const parts = [], central = [];
  let off = 0;
  const u16 = v => [v & 255, (v >>> 8) & 255], u32 = v => [v & 255, (v >>> 8) & 255, (v >>> 16) & 255, (v >>> 24) & 255];
  for (const [name, text] of Object.entries(files)) {
    const nb = xmEnc.encode(name), data = xmEnc.encode(text), crc = xmCrc(data);
    const head = [...u32(0x04034b50), ...u16(20), ...u16(0), ...u16(0), ...u16(0), ...u16(0), ...u32(crc), ...u32(data.length), ...u32(data.length), ...u16(nb.length), ...u16(0)];
    parts.push(Uint8Array.from(head), nb, data);
    central.push(Uint8Array.from([...u32(0x02014b50), ...u16(20), ...u16(20), ...u16(0), ...u16(0), ...u16(0), ...u16(0), ...u32(crc), ...u32(data.length), ...u32(data.length), ...u16(nb.length), ...u16(0), ...u16(0), ...u16(0), ...u16(0), ...u32(0), ...u32(off)]), nb);
    off += head.length + nb.length + data.length;
  }
  const csize = central.reduce((a, p) => a + p.length, 0), n = Object.keys(files).length;
  const end = Uint8Array.from([...u32(0x06054b50), ...u16(0), ...u16(0), ...u16(n), ...u16(n), ...u32(csize), ...u32(off), ...u16(0)]);
  const all = [...parts, ...central, end], total = all.reduce((a, p) => a + p.length, 0), out = new Uint8Array(total);
  let p = 0; for (const a of all) { out.set(a, p); p += a.length; }
  return out;
}
/** {name: Uint8Array} of a STORED zip (null when compressed entries exist). */
export function xmUnzip(bytes) {
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength), out = {};
  let p = 0;
  while (p + 30 <= bytes.length && dv.getUint32(p, true) === 0x04034b50) {
    const method = dv.getUint16(p + 8, true), size = dv.getUint32(p + 18, true), nl = dv.getUint16(p + 26, true), xl = dv.getUint16(p + 28, true);
    const name = new TextDecoder().decode(bytes.subarray(p + 30, p + 30 + nl));
    if (method !== 0) return null;
    out[name] = bytes.subarray(p + 30 + nl + xl, p + 30 + nl + xl + size);
    p += 30 + nl + xl + size;
  }
  return out;
}
const xmX = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function xmXlsx(m) {
  const NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main";
  const col = i => String.fromCharCode(65 + i);
  const cell = (r, i, v) => typeof v === "number" ? `<c r="${col(i)}${r}"><v>${v}</v></c>` : `<c r="${col(i)}${r}" t="inlineStr"><is><t>${xmX(v)}</t></is></c>`;
  const rows = [["_id", "uid", "kind", "section", "story"], ...(m.members || []).map((x, i) => [String(i), x.uid, x.kind, x.section, x.story || ""])];
  const sheet = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="${NS}"><sheetData>${rows.map((r, ri) => `<row r="${ri + 1}">${r.map((v, i) => cell(ri + 1, i, v)).join("")}</row>`).join("")}</sheetData></worksheet>`;
  const bytes = xmZip({
    "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="json" ContentType="application/json"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
    "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
    "xl/workbook.xml": `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="${NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="frame_objects" sheetId="1" r:id="rId1"/></sheets></workbook>`,
    "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
    "xl/worksheets/sheet1.xml": sheet,
    "customXml/skyframe_model.json": JSON.stringify(m),
  });
  let bin = ""; bytes.forEach(b => { bin += String.fromCharCode(b); });
  return { content_b64: btoa(bin), report: { format: "xlsx", written: { frame_objects: (m.members || []).length }, sheets: 1,
    skipped: ["mock mode: one sheet (frame_objects); the live server writes every model table"], mock: true } };
}

/* ---------------------------------------------------------------- api */
export function mockExchangeExport(fmt, model, opts = {}) {
  const m = JSON.parse(JSON.stringify(model || {}));
  const o = { include: {}, ...opts };
  const name = (m.name || "model").replace(/\//g, "_");
  const mime = { e2k: "text/plain", ifc: "application/x-step", dxf: "application/dxf",
    xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }[fmt];
  if (!mime) return { ok: false, error: `unknown format '${fmt}'` };
  const r = fmt === "e2k" ? xmE2k(m, o) : fmt === "ifc" ? xmIfc(m, o) : fmt === "dxf" ? xmDxf(m, o) : xmXlsx(m);
  return { ok: true, filename: `${name}.${fmt}`, mime, ...r };
}

export function mockExchangeImport(fmt, body, current) {
  try {
    let res;
    if (fmt === "e2k") res = xmE2kRead(body.text || "");
    else if (fmt === "dxf") res = xmDxfRead(body.text || "", body, body.sections_from_current !== false ? current : null);
    else if (fmt === "ifc") {
      const mk = (body.text || "").match(/\/\* SF-MOCK (\S+) \*\//);
      if (!mk) return { ok: false, error: "mock mode reads only IFC files exported by SkyFrame — run the live server to import other IFC files" };
      res = { model: JSON.parse(xmUnB64(mk[1])), warnings: [] };
    } else if (fmt === "xlsx") {
      const bin = atob(body.content_b64 || ""), bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      const files = xmUnzip(bytes);
      const part = files && files["customXml/skyframe_model.json"];
      if (!part) return { ok: false, error: "mock mode reads only workbooks exported by SkyFrame — run the live server to import other .xlsx files" };
      res = { model: JSON.parse(new TextDecoder().decode(part)), warnings: [] };
    } else return { ok: false, error: `unknown format '${fmt}'` };
    if (!res.model || !Array.isArray(res.model.members) || !Array.isArray(res.model.stories)) return { ok: false, error: "the file holds no model" };
    return { ok: true, model: res.model, warnings: res.warnings, report: { format: fmt, read: xmCounts(res.model), mock: true } };
  } catch (e) {
    return { ok: false, error: `mock import failed: ${e.message}` };
  }
}

export const xmEncodeText = s => xmB64(s);
