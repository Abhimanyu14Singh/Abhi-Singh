/* SkyFrame model editing — pure mutations on the client-side model dict
   (same shape as BuildingModel.to_dict(), see CONTRACT.md v0.2). */

const dist = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
const near = (a, b, tol = 1e-6) =>
  Math.abs(a[0] - b[0]) < tol && Math.abs(a[1] - b[1]) < tol && Math.abs(a[2] - b[2]) < tol;

/** Ensure every v0.2 collection exists so the editor can run against an
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
  };
  model.shells.push(sh);
  return sh;
}

/* ------------------------------------------------ erase */
/** ref: {type:"member"|"shell", uid}. Also removes loads that reference it. */
export function eraseElement(model, ref) {
  let removed = false;
  if (ref.type === "member") {
    const i = model.members.findIndex(m => m.uid === ref.uid);
    if (i >= 0) { model.members.splice(i, 1); removed = true; }
    for (const p of Object.values(model.patterns))
      p.member_loads = (p.member_loads || []).filter(l => l.member_uid !== ref.uid);
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
    model.patterns[name] = { name, member_loads: [], area_loads: [] };
  const p = model.patterns[name];
  p.member_loads = p.member_loads || [];
  p.area_loads = p.area_loads || [];
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
