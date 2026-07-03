/* SkyFrame mock backend — matches CONTRACT.md shapes so the UI is fully
   explorable without the Flask server (offline / ?mock=1). */

const G = 9.80665;

function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const XL = i => String.fromCharCode(65 + i);

/** Mirror of quick_building() → model.to_dict(). */
export function mockModel(p = {}) {
  const o = {
    name: "Quick Building", bays_x: 3, bay_width_x: 6, bays_y: 2, bay_width_y: 6,
    stories: 4, story_height: 3.2, E: 25_000_000, column_size: 0.5,
    beam_b: 0.3, beam_h: 0.6, dead_udl: 25, live_udl: 10,
    quake_coeff: 0.08, base_fixity: "fixed", ...p,
  };
  const xs = Array.from({ length: o.bays_x + 1 }, (_, i) => i * o.bay_width_x);
  const ys = Array.from({ length: o.bays_y + 1 }, (_, j) => j * o.bay_width_y);
  const grid = {
    x_lines: xs, y_lines: ys,
    x_labels: xs.map((_, i) => XL(i)),
    y_labels: ys.map((_, j) => String(j + 1)),
  };
  const stories = [];
  for (let s = 0; s < o.stories; s++) {
    stories.push({
      name: `Story${s + 1}`, height: o.story_height,
      elevation: (s + 1) * o.story_height,
    });
  }
  const members = [];
  const dist = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
  const add = (kind, section, pi, pj, story, uid) =>
    members.push({ uid, kind, section, pi, pj, story, length: dist(pi, pj), releases: "" });

  stories.forEach((st, si) => {
    const zt = st.elevation, zb = st.elevation - st.height;
    ys.forEach((y, yi) => xs.forEach((x, xi) =>
      add("column", "COL", [x, y, zb], [x, y, zt], st.name,
        `C${si + 1}-${XL(xi)}${yi + 1}`)));
    ys.forEach((y, yi) => {
      for (let xi = 0; xi < xs.length - 1; xi++)
        add("beam", "BEAM", [xs[xi], y, zt], [xs[xi + 1], y, zt], st.name,
          `BX${si + 1}-${XL(xi)}${yi + 1}`);
    });
    xs.forEach((x, xi) => {
      for (let yi = 0; yi < ys.length - 1; yi++)
        add("beam", "BEAM", [x, ys[yi], zt], [x, ys[yi + 1], zt], st.name,
          `BY${si + 1}-${XL(xi)}${yi + 1}`);
    });
  });

  // story mass from dead UDL on beams (as builder.py does)
  const story_masses = {};
  stories.forEach(st => {
    let w = 0;
    for (const m of members)
      if (m.story === st.name && m.kind === "beam") w += o.dead_udl * m.length;
    story_masses[st.name] = w / G;
  });

  // v0.2: shell sections + a couple of walls & slabs (contract shapes)
  const shell_sections = {
    SH200: { name: "SH200", material: "CONC", thickness: 0.2 },
    SLAB150: { name: "SLAB150", material: "CONC", thickness: 0.15 },
  };
  const shells = [];
  const wallLen = Math.min(o.bay_width_x, 8);
  for (let s = 0; s < Math.min(2, stories.length); s++) {
    const st = stories[s], zb = st.elevation - st.height, zt = st.elevation;
    shells.push({
      uid: `W${s + 1}`, kind: "wall", behavior: "shell", section: "SH200",
      corners: [[0, 0, zb], [wallLen, 0, zb], [wallLen, 0, zt], [0, 0, zt]],
      mesh_size: 1.0, story: st.name,
    });
  }
  if (xs.length >= 2 && ys.length >= 2) {
    const z1 = stories[0].elevation;
    shells.push({
      uid: "SL1", kind: "slab", behavior: "shell", section: "SLAB150",
      corners: [[xs[0], ys[0], z1], [xs[1], ys[0], z1], [xs[1], ys[1], z1], [xs[0], ys[1], z1]],
      mesh_size: 1.5, story: stories[0].name,
    });
    if (stories.length > 1) {
      const z2 = stories[1].elevation;
      const xl = xs[xs.length - 2], xr = xs[xs.length - 1];
      const yl = ys[ys.length - 2], yr = ys[ys.length - 1];
      shells.push({
        uid: "SL2", kind: "slab", behavior: "membrane", section: "SLAB150",
        corners: [[xl, yl, z2], [xr, yl, z2], [xr, yr, z2], [xl, yr, z2]],
        mesh_size: 1.5, story: stories[1].name,
      });
    }
  }

  // load patterns with per-member UDLs + area loads (contract MemberLoad/AreaLoad)
  const beamUdls = w => members.filter(m => m.kind === "beam").map(m => ({
    member_uid: m.uid, kind: "udl", w, w2: 0, a: 0, b: 1, direction: "gravity",
  }));
  const patterns = {
    DEAD: {
      name: "DEAD", member_loads: beamUdls(o.dead_udl),
      area_loads: shells.filter(s => s.kind === "slab").map(s => ({ region_uid: s.uid, q: 2.0 })),
    },
    LIVE: {
      name: "LIVE", member_loads: beamUdls(o.live_udl),
      area_loads: shells.filter(s => s.kind === "slab").map(s => ({ region_uid: s.uid, q: 3.0 })),
    },
    EQX: { name: "EQX", member_loads: [], area_loads: [] },
    EQY: { name: "EQY", member_loads: [], area_loads: [] },
  };

  return {
    name: o.name,
    materials: { CONC: { name: "CONC", E: o.E, nu: 0.2, unit_weight: 24 } },
    sections: {
      COL: { name: "COL", material: "CONC", b: o.column_size, h: o.column_size },
      BEAM: { name: "BEAM", material: "CONC", b: o.beam_b, h: o.beam_h },
    },
    shell_sections, shells,
    grid, stories, members,
    base_fixity: o.base_fixity,
    supports: [], nodal_masses: [], rigid_diaphragms: true,
    story_masses,
    patterns,
    cases: {
      DEAD: { name: "DEAD", patterns: { DEAD: 1 } },
      LIVE: { name: "LIVE", patterns: { LIVE: 1 } },
      EQX: { name: "EQX", patterns: { EQX: 1 } },
      EQY: { name: "EQY", patterns: { EQY: 1 } },
    },
    combos: {
      "1.2D + 1.6L": { name: "1.2D + 1.6L", cases: { DEAD: 1.2, LIVE: 1.6 } },
      "1.2D + 1.0L + 1.0EX": { name: "1.2D + 1.0L + 1.0EX", cases: { DEAD: 1.2, LIVE: 1.0, EQX: 1.0 } },
      "1.2D + 1.0L + 1.0EY": { name: "1.2D + 1.0L + 1.0EY", cases: { DEAD: 1.2, LIVE: 1.0, EQY: 1.0 } },
      "0.9D + 1.0EX": { name: "0.9D + 1.0EX", cases: { DEAD: 0.9, EQX: 1.0 } },
    },
    num_modes: Math.min(3 * o.stories, 12),
    _mock_params: o,
  };
}

/** Mock results.to_dict() for a mock (or real-shaped) model dict. */
export function mockResults(model) {
  const rnd = mulberry32(1234567);
  const jit = (a = 0.05) => 1 + (rnd() - 0.5) * 2 * a;

  const stories = model.stories;
  const H = stories[stories.length - 1].elevation;
  const storyOrder = stories.map(s => s.name);
  const storyElev = Object.fromEntries(stories.map(s => [s.name, s.elevation]));
  const elevs = [0, ...stories.map(s => s.elevation)];

  // ---- FE nodes at every unique member endpoint
  const nodes = {}; const tagOf = new Map(); let nextTag = 1;
  const key = p => p.map(v => v.toFixed(4)).join(",");
  const tagFor = p => {
    const k = key(p);
    if (!tagOf.has(k)) { tagOf.set(k, String(nextTag)); nodes[String(nextTag)] = [...p]; nextTag++; }
    return tagOf.get(k);
  };
  const members = model.members.map(m => ({
    uid: m.uid, kind: m.kind, section: m.section,
    ni: tagFor(m.pi), nj: tagFor(m.pj), story: m.story,
  }));

  // ---- shell meshes (v0.2): structured quads for shell-behavior regions.
  // Mesh nodes registered before case generation so node_disp covers them.
  const shell_quads = [];
  for (const sh of (model.shells || [])) {
    if (sh.behavior !== "shell") continue;
    const [c0, c1, c2, c3] = sh.corners;
    const lerp3 = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
    const bilin = (u, v) => lerp3(lerp3(c0, c1, u), lerp3(c3, c2, u), v);
    const du = Math.hypot(...[0, 1, 2].map(i => c1[i] - c0[i]));
    const dv = Math.hypot(...[0, 1, 2].map(i => c3[i] - c0[i]));
    const nx = Math.max(1, Math.round(du / (sh.mesh_size || 1)));
    const ny = Math.max(1, Math.round(dv / (sh.mesh_size || 1)));
    const tag = [];
    for (let j = 0; j <= ny; j++) {
      tag.push([]);
      for (let i = 0; i <= nx; i++) tag[j].push(tagFor(bilin(i / nx, j / ny)));
    }
    for (let j = 0; j < ny; j++)
      for (let i = 0; i < nx; i++)
        shell_quads.push({
          region: sh.uid,
          nodes: [tag[j][i], tag[j][i + 1], tag[j + 1][i + 1], tag[j + 1][i]],
        });
  }

  const supports = Object.keys(nodes).filter(t => nodes[t][2] < 1e-9);

  // ---- station helpers (11 stations, statics-plausible curves)
  const lenOf = {};
  for (const m of model.members) lenOf[m.uid] = m.length ||
    Math.hypot(m.pj[0] - m.pi[0], m.pj[1] - m.pi[1], m.pj[2] - m.pi[2]);
  const udlOf = patName => {
    const map = {};
    const p = model.patterns && model.patterns[patName];
    for (const l of (p && p.member_loads) || [])
      if ((l.kind || "udl") === "udl") map[l.member_uid] = l.w;
    return map;
  };
  /** Build member_stations for a case from its end forces.
      N & V2 linear; M3 = end-moment interpolation + parabolic sag (udl). */
  function buildStations(member_forces, wMap) {
    const out = {};
    const NS = 11;
    for (const mm of members) {
      const f = member_forces[mm.uid];
      if (!f) continue;
      const L = lenOf[mm.uid] || 6;
      const w = (mm.kind === "beam" && wMap) ? (wMap[mm.uid] || 0) : 0;
      const x = [], N = [], V2 = [], V3 = [], T = [], M2 = [], M3 = [];
      const Msag = w * L * L / 8;
      for (let k = 0; k < NS; k++) {
        const t = k / (NS - 1);
        x.push(+(t * L).toFixed(3));
        N.push(f[0] + (-f[6] - f[0]) * t);
        V2.push(f[1] + (-f[7] - f[1]) * t);
        V3.push(f[2] + (-f[8] - f[2]) * t);
        T.push(f[3] + (-f[9] - f[3]) * t);
        M2.push(f[4] + (-f[10] - f[4]) * t);
        M3.push(f[5] + (-f[11] - f[5]) * t + 4 * Msag * t * (1 - t));
      }
      out[mm.uid] = { x, N, V2, V3, T, M2, M3 };
    }
    return out;
  }

  // ---- masses / seismic
  const masses = model.story_masses || {};
  const W = storyOrder.reduce((a, s) => a + (masses[s] || 100) * G, 0);
  const C = (model._mock_params && model._mock_params.quake_coeff) || 0.08;
  const V = C * W;
  const wh = storyOrder.map(s => (masses[s] || 100) * G * storyElev[s]);
  const sumWh = wh.reduce((a, b) => a + b, 0) || 1;
  const storyF = storyOrder.map((s, i) => V * wh[i] / sumWh); // triangular

  const colsPerStory = model.members.filter(m => m.kind === "column" && m.story === storyOrder[0]).length || 1;

  // lateral sway profile 0..1 (soft-story-free cantilever-ish shape)
  const sway = z => Math.pow(z / H, 1.25);
  const roofDrift = H / 620; // roof displacement under EQ, plausible for RC MRF

  function lateralCase(dirX) {
    const node_disp = {}, reactions = {}, member_forces = {}, story = {};
    for (const [t, p] of Object.entries(nodes)) {
      const d = roofDrift * sway(p[2]) * jit(0.015);
      const rot = -roofDrift * 1.25 * Math.pow(Math.max(p[2], 0.01) / H, 0.25) / H * 0.6;
      node_disp[t] = dirX ? [d, 0, 0, 0, rot, 0] : [0, d, 0, -rot, 0, 0];
    }
    // reactions: base shear split between supports; overturning via axial couple
    const xs = model.grid.x_lines, ys = model.grid.y_lines;
    const cx = (xs[0] + xs[xs.length - 1]) / 2, cy = (ys[0] + ys[ys.length - 1]) / 2;
    const Mot = storyOrder.reduce((a, s, i) => a + storyF[i] * storyElev[s], 0);
    const span = dirX ? (xs[xs.length - 1] - xs[0] || 1) : (ys[ys.length - 1] - ys[0] || 1);
    let sumFx = 0, sumFy = 0, sumFz = 0;
    supports.forEach(t => {
      const p = nodes[t];
      const fh = -V / supports.length * jit(0.1);
      const lever = dirX ? (p[0] - cx) : (p[1] - cy);
      const fz = -Mot / supports.length * lever / (span * span / 4) * 2 * jit(0.08);
      const m = V / supports.length * 1.8 * jit(0.1);
      reactions[t] = dirX ? [fh, 0, fz, 0, m, 0] : [0, fh, fz, -m, 0, 0];
      sumFx += reactions[t][0]; sumFy += reactions[t][1]; sumFz += reactions[t][2];
    });
    const base = { FX: sumFx, FY: sumFy, FZ: sumFz, MX: dirX ? 0 : Mot, MY: dirX ? -Mot : 0, MZ: 0 };

    // story results
    let shear = 0;
    const storyRes = {};
    for (let i = storyOrder.length - 1; i >= 0; i--) shear += storyF[i], storyRes[storyOrder[i]] = shear;
    storyOrder.forEach((s, i) => {
      const zTop = storyElev[s], zBot = elevs[i];
      const u = roofDrift * sway(zTop), ub = roofDrift * sway(zBot);
      const dr = (u - ub) / (zTop - zBot);
      story[s] = {
        ux: dirX ? u : 0, uy: dirX ? 0 : u,
        drift_x: dirX ? dr : 0, drift_y: dirX ? 0 : dr,
        shear_x: dirX ? storyRes[s] : 0, shear_y: dirX ? 0 : storyRes[s],
      };
    });

    // member forces (local): columns carry story shear / #cols, beams frame moments
    members.forEach(mm => {
      const si = storyOrder.indexOf(mm.story);
      const Vst = storyRes[mm.story] || 0;
      if (mm.kind === "column") {
        const v = Vst / colsPerStory * jit(0.2);
        const h = stories[si].height;
        const n = Mot / supports.length / 8 * (1 - si / storyOrder.length) * jit(0.4);
        const m = v * h / 2;
        member_forces[mm.uid] = [n, v, 0, 0, 0, m, -n, -v, 0, 0, 0, m * jit(0.15)];
      } else {
        const m = Vst / colsPerStory * stories[si].height * 0.45 * jit(0.25);
        const L = 6, v = 2 * m / L;
        member_forces[mm.uid] = [0, v, 0, 0, 0, m, 0, -v, 0, 0, 0, -m * jit(0.2)];
      }
    });
    const member_stations = buildStations(member_forces, null);
    return { node_disp, reactions, base, member_forces, member_stations, story };
  }

  function gravityCase(w /* kN/m on beams */, patName) {
    const node_disp = {}, reactions = {}, member_forces = {}, story = {};
    const wMap = udlOf(patName);
    for (const mm of model.members)
      if (mm.kind === "beam" && wMap[mm.uid] == null) wMap[mm.uid] = w;
    for (const [t, p] of Object.entries(nodes)) {
      const shorten = -0.00004 * w / 25 * p[2] * jit(0.05);
      node_disp[t] = [0, 0, shorten, 0, 0, 0];
    }
    const beams = model.members.filter(m => m.kind === "beam");
    const totalW = beams.reduce((a, b) => a + w * b.length, 0);
    let sumFz = 0;
    supports.forEach(t => {
      const fz = totalW / supports.length * jit(0.15);
      reactions[t] = [0, 0, fz, 0, 0, 0]; sumFz += fz;
    });
    const base = { FX: 0, FY: 0, FZ: sumFz, MX: 0, MY: 0, MZ: 0 };
    storyOrder.forEach(s => {
      story[s] = { ux: 0, uy: 0, drift_x: 0, drift_y: 0, shear_x: 0, shear_y: 0 };
    });
    members.forEach(mm => {
      const si = storyOrder.indexOf(mm.story);
      if (mm.kind === "column") {
        const trib = totalW / colsPerStory * (storyOrder.length - si) / storyOrder.length;
        const n = -trib * jit(0.2);
        member_forces[mm.uid] = [n, 0, 0, 0, 0, n * 0.02, -n, 0, 0, 0, 0, n * 0.02];
      } else {
        const L = lenOf[mm.uid] || 6;
        const wm = wMap[mm.uid] != null ? wMap[mm.uid] : w;
        const m = wm * L * L / 11 * jit(0.15), v = wm * L / 2 * jit(0.1);
        member_forces[mm.uid] = [0, v, 0, 0, 0, -m, 0, v, 0, 0, 0, m * 0.8];
      }
    });
    const member_stations = buildStations(member_forces, wMap);
    return { node_disp, reactions, base, member_forces, member_stations, story };
  }

  const cases = {
    DEAD: gravityCase((model._mock_params && model._mock_params.dead_udl) || 25, "DEAD"),
    LIVE: gravityCase((model._mock_params && model._mock_params.live_udl) || 10, "LIVE"),
    EQX: lateralCase(true),
    EQY: lateralCase(false),
  };

  // combos = linear superposition of case dicts
  function combine(factors) {
    const out = { node_disp: {}, reactions: {}, base: { FX: 0, FY: 0, FZ: 0, MX: 0, MY: 0, MZ: 0 }, member_forces: {}, member_stations: {}, story: {} };
    const add6 = (dst, k, arr, f) => {
      if (!dst[k]) dst[k] = new Array(arr.length).fill(0);
      arr.forEach((v, i) => dst[k][i] += f * v);
    };
    for (const [cn, f] of Object.entries(factors)) {
      const c = cases[cn]; if (!c) continue;
      for (const [t, d] of Object.entries(c.node_disp)) add6(out.node_disp, t, d, f);
      for (const [t, r] of Object.entries(c.reactions)) add6(out.reactions, t, r, f);
      for (const [u, mf] of Object.entries(c.member_forces)) add6(out.member_forces, u, mf, f);
      for (const [u, st] of Object.entries(c.member_stations || {})) {
        if (!out.member_stations[u]) {
          out.member_stations[u] = { x: st.x.slice() };
          for (const k of ["N", "V2", "V3", "T", "M2", "M3"])
            out.member_stations[u][k] = new Array(st.x.length).fill(0);
        }
        for (const k of ["N", "V2", "V3", "T", "M2", "M3"])
          st[k].forEach((v, i) => out.member_stations[u][k][i] += f * v);
      }
      for (const k of Object.keys(out.base)) out.base[k] += f * c.base[k];
      for (const [s, sr] of Object.entries(c.story)) {
        if (!out.story[s]) out.story[s] = { ux: 0, uy: 0, drift_x: 0, drift_y: 0, shear_x: 0, shear_y: 0 };
        for (const k of Object.keys(sr)) out.story[s][k] += f * sr[k];
      }
    }
    return out;
  }
  const combos = {};
  for (const [name, cb] of Object.entries(model.combos || {})) combos[name] = combine(cb.cases);

  // ---- modal
  const N = model.num_modes || 6;
  const T1 = 0.075 * Math.pow(H, 0.85) * 1.35;
  const periods = [], frequencies = [], participation = [], shapes = {};
  const xs2 = model.grid.x_lines, ys2 = model.grid.y_lines;
  const cx = (xs2[0] + xs2[xs2.length - 1]) / 2, cy = (ys2[0] + ys2[ys2.length - 1]) / 2;
  for (let m = 1; m <= N; m++) {
    const order = Math.ceil(m / 3);          // 1st, 2nd… vertical order
    const dir = (m - 1) % 3;                 // 0 = X sway, 1 = Y sway, 2 = torsion
    const T = T1 / ((2 * order - 1) * (dir === 2 ? 1.35 : 1) * (dir === 1 ? 1.08 : 1));
    periods.push(T); frequencies.push(1 / T);
    const p1 = order === 1 ? 0.82 : order === 2 ? 0.11 : 0.04;
    participation.push({
      mode: m, T,
      ux: dir === 0 ? p1 : 0.0,
      uy: dir === 1 ? p1 : 0.0,
      rz: dir === 2 ? p1 : 0.005,
    });
    const shape = {};
    for (const [t, p] of Object.entries(nodes)) {
      const z = p[2];
      const phi = Math.sin((2 * order - 1) * Math.PI * z / (2 * H));
      if (dir === 0) shape[t] = [phi, 0, 0, 0, 0, 0];
      else if (dir === 1) shape[t] = [0, phi, 0, 0, 0, 0];
      else shape[t] = [-(p[1] - cy) * 0.12 * phi, (p[0] - cx) * 0.12 * phi, 0, 0, 0, phi * 0.1];
    }
    shapes[String(m)] = shape;
  }

  return {
    model_name: model.name,
    nodes, members, supports,
    story_order: storyOrder,
    story_elev: storyElev,
    shell_quads,
    cases, combos,
    modal: { periods, frequencies, participation, shapes },
  };
}
