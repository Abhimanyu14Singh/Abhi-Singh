/* SkyFrame — ?mock=1 support for frame end releases / partial fixity and
   joint local axes (CONTRACT "Frame end releases, partial fixity and joint
   local axes"): the backend validation messages (mockValidateReleases) and
   the two DB-edit tables (relMockTables), a client port of
   skyframe/core/releases.py. The mock backend round-trips the new keys
   untouched (members[].end_releases, joint_local_axes). */

const MR_DOFS = ["P", "V2", "V3", "T", "M2", "M3"];
const MR_ENDS = ["i", "j"];
const mrNum = v => typeof v === "number" && isFinite(v);

/** First validation error (backend wording) or null. */
export function mockValidateReleases(d) {
  for (const m of (d && d.members) || []) {
    const er = m.end_releases;
    if (er == null) continue;
    const where = `Member ${m.uid}: end_releases`;
    if (typeof er !== "object" || Array.isArray(er)) return `${where} must be an object {'i': {...}, 'j': {...}}`;
    const bad = Object.keys(er).filter(k => !MR_ENDS.includes(k));
    if (bad.length) return `${where}: unknown end(s) ${JSON.stringify(bad)} (use 'i' / 'j')`;
    for (const e of MR_ENDS) {
      const sub = er[e];
      if (sub == null) continue;
      if (typeof sub !== "object") return `${where}.${e} must be an object {dof: spring}`;
      for (const [k, v] of Object.entries(Array.isArray(sub) ? Object.fromEntries(sub.map(x => [x, 0])) : sub)) {
        if (!MR_DOFS.includes(k)) return `${where}.${e}: unknown DOF '${k}' (use ${MR_DOFS.join(", ")})`;
        if (v != null && (!mrNum(v) || v < 0)) return `${where}.${e}.${k}: spring stiffness must be a finite value >= 0 (got ${v})`;
      }
    }
  }
  const jla = d && d.joint_local_axes;
  if (jla != null) {
    if (!Array.isArray(jla)) return "joint_local_axes must be a list";
    const seen = new Set();
    for (let k = 0; k < jla.length; k++) {
      const e = jla[k], where = `joint_local_axes[${k}]`;
      if (!e || typeof e !== "object") return `${where} must be an object`;
      if (!Array.isArray(e.point) || e.point.length !== 3 || !e.point.every(mrNum)) return `${where}.point must be 3 finite numbers`;
      const a = e.angles ?? [0, 0, 0];
      if (!Array.isArray(a) || a.length !== 3 || !a.every(mrNum)) return `${where}.angles must be 3 finite numbers (deg)`;
      const key = e.point.map(v => v.toFixed(6)).join(",");
      if (seen.has(key)) return `${where}: duplicate joint`;
      seen.add(key);
    }
  }
  return null;
}

/* ------------------------------------------------------------ DB-edit tables */
const mrEff = o => {
  const toks = String(o.releases || "").split(",").map(s => s.trim()).filter(Boolean);
  const er = o.end_releases || {};
  const out = {};
  for (const e of MR_ENDS) {
    const d = {};
    if (toks.includes(e === "i" ? "Mi" : "Mj")) { d.M2 = 0; d.M3 = 0; }
    for (const [k, v] of Object.entries(er[e] || {})) d[k] = +v || 0;
    out[e] = d;
  }
  return out;
};
const mrWrite = (o, eff) => {
  const toks = [], er = {};
  for (const e of MR_ENDS) {
    const d = eff[e];
    const ks = Object.keys(d);
    if (!ks.length) continue;
    if (ks.length === 2 && d.M2 === 0 && d.M3 === 0) toks.push(e === "i" ? "Mi" : "Mj");
    else { er[e] = {}; for (const k of MR_DOFS) if (k in d) er[e][k] = d[k]; }
  }
  o.releases = toks.join(",");
  if (Object.keys(er).length) o.end_releases = er; else delete o.end_releases;
};

/** The two tables, built with mock_dbedit's own helpers (obj / col / G). */
export function relMockTables({ obj, col, G }) {
  const cols = [col("uid", "Frame", "text", "none", { editable: false })];
  for (const e of MR_ENDS) for (const d of MR_DOFS)
    cols.push(col(`${d.toLowerCase()}${e}`, `${d}${e.toUpperCase()}`, "bool", "none", { def: false,
      get: o => d in mrEff(o)[e],
      put: (o, v) => { const eff = mrEff(o); if (v) eff[e][d] = eff[e][d] ?? 0; else delete eff[e][d]; mrWrite(o, eff); } }));
  for (const e of MR_ENDS) for (const d of MR_DOFS)
    cols.push(col(`k_${d.toLowerCase()}${e}`, `${d}${e.toUpperCase()} Spring`, "number", ["P", "V2", "V3"].includes(d) ? "stiffness" : "rot_stiffness", { def: 0,
      get: o => +(mrEff(o)[e][d] ?? 0),
      put: (o, v) => { const eff = mrEff(o); if (!(d in eff[e])) return; if (!(v >= 0)) throw new Error("spring stiffness must be >= 0"); eff[e][d] = +v; mrWrite(o, eff); } }));
  const pt = i => ({ get: o => +((o.point || [0, 0, 0])[i]), put: (o, v) => { const p = [...(o.point || [0, 0, 0])]; p[i] = v; o.point = p; } });
  const an = i => ({ get: o => +((o.angles || [0, 0, 0])[i]), put: (o, v) => { const a = [...(o.angles || [0, 0, 0])]; a[i] = v; o.angles = a; } });
  return [
    obj("frame_releases", "Frame Releases and Partial Fixity", G.ASN, cols,
      { container: "members", kind: "list", keycol: "uid", canAdd: false, canDelete: false }),
    obj("joint_local_axes", "Joint Local Axes", G.ASN, [
      col("x", "X", "number", "length", { required: true, ...pt(0) }), col("y", "Y", "number", "length", { required: true, ...pt(1) }),
      col("z", "Z", "number", "length", { required: true, ...pt(2) }),
      col("angle_a", "Angle A (about Z)", "number", "angle_deg", { def: 0, ...an(0) }),
      col("angle_b", "Angle B (about 2)", "number", "angle_deg", { def: 0, ...an(1) }),
      col("angle_c", "Angle C (about 1)", "number", "angle_deg", { def: 0, ...an(2) }),
    ], { container: "joint_local_axes", kind: "list", newObj: () => ({ point: [0, 0, 0], angles: [0, 0, 0] }) }),
  ];
}
