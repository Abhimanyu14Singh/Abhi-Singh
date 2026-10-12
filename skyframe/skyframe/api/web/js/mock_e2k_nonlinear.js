/* SkyFrame — ?mock=1 stand-in for the Wave C native E2K records
   (CONTRACT "E2K native nonlinear records (Wave C)") and the DWG check of
   File › Import › DXF.

   mockE2kNonlinear(m, L) returns the e2k lines the live exporter writes
   for pushover / nonlinear static / staged / steady-state / PSD cases,
   hinge properties + assignments, device link types and temperature
   loads — a simplified but same-dialect subset (the mock file still
   carries the exact model in its $SF-MOCK comment, so import is exact).
   L(v) converts a SI length to the file unit.

   mockIsDwg(text) / MOCK_DWG_HELP mirror skyframe.io.dwg: a binary DWG
   ("AC10xx" signature) handed to the DXF importer is refused with the
   "save as DXF from your CAD program" guidance. */

const mnQ = s => '"' + String(s).replace(/"/g, "''") + '"';
const mnNum = v => { const s = String(+(+v).toPrecision(15)); return s === "-0" ? "0" : s; };

export const MOCK_DWG_HELP = "This is a binary DWG drawing. SkyFrame imports drawings as DXF and does not bundle a DWG converter. "
  + "Save the drawing as DXF from your CAD program (AutoCAD / BricsCAD / ZWCAD: SAVEAS -> 'AutoCAD DXF (*.dxf)', any version from R12 on; "
  + "DraftSight / LibreCAD / FreeCAD: File > Save As / Export -> DXF), or convert it with the free ODA File Converter or LibreDWG (dwg2dxf), "
  + "then use File > Import > DXF with the .dxf file.";

export function mockIsDwg(text) {
  const s = String(text || "").slice(0, 6);
  return /^AC10\d\d$/.test(s);
}

const MN_LINK = { damper: "DAMPER - EXPONENTIAL", damper_bilinear: "DAMPER - BILINEAR", gap: "GAP", hook: "HOOK",
  isolator: "RUBBER ISOLATOR", rubber_isolator_bouc_wen: "RUBBER ISOLATOR", fp_isolator: "FRICTION ISOLATOR",
  tc_fp_isolator: "T/C FRICTION ISOLATOR", triple_fp: "TRIPLE PENDULUM ISOLATOR", multilinear: "MULTILINEAR ELASTIC",
  multilinear_kinematic: "MULTILINEAR PLASTIC", multilinear_takeda: "MULTILINEAR PLASTIC", multilinear_pivot: "MULTILINEAR PLASTIC",
  friction_spring: "DAMPER - FRICTION SPRING", plastic_wen: "PLASTIC (WEN)", brb: "PLASTIC (WEN)" };
const MN_GEO = { linear: "None", none: "None", pdelta: "PDelta", p_delta: "PDelta", corotational: "LargeDispl", large_displacement: "LargeDispl" };
const MN_DIST = { roof_point: "Roof Point", pattern: "Pattern", mode: "Mode", uniform_accel: "Uniform Acceleration", triangular: "Triangular" };

/** Native-style records of the Wave C families present in model ``m``. */
export function mockE2kNonlinear(m, L = v => v) {
  const out = [];
  const links = (m.links || []).filter(l => l.link_type && l.link_type !== "elastic");
  if (links.length) {
    out.push("", "$ LINK PROPERTIES");
    for (const l of links) {
      out.push(`  LINKPROP  ${mnQ("LP_" + l.uid)}  TYPE ${mnQ(MN_LINK[l.link_type] || "LINEAR")}`);
      const p = Object.entries(l.params || {}).filter(([, v]) => typeof v === "number");
      if (p.length) out.push(`  LINKPROP  ${mnQ("LP_" + l.uid)}  DOF "U1"  NONLIN "Yes"  ` + p.map(([k, v]) => `${k.toUpperCase()} ${mnNum(v)}`).join("  "));
    }
  }
  const hp = m.hinge_properties || {};
  if (Object.keys(hp).length) {
    out.push("", "$ FRAME HINGE PROPERTIES");
    for (const [n, h] of Object.entries(hp)) {
      if (h.type === "auto") out.push(`  HINGE ${mnQ(n)}  AUTOHINGETYPE ${mnQ(h.auto.code)}  DOF ${mnQ(h.auto.dof)}`);
      else out.push(`  HINGE ${mnQ(n)}  BEHAVIOR "Deformation Controlled"  DOF ${mnQ(h.type)}  HYSTYPE ${mnQ(h.hysteresis || "kinematic")}`);
    }
  }
  const asg = [];
  for (const mb of m.members || []) {
    if (Array.isArray(mb.hinges)) mb.hinges.forEach(h => asg.push(`  HINGEASSIGN ${mnQ(mb.uid)}  HINGEPROP ${mnQ(h.property)}  RDISTANCE ${mnNum(h.relative_distance)}`));
    else if (mb.hinges === "auto_m3") [0, 1].forEach(r => asg.push(`  HINGEASSIGN ${mnQ(mb.uid)}  AUTOHINGETYPE "ASCE41-17"  DOF "M3"  RDISTANCE ${r}`));
  }
  if (asg.length) out.push("", "$ FRAME HINGE ASSIGNMENTS", ...asg);
  const temp = [];
  for (const p of Object.values(m.patterns || {})) {
    for (const t of p.thermal_loads || []) temp.push(`  LINELOAD  ${mnQ(t.member_uid)}  TYPE "TEMPERATURE"  LC ${mnQ(p.name)}  T ${mnNum(t.dT)}`);
    for (const t of p.shell_thermal_loads || []) temp.push(`  AREALOAD  ${mnQ(t.region_uid)}  TYPE "TEMPERATURE"  LC ${mnQ(p.name)}  T ${mnNum(t.dT)}`);
  }
  if (temp.length) out.push("", "$ TEMPERATURE LOADS", ...temp);
  const cs = [];
  for (const c of Object.values(m.nonlinear_static_cases || {})) {
    cs.push(`  LOADCASE ${mnQ(c.name)}  TYPE  "Nonlinear Static"  INITCOND  ${mnQ(c.start_from || "NONE")}`);
    (c.loads || []).forEach(l => cs.push(`  LOADCASE ${mnQ(c.name)}  LOADPAT  ${mnQ(l.pattern)}  SF  ${mnNum(l.scale)}`));
    cs.push(`  LOADCASE ${mnQ(c.name)}  NLGEOMTYPE  ${mnQ(MN_GEO[c.geometric] || "None")}`);
    cs.push(`  LOADCASE ${mnQ(c.name)}  LOADCONTROL  ${c.load_application === "displacement_control" ? '"Displacement"' : '"Full"'}  DISPLTYPE  "Monitored"`
      + (c.target_disp != null ? `  DISPLMAG  ${mnNum(L(c.target_disp))}` : ""));
  }
  for (const c of Object.values(m.pushover_cases || {})) {
    cs.push(`  LOADCASE ${mnQ(c.name)}  TYPE  "Nonlinear Static"  INITCOND  "NONE"`);
    cs.push(`  LOADCASE ${mnQ(c.name)}  NLGEOMTYPE  ${mnQ(MN_GEO[c.geometric] || "None")}`);
    cs.push(`  LOADCASE ${mnQ(c.name)}  LOADDIST ${mnQ(MN_DIST[c.load_distribution || "roof_point"] || "Roof Point")}  DIR ${mnQ(c.direction)}  TARGETDRIFT ${mnNum(c.target_drift ?? 0.02)}`);
  }
  for (const c of Object.values(m.staged_cases || {})) {
    cs.push(`  LOADCASE ${mnQ(c.name)}  TYPE  "Nonlinear Staged Construction"  INITCOND  "NONE"`);
    if (Array.isArray(c.stages)) c.stages.forEach(s => cs.push(`  LOADCASE ${mnQ(c.name)}  STAGE  ${mnQ(s.name)}`));
    else cs.push(`  LOADCASE ${mnQ(c.name)}  AUTOSTAGE  "Per Story"`);
  }
  for (const [kind, dct] of [["Steady State", m.steady_state_cases], ["Power Spectral Density", m.psd_cases]]) {
    for (const c of Object.values(dct || {})) cs.push(`  LOADCASE ${mnQ(c.name)}  TYPE  ${mnQ(kind)}  INITCOND  "PRESET"`);
  }
  if (cs.length) out.push("", "$ NONLINEAR / FREQUENCY LOAD CASES", ...cs);
  return out;
}

/** Report counts the live exporter adds for these families. */
export function mockE2kNonlinearCounts(m) {
  const n = Object.keys(m.hinge_properties || {}).length;
  return n ? { hinge_properties: n } : {};
}
