/* SkyFrame ?mock=1 — response-spectrum completeness (CONTRACT "Response
   spectrum: eccentricity, U3 / angle, directional combination, shell
   forces"). Post-processes mockResults():

   * every RS case gets shell_forces (positive envelope of the mock lateral
     case's shell resultants, same per-quad shape as static cases);
   * U3 / angle / several directions: per direction X·cos, Y·sin, Z from the
     gravity case; SRSS / ABS (scale factor) / CQC3 (= SRSS in the mock);
   * eccentricity: torques = ecc × story force × perpendicular plan
     dimension (results.rs_cases[n].rs.ecc), envelope grown by |torsion|;
   * rs_combos CQC3 / ABS; modal participation gains uz / gamma_z when
     vertical mass is requested.
   Plausible numbers only — the real values come from the backend. */

const rxmIsNum = v => typeof v === "number" && isFinite(v);

/** Apply fn(values…) to every number of the case dicts (structure of the
    first; member_stations x arrays are copied). */
function rxmMap(fn, ...cs) {
  const a = cs[0];
  const walk = (key, ...vs) => {
    const v0 = vs[0];
    if (rxmIsNum(v0)) return fn(...vs.map(v => (rxmIsNum(v) ? v : 0)));
    if (Array.isArray(v0)) {
      if (key === "x") return v0.slice();
      return v0.map((_, i) => walk(i, ...vs.map(v => (Array.isArray(v) ? v[i] : 0))));
    }
    if (v0 && typeof v0 === "object") {
      const out = {};
      for (const k of Object.keys(v0)) out[k] = walk(k, ...vs.map(v => (v && typeof v === "object" ? v[k] : 0)));
      return out;
    }
    return v0;
  };
  const out = {};
  for (const k of ["node_disp", "reactions", "base", "member_forces", "member_stations", "story", "shell_forces"])
    if (a && a[k]) out[k] = walk(k, ...cs.map(c => (c || {})[k]));
  return out;
}

const rxmEnv = (c, f) => rxmMap(v => Math.abs(v) * f, c);

function rxmLoads(rc) {
  if (Array.isArray(rc.loads) && rc.loads.length)
    return rc.loads.map(l => ({ dir: l.dir, scale: rxmIsNum(l.scale) ? l.scale : 1 }));
  return [{ dir: rc.direction || "X", scale: 1 }];
}

function rxmExtents(model) {
  const g = model.grid || {};
  const xs = g.x_lines || [], ys = g.y_lines || [];
  const span = a => (a.length ? Math.max(...a) - Math.min(...a) : 0);
  return [span(xs), span(ys)];
}

function rxmDirCombine(parts, method, sf) {
  if (parts.length === 1) return parts[0];
  if (method === "ABS") {
    return rxmMap((...v) => {
      const tot = v.reduce((s, x) => s + x, 0);
      return Math.max(...v.map(x => x + sf * (tot - x)));
    }, ...parts);
  }
  return rxmMap((...v) => Math.sqrt(v.reduce((s, x) => s + x * x, 0)), ...parts);
}

export function rsxMockAugment(model, r) {
  if (!r || !r.rs_cases) return r;
  const cases = r.cases || {};
  const ex = cases.EQX, ey = cases.EQY, dead = cases.DEAD;
  const [Lx, Ly] = rxmExtents(model);
  for (const [name, rc] of Object.entries(model.rs_cases || {})) {
    const cur = r.rs_cases[name];
    if (!cur) continue;
    const s = (rxmIsNum(rc.scale) ? rc.scale : 1) * 1.12;
    const X = ex ? rxmEnv(ex, s) : cur, Y = ey ? rxmEnv(ey, s) : cur;
    const loads = rxmLoads(rc);
    const angle = rxmIsNum(rc.angle) ? rc.angle : 0;
    const ext = (Array.isArray(rc.loads) && rc.loads.length) || rc.direction === "Z" || angle !== 0;
    const ecc = rxmIsNum(rc.ecc) ? rc.ecc : 0;
    let res = cur;
    if (ext) {
      const a = angle * Math.PI / 180, c = Math.cos(a), sn = Math.sin(a);
      const parts = loads.map(l => {
        if (l.dir === "Z") return dead ? rxmEnv(dead, 0.25 * s * l.scale) : rxmEnv(cur, 0.1);
        const [ux, uy] = l.dir === "X" ? [c, sn] : [-sn, c];
        return rxmMap((p, q) => l.scale * Math.hypot(ux * p, uy * q), X, Y);
      });
      const method = rc.dir_combo || "SRSS";
      res = rxmDirCombine(parts, method === "ABS" ? "ABS" : "SRSS", rxmIsNum(rc.dir_sf) ? rc.dir_sf : 1);
      res.rs = { loads: loads.map(l => ({ dir: l.dir, vector: l.dir === "Z" ? [0, 0, 1] : (l.dir === "X" ? [c, sn, 0] : [-sn, c, 0]), scale: 9.80665 * s / 1.12 * l.scale })), dir_combo: method };
    } else if (!cur.shell_forces && (ex || ey) && (ex || ey).shell_forces) {
      const src = rc.direction === "Y" ? (ey || ex) : (ex || ey);
      res = { ...cur, shell_forces: rxmEnv({ shell_forces: src.shell_forces }, s).shell_forces };
    }
    if (ecc > 0) {
      const hz = loads.filter(l => l.dir !== "Z");
      const torques = {}, forces = {};
      const stories = (model.stories || []).map(st => st.name);
      stories.forEach((sn, i) => {
        const row = (res.story || {})[sn] || {}, up = (res.story || {})[stories[i + 1]] || {};
        forces[sn] = {};
        let t2 = 0;
        for (const l of hz) {
          const k = l.dir === "Y" ? "shear_y" : "shear_x";
          const F = Math.max((row[k] || 0) - (up[k] || 0), 0);
          forces[sn][l.dir] = F;
          const T = ecc * F * (l.dir === "Y" ? Lx : Ly);
          t2 += T * T;
        }
        torques[sn] = Math.sqrt(t2);
      });
      const info = res.rs || {};
      res = rxmMap(v => v * (1 + 2 * ecc), res);
      res.rs = { ...info, ecc: { ratio: ecc, forces, torques } };
      if (rc.ecc_ax) res.rs.ecc.ax = Object.fromEntries(stories.map(s2 => [s2, 1.0]));
    }
    r.rs_cases[name] = res;
  }
  for (const [name, cb] of Object.entries(model.rs_combos || {})) {
    const a = r.rs_cases[cb.name_x], b = r.rs_cases[cb.name_y], cur = r.rs_cases[name];
    if (!a || !b || !cur) continue;
    if (cb.method === "CQC3" || cb.method === "ABS") {
      r.rs_cases[name] = rxmMap(cb.method === "ABS" ? (p, q) => p + q : (p, q) => Math.hypot(p, q), a, b);
    } else if (!cur.shell_forces && a.shell_forces) {
      const f = cb.method === "SRSS" ? (p, q) => Math.hypot(p, q) : (p, q) => Math.max(p + 0.3 * q, 0.3 * p + q);
      cur.shell_forces = rxmMap(f, { shell_forces: a.shell_forces }, { shell_forces: b.shell_forces || {} }).shell_forces;
    }
  }
  const wantUz = !!((model.mass_options || {}).include_vertical) ||
    Object.values(model.rs_cases || {}).some(rc => rxmLoads(rc).some(l => l.dir === "Z"));
  const part = (r.modal || {}).participation || [];
  if (wantUz && part.length) {
    const n = part.length;
    part.forEach((p, i) => {
      const uz = i === n - 1 ? 0.62 : (i === n - 2 ? 0.21 : 0.01);
      p.uz = uz;
      p.gamma_z = Math.sqrt(uz) * 3.0;
    });
  }
  return r;
}

export default { rsxMockAugment };
