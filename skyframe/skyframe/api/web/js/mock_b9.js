/* SkyFrame mock (?mock=1) — frame auto mesh / output stations, named spring
   properties and link hysteresis types (CONTRACT B9 / B11).

   mockValidateB9(model)      mirrors the backend ValueErrors of POST /api/model
                              for the new fields (null = accepted).
   mockAugmentB9(model, res)  re-samples member_stations / member_deflections
                              onto each member's output-station list (option
                              stations + auto-mesh segment ends + concentrated
                              load points), so variable station counts show up
                              in the member diagrams exactly like live. */

import { autoMeshPoints as mbPoints, stationXs as mbStationXs,
  valueAt as mbValueAt, memberLength as mbLen } from "./framemesh_geom.js";
import { validateSpringProp as mbValidateSpring } from "./springdlg.js";
import { validateHyst as mbValidateHyst, isolatorLayoutOk as mbLayoutOk, HYST_TYPES as MB_HYST } from "./linkhyst.js";

const isNum = v => typeof v === "number" && isFinite(v);
const AM_KEYS = ["at_intermediate_joints", "at_intersections", "max_length", "min_segments"];

function amError(where, d) {
  if (d == null) return null;
  if (typeof d !== "object" || Array.isArray(d)) return `${where}: auto_mesh must be an object or null`;
  const bad = Object.keys(d).filter(k => !AM_KEYS.includes(k));
  if (bad.length) return `${where}: auto_mesh: unknown key(s) ${JSON.stringify(bad)}`;
  if (d.max_length != null && !(isNum(d.max_length) && d.max_length > 0)) return `${where}: auto_mesh.max_length must be a finite value > 0`;
  if (d.min_segments != null && !(Number.isInteger(+d.min_segments) && +d.min_segments >= 1)) return `${where}: auto_mesh.min_segments must be an integer >= 1`;
  return null;
}
function osError(where, d) {
  if (d == null) return null;
  const ks = d && typeof d === "object" ? Object.keys(d) : [];
  if (ks.length !== 1) return `${where}: output_stations must be {"max_spacing": s} or {"min_number": n}`;
  const v = d[ks[0]];
  if (ks[0] === "max_spacing") return isNum(v) && v > 0 ? null : `${where}: output_stations.max_spacing must be a finite value > 0`;
  if (ks[0] === "min_number") return Number.isInteger(v) && v >= 2 ? null : `${where}: output_stations.min_number must be >= 2`;
  return `${where}: output_stations: unknown key '${ks[0]}'`;
}

export function mockValidateB9(model) {
  if (!model) return null;
  const e0 = amError("frame_auto_mesh", model.frame_auto_mesh);
  if (e0) return e0;
  for (const m of model.members || []) {
    const e = amError(`Member ${m.uid}`, m.auto_mesh) || osError(`Member ${m.uid}`, m.output_stations);
    if (e) return e;
  }
  const props = model.spring_properties || {};
  for (const [n, p] of Object.entries(props)) {
    const e = mbValidateSpring(n, p);
    if (e) return e;
  }
  for (const s of model.spring_supports || []) {
    if (s.property != null && !props[s.property]) return `spring support at (${s.point.join(", ")}): unknown spring property '${s.property}'`;
    if (s.property != null && (s.stiffness || []).some(v => v !== 0)) return `spring support at (${s.point.join(", ")}): inline stiffness must be all zero when a property is set`;
    if (s.angle_deg != null && !isNum(s.angle_deg)) return `spring support at (${s.point.join(", ")}): angle_deg must be finite`;
  }
  for (const l of model.links || []) {
    if (!MB_HYST[l.link_type]) continue;
    const e = mbValidateHyst(l.uid, l.link_type, l.params || {});
    if (e) return e;
    if (!mbLayoutOk(l)) return `Link ${l.uid} (${l.link_type}): the link axis must be vertical or zero length (isolator layout)`;
  }
  return null;
}

/** Re-sample every member_stations / member_deflections block in place. */
export function mockAugmentB9(model, res) {
  if (!model || !res) return res;
  const cuts = mbPoints(model);
  const target = new Map();
  for (const m of model.members || []) {
    if (!m.output_stations) continue;          // no option: the fixed 11 stations (even when auto-meshed)
    const segEnds = (cuts.get(m.uid) || []).map(c => c.t);
    target.set(m.uid, mbStationXs(model, m, segEnds));
  }
  if (!target.size) return res;
  const seen = new Set();
  const walk = o => {
    if (!o || typeof o !== "object" || seen.has(o)) return;
    seen.add(o);
    for (const key of ["member_stations", "member_deflections"]) {
      const blk = o[key];
      if (!blk || typeof blk !== "object") continue;
      for (const [uid, xs] of target) {
        const st = blk[uid];
        if (!st || !Array.isArray(st.x) || st.x.length < 2) continue;
        const L = st.x[st.x.length - 1];
        const mL = mbLen((model.members || []).find(m => m.uid === uid));
        const sc = mL > 0 ? L / mL : 1;
        const nx = xs.map(x => x * sc);
        for (const [k, v] of Object.entries(st)) {
          if (k === "x" || !Array.isArray(v) || v.length !== st.x.length) continue;
          st[k] = nx.map(x => mbValueAt(st.x, v, x));
        }
        st.x = nx.map(x => +x.toFixed(6));
      }
    }
    for (const v of Object.values(o)) if (v && typeof v === "object") walk(v);
  };
  walk(res);
  return res;
}

