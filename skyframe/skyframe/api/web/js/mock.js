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
    members.push({ uid, kind, section, pi, pj, story, length: dist(pi, pj), releases: "", angle: 0 });

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
      // v0.5: ground-story wall gets a door + a window opening
      openings: s === 0
        ? [{ u0: 0.12, v0: 0, u1: 0.32, v1: 0.72 },
           { u0: 0.55, v0: 0.35, u1: 0.82, v1: 0.78 }]
        : [],
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
  // v0.3: equivalent-lateral story forces on the quake patterns
  const W_ = Object.values(story_masses).reduce((a, b) => a + b, 0) * G;
  const wh_ = stories.map(s => story_masses[s.name] * G * s.elevation);
  const sumWh_ = wh_.reduce((a, b) => a + b, 0) || 1;
  const storyForces = dir => stories.map((s, i) => ({
    story: s.name,
    fx: dir === "x" ? +(o.quake_coeff * W_ * wh_[i] / sumWh_).toFixed(2) : 0,
    fy: dir === "y" ? +(o.quake_coeff * W_ * wh_[i] / sumWh_).toFixed(2) : 0,
  }));
  const patterns = {
    DEAD: {
      name: "DEAD", kind: "dead", member_loads: beamUdls(o.dead_udl),
      area_loads: shells.filter(s => s.kind === "slab").map(s => ({ region_uid: s.uid, q: 2.0 })),
      story_forces: [],
    },
    LIVE: {
      name: "LIVE", kind: "live", member_loads: beamUdls(o.live_udl),
      area_loads: shells.filter(s => s.kind === "slab").map(s => ({ region_uid: s.uid, q: 3.0 })),
      story_forces: [],
    },
    EQX: { name: "EQX", kind: "quake", member_loads: [], area_loads: [], story_forces: storyForces("x") },
    EQY: { name: "EQY", kind: "quake", member_loads: [], area_loads: [], story_forces: storyForces("y") },
  };

  // v0.3: response-spectrum cases (UBC-style default shape)
  const ubc = [[0, 0.4], [0.11, 1.0], [0.56, 1.0], [0.8, 0.7], [1.0, 0.56],
    [1.5, 0.373], [2.0, 0.28], [3.0, 0.187], [4.0, 0.14]];
  const rs_cases = {
    "EQ-RS-X": {
      name: "EQ-RS-X", direction: "X", spectrum: ubc.map(p => [...p]),
      combo_method: "CQC", damping: 0.05, scale: 1.0,
    },
    "EQ-RS-Y": {
      name: "EQ-RS-Y", direction: "Y", spectrum: ubc.map(p => [...p]),
      combo_method: "SRSS", damping: 0.05, scale: 1.0,
    },
  };

  // v0.4: a demo time-history case (ramped decaying sine, m/s²)
  const sine = [];
  for (let i = 0; i <= 400; i++) {
    const t = i * 0.02;
    const env = Math.min(t / 1.0, 1) * Math.exp(-0.18 * Math.max(t - 4, 0));
    sine.push(+(2.5 * env * Math.sin(2 * Math.PI * 1.2 * t)).toFixed(4));
  }

  // v0.5: a demo pushover case — yield moments on the ground-story columns
  const pushMy = {};
  for (const m of members)
    if (m.kind === "column" && m.story === stories[0].name) pushMy[m.uid] = 250;
  const pushover_cases = {
    "PUSH-X": {
      name: "PUSH-X", direction: "X", gravity: { DEAD: 1.0 },
      target_drift: 0.02, steps: 100, My: pushMy, default_My: 250, hardening: 0.02,
    },
  };

  return {
    name: o.name,
    materials: { CONC: { name: "CONC", E: o.E, nu: 0.2, unit_weight: 24 } },
    sections: {
      COL: { name: "COL", material: "CONC", b: o.column_size, h: o.column_size,
        mod_A: 1, mod_I33: 1, mod_I22: 1, mod_J: 1 },
      BEAM: { name: "BEAM", material: "CONC", b: o.beam_b, h: o.beam_h,
        mod_A: 1, mod_I33: 1, mod_I22: 1, mod_J: 1 },
    },
    shell_sections, shells,
    grid, stories, members,
    base_fixity: o.base_fixity,
    supports: [], nodal_masses: [], rigid_diaphragms: true,
    story_masses,
    mass_source: { DEAD: 1.0 },
    patterns,
    cases: {
      DEAD: { name: "DEAD", patterns: { DEAD: 1 }, pdelta: false },
      LIVE: { name: "LIVE", patterns: { LIVE: 1 }, pdelta: false },
      EQX: { name: "EQX", patterns: { EQX: 1 }, pdelta: true },
      EQY: { name: "EQY", patterns: { EQY: 1 }, pdelta: false },
    },
    rs_cases,
    th_cases: {
      "TH-SINE-X": {
        name: "TH-SINE-X", direction: "X", accel: sine,
        dt: 0.02, damping: 0.05, scale: 1.0,
        nonlinear: false, gravity: {}, hinges: "column_base", My: {}, hardening: 0.02,
      },
      "TH-NL-X": {
        name: "TH-NL-X", direction: "X", accel: sine,
        dt: 0.02, damping: 0.05, scale: 1.0,
        // v0.6: nonlinear plastic-hinge run — yield moments on ground columns
        nonlinear: true, gravity: { DEAD: 1.0 }, hinges: "column_base",
        My: { ...pushMy }, default_My: 250, hardening: 0.03,
      },
    },
    // v0.6: staged construction — sequential story-by-story DEAD build
    staged_cases: {
      "Staged-DEAD": {
        name: "Staged-DEAD", pattern: "DEAD", stages: "per_story",
        include_live: { LIVE: 0.25 },
      },
    },
    pushover_cases,
    diaphragm: "rigid",
    story_diaphragm: {},
    links: [],
    combos: {
      "1.2D + 1.6L": { name: "1.2D + 1.6L", combo_type: "add", cases: { DEAD: 1.2, LIVE: 1.6 } },
      "1.2D + 1.0L + 1.0EX": { name: "1.2D + 1.0L + 1.0EX", combo_type: "add", cases: { DEAD: 1.2, LIVE: 1.0, EQX: 1.0 } },
      "1.2D + 1.0L + 1.0EY": { name: "1.2D + 1.0L + 1.0EY", combo_type: "add", cases: { DEAD: 1.2, LIVE: 1.0, EQY: 1.0 } },
      "0.9D + 1.0EX": { name: "0.9D + 1.0EX", combo_type: "add", cases: { DEAD: 0.9, EQX: 1.0 } },
      "ENV: EQ": { name: "ENV: EQ", combo_type: "envelope", cases: { EQX: 1.0, EQY: 1.0 } },
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
    // v0.5: the mesher omits quads whose cell centre falls inside an opening
    const covered = (u, v) => (sh.openings || []).some(o =>
      u > o.u0 + 1e-9 && u < o.u1 - 1e-9 && v > o.v0 + 1e-9 && v < o.v1 - 1e-9);
    for (let j = 0; j < ny; j++)
      for (let i = 0; i < nx; i++) {
        if (covered((i + 0.5) / nx, (j + 0.5) / ny)) continue;
        shell_quads.push({
          region: sh.uid,
          nodes: [tag[j][i], tag[j][i + 1], tag[j + 1][i + 1], tag[j + 1][i]],
        });
      }
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

  /* ---- v0.4: per-quad shell internal forces for STATIC cases.
     [Nxx, Nyy, Nxy, Mxx, Myy, Mxy, Vxz, Vyz] — kN/m and kN·m/m.
     Deterministic plausible fields: slab sagging bubbles under gravity,
     wall shear-flow + chord forces under lateral load. */
  const regionOf = {};
  for (const sh of (model.shells || [])) regionOf[sh.uid] = sh;
  function shellForcesFor(mode, dirX) {
    const sf = {};
    shell_quads.forEach((q, i) => {
      const c = [0, 0, 0];
      for (const tg of q.nodes) {
        const p = nodes[tg];
        c[0] += p[0] / 4; c[1] += p[1] / 4; c[2] += p[2] / 4;
      }
      const sh = regionOf[q.region];
      const kind = sh ? sh.kind : "slab";
      let u = 0.5, v = 0.5, Lu = 6, Lv = 6;
      if (sh) {
        const xs3 = sh.corners.map(p => p[0]), ys3 = sh.corners.map(p => p[1]),
          zs3 = sh.corners.map(p => p[2]);
        if (kind === "slab") {
          Lu = Math.max(...xs3) - Math.min(...xs3) || 1;
          Lv = Math.max(...ys3) - Math.min(...ys3) || 1;
          u = (c[0] - Math.min(...xs3)) / Lu;
          v = (c[1] - Math.min(...ys3)) / Lv;
        } else {
          const a = sh.corners[0], b = sh.corners[1];
          Lu = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
          Lv = Math.max(...zs3) - Math.min(...zs3) || 1;
          u = ((c[0] - a[0]) * (b[0] - a[0]) + (c[1] - a[1]) * (b[1] - a[1])) / (Lu * Lu);
          v = (c[2] - Math.min(...zs3)) / Lv;
        }
      }
      let f;
      if (kind === "slab") {
        if (mode === "gravity") {
          const bub = Math.sin(Math.PI * u) * Math.sin(Math.PI * v);   // 0 edge → 1 center
          const q0 = 5.5;
          const Mx = q0 * Lu * Lu / 24 * (bub - 0.32);
          const My = q0 * Lv * Lv / 27 * (bub - 0.30);
          f = [-q0 * 1.6 * (1 - bub), -q0 * 1.9 * (1 - bub),
            q0 * 2.2 * (u - 0.5) * (v - 0.5) * 4,
            Mx, My, Mx * 0.4 * (u - 0.5) * (v - 0.5) * 4,
            q0 * Lu / 5 * (0.5 - u) * 2, q0 * Lv / 5 * (0.5 - v) * 2];
        } else {
          // diaphragm: modest in-plane shear, near-zero plate bending
          const nq = V / 40 * (1 - c[2] / H);
          f = [nq * (u - 0.5) * 2, nq * (v - 0.5) * 2, nq * (dirX ? 1 : 0.7),
            0.15 * (u - 0.5), 0.12 * (v - 0.5), 0.05, 0.3, 0.25];
        }
      } else {                                     // wall
        if (mode === "gravity") {
          const Nc = -(1 - c[2] / H) * 55;
          f = [8 * (u - 0.5), Nc, 5 * (u - 0.5) * (1 - v),
            0.3 * (u - 0.5), 0.9 * (0.5 - v), 0.15, 0.4, 1.1];
        } else {
          const s = dirX ? 1 : 0.55;
          const Vs = V * (1 - 0.8 * c[2] / H) * s;   // shear flow, decays with height
          const nxy = Vs / Math.max(Lu, 1) / Math.max(supports.length / 4, 1);
          const nxx = (u - 0.5) * 2 * V * (1 - c[2] / H) * 2.6 * s / Math.max(Lu, 1);
          f = [nxx, nxx * 0.3, nxy,
            0.6 * (u - 0.5) * s, 0.4 * (0.5 - v) * s, 0.2 * s,
            1.4 * s * (0.5 - v), 0.8 * s];
        }
      }
      sf[String(i)] = f.map(x => +(x * jit(0.06)).toFixed(4));
    });
    return sf;
  }

  const cases = {
    DEAD: gravityCase((model._mock_params && model._mock_params.dead_udl) || 25, "DEAD"),
    LIVE: gravityCase((model._mock_params && model._mock_params.live_udl) || 10, "LIVE"),
    EQX: lateralCase(true),
    EQY: lateralCase(false),
  };
  if (shell_quads.length) {
    cases.DEAD.shell_forces = shellForcesFor("gravity", true);
    const liveSf = shellForcesFor("gravity", true);
    for (const k of Object.keys(liveSf)) liveSf[k] = liveSf[k].map(x => +(x * 0.42).toFixed(4));
    cases.LIVE.shell_forces = liveSf;
    cases.EQX.shell_forces = shellForcesFor("lateral", true);
    cases.EQY.shell_forces = shellForcesFor("lateral", false);
  }

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
  /* ---- v0.4: envelope combos — element-wise max (standard keys) and min
     (nested "min" block) across the factored single-case results. */
  function foldInto(dst, src, fn) {
    for (const [k, v] of Object.entries(src)) {
      if (typeof v === "number") dst[k] = fn(dst[k] === undefined ? v : dst[k], v);
      else if (Array.isArray(v)) {
        if (!Array.isArray(dst[k])) dst[k] = v.slice();
        else dst[k] = dst[k].map((x, i) => fn(x, v[i]));
      } else if (v && typeof v === "object") {
        dst[k] = dst[k] || {};
        foldInto(dst[k], v, fn);
      }
    }
  }
  function combineEnvelope(factors) {
    const parts = Object.entries(factors)
      .filter(([cn]) => cases[cn])
      .map(([cn, f]) => combine({ [cn]: f }));
    if (!parts.length) return combine({});
    const mx = JSON.parse(JSON.stringify(parts[0]));
    const mn = JSON.parse(JSON.stringify(parts[0]));
    for (let i = 1; i < parts.length; i++) {
      foldInto(mx, parts[i], Math.max);
      foldInto(mn, parts[i], Math.min);
    }
    // station x-coordinates stay coordinates, not extrema of themselves
    for (const [u, st] of Object.entries(mx.member_stations || {}))
      if (mn.member_stations[u]) mn.member_stations[u].x = st.x.slice();
    return { ...mx, min: mn };
  }
  const combos = {};
  for (const [name, cb] of Object.entries(model.combos || {})) {
    combos[name] = ((cb.combo_type || "add") === "envelope")
      ? combineEnvelope(cb.cases)
      : combine(cb.cases);
  }

  // ---- v0.3: response-spectrum cases — POSITIVE ENVELOPES of a lateral run
  function envelope(src, f) {
    const abs6 = a => a.map(v => Math.abs(v) * f);
    const out = {
      node_disp: {}, reactions: {}, member_forces: {}, member_stations: {}, story: {},
      base: Object.fromEntries(Object.entries(src.base).map(([k, v]) => [k, Math.abs(v) * f])),
    };
    for (const [t, d] of Object.entries(src.node_disp)) out.node_disp[t] = abs6(d);
    for (const [t, r] of Object.entries(src.reactions)) out.reactions[t] = abs6(r);
    for (const [u, mf] of Object.entries(src.member_forces)) out.member_forces[u] = abs6(mf);
    for (const [u, st] of Object.entries(src.member_stations || {})) {
      out.member_stations[u] = { x: st.x.slice() };
      for (const k of ["N", "V2", "V3", "T", "M2", "M3"])
        out.member_stations[u][k] = st[k].map(v => Math.abs(v) * f);
    }
    for (const [s, sr] of Object.entries(src.story)) {
      out.story[s] = {};
      for (const k of Object.keys(sr)) out.story[s][k] = Math.abs(sr[k]) * f;
    }
    return out;
  }
  const rs_cases = {};
  for (const [name, rc] of Object.entries(model.rs_cases || {})) {
    const src = lateralCase(rc.direction !== "Y");
    rs_cases[name] = envelope(src, (rc.scale || 1) * 1.12);   // modal RSA ≳ static ELF
  }

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
    const gamma = order === 1 ? 1.28 : order === 2 ? -0.47 : 0.24;  // participation factor Γ
    participation.push({
      mode: m, T,
      ux: dir === 0 ? p1 : 0.0,
      uy: dir === 1 ? p1 : 0.0,
      rz: dir === 2 ? p1 : 0.005,
      gamma_x: dir === 0 ? gamma : 0.0,
      gamma_y: dir === 1 ? gamma * 1.03 : 0.0,
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

  /* ---- v0.4: time-history cases — SDOF (first mode) central-difference
     integration of the ground record, spread over the sway profile. */
  const th_out = {};
  for (const [name, tc] of Object.entries(model.th_cases || {})) {
    const dt = (isFinite(tc.dt) && tc.dt > 0) ? tc.dt : 0.02;
    const ag = (Array.isArray(tc.accel) && tc.accel.length ? tc.accel : [0, 0])
      .map(a => a * (isFinite(tc.scale) ? tc.scale : 1));
    const n = ag.length;
    const dirX = tc.direction !== "Y";
    const T = periods[dirX ? 0 : 1] || periods[0] || 0.6;
    const om = 2 * Math.PI / T;
    const zeta = isFinite(tc.damping) ? tc.damping : 0.05;
    // u'' + 2ζω u' + ω² u = -ag   (central difference, u in m)
    const u = new Array(n).fill(0);
    if (n > 2) {
      const a0 = 1 / (dt * dt) + zeta * om / dt;
      for (let i = 1; i < n - 1; i++) {
        const rhs = -ag[i]
          - (om * om - 2 / (dt * dt)) * u[i]
          - (1 / (dt * dt) - zeta * om / dt) * u[i - 1];
        u[i + 1] = rhs / a0;
      }
    }
    const t = Array.from({ length: n }, (_, i) => +(i * dt).toFixed(4));
    const Mtot = W / G;                                  // tonnes
    const round = v => +v.toFixed(6);
    const story_ux = {}, story_uy = {};
    for (const s of storyOrder) {
      const f = sway(storyElev[s]);
      const tr = u.map(x => round(x * f));
      story_ux[s] = dirX ? tr : tr.map(() => 0);
      story_uy[s] = dirX ? tr.map(() => 0) : tr;
    }
    const Vt = u.map(x => +(om * om * x * Mtot * 0.82).toFixed(3));   // kN
    const base_FX = dirX ? Vt : Vt.map(() => 0);
    const base_FY = dirX ? Vt.map(() => 0) : Vt;
    const peakOf = arr => arr.reduce((a, b) => Math.max(a, Math.abs(b)), 0);
    const peaks = { story: {}, base: { FX: peakOf(base_FX), FY: peakOf(base_FY) } };
    for (const s of storyOrder)
      peaks.story[s] = { ux: peakOf(story_ux[s]), uy: peakOf(story_uy[s]) };
    const rec = { t, story_ux, story_uy, base_FX, base_FY, peaks };

    /* ---- v0.6: nonlinear (plastic-hinge) run gains hinge rotations + a
       yielded-member list. Peak rotation scales with roof drift; a hinge is
       "yielded" once its peak exceeds My/k_theta (approximated My/2e5). */
    if (tc.nonlinear) {
      const uids = Object.keys(tc.My || {}).length
        ? Object.keys(tc.My)
        : (tc.default_My != null
          ? model.members.filter(mm => mm.kind === "column" &&
              mm.story === storyOrder[0]).map(mm => mm.uid) : []);
      const byUid = {};
      for (const mm of model.members) byUid[mm.uid] = mm;
      const uroof = peakOf(story_ux[storyOrder[storyOrder.length - 1]] || [0]) +
        peakOf(story_uy[storyOrder[storyOrder.length - 1]] || [0]);
      const drift = uroof / H;
      const hinge_rotations = {}, yielded = [];
      uids.forEach((uid, i) => {
        const mm = byUid[uid];
        if (!mm) return;
        const si = Math.max(storyOrder.indexOf(mm.story), 0);
        const rot = drift * 0.9 * (1 - si / Math.max(storyOrder.length, 1)) * jit(0.2);
        hinge_rotations[uid] = +Math.max(rot, 0).toFixed(6);
        const My = (tc.My && tc.My[uid] != null) ? tc.My[uid] : (tc.default_My || 250);
        const thetaY = My / 2e5;
        if (rot > thetaY) yielded.push(uid);
      });
      rec.hinge_rotations = hinge_rotations;
      rec.yielded = yielded.sort();
    }
    th_out[name] = rec;
  }

  /* ---- v0.6: staged construction — the accumulated final state plus a
     comparison to the internal one-shot solve. We reuse the DEAD gravity
     case shape; staged column axials differ slightly from one-shot by the
     documented "slab built level" effect (upper stories settle less). */
  const staged = {};
  for (const [name, sc] of Object.entries(model.staged_cases || {})) {
    const base = gravityCase(
      (model._mock_params && model._mock_params.dead_udl) || 25, sc.pattern || "DEAD");
    const oneshot = JSON.parse(JSON.stringify(base));
    const stagedState = JSON.parse(JSON.stringify(base));
    // staged upper-story node displacements exclude lower-stage shortening
    for (const [t, p] of Object.entries(nodes)) {
      const frac = Math.min(1, (p[2] / H) * 0.6 + 0.4);   // upper nodes settle less
      if (stagedState.node_disp[t])
        stagedState.node_disp[t] = stagedState.node_disp[t].map(v => v * frac);
    }
    // staged beam end moments (indeterminate) genuinely differ from one-shot
    let maxOne = 0, maxDiff = 0;
    for (const mm of members) {
      const one = oneshot.member_forces[mm.uid];
      const st = stagedState.member_forces[mm.uid];
      if (!one || !st) continue;
      if (mm.kind === "column") {
        maxOne = Math.max(maxOne, Math.abs(one[0]));
        maxDiff = Math.max(maxDiff, Math.abs(st[0] - one[0]));
      } else {
        // shift indeterminate beam moments a few percent
        st[5] = one[5] * 1.06 * jit(0.03);
        st[11] = one[11] * 0.95 * jit(0.03);
      }
    }
    stagedState.member_stations = buildStations(stagedState.member_forces,
      udlOf(sc.pattern || "DEAD"));
    const pct = maxOne > 1e-9 ? +(100 * maxDiff / maxOne).toFixed(3) : 0.0;
    stagedState.comparison = { column_axial_max_diff_pct: pct, oneshot_case: oneshot };
    staged[name] = stagedState;
  }

  /* ---- v0.5: pushover cases — bilinear-ish capacity curve with two slope
     breaks (first yield, mechanism) plus hinge plastic rotations. */
  const pushover = {};
  for (const [name, pc] of Object.entries(model.pushover_cases || {})) {
    const steps = (isFinite(pc.steps) && pc.steps > 1) ? Math.round(pc.steps) : 100;
    const target = ((isFinite(pc.target_drift) && pc.target_drift > 0)
      ? pc.target_drift : 0.02) * H;
    const hard = isFinite(pc.hardening) ? Math.max(pc.hardening, 0.005) : 0.02;
    const u1 = 0.30 * target, u2 = 0.62 * target;      // slope-break displacements
    const K0 = 1.6 * V / u1, K1 = 0.45 * K0, K2 = hard * K0;
    const Vat = u => u <= u1 ? K0 * u
      : u <= u2 ? K0 * u1 + K1 * (u - u1)
      : K0 * u1 + K1 * (u2 - u1) + K2 * (u - u2);
    const roof_disp = [], base_shear = [], roof_drift = [];
    for (let i = 0; i <= steps; i++) {
      const u = target * i / steps;
      roof_disp.push(+u.toFixed(6));
      base_shear.push(+Vat(u).toFixed(3));
      roof_drift.push(+(u / H).toFixed(8));
    }
    // hinge rotations: explicit My members, else all columns via default_My
    const uids = Object.keys(pc.My || {}).length
      ? Object.keys(pc.My)
      : (pc.default_My != null
        ? model.members.filter(mm => mm.kind === "column").map(mm => mm.uid) : []);
    const byUid = {};
    for (const mm of model.members) byUid[mm.uid] = mm;
    const hinge_rotations = {};
    for (const uid of uids) {
      const mm = byUid[uid];
      if (!mm) continue;
      const si = Math.max(storyOrder.indexOf(mm.story), 0);
      const rot = (target / H) * 0.65 * (1 - si / Math.max(storyOrder.length, 1)) * jit(0.18);
      if (rot > 5e-4) hinge_rotations[uid] = +rot.toFixed(6);
    }
    const warnings = [];
    const over = Object.values(hinge_rotations).filter(r => r > 0.01).length;
    if (over) warnings.push(
      `${over} hinge${over > 1 ? "s" : ""} exceed${over > 1 ? "" : "s"} 0.010 rad plastic rotation at target drift`);
    if (!uids.length) warnings.push("no hinges defined — curve is elastic only");
    pushover[name] = { roof_disp, base_shear, roof_drift, hinge_rotations, warnings };
  }

  const out = {
    model_name: model.name,
    nodes, members, supports,
    story_order: storyOrder,
    story_elev: storyElev,
    shell_quads,
    cases, combos, rs_cases,
    th_cases: th_out,
    staged,
    modal: { periods, frequencies, participation, shapes },
  };
  if (Object.keys(pushover).length) out.pushover = pushover;
  return out;
}

/* ================================================================
   v0.4 — mock POST /api/pattern/wind
   ================================================================ */

/** Adds a wind load pattern with an ASCE-7-style story-force profile to the
    model dict (mutates + returns it). p: {name, direction, V (m/s),
    exposure "B"|"C"|"D", Cp}. */
export function mockWindPattern(model, p = {}) {
  const name = (p.name || "WIND").trim() || "WIND";
  const dirX = p.direction !== "Y";
  const V = isFinite(p.V) && p.V > 0 ? p.V : 40;         // m/s, 3-s gust
  const exp_ = ["B", "C", "D"].includes(p.exposure) ? p.exposure : "C";
  const Cp = isFinite(p.Cp) ? p.Cp : 0.8;
  const alpha = { B: 7.0, C: 9.5, D: 11.5 }[exp_];
  const zg = { B: 365.76, C: 274.32, D: 213.36 }[exp_];  // m
  const Kz = z => 2.01 * Math.pow(Math.max(z, 4.6) / zg, 2 / alpha);
  const qz = z => 0.613 * Kz(z) * V * V / 1000;          // kPa

  const g = model.grid || { x_lines: [0, 18], y_lines: [0, 12] };
  const width = dirX
    ? (g.y_lines[g.y_lines.length - 1] - g.y_lines[0])   // face ⟂ X wind
    : (g.x_lines[g.x_lines.length - 1] - g.x_lines[0]);

  const stories = model.stories || [];
  const story_forces = stories.map((st, i) => {
    const hAbove = i + 1 < stories.length ? stories[i + 1].height : 0;
    const trib = st.height / 2 + hAbove / 2;             // ground half sheds to base
    const F = +(qz(st.elevation) * Cp * Math.max(width, 1) * trib).toFixed(2);
    return { story: st.name, fx: dirX ? F : 0, fy: dirX ? 0 : F };
  });

  model.patterns = model.patterns || {};
  model.patterns[name] = {
    name, kind: "other", member_loads: [], area_loads: [], story_forces,
    wind: { direction: dirX ? "X" : "Y", V, exposure: exp_, Cp },
  };
  return model;
}

/* ================================================================
   v0.3 — mock section library + mock model-file store
   ================================================================ */

/** GET /api/sections/library — six AISC W-shapes (SI units: m², m⁴). */
export function mockSectionLibrary() {
  return [
    { name: "W12x26", A: 4.95e-3, I33: 8.49e-5, I22: 7.24e-6, J: 1.24e-7 },
    { name: "W14x30", A: 5.70e-3, I33: 1.21e-4, I22: 8.13e-6, J: 1.58e-7 },
    { name: "W16x40", A: 7.61e-3, I33: 2.15e-4, I22: 1.19e-5, J: 3.30e-7 },
    { name: "W18x50", A: 9.48e-3, I33: 3.33e-4, I22: 1.68e-5, J: 5.20e-7 },
    { name: "W21x62", A: 1.18e-2, I33: 5.54e-4, I22: 2.40e-5, J: 7.70e-7 },
    { name: "W24x76", A: 1.45e-2, I33: 8.74e-4, I22: 3.42e-5, J: 1.18e-6 },
  ];
}

/** In-memory stand-in for the /api/models file store (used when the backend
    is unreachable or ?mock=1). Shapes match the v0.3 endpoints. */
const _files = new Map();   // name -> {model, mtime}
let _filesSeeded = false;
function seedFiles() {
  if (_filesSeeded) return;
  _filesSeeded = true;
  const now = Date.now() / 1000;
  _files.set("Tower A", {
    model: mockModel({ name: "Tower A", stories: 8, bays_x: 4, bays_y: 3 }),
    mtime: now - 3 * 86400,
  });
  _files.set("Podium_4st", {
    model: mockModel({ name: "Podium_4st", stories: 4, bays_x: 5, bay_width_x: 7.5 }),
    mtime: now - 7200,
  });
}

export const mockModelFiles = {
  list() {
    seedFiles();
    return [..._files.entries()].map(([name, f]) => ({
      name, mtime: f.mtime,
      stories: f.model.stories.length,
      members: f.model.members.length,
    })).sort((a, b) => b.mtime - a.mtime);
  },
  save(name, model) {
    seedFiles();
    _files.set(name, { model: JSON.parse(JSON.stringify(model)), mtime: Date.now() / 1000 });
    return { saved: name };
  },
  open(name) {
    seedFiles();
    const f = _files.get(name);
    if (!f) throw new Error(`No saved model named “${name}”`);
    return JSON.parse(JSON.stringify(f.model));
  },
  remove(name) {
    seedFiles();
    if (!_files.delete(name)) throw new Error(`No saved model named “${name}”`);
    return { deleted: name };
  },
};
