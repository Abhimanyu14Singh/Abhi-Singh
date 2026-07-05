/* SkyFrame model editing — pure mutations on the client-side model dict
   (same shape as BuildingModel.to_dict(), see CONTRACT.md v0.2). */

const dist = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
const near = (a, b, tol = 1e-6) =>
  Math.abs(a[0] - b[0]) < tol && Math.abs(a[1] - b[1]) < tol && Math.abs(a[2] - b[2]) < tol;

/** Ensure every v0.2/v0.3 collection exists so the editor can run against an
    older backend model dict. */
export function normalizeModel(m) {
  if (!m) return m;
  m.materials = m.materials || {};
  m.sections = m.sections || {};
  m.shell_sections = m.shell_sections || {};
  m.shells = m.shells || [];
  m.patterns = m.patterns || {};
  m.members = m.members || [];
  for (const mm of m.members) if (mm.releases == null) mm.releases = "";
  for (const p of Object.values(m.patterns)) {
    p.member_loads = p.member_loads || [];
    p.area_loads = p.area_loads || [];
    p.story_forces = p.story_forces || [];
    if (!p.kind) p.kind = guessPatternKind(p.name || "");
    // v0.7 — ETABS-style self-weight factor (absent = 0.0)
    if (!isFinite(p.self_weight_factor)) p.self_weight_factor = 0.0;
    // v0.8 — accidental torsion (ASCE 7 §12.8.4) + thermal loads
    p.accidental_torsion = !!p.accidental_torsion;
    if (!isFinite(p.ecc)) p.ecc = 0.05;
    p.thermal_loads = (Array.isArray(p.thermal_loads) ? p.thermal_loads : [])
      .filter(t => t && t.member_uid != null && isFinite(t.dT));
  }
  // v0.3 — analysis cases, combos, response-spectrum cases
  m.cases = m.cases || {};
  m.combos = m.combos || {};
  m.rs_cases = m.rs_cases || {};
  for (const [n, c] of Object.entries(m.cases)) {
    c.name = c.name || n;
    c.patterns = c.patterns || {};
    c.pdelta = !!c.pdelta;
  }
  for (const [n, cb] of Object.entries(m.combos)) {
    cb.name = cb.name || n;
    cb.cases = cb.cases || {};
    cb.combo_type = cb.combo_type === "envelope" ? "envelope" : "add";   // v0.4
  }
  // v0.4 — member orientation angle, stiffness modifiers, mass source,
  // time-history cases
  for (const mm of m.members) if (!isFinite(mm.angle)) mm.angle = 0;
  // v0.9 — rigid-end offsets (rigid zones at member ends) + rigid factor
  for (const mm of m.members) {
    if (!(isFinite(mm.rigid_i) && mm.rigid_i >= 0)) mm.rigid_i = 0;
    if (!(isFinite(mm.rigid_j) && mm.rigid_j >= 0)) mm.rigid_j = 0;
    if (!(isFinite(mm.rigid_factor) && mm.rigid_factor >= 0 && mm.rigid_factor <= 1))
      mm.rigid_factor = 1.0;
  }
  // v0.11 — elastic (Winkler) foundation: subgrade modulus + bearing width.
  // A member is on an elastic foundation only when BOTH are > 0.
  for (const mm of m.members) {
    if (!(isFinite(mm.foundation_ks) && mm.foundation_ks >= 0)) mm.foundation_ks = 0;
    if (!(isFinite(mm.foundation_width) && mm.foundation_width >= 0)) mm.foundation_width = 0;
  }
  // v0.12 — axial-limit behavior (tension/compression-only makes the run
  // nonlinear). Anything other than the two limited modes falls back to "both".
  for (const mm of m.members) {
    if (mm.axial_limit !== "tension" && mm.axial_limit !== "compression")
      mm.axial_limit = "both";
  }
  for (const s of Object.values(m.sections)) {
    for (const k of ["mod_A", "mod_I33", "mod_I22", "mod_J"])
      if (!isFinite(s[k])) s[k] = 1.0;
  }
  for (const s of Object.values(m.shell_sections))
    if (!isFinite(s.mod)) s.mod = 1.0;
  if (!m.mass_source || typeof m.mass_source !== "object" ||
      !Object.keys(m.mass_source).length)
    m.mass_source = { DEAD: 1.0 };
  m.th_cases = m.th_cases || {};
  for (const [n, tc] of Object.entries(m.th_cases)) {
    tc.name = tc.name || n;
    tc.direction = tc.direction === "Y" ? "Y" : "X";
    tc.accel = Array.isArray(tc.accel) ? tc.accel : [];
    tc.dt = isFinite(tc.dt) && tc.dt > 0 ? tc.dt : 0.02;
    tc.damping = isFinite(tc.damping) ? tc.damping : 0.05;
    tc.scale = isFinite(tc.scale) ? tc.scale : 1.0;
    // v0.6 — nonlinear (plastic-hinge) time history
    tc.nonlinear = !!tc.nonlinear;
    tc.gravity = (tc.gravity && typeof tc.gravity === "object") ? tc.gravity : {};
    tc.hinges = tc.hinges === "all_ends" ? "all_ends" : "column_base";
    tc.My = (tc.My && typeof tc.My === "object") ? tc.My : {};
    if (tc.default_My != null && !isFinite(tc.default_My)) delete tc.default_My;
    tc.hardening = isFinite(tc.hardening) ? tc.hardening : 0.02;
    // v0.13 — optional reference to a library time-history function ("" = inline)
    tc.function = typeof tc.function === "string" ? tc.function : "";
  }
  for (const [n, rc] of Object.entries(m.rs_cases)) {
    rc.name = rc.name || n;
    rc.direction = rc.direction === "Y" ? "Y" : "X";
    rc.spectrum = Array.isArray(rc.spectrum) ? rc.spectrum : [];
    rc.combo_method = rc.combo_method === "SRSS" ? "SRSS" : "CQC";
    rc.damping = isFinite(rc.damping) ? rc.damping : 0.05;
    rc.scale = isFinite(rc.scale) ? rc.scale : 1.0;
    // v0.13 — optional reference to a library spectrum function (name; "" = inline)
    rc.function = typeof rc.function === "string" ? rc.function : "";
  }
  // v0.5 — shell openings, pushover cases, diaphragm option, links
  for (const s of m.shells) {
    s.openings = (Array.isArray(s.openings) ? s.openings : []).filter(o =>
      o && isFinite(o.u0) && isFinite(o.v0) && isFinite(o.u1) && isFinite(o.v1));
  }
  m.pushover_cases = m.pushover_cases || {};
  for (const [n, pc] of Object.entries(m.pushover_cases)) {
    pc.name = pc.name || n;
    pc.direction = pc.direction === "Y" ? "Y" : "X";
    pc.gravity = (pc.gravity && typeof pc.gravity === "object") ? pc.gravity : {};
    pc.target_drift = (isFinite(pc.target_drift) && pc.target_drift > 0) ? pc.target_drift : 0.02;
    pc.steps = (isFinite(pc.steps) && pc.steps > 1) ? Math.round(pc.steps) : 100;
    pc.My = (pc.My && typeof pc.My === "object") ? pc.My : {};
    if (pc.default_My != null && !isFinite(pc.default_My)) delete pc.default_My;
    pc.hardening = isFinite(pc.hardening) ? pc.hardening : 0.02;
  }
  m.diaphragm = m.diaphragm === "none" ? "none" : "rigid";
  m.story_diaphragm = (m.story_diaphragm && typeof m.story_diaphragm === "object")
    ? m.story_diaphragm : {};
  m.links = Array.isArray(m.links) ? m.links : [];
  for (const l of m.links) {
    if (!(Array.isArray(l.stiffness) && l.stiffness.length === 6 &&
          l.stiffness.every(v => isFinite(v))))
      l.stiffness = [1e5, 1e5, 1e5, 1e4, 1e4, 1e4];
  }
  // v0.8 — spring supports + global thermal expansion coefficient
  m.spring_supports = Array.isArray(m.spring_supports) ? m.spring_supports : [];
  for (const s of m.spring_supports) {
    if (!(Array.isArray(s.point) && s.point.length === 3 && s.point.every(v => isFinite(v))))
      s.point = [0, 0, 0];
    if (!(Array.isArray(s.stiffness) && s.stiffness.length === 6 &&
          s.stiffness.every(v => isFinite(v) && v >= 0)))
      s.stiffness = [1e5, 1e5, 1e5, 0, 0, 0];
  }
  if (!isFinite(m.thermal_alpha)) m.thermal_alpha = 1.2e-5;
  // v0.6 — staged construction cases (sequential gravity)
  m.staged_cases = m.staged_cases || {};
  for (const [n, sc] of Object.entries(m.staged_cases)) {
    sc.name = sc.name || n;
    sc.pattern = sc.pattern || "DEAD";
    sc.stages = "per_story";
    sc.include_live = (sc.include_live && typeof sc.include_live === "object")
      ? sc.include_live : {};
  }
  // v0.10 — buckling cases (linearized eigenvalue) + RS directional combos
  m.buckling_cases = m.buckling_cases || {};
  for (const [n, bc] of Object.entries(m.buckling_cases)) {
    bc.name = bc.name || n;
    bc.gravity = (bc.gravity && typeof bc.gravity === "object") ? bc.gravity : {};
    bc.num_modes = (isFinite(bc.num_modes) && bc.num_modes >= 1) ? Math.round(bc.num_modes) : 3;
  }
  m.rs_combos = m.rs_combos || {};
  for (const [n, rcmb] of Object.entries(m.rs_combos)) {
    rcmb.name = rcmb.name || n;
    rcmb.name_x = rcmb.name_x || "";
    rcmb.name_y = rcmb.name_y || "";
    rcmb.method = rcmb.method === "SRSS" ? "SRSS" : "100_30";
  }
  // v0.13 — section cuts (list of cutting planes) + function library
  m.section_cuts = (Array.isArray(m.section_cuts) ? m.section_cuts : [])
    .filter(c => c && typeof c === "object")
    .map(c => {
      const axis = (c.axis === "x" || c.axis === "y") ? c.axis : "z";
      const cut = {
        name: c.name || "CUT",
        axis,
        coord: isFinite(c.coord) ? c.coord : 0,
      };
      // optional bounding ranges [lo, hi] on each in-plane axis
      for (const k of ["x_range", "y_range", "z_range"]) {
        if (Array.isArray(c[k]) && c[k].length === 2 && c[k].every(isFinite))
          cut[k] = [Math.min(c[k][0], c[k][1]), Math.max(c[k][0], c[k][1])];
      }
      return cut;
    });
  m.spectrum_functions = (m.spectrum_functions && typeof m.spectrum_functions === "object")
    ? m.spectrum_functions : {};
  for (const [n, sf] of Object.entries(m.spectrum_functions)) {
    sf.name = sf.name || n;
    sf.points = (Array.isArray(sf.points) ? sf.points : [])
      .filter(p => Array.isArray(p) && p.length === 2 && p.every(isFinite));
    sf.damping = isFinite(sf.damping) ? sf.damping : 0.05;
  }
  m.th_functions = (m.th_functions && typeof m.th_functions === "object")
    ? m.th_functions : {};
  for (const [n, tf] of Object.entries(m.th_functions)) {
    tf.name = tf.name || n;
    tf.values = (Array.isArray(tf.values) ? tf.values : []).filter(isFinite);
    tf.dt = isFinite(tf.dt) && tf.dt > 0 ? tf.dt : 0.02;
  }
  return m;
}

export function storyByName(model, name) {
  return model.stories.find(s => s.name === name) || null;
}
export function storyZ(model, name) {
  const st = storyByName(model, name);
  if (!st) return { zb: 0, zt: 0 };
  return { zb: st.elevation - st.height, zt: st.elevation };
}

export function nextUid(model, prefix) {
  let mx = 0;
  const re = new RegExp(`^${prefix}(\\d+)$`);
  for (const m of model.members) {
    const g = re.exec(m.uid);
    if (g) mx = Math.max(mx, +g[1]);
  }
  for (const s of model.shells) {
    const g = re.exec(s.uid);
    if (g) mx = Math.max(mx, +g[1]);
  }
  for (const l of (model.links || [])) {
    const g = re.exec(l.uid);
    if (g) mx = Math.max(mx, +g[1]);
  }
  return `${prefix}${mx + 1}`;
}

/* ------------------------------------------------ default assignments */
export function defaultFrameSection(model, kind) {
  const names = Object.keys(model.sections);
  if (!names.length) {
    const mat = defaultMaterial(model);
    model.sections.SEC1 = { name: "SEC1", material: mat, b: 0.3, h: 0.6 };
    return "SEC1";
  }
  const want = kind === "column" ? "COL" : "BEAM";
  return names.find(n => n.toUpperCase().includes(want)) || names[0];
}

export function defaultShellSection(model) {
  const names = Object.keys(model.shell_sections);
  if (names.length) return names[0];
  const mat = defaultMaterial(model);
  model.shell_sections.SH200 = { name: "SH200", material: mat, thickness: 0.2 };
  return "SH200";
}

export function defaultMaterial(model) {
  const names = Object.keys(model.materials);
  if (names.length) return names[0];
  model.materials.CONC = { name: "CONC", E: 25_000_000, nu: 0.2, unit_weight: 24 };
  return "CONC";
}

/* ------------------------------------------------ element creation
   All return the created element, or null when an identical element
   already exists (duplicate-safe). */
export function addColumn(model, x, y, story) {
  const { zb, zt } = storyZ(model, story);
  const pi = [x, y, zb], pj = [x, y, zt];
  if (model.members.some(m => m.kind === "column" && near(m.pi, pi) && near(m.pj, pj))) return null;
  const mem = {
    uid: nextUid(model, "C"), kind: "column",
    section: defaultFrameSection(model, "column"),
    pi, pj, story, length: dist(pi, pj), releases: "",
  };
  model.members.push(mem);
  return mem;
}

export function addBeam(model, p1, p2, story) {
  const { zt } = storyZ(model, story);
  const pi = [p1.x, p1.y, zt], pj = [p2.x, p2.y, zt];
  if (dist(pi, pj) < 1e-6) return null;
  if (model.members.some(m => m.kind !== "column" &&
    ((near(m.pi, pi) && near(m.pj, pj)) || (near(m.pi, pj) && near(m.pj, pi))))) return null;
  const mem = {
    uid: nextUid(model, "B"), kind: "beam",
    section: defaultFrameSection(model, "beam"),
    pi, pj, story, length: dist(pi, pj), releases: "",
  };
  model.members.push(mem);
  return mem;
}

/** v0.4 — diagonal brace: plan point A at the story's bottom elevation up
    to plan point B at the story's top elevation. */
export function addBrace(model, p1, p2, story) {
  const { zb, zt } = storyZ(model, story);
  const pi = [p1.x, p1.y, zb], pj = [p2.x, p2.y, zt];
  if (Math.hypot(p2.x - p1.x, p2.y - p1.y) < 1e-6) return null;   // needs plan run
  if (model.members.some(m => m.kind === "brace" && near(m.pi, pi) && near(m.pj, pj))) return null;
  const names = Object.keys(model.sections);
  const mem = {
    uid: nextUid(model, "BR"), kind: "brace",
    section: names[0] || defaultFrameSection(model, "beam"),
    pi, pj, story, length: dist(pi, pj), releases: "", angle: 0,
  };
  model.members.push(mem);
  return mem;
}

/** Vertical wall spanning the story height between two plan points. */
export function addWall(model, p1, p2, story) {
  const { zb, zt } = storyZ(model, story);
  if (Math.hypot(p2.x - p1.x, p2.y - p1.y) < 1e-6) return null;
  const corners = [
    [p1.x, p1.y, zb], [p2.x, p2.y, zb],
    [p2.x, p2.y, zt], [p1.x, p1.y, zt],
  ];
  if (model.shells.some(s => s.kind === "wall" && s.story === story &&
    s.corners.every((c, i) => near(c, corners[i])))) return null;
  const sh = {
    uid: nextUid(model, "W"), kind: "wall", behavior: "shell",
    section: defaultShellSection(model), corners, mesh_size: 1.0, story,
    openings: [],
  };
  model.shells.push(sh);
  return sh;
}

/** Horizontal rectangular slab at the story's top elevation (CCW corners). */
export function addSlab(model, x0, y0, x1, y1, story) {
  const { zt } = storyZ(model, story);
  const [xa, xb] = [Math.min(x0, x1), Math.max(x0, x1)];
  const [ya, yb] = [Math.min(y0, y1), Math.max(y0, y1)];
  if (xb - xa < 1e-6 || yb - ya < 1e-6) return null;
  const corners = [
    [xa, ya, zt], [xb, ya, zt], [xb, yb, zt], [xa, yb, zt],
  ];
  if (model.shells.some(s => s.kind === "slab" && s.story === story &&
    s.corners.every((c, i) => near(c, corners[i])))) return null;
  const sh = {
    uid: nextUid(model, "SL"), kind: "slab", behavior: "shell",
    section: defaultShellSection(model), corners, mesh_size: 1.0, story,
    openings: [],
  };
  model.shells.push(sh);
  return sh;
}

/* ================================================================
   v0.5 — elevation drawing, links, shell openings
   ================================================================ */

/** Story containing elevation z (bottom-exclusive), clamped at the ends. */
export function storyContainingZ(model, z) {
  for (const st of model.stories)
    if (z > st.elevation - st.height + 1e-9 && z <= st.elevation + 1e-9) return st.name;
  if (model.stories.length && z <= 1e-9) return model.stories[0].name;
  return model.stories.length ? model.stories[model.stories.length - 1].name : "";
}

/** Story whose TOP elevation matches z (beams drawn in elevation view). */
export function storyAtLevel(model, z) {
  const st = model.stories.find(s => Math.abs(s.elevation - z) < 1e-6);
  return st ? st.name : storyContainingZ(model, z);
}

/** Beam between two explicit 3D points (elevation view drawing). */
export function addBeamAt(model, pi, pj, story) {
  if (dist(pi, pj) < 1e-6) return null;
  if (model.members.some(m => m.kind !== "column" &&
    ((near(m.pi, pi) && near(m.pj, pj)) || (near(m.pi, pj) && near(m.pj, pi))))) return null;
  const mem = {
    uid: nextUid(model, "B"), kind: "beam",
    section: defaultFrameSection(model, "beam"),
    pi: [...pi], pj: [...pj], story, length: dist(pi, pj), releases: "",
  };
  model.members.push(mem);
  return mem;
}

/** Brace between two explicit 3D points at DIFFERENT levels (elevation view). */
export function addBraceAt(model, pi, pj, story) {
  if (dist(pi, pj) < 1e-6 || Math.abs(pj[2] - pi[2]) < 1e-6) return null;
  const [a, b] = pi[2] <= pj[2] ? [pi, pj] : [pj, pi];   // store bottom → top
  if (model.members.some(m => m.kind === "brace" &&
    ((near(m.pi, a) && near(m.pj, b)) || (near(m.pi, b) && near(m.pj, a))))) return null;
  const names = Object.keys(model.sections);
  const mem = {
    uid: nextUid(model, "BR"), kind: "brace",
    section: names[0] || defaultFrameSection(model, "beam"),
    pi: [...a], pj: [...b], story, length: dist(a, b), releases: "", angle: 0,
  };
  model.members.push(mem);
  return mem;
}

/** Wall region from four explicit 3D corners (elevation view drawing). */
export function addWallAt(model, corners, story) {
  if (model.shells.some(s => s.kind === "wall" &&
    s.corners.length === corners.length &&
    s.corners.every((c, i) => near(c, corners[i])))) return null;
  const sh = {
    uid: nextUid(model, "W"), kind: "wall", behavior: "shell",
    section: defaultShellSection(model), corners: corners.map(c => [...c]),
    mesh_size: 1.0, story, openings: [],
  };
  model.shells.push(sh);
  return sh;
}

/** Two-node link with the default 6-dof stiffness (v0.5 contract). */
export function addLink(model, pi, pj) {
  model.links = model.links || [];
  if (dist(pi, pj) < 1e-6) return null;
  if (model.links.some(l =>
    (near(l.pi, pi) && near(l.pj, pj)) || (near(l.pi, pj) && near(l.pj, pi)))) return null;
  const link = {
    uid: nextUid(model, "LK"), pi: [...pi], pj: [...pj],
    stiffness: [1e5, 1e5, 1e5, 1e4, 1e4, 1e4],
  };
  model.links.push(link);
  return link;
}

/* ================================================================
   v0.8 — spring supports (grounded 6-dof stiffness at a base point)
   ================================================================ */
/** Stable string key for a spring support (its base point). */
export function springKey(point) {
  return point.map(v => +(+v).toFixed(4)).join(",");
}

/** Add a spring support at a base point with a default 6-dof stiffness
    [kx,ky,kz,krx,kry,krz]. Duplicate-safe by point. */
export function addSpringSupport(model, x, y, z, stiffness) {
  model.spring_supports = model.spring_supports || [];
  const point = [x, y, z];
  if (model.spring_supports.some(s => near(s.point, point))) return null;
  const sp = {
    point,
    stiffness: (Array.isArray(stiffness) && stiffness.length === 6)
      ? stiffness.map(Number) : [1e5, 1e5, 1e5, 0, 0, 0],
  };
  model.spring_supports.push(sp);
  return sp;
}

export function springByKey(model, key) {
  return (model.spring_supports || []).find(s => springKey(s.point) === key) || null;
}

/* ---- thermal loads (per pattern: {member_uid, dT °C}) */
export function getThermalLoad(model, pat, uid) {
  const p = model.patterns[pat];
  if (!p) return null;
  const t = (p.thermal_loads || []).find(t => t.member_uid === uid);
  return t ? t.dT : null;
}

export function setThermalLoad(model, pat, uid, dT) {
  const p = ensurePattern(model, pat);
  p.thermal_loads = (p.thermal_loads || []).filter(t => t.member_uid !== uid);
  if (isFinite(dT) && dT !== 0) p.thermal_loads.push({ member_uid: uid, dT });
}

/** Patterns carrying a thermal load on a given member. */
export function memberThermalPatterns(model, uid) {
  const out = [];
  for (const p of Object.values(model.patterns || {}))
    if ((p.thermal_loads || []).some(t => t.member_uid === uid)) out.push(p.name);
  return out;
}

/** Any pattern carries a thermal load on this member. */
export function anyThermalMember(model, uid) {
  return Object.values(model.patterns || {}).some(p =>
    (p.thermal_loads || []).some(t => t.member_uid === uid));
}

/** v0.9 — the member has a rigid end zone (offset at either end > 0). */
export function hasRigidZone(mm) {
  return !!mm && ((isFinite(mm.rigid_i) && mm.rigid_i > 0) ||
                  (isFinite(mm.rigid_j) && mm.rigid_j > 0));
}

/** v0.11 — the member sits on an elastic (Winkler) foundation: BOTH the
    subgrade modulus (kN/m³) and bearing width (m) must be positive. */
export function onFoundation(mm) {
  return !!mm && (mm.foundation_ks || 0) > 0 && (mm.foundation_width || 0) > 0;
}

/** v0.12 — axial-limit (tension/compression-only) behavior of a frame member.
    Returns "both" | "tension" | "compression"; anything else reads as "both". */
export function axialLimit(mm) {
  const v = mm && mm.axial_limit;
  return (v === "tension" || v === "compression") ? v : "both";
}

/** v0.12 — short badge label for a limited-axial member ("T-only" / "C-only");
    empty string when the member takes both tension and compression. */
export function axialLimitBadge(mm) {
  const v = axialLimit(mm);
  return v === "tension" ? "T-only" : v === "compression" ? "C-only" : "";
}

/* ---- shell openings (region-parametric fractions 0..1) */
export function addOpening(shell) {
  shell.openings = shell.openings || [];
  const o = { u0: 0.3, v0: 0.3, u1: 0.7, v1: 0.7 };
  shell.openings.push(o);
  return o;
}

/** Set one opening bound; clamps to 0..1 and keeps u0<u1 / v0<v1 ordered. */
export function setOpeningField(o, key, v) {
  if (!isFinite(v)) return false;
  o[key] = Math.max(0, Math.min(1, v));
  if (o.u1 < o.u0) { const t = o.u0; o.u0 = o.u1; o.u1 = t; }
  if (o.v1 < o.v0) { const t = o.v0; o.v0 = o.v1; o.v1 = t; }
  return true;
}

/** Bilinear point on a quad region at parametric (u along c0→c1, v along c0→c3). */
export function shellPointAt(corners, u, v) {
  const [c0, c1, c2, c3] = corners;
  const lerp = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
  return lerp(lerp(c0, c1, u), lerp(c3, c2, u), v);
}

/** 3D corner quad of an opening rectangle on a shell region. */
export function openingCorners(shell, o) {
  return [
    shellPointAt(shell.corners, o.u0, o.v0),
    shellPointAt(shell.corners, o.u1, o.v0),
    shellPointAt(shell.corners, o.u1, o.v1),
    shellPointAt(shell.corners, o.u0, o.v1),
  ];
}

/* ---- pushover cases */
export function addPushoverCase(model) {
  const name = uniqueKey(model.pushover_cases, "PUSH");
  model.pushover_cases[name] = {
    name, direction: "X", gravity: { DEAD: 1.0 }, target_drift: 0.02,
    steps: 100, My: {}, default_My: 250, hardening: 0.02,
  };
  return name;
}

export function renamePushoverCase(model, oldName, newName) {
  if (!newName || newName === oldName || model.pushover_cases[newName]) return false;
  model.pushover_cases[newName] = { ...model.pushover_cases[oldName], name: newName };
  delete model.pushover_cases[oldName];
  return true;
}

export function deletePushoverCase(model, name) {
  delete model.pushover_cases[name];
  return true;
}

/* ------------------------------------------------ erase */
/** ref: {type:"member"|"shell"|"link", uid}. Also removes loads/hinges that
    reference it. */
export function eraseElement(model, ref) {
  let removed = false;
  if (ref.type === "member") {
    const i = model.members.findIndex(m => m.uid === ref.uid);
    if (i >= 0) { model.members.splice(i, 1); removed = true; }
    for (const p of Object.values(model.patterns)) {
      p.member_loads = (p.member_loads || []).filter(l => l.member_uid !== ref.uid);
      p.thermal_loads = (p.thermal_loads || []).filter(t => t.member_uid !== ref.uid);
    }
    for (const pc of Object.values(model.pushover_cases || {}))
      if (pc.My) delete pc.My[ref.uid];
  } else if (ref.type === "spring") {
    const i = (model.spring_supports || []).findIndex(s => springKey(s.point) === ref.uid);
    if (i >= 0) { model.spring_supports.splice(i, 1); removed = true; }
  } else if (ref.type === "link") {
    const i = (model.links || []).findIndex(l => l.uid === ref.uid);
    if (i >= 0) { model.links.splice(i, 1); removed = true; }
  } else {
    const i = model.shells.findIndex(s => s.uid === ref.uid);
    if (i >= 0) { model.shells.splice(i, 1); removed = true; }
    for (const p of Object.values(model.patterns))
      p.area_loads = (p.area_loads || []).filter(l => l.region_uid !== ref.uid);
  }
  return removed;
}

/* ------------------------------------------------ loads */
export function ensurePattern(model, name) {
  if (!model.patterns[name])
    model.patterns[name] = { name, member_loads: [], area_loads: [], thermal_loads: [] };
  const p = model.patterns[name];
  p.member_loads = p.member_loads || [];
  p.area_loads = p.area_loads || [];
  p.thermal_loads = p.thermal_loads || [];
  return p;
}

export function patternNames(model) {
  const names = Object.keys(model.patterns || {});
  for (const n of ["DEAD", "LIVE"]) if (!names.includes(n)) names.push(n);
  return names;
}

/** Full-span gravity UDL on a member in a pattern (kN/m). */
export function getMemberUdl(model, pat, uid) {
  const p = model.patterns[pat];
  if (!p) return null;
  const l = (p.member_loads || []).find(l =>
    l.member_uid === uid && (l.kind || "udl") === "udl" && (l.direction || "gravity") === "gravity");
  return l ? l.w : null;
}

export function setMemberUdl(model, pat, uid, w) {
  const p = ensurePattern(model, pat);
  p.member_loads = p.member_loads.filter(l =>
    !(l.member_uid === uid && (l.kind || "udl") === "udl" && (l.direction || "gravity") === "gravity"));
  if (isFinite(w) && w !== 0)
    p.member_loads.push({ member_uid: uid, kind: "udl", w, w2: 0, a: 0, b: 1, direction: "gravity" });
}

/** Area load q (kPa, downward) on a shell region in a pattern. */
export function getAreaLoad(model, pat, uid) {
  const p = model.patterns[pat];
  if (!p) return null;
  const l = (p.area_loads || []).find(l => l.region_uid === uid);
  return l ? l.q : null;
}

export function setAreaLoad(model, pat, uid, q) {
  const p = ensurePattern(model, pat);
  p.area_loads = p.area_loads.filter(l => l.region_uid !== uid);
  if (isFinite(q) && q !== 0) p.area_loads.push({ region_uid: uid, q });
}

/* ------------------------------------------------ sections & materials */
export function renameFrameSection(model, oldName, newName) {
  if (!newName || newName === oldName || model.sections[newName]) return false;
  model.sections[newName] = { ...model.sections[oldName], name: newName };
  delete model.sections[oldName];
  for (const m of model.members) if (m.section === oldName) m.section = newName;
  return true;
}

export function renameShellSection(model, oldName, newName) {
  if (!newName || newName === oldName || model.shell_sections[newName]) return false;
  model.shell_sections[newName] = { ...model.shell_sections[oldName], name: newName };
  delete model.shell_sections[oldName];
  for (const s of model.shells) if (s.section === oldName) s.section = newName;
  return true;
}

export function renameMaterial(model, oldName, newName) {
  if (!newName || newName === oldName || model.materials[newName]) return false;
  model.materials[newName] = { ...model.materials[oldName], name: newName };
  delete model.materials[oldName];
  for (const s of Object.values(model.sections)) if (s.material === oldName) s.material = newName;
  for (const s of Object.values(model.shell_sections)) if (s.material === oldName) s.material = newName;
  return true;
}

export function sectionInUse(model, name) {
  return model.members.some(m => m.section === name);
}
export function shellSectionInUse(model, name) {
  return model.shells.some(s => s.section === name);
}
export function materialInUse(model, name) {
  return Object.values(model.sections).some(s => s.material === name) ||
    Object.values(model.shell_sections).some(s => s.material === name);
}

function uniqueKey(dict, base) {
  let i = 1;
  while (dict[`${base}${i}`]) i++;
  return `${base}${i}`;
}
export function addFrameSection(model) {
  const name = uniqueKey(model.sections, "FSEC");
  model.sections[name] = { name, material: defaultMaterial(model), b: 0.3, h: 0.6 };
  return name;
}
export function addShellSection(model) {
  const name = uniqueKey(model.shell_sections, "SSEC");
  model.shell_sections[name] = { name, material: defaultMaterial(model), thickness: 0.2 };
  return name;
}
export function addMaterial(model) {
  const name = uniqueKey(model.materials, "MAT");
  model.materials[name] = { name, E: 25_000_000, nu: 0.2, unit_weight: 24 };
  return name;
}

/* ================================================================
   v0.3 — load patterns / cases / combos / response-spectrum cases
   ================================================================ */
export const PATTERN_KINDS = ["dead", "live", "quake", "other"];

export function guessPatternKind(name) {
  const n = String(name).toUpperCase();
  if (/DEAD|^DL$|SDL|SUPER/.test(n)) return "dead";
  if (/LIVE|^LL$|ROOF/.test(n)) return "live";
  if (/EQ|QUAKE|SEIS|^E[XY]$|SPEC/.test(n)) return "quake";
  return "other";
}

/** Names of static cases that reference a pattern. */
export function patternRefs(model, name) {
  return Object.values(model.cases || {})
    .filter(c => (c.patterns || {})[name] !== undefined)
    .map(c => c.name);
}

export function addPattern(model, base = "PAT") {
  const name = uniqueKey(model.patterns, base);
  model.patterns[name] = {
    name, kind: "other", member_loads: [], area_loads: [], story_forces: [],
  };
  return name;
}

export function renamePattern(model, oldName, newName) {
  if (!newName || newName === oldName || model.patterns[newName]) return false;
  model.patterns[newName] = { ...model.patterns[oldName], name: newName };
  delete model.patterns[oldName];
  for (const c of Object.values(model.cases || {})) {
    if (c.patterns && c.patterns[oldName] !== undefined) {
      c.patterns[newName] = c.patterns[oldName];
      delete c.patterns[oldName];
    }
  }
  return true;
}

/** Delete a pattern — blocked (returns false) while any case references it. */
export function deletePattern(model, name) {
  if (patternRefs(model, name).length) return false;
  delete model.patterns[name];
  return true;
}

/** Names of combos that reference a static case. */
export function caseRefs(model, name) {
  return Object.values(model.combos || {})
    .filter(cb => (cb.cases || {})[name] !== undefined)
    .map(cb => cb.name);
}

export function addCase(model, base = "CASE") {
  const name = uniqueKey(model.cases, base);
  model.cases[name] = { name, patterns: {}, pdelta: false };
  return name;
}

export function renameCase(model, oldName, newName) {
  if (!newName || newName === oldName || model.cases[newName]) return false;
  model.cases[newName] = { ...model.cases[oldName], name: newName };
  delete model.cases[oldName];
  for (const cb of Object.values(model.combos || {})) {
    if (cb.cases && cb.cases[oldName] !== undefined) {
      cb.cases[newName] = cb.cases[oldName];
      delete cb.cases[oldName];
    }
  }
  return true;
}

/** Delete a static case — blocked while any combo references it. */
export function deleteCase(model, name) {
  if (caseRefs(model, name).length) return false;
  delete model.cases[name];
  return true;
}

export function addCombo(model, base = "COMBO") {
  const name = uniqueKey(model.combos, base);
  model.combos[name] = { name, cases: {} };
  return name;
}

export function renameCombo(model, oldName, newName) {
  if (!newName || newName === oldName || model.combos[newName]) return false;
  model.combos[newName] = { ...model.combos[oldName], name: newName };
  delete model.combos[oldName];
  return true;
}

export function deleteCombo(model, name) {
  delete model.combos[name];
  return true;
}

/** UBC-style design spectrum — flat plateau (2.5·Ca) then Cv/T decay. */
export function ubcSpectrum(Ca = 0.4, Cv = 0.56) {
  const Ts = Cv / (2.5 * Ca), T0 = 0.2 * Ts;
  const pts = [[0, Ca], [+T0.toFixed(3), 2.5 * Ca], [+Ts.toFixed(3), 2.5 * Ca]];
  for (const T of [0.8, 1.0, 1.5, 2.0, 3.0, 4.0])
    if (T > Ts) pts.push([T, +(Cv / T).toFixed(3)]);
  return pts;
}

export function addRsCase(model, base = "RS") {
  const name = uniqueKey(model.rs_cases, base);
  model.rs_cases[name] = {
    name, direction: "X", spectrum: ubcSpectrum(),
    combo_method: "CQC", damping: 0.05, scale: 1.0, function: "",
  };
  return name;
}

export function renameRsCase(model, oldName, newName) {
  if (!newName || newName === oldName || model.rs_cases[newName]) return false;
  model.rs_cases[newName] = { ...model.rs_cases[oldName], name: newName };
  delete model.rs_cases[oldName];
  return true;
}

export function deleteRsCase(model, name) {
  delete model.rs_cases[name];
  return true;
}

/* ================================================================
   v0.10 — buckling cases (linearized eigenvalue) + RS directional combos
   ================================================================ */
export function addBucklingCase(model, base = "BUCK") {
  model.buckling_cases = model.buckling_cases || {};
  const name = uniqueKey(model.buckling_cases, base);
  const grav = (model.patterns && model.patterns.DEAD) ? { DEAD: 1.0 } : {};
  model.buckling_cases[name] = { name, gravity: grav, num_modes: 3 };
  return name;
}

export function renameBucklingCase(model, oldName, newName) {
  if (!newName || newName === oldName || model.buckling_cases[newName]) return false;
  model.buckling_cases[newName] = { ...model.buckling_cases[oldName], name: newName };
  delete model.buckling_cases[oldName];
  return true;
}

export function deleteBucklingCase(model, name) {
  delete model.buckling_cases[name];
  return true;
}

/** Delete an RS directional combination (created via /api/case/rs-directional).
    The combined result only reappears in rs_cases after the next solve. */
export function deleteRsCombo(model, name) {
  if (model.rs_combos) delete model.rs_combos[name];
  return true;
}

/* ================================================================
   v0.13 — section cuts + function library
   ================================================================ */

/** A unique section-cut name against the current list. */
function uniqueCutName(model, base = "CUT") {
  const taken = new Set((model.section_cuts || []).map(c => c.name));
  let i = 1;
  while (taken.has(`${base}${i}`)) i++;
  return `${base}${i}`;
}

/** Model bounding box over member endpoints + shell corners. */
export function modelBBox(model) {
  let lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  const grow = p => { for (let i = 0; i < 3; i++) { lo[i] = Math.min(lo[i], p[i]); hi[i] = Math.max(hi[i], p[i]); } };
  for (const mm of model.members || []) { grow(mm.pi); grow(mm.pj); }
  for (const sh of model.shells || []) for (const c of sh.corners) grow(c);
  if (!isFinite(lo[0])) { lo = [0, 0, 0]; hi = [10, 10, 10]; }
  return [lo, hi];
}

/** Add a new section cut. Default: a horizontal (z) plane at mid-height,
    spanning the full plan. Returns the created cut. */
export function addSectionCut(model) {
  model.section_cuts = model.section_cuts || [];
  const [lo, hi] = modelBBox(model);
  const cut = {
    name: uniqueCutName(model),
    axis: "z",
    coord: +(((lo[2] + hi[2]) / 2) || 0).toFixed(3),
  };
  model.section_cuts.push(cut);
  return cut;
}

export function renameSectionCut(model, oldName, newName) {
  newName = (newName || "").trim();
  if (!newName || newName === oldName) return false;
  if ((model.section_cuts || []).some(c => c.name === newName)) return false;
  const cut = (model.section_cuts || []).find(c => c.name === oldName);
  if (!cut) return false;
  cut.name = newName;
  return true;
}

export function deleteSectionCut(model, name) {
  const i = (model.section_cuts || []).findIndex(c => c.name === name);
  if (i < 0) return false;
  model.section_cuts.splice(i, 1);
  return true;
}

/** Toggle an optional bounding range on a cut. lo/hi finite → set (ordered);
    null → remove. Returns the cut. */
export function setCutRange(cut, key, lo, hi) {
  if (lo == null || hi == null || !isFinite(lo) || !isFinite(hi)) delete cut[key];
  else cut[key] = [Math.min(lo, hi), Math.max(lo, hi)];
  return cut;
}

/* ---- spectrum functions (response-spectrum curve library) */
export function addSpectrumFunction(model, base = "SPEC") {
  model.spectrum_functions = model.spectrum_functions || {};
  const name = uniqueKey(model.spectrum_functions, base);
  model.spectrum_functions[name] = { name, points: ubcSpectrum(), damping: 0.05 };
  return name;
}

export function renameSpectrumFunction(model, oldName, newName) {
  if (!newName || newName === oldName || model.spectrum_functions[newName]) return false;
  model.spectrum_functions[newName] = { ...model.spectrum_functions[oldName], name: newName };
  delete model.spectrum_functions[oldName];
  // keep referencing RS cases pointed at the renamed function
  for (const rc of Object.values(model.rs_cases || {}))
    if (rc.function === oldName) rc.function = newName;
  return true;
}

/** Names of RS cases referencing a spectrum function (blocks deletion). */
export function spectrumFunctionRefs(model, name) {
  return Object.values(model.rs_cases || {})
    .filter(rc => rc.function === name).map(rc => rc.name);
}

export function deleteSpectrumFunction(model, name) {
  if (spectrumFunctionRefs(model, name).length) return false;
  delete model.spectrum_functions[name];
  return true;
}

/* ---- time-history functions (ground-acceleration record library) */
export function addThFunction(model, base = "REC") {
  model.th_functions = model.th_functions || {};
  const name = uniqueKey(model.th_functions, base);
  model.th_functions[name] = { name, values: sineRecord(0.02, 8), dt: 0.02 };
  return name;
}

export function renameThFunction(model, oldName, newName) {
  if (!newName || newName === oldName || model.th_functions[newName]) return false;
  model.th_functions[newName] = { ...model.th_functions[oldName], name: newName };
  delete model.th_functions[oldName];
  for (const tc of Object.values(model.th_cases || {}))
    if (tc.function === oldName) tc.function = newName;
  return true;
}

export function thFunctionRefs(model, name) {
  return Object.values(model.th_cases || {})
    .filter(tc => tc.function === name).map(tc => tc.name);
}

export function deleteThFunction(model, name) {
  if (thFunctionRefs(model, name).length) return false;
  delete model.th_functions[name];
  return true;
}

/** Eurocode 8 (EN 1998-1) elastic response spectrum, Type 1, in Sa(g) vs T(s).
    ag = design ground accel (g), S = soil factor, TB/TC/TD corner periods,
    eta = damping correction sqrt(10/(5+ζ%)) ≥ 0.55. Returns [[T, Sa], …]. */
export function ec8Spectrum(opts = {}) {
  const ag = isFinite(opts.ag) ? opts.ag : 0.25;      // g
  const S = isFinite(opts.S) ? opts.S : 1.2;          // soil factor (ground type B/C)
  const TB = isFinite(opts.TB) ? opts.TB : 0.15;
  const TC = isFinite(opts.TC) ? opts.TC : 0.5;
  const TD = isFinite(opts.TD) ? opts.TD : 2.0;
  const zeta = isFinite(opts.damping) ? opts.damping * 100 : 5;   // %
  const eta = Math.max(0.55, Math.sqrt(10 / (5 + zeta)));
  const B = 2.5;                                       // amplification factor β0
  const Se = T => {
    if (T <= TB) return ag * S * (1 + (T / TB) * (eta * B - 1));
    if (T <= TC) return ag * S * eta * B;
    if (T <= TD) return ag * S * eta * B * (TC / T);
    return ag * S * eta * B * (TC * TD / (T * T));
  };
  const Ts = [0, TB, TC];
  for (const T of [0.7, 1.0, 1.5, TD, 3.0, 4.0]) if (T > TC) Ts.push(T);
  const seen = new Set();
  return Ts.filter(T => { const k = +T.toFixed(4); if (seen.has(k)) return false; seen.add(k); return true; })
    .sort((a, b) => a - b)
    .map(T => [+T.toFixed(3), +Se(T).toFixed(4)]);
}

/* ================================================================
   v0.4 — grid & story editing
   ================================================================ */

/** A..Z, AA..AZ, … column-style labels. */
function alphaLabel(i) {
  let s = "";
  i = Math.floor(i);
  do { s = String.fromCharCode(65 + (i % 26)) + s; i = Math.floor(i / 26) - 1; } while (i >= 0);
  return s;
}

export function relabelGrid(grid) {
  grid.x_labels = grid.x_lines.map((_, i) => alphaLabel(i));
  grid.y_labels = grid.y_lines.map((_, i) => String(i + 1));
}

/** Set a grid line position (keeps the list sorted, relabels).
    Returns false when the value collides with another line. */
export function setGridLine(model, axis, idx, value) {
  const lines = axis === "x" ? model.grid.x_lines : model.grid.y_lines;
  if (!isFinite(value) || idx < 0 || idx >= lines.length) return false;
  if (lines.some((v, i) => i !== idx && Math.abs(v - value) < 1e-6)) return false;
  lines[idx] = value;
  lines.sort((a, b) => a - b);
  relabelGrid(model.grid);
  return true;
}

/** Append a grid line one typical bay beyond the last line. Returns its value. */
export function addGridLine(model, axis) {
  const lines = axis === "x" ? model.grid.x_lines : model.grid.y_lines;
  const n = lines.length;
  const spacing = n >= 2 ? lines[n - 1] - lines[n - 2] : 6;
  const v = +((n ? lines[n - 1] : 0) + (spacing || 6)).toFixed(3);
  lines.push(v);
  lines.sort((a, b) => a - b);
  relabelGrid(model.grid);
  return v;
}

/** Remove a grid line (a grid keeps at least 2 lines per direction).
    Members are untouched — anything outside the grid is kept. */
export function removeGridLine(model, axis, idx) {
  const lines = axis === "x" ? model.grid.x_lines : model.grid.y_lines;
  if (lines.length <= 2 || idx < 0 || idx >= lines.length) return false;
  lines.splice(idx, 1);
  relabelGrid(model.grid);
  return true;
}

/* ---- stories.
   Height / order edits recompute every elevation AND remap member/shell
   z-coordinates story by story, so elements stay attached to their story. */
function storySpans(model) {
  return model.stories.map(s => ({
    name: s.name, zb: s.elevation - s.height, zt: s.elevation,
  }));
}

function recomputeElevations(model) {
  let z = 0;
  for (const s of model.stories) { z += s.height; s.elevation = +z.toFixed(6); }
}

/** Remap all member/shell z-coords from an old story-span snapshot to the
    current spans (linear within each story: bottoms→bottoms, tops→tops). */
function remapStoryZ(model, oldSpans) {
  const oldBy = {}; for (const o of oldSpans) oldBy[o.name] = o;
  const newBy = {}; for (const n of storySpans(model)) newBy[n.name] = n;
  const mapz = (story, z) => {
    const o = oldBy[story], n = newBy[story];
    if (!o || !n) return z;
    const t = (o.zt - o.zb) > 1e-9 ? (z - o.zb) / (o.zt - o.zb) : 1;
    return +(n.zb + t * (n.zt - n.zb)).toFixed(6);
  };
  for (const mm of model.members) {
    mm.pi[2] = mapz(mm.story, mm.pi[2]);
    mm.pj[2] = mapz(mm.story, mm.pj[2]);
    mm.length = dist(mm.pi, mm.pj);
  }
  for (const sh of model.shells)
    for (const c of sh.corners) c[2] = mapz(sh.story, c[2]);
}

export function setStoryHeight(model, name, h) {
  const st = storyByName(model, name);
  if (!st || !isFinite(h) || h <= 0) return false;
  const snap = storySpans(model);
  st.height = h;
  recomputeElevations(model);
  remapStoryZ(model, snap);
  return true;
}

export function renameStory(model, oldName, newName) {
  if (!newName || newName === oldName) return false;
  if (model.stories.some(s => s.name === newName)) return false;
  const st = storyByName(model, oldName);
  if (!st) return false;
  st.name = newName;
  for (const mm of model.members) if (mm.story === oldName) mm.story = newName;
  for (const sh of model.shells) if (sh.story === oldName) sh.story = newName;
  if (model.story_masses && model.story_masses[oldName] !== undefined) {
    model.story_masses[newName] = model.story_masses[oldName];
    delete model.story_masses[oldName];
  }
  if (model.story_diaphragm && model.story_diaphragm[oldName] !== undefined) {
    model.story_diaphragm[newName] = model.story_diaphragm[oldName];
    delete model.story_diaphragm[oldName];
  }
  for (const p of Object.values(model.patterns || {}))
    for (const f of (p.story_forces || []))
      if (f.story === oldName) f.story = newName;
  return true;
}

function uniqueStoryName(model) {
  let mx = 0;
  for (const s of model.stories) {
    const g = /^Story(\d+)$/.exec(s.name);
    if (g) mx = Math.max(mx, +g[1]);
  }
  let n = mx + 1;
  while (model.stories.some(s => s.name === `Story${n}`)) n++;
  return `Story${n}`;
}

/** Insert an empty story above/below `refName` (same height as the
    reference story). Stories above translate up. Returns the new name. */
export function insertStory(model, refName, where = "above") {
  const i = model.stories.findIndex(s => s.name === refName);
  if (i < 0) return null;
  const snap = storySpans(model);
  const ref = model.stories[i];
  const st = { name: uniqueStoryName(model), height: ref.height, elevation: 0 };
  model.stories.splice(where === "above" ? i + 1 : i, 0, st);
  recomputeElevations(model);
  remapStoryZ(model, snap);
  if (model.story_masses) model.story_masses[st.name] = 0;
  return st.name;
}

/** Elements assigned to a story (deleted along with it). */
export function storyElementCounts(model, name) {
  return {
    members: model.members.filter(m => m.story === name).length,
    shells: model.shells.filter(s => s.story === name).length,
  };
}

/** Delete a story: its members/shells (and their loads) are removed and the
    stories above translate down. Blocked for the last remaining story. */
export function deleteStory(model, name) {
  if (model.stories.length <= 1) return false;
  const i = model.stories.findIndex(s => s.name === name);
  if (i < 0) return false;
  const snap = storySpans(model);
  for (const mm of model.members.filter(m => m.story === name))
    eraseElement(model, { type: "member", uid: mm.uid });
  for (const sh of model.shells.filter(s => s.story === name))
    eraseElement(model, { type: "shell", uid: sh.uid });
  model.stories.splice(i, 1);
  if (model.story_masses) delete model.story_masses[name];
  if (model.story_diaphragm) delete model.story_diaphragm[name];
  for (const p of Object.values(model.patterns || {}))
    p.story_forces = (p.story_forces || []).filter(f => f.story !== name);
  recomputeElevations(model);
  remapStoryZ(model, snap);
  return true;
}

/* ================================================================
   v0.4 — time-history cases
   ================================================================ */

/** Ramped decaying sine acceleration record (m/s²) — the "sine demo" seed. */
export function sineRecord(dt = 0.02, dur = 8, freq = 1.2, amp = 2.5) {
  const n = Math.round(dur / dt);
  const out = [];
  for (let i = 0; i <= n; i++) {
    const t = i * dt;
    const env = Math.min(t / 1.0, 1) * Math.exp(-0.18 * Math.max(t - 4, 0));
    out.push(+(amp * env * Math.sin(2 * Math.PI * freq * t)).toFixed(4));
  }
  return out;
}

export function addThCase(model, base = "TH") {
  const name = uniqueKey(model.th_cases, base);
  model.th_cases[name] = {
    name, direction: "X", accel: sineRecord(),
    dt: 0.02, damping: 0.05, scale: 1.0, function: "",
  };
  return name;
}

export function renameThCase(model, oldName, newName) {
  if (!newName || newName === oldName || model.th_cases[newName]) return false;
  model.th_cases[newName] = { ...model.th_cases[oldName], name: newName };
  delete model.th_cases[oldName];
  return true;
}

export function deleteThCase(model, name) {
  delete model.th_cases[name];
  return true;
}

/* ---------------- v0.6: staged construction cases ---------------- */
export function addStagedCase(model, base = "STAGE") {
  model.staged_cases = model.staged_cases || {};
  const name = uniqueKey(model.staged_cases, base);
  const pat = model.patterns && model.patterns.DEAD ? "DEAD"
    : (patternNames(model)[0] || "DEAD");
  model.staged_cases[name] = {
    name, pattern: pat, stages: "per_story", include_live: {},
  };
  return name;
}

export function renameStagedCase(model, oldName, newName) {
  if (!newName || newName === oldName || model.staged_cases[newName]) return false;
  model.staged_cases[newName] = { ...model.staged_cases[oldName], name: newName };
  delete model.staged_cases[oldName];
  return true;
}

export function deleteStagedCase(model, name) {
  delete model.staged_cases[name];
  return true;
}

/** Parse a comma/whitespace-separated acceleration record. Returns numbers
    (silently dropping empty tokens) or null when any token is not a number. */
export function parseAccel(text) {
  const toks = String(text).trim().split(/[\s,;]+/).filter(t => t.length);
  if (!toks.length) return [];
  const out = [];
  for (const t of toks) {
    const v = parseFloat(t);
    if (!isFinite(v)) return null;
    out.push(v);
  }
  return out;
}

/* ---------------- v0.3: section library ---------------- */
/** Add a library entry (property-based section, e.g. an AISC W-shape) as a
    frame section. Returns the section name, or null if it already exists. */
export function addLibraryFrameSection(model, entry, material) {
  if (model.sections[entry.name]) return null;
  model.sections[entry.name] = {
    name: entry.name, material: material || defaultMaterial(model),
    A: entry.A, I33: entry.I33, I22: entry.I22, J: entry.J,
    // drawing-only dims when the library provides them (flange width / depth)
    b: entry.b || 0, h: entry.h || 0,
    shape: "W",
  };
  return entry.name;
}
