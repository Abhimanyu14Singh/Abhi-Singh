/* SkyFrame — ETABS Groups: pure model helpers (no DOM, no imports).

   CONTRACT "Groups and user-defined staged construction":
     model.groups = {name: {members: [uid], shells: [uid], links: [uid],
                            points: [[x, y, z]], color: "<css>"}}
     SectionCut.group (optional) · StagedCase.stages = "per_story" | [stage]
     stage = {name, duration_days, operations: [
       {op: "add", group, age_days} | {op: "remove", group} |
       {op: "load", group, pattern, scale}]}

   These mirror skyframe/core/groups.py (normalize / prune / rename /
   delete / stage validation) so the UI keeps a model consistent BEFORE it
   is posted. The key order of every dict written here matches the backend
   normalisation (members, shells, links, points, color) so a round trip
   through POST /api/model is identical. `groups` is removed from the model
   when it becomes empty (the backend omits it then). */

export const GROUP_KINDS = ["members", "shells", "links"];
export const STAGE_OPS = ["add", "remove", "load"];
export const DEFAULT_ADD_AGE_DAYS = 28;
const PT_TOL = 1e-6;

/** ETABS-like default palette for new groups. */
export const GROUP_PALETTE = ["#e6194b", "#3cb44b", "#ffb000", "#4363d8", "#f58231",
  "#911eb4", "#42d4f4", "#f032e6", "#9acd32", "#fabed4", "#469990", "#dcbeff",
  "#9a6324", "#800000", "#aaffc3", "#808000"];

/** selection ref type → group list key */
export const REF_KIND = { member: "members", shell: "shells", link: "links" };
export const KIND_REF = { members: "member", shells: "shell", links: "link" };

export const groupsOf = m => (m && m.groups && typeof m.groups === "object") ? m.groups : {};
export const groupNames = m => Object.keys(groupsOf(m));

/** Canonical group dict (backend key order, lists copied). */
export function normGroup(g) {
  g = g || {};
  return {
    members: [...(g.members || [])].map(String),
    shells: [...(g.shells || [])].map(String),
    links: [...(g.links || [])].map(String),
    points: (g.points || []).map(p => [+p[0], +p[1], +p[2]]),
    color: typeof g.color === "string" ? g.color : "",
  };
}

export function nextGroupColor(m) {
  const used = new Set(Object.values(groupsOf(m)).map(g => (g.color || "").toLowerCase()));
  return GROUP_PALETTE.find(c => !used.has(c)) || GROUP_PALETTE[groupNames(m).length % GROUP_PALETTE.length];
}

export function uniqueGroupName(m, base = "GROUP") {
  const g = groupsOf(m);
  let i = 1;
  while (g[`${base}${i}`]) i++;
  return `${base}${i}`;
}

/** Drop the `groups` key when empty (backend to_dict omits it). */
export function tidyGroups(m) {
  if (m && m.groups && !Object.keys(m.groups).length) delete m.groups;
}

export const samePoint = (a, b) => [0, 1, 2].every(i => Math.abs(+a[i] - +b[i]) < PT_TOL);

/** Every place a group is referenced: section cuts + staged-case stage lists. */
export function groupReferences(m, name) {
  const refs = [];
  for (const c of (m.section_cuts || []))
    if (c && c.group === name) refs.push(`section cut ${c.name}`);
  for (const sc of Object.values(m.staged_cases || {}))
    if (Array.isArray(sc.stages) &&
        sc.stages.some(st => (st.operations || []).some(op => op.group === name)))
      refs.push(`staged case ${sc.name}`);
  return refs;
}

/** Rename a group and every section-cut / stage-operation reference. */
export function renameGroup(m, oldName, newName) {
  newName = (newName || "").trim();
  const g = groupsOf(m);
  if (!g[oldName] || !newName || newName === oldName || g[newName]) return false;
  m.groups = Object.fromEntries(Object.entries(g).map(([k, v]) => [k === oldName ? newName : k, v]));
  for (const c of (m.section_cuts || [])) if (c && c.group === oldName) c.group = newName;
  for (const sc of Object.values(m.staged_cases || {}))
    if (Array.isArray(sc.stages))
      for (const st of sc.stages)
        for (const op of (st.operations || [])) if (op.group === oldName) op.group = newName;
  return true;
}

/** Delete a group. Refused (false) while referenced unless `force` (then
    cuts lose their group filter and the naming stage operations are dropped). */
export function deleteGroup(m, name, force = false) {
  const g = groupsOf(m);
  if (!g[name]) return false;
  if (groupReferences(m, name).length && !force) return false;
  for (const c of (m.section_cuts || [])) if (c && c.group === name) delete c.group;
  for (const sc of Object.values(m.staged_cases || {}))
    if (Array.isArray(sc.stages))
      for (const st of sc.stages)
        st.operations = (st.operations || []).filter(op => op.group !== name);
  delete g[name];
  tidyGroups(m);
  return true;
}

/** Drop references to deleted members / shells / links. Returns {group: {kind: [uids]}}. */
export function pruneGroups(m) {
  const known = {
    members: new Set((m.members || []).map(x => x.uid)),
    shells: new Set((m.shells || []).map(x => x.uid)),
    links: new Set((m.links || []).map(x => x.uid)),
  };
  const changed = {};
  for (const [name, g] of Object.entries(groupsOf(m)))
    for (const k of GROUP_KINDS) {
      const lst = g[k] || [];
      const gone = lst.filter(u => !known[k].has(u));
      if (gone.length) {
        g[k] = lst.filter(u => known[k].has(u));
        (changed[name] = changed[name] || {})[k] = gone;
      }
    }
  return changed;
}

/** Remove one object from every group (kind: members|shells|links). */
export function removeObjectFromGroups(m, kind, uid) {
  const hit = [];
  for (const [name, g] of Object.entries(groupsOf(m)))
    if ((g[kind] || []).includes(uid)) { g[kind] = g[kind].filter(u => u !== uid); hit.push(name); }
  return hit;
}

/** Rename an object uid in every group. */
export function renameObjectInGroups(m, kind, oldUid, newUid) {
  const hit = [];
  for (const [name, g] of Object.entries(groupsOf(m)))
    if ((g[kind] || []).includes(oldUid)) { g[kind] = g[kind].map(u => u === oldUid ? newUid : u); hit.push(name); }
  return hit;
}

/** Move (newPt) or delete (null) a group point. */
export function movePointInGroups(m, oldPt, newPt) {
  const hit = [];
  for (const [name, g] of Object.entries(groupsOf(m))) {
    const pts = g.points || [];
    if (!pts.some(p => samePoint(p, oldPt))) continue;
    g.points = newPt ? pts.map(p => samePoint(p, oldPt) ? [+newPt[0], +newPt[1], +newPt[2]] : p)
      : pts.filter(p => !samePoint(p, oldPt));
    hit.push(name);
  }
  return hit;
}

/** Erase hook (modeledit.eraseElement): keep groups consistent. */
export function onObjectErased(m, ref) {
  if (!m || !m.groups || !ref) return;
  const kind = REF_KIND[ref.type];
  if (kind) removeObjectFromGroups(m, kind, ref.uid);
}

/** Names of every group containing an object. */
export function groupsContaining(m, kind, uid) {
  return Object.entries(groupsOf(m)).filter(([, g]) => (g[kind] || []).includes(uid)).map(([n]) => n);
}

/** Stage load operations naming a pattern (pattern refs / rename hooks). */
export function stagePatternRefs(m, pattern) {
  return Object.values(m.staged_cases || {})
    .filter(sc => Array.isArray(sc.stages) &&
      sc.stages.some(st => (st.operations || []).some(op => op.op === "load" && op.pattern === pattern)))
    .map(sc => sc.name);
}
export function renamePatternInStages(m, oldName, newName) {
  for (const sc of Object.values(m.staged_cases || {}))
    if (Array.isArray(sc.stages))
      for (const st of sc.stages)
        for (const op of (st.operations || []))
          if (op.op === "load" && op.pattern === oldName) op.pattern = newName;
}

/** Validate a stage list (mirrors groups.validate_user_stages) by simulating
    the activation sequence. Returns {errors: [..], warnings: [..]}. A remove
    of objects that are not active is an ERROR in the backend; we report it
    as an error too, plus a softer warning when a group is removed twice /
    loaded while nothing of it is active. */
export function checkStages(m, stages) {
  const errors = [], warnings = [];
  const groups = groupsOf(m);
  if (!Array.isArray(stages) || !stages.length) {
    errors.push("Define at least one stage.");
    return { errors, warnings };
  }
  const names = new Set();
  const active = { members: new Set(), shells: new Set(), links: new Set() };
  stages.forEach((st, i) => {
    const sn = (st.name || "").trim();
    const where = `Stage ${i + 1}${sn ? ` (${sn})` : ""}`;
    if (!sn) errors.push(`${where}: needs a name.`);
    else if (names.has(sn)) errors.push(`${where}: duplicate stage name.`);
    names.add(sn);
    if (!(isFinite(st.duration_days) && st.duration_days >= 0)) errors.push(`${where}: duration must be ≥ 0 days.`);
    if (!(st.operations || []).length) warnings.push(`${where}: has no operations.`);
    for (const op of (st.operations || [])) {
      const g = groups[op.group];
      if (!op.group || !g) { errors.push(`${where}: ${op.op} — group "${op.group || ""}" does not exist.`); continue; }
      const ng = normGroup(g);
      const nObj = GROUP_KINDS.reduce((a, k) => a + ng[k].length, 0);
      if (op.op === "add") {
        if (!(isFinite(op.age_days) && op.age_days >= 0)) errors.push(`${where}: add ${op.group} — age must be ≥ 0 days.`);
        if (!nObj && !ng.points.length) warnings.push(`${where}: add ${op.group} — the group is empty.`);
        for (const k of GROUP_KINDS) {
          const dup = ng[k].filter(u => active[k].has(u));
          if (dup.length) errors.push(`${where}: add ${op.group} — ${k} ${dup.slice(0, 4).join(", ")}${dup.length > 4 ? "…" : ""} already in the structure.`);
          ng[k].forEach(u => active[k].add(u));
        }
      } else if (op.op === "remove") {
        let present = 0;
        for (const k of GROUP_KINDS) {
          const miss = ng[k].filter(u => !active[k].has(u));
          present += ng[k].length - miss.length;
          if (miss.length) errors.push(`${where}: remove ${op.group} — ${k} ${miss.slice(0, 4).join(", ")}${miss.length > 4 ? "…" : ""} not present in the structure.`);
          ng[k].forEach(u => active[k].delete(u));
        }
        if (!present) warnings.push(`${where}: remove ${op.group} — nothing of this group is present.`);
      } else if (op.op === "load") {
        if (!(m.patterns || {})[op.pattern]) errors.push(`${where}: load ${op.group} — pattern "${op.pattern || ""}" does not exist.`);
        if (!isFinite(op.scale)) errors.push(`${where}: load ${op.group} — scale must be a number.`);
        const anyActive = GROUP_KINDS.some(k => ng[k].some(u => active[k].has(u)));
        if (!anyActive) warnings.push(`${where}: load ${op.group} — no object of the group is active yet (load skipped).`);
      } else errors.push(`${where}: unknown operation "${op.op}".`);
    }
  });
  return { errors, warnings };
}

/** Canonical stage / operation dicts (backend key order, explicit defaults). */
export function normOp(op) {
  if (op.op === "add") return { op: "add", group: op.group, age_days: +op.age_days };
  if (op.op === "remove") return { op: "remove", group: op.group };
  return { op: "load", group: op.group, pattern: op.pattern, scale: +op.scale };
}
export function normStage(st) {
  return { name: String(st.name || "").trim(), duration_days: +st.duration_days || 0,
    operations: (st.operations || []).map(normOp) };
}

/** Validate model.groups (mirrors groups.validate_group). Returns an error string or null. */
export function validateGroups(m) {
  const g = m && m.groups;
  if (g == null) return null;
  if (typeof g !== "object" || Array.isArray(g)) return "groups must be a dict name -> group";
  const known = {
    members: new Set((m.members || []).map(x => x.uid)),
    shells: new Set((m.shells || []).map(x => x.uid)),
    links: new Set((m.links || []).map(x => x.uid)),
  };
  for (const [name, gr] of Object.entries(g)) {
    if (!name.trim()) return "group: name must be a non-empty string";
    for (const k of Object.keys(gr || {}))
      if (![...GROUP_KINDS, "points", "color"].includes(k)) return `Group ${name}: unknown key '${k}'`;
    for (const k of GROUP_KINDS) {
      const seen = new Set();
      for (const u of (gr[k] || [])) {
        if (seen.has(u)) return `Group ${name}: duplicate ${k.slice(0, -1)} '${u}'`;
        seen.add(u);
        if (!known[k].has(u)) return `Group ${name}: unknown ${k.slice(0, -1)} '${u}'`;
      }
    }
    for (const p of (gr.points || []))
      if (!Array.isArray(p) || p.length !== 3 || !p.every(v => typeof v === "number" && isFinite(v)))
        return "group: every point must be a finite [x, y, z]";
  }
  return null;
}
