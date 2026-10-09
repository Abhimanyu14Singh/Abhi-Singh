/* SkyFrame — ?mock=1 mirror of Groups + user-defined staged construction.

   * mockValidateGroups(model)  — the backend ValueErrors for model.groups,
     SectionCut.group and StagedCase.stages lists (POST /api/model).
   * mockUserStages(model, sc, state, ctx) — plausible results.staged[name]
     .stages for a stage list: the activation sequence is simulated, the
     cumulative load share grows with every "load" operation, inactive
     objects report zeros and a "remove" redistributes (displacements grow).
     The final stage becomes the case's final state.
   * mockCutAllows(model, cut, kind, uid) — section cut defined by group. */

import { groupsOf, normGroup, checkStages, validateGroups, GROUP_KINDS } from "./groups_model.js";

export function mockValidateGroups(m) {
  if (!m || typeof m !== "object") return null;
  const g = validateGroups(m);
  if (g) return g;
  const groups = groupsOf(m);
  for (const c of (m.section_cuts || []))
    if (c && c.group != null && !groups[c.group]) return `Section cut ${c.name}: unknown group '${c.group}'`;
  for (const sc of Object.values(m.staged_cases || {})) {
    if (!Array.isArray(sc.stages)) continue;
    const { errors } = checkStages(m, sc.stages);
    if (errors.length) return `Staged case ${sc.name}: ${errors[0]}`;
  }
  return null;
}

export function mockCutAllows(model, cut, kind, uid) {
  if (!cut || cut.group == null) return true;
  const g = groupsOf(model)[cut.group];
  return !!g && (g[kind] || []).includes(uid);
}

const scaleArr = (a, f) => a.map(v => v * f);

/** Fill state.stages (and overwrite the final state) for a stage list. */
export function mockUserStages(model, sc, state, ctx) {
  const stages = sc.stages;
  const groups = groupsOf(model);
  const { members } = ctx;                     // results members {uid, ni, nj}
  const memByUid = new Map(members.map(x => [x.uid, x]));
  const full = JSON.parse(JSON.stringify({ node_disp: state.node_disp, reactions: state.reactions,
    member_forces: state.member_forces, base: state.base }));
  // load weights per stage
  const weights = stages.map(st => (st.operations || []).filter(o => o.op === "load")
    .reduce((a, o) => a + Math.abs(+o.scale || 0) * Math.max(1, normGroup(groups[o.group]).members.length), 0));
  const total = weights.reduce((a, b) => a + b, 0);
  const active = { members: new Set(), shells: new Set(), links: new Set() };
  let cum = 0, t = 0, removed = 0;
  const out = [];
  stages.forEach((st, si) => {
    for (const op of (st.operations || [])) {
      const g = normGroup(groups[op.group]);
      if (op.op === "add") GROUP_KINDS.forEach(k => g[k].forEach(u => active[k].add(u)));
      if (op.op === "remove") { GROUP_KINDS.forEach(k => g[k].forEach(u => active[k].delete(u))); removed += g.members.length + g.shells.length; }
    }
    cum += weights[si];
    const f = total > 0 ? cum / total : (si + 1) / stages.length;
    const amp = 1 + 0.12 * removed;            // redistribution after removals
    const liveNodes = new Set();
    for (const u of active.members) { const r = memByUid.get(u); if (r) { liveNodes.add(r.ni); liveNodes.add(r.nj); } }
    const node_disp = {}, reactions = {}, member_forces = {};
    for (const [tg, d] of Object.entries(full.node_disp || {}))
      node_disp[tg] = liveNodes.has(tg) ? scaleArr(d, f * amp) : [0, 0, 0, 0, 0, 0];
    let fz = 0;
    for (const [tg, rr] of Object.entries(full.reactions || {})) {
      reactions[tg] = liveNodes.has(tg) ? scaleArr(rr, f) : [0, 0, 0, 0, 0, 0];
      fz += reactions[tg][2];
    }
    for (const [u, mf] of Object.entries(full.member_forces || {}))
      member_forces[u] = active.members.has(u) ? scaleArr(mf, f * (removed ? 1.08 : 1)) : new Array(12).fill(0);
    const b = full.base || {};
    // equilibrium: the remaining supports pick up a removed prop's share
    const want = (b.FZ || 0) * f;
    if (Math.abs(fz) > 1e-12 && Math.abs(want - fz) > 1e-9)
      for (const tg of Object.keys(reactions)) reactions[tg] = scaleArr(reactions[tg], want / fz);
    const base = { FX: (b.FX || 0) * f, FY: (b.FY || 0) * f, FZ: want, MX: (b.MX || 0) * f, MY: (b.MY || 0) * f, MZ: (b.MZ || 0) * f };
    const dur = +st.duration_days || 0;
    out.push({ name: st.name, duration_days: dur, t_start: t, t_end: t + dur,
      active: { members: [...active.members].sort(), shells: [...active.shells].sort(), links: [...active.links].sort() },
      node_disp, reactions, base, member_forces });
    t += dur;
  });
  const last = out[out.length - 1];
  if (last) {
    state.node_disp = JSON.parse(JSON.stringify(last.node_disp));
    state.reactions = JSON.parse(JSON.stringify(last.reactions));
    state.member_forces = JSON.parse(JSON.stringify(last.member_forces));
    state.base = { ...last.base };
  }
  delete state.shortening;
  state.stages = out;
  return state;
}
