/* SkyFrame mock backend — matches CONTRACT.md shapes so the UI is fully
   explorable without the Flask server (offline / ?mock=1). */

import { designerProps } from "./modeledit.js";   // v0.21 — shoelace properties

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
    // v0.14 — the legacy grid IS the primary orthogonal grid system
    kind: "orthogonal", name: "G1", origin: [0, 0], rotation: 0,
  };
  // v0.14 — multiple grid systems: the default grid + a wing rotated 30° off
  // the building's +X edge + a radial grid to the west. Each system's lines
  // are defined in LOCAL coords and transformed by origin + rotation (radial:
  // circles at radii, spokes at theta) to GLOBAL — mirrored from the backend.
  const xEnd = xs[xs.length - 1], yMid = (ys[0] + ys[ys.length - 1]) / 2;
  const grid_systems = [
    grid,
    {
      kind: "orthogonal", name: "Wing 30°", origin: [xEnd + 3, ys[0]], rotation: 30,
      x_lines: [0, 5, 10], y_lines: [0, 5, 10],
      x_labels: ["A'", "B'", "C'"], y_labels: ["1'", "2'", "3'"],
    },
    {
      kind: "radial", name: "Radial", origin: [xs[0] - 11, yMid], rotation: 0,
      radii: [3, 6, 9], theta_deg: [0, 45, 90, 135, 180, 225, 270, 315],
    },
  ];
  const stories = [];
  for (let s = 0; s < o.stories; s++) {
    stories.push({
      name: `Story${s + 1}`, height: o.story_height,
      elevation: (s + 1) * o.story_height,
    });
  }
  const members = [];
  const dist = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
  const add = (kind, section, pi, pj, story, uid) => {
    const mm = { uid, kind, section, pi, pj, story, length: dist(pi, pj),
      releases: "", angle: 0, rigid_i: 0, rigid_j: 0, rigid_factor: 1,
      foundation_ks: 0, foundation_width: 0, axial_limit: "both" };
    members.push(mm);
    return mm;
  };

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

  // v0.12: demo X-bracing on the first bay (y = 0 plane) of the two lowest
  // stories, flagged tension-only (axial_limit "tension") so the T-only glyph
  // is visible in plan & 3D. Tension/compression-only makes the run nonlinear.
  if (xs.length >= 2) {
    for (let s = 0; s < Math.min(2, stories.length); s++) {
      const st = stories[s], zt = st.elevation, zb = st.elevation - st.height;
      const x0 = xs[0], x1 = xs[1], y = ys[0];
      const b1 = add("brace", "BRACE", [x0, y, zb], [x1, y, zt], st.name, `BR${s + 1}-a`);
      const b2 = add("brace", "BRACE", [x1, y, zb], [x0, y, zt], st.name, `BR${s + 1}-b`);
      b1.axial_limit = "tension";
      b2.axial_limit = "tension";
    }
  }

  // v0.9: demo rigid-end offsets on a couple of Story1 members — a beam
  // (rigid zones at both ends where it frames into columns) and a column.
  const demoBeam = members.find(mm => mm.kind === "beam" && mm.story === "Story1");
  if (demoBeam) { demoBeam.rigid_i = 0.25; demoBeam.rigid_j = 0.25; demoBeam.rigid_factor = 1.0; }
  const demoCol = members.find(mm => mm.kind === "column" && mm.story === "Story1");
  if (demoCol) { demoCol.rigid_j = 0.30; demoCol.rigid_factor = 0.5; }

  // v0.11: a couple of Story1 grade beams on an elastic (Winkler) foundation —
  // subgrade modulus ks (kN/m³) + bearing width (m). Drives the soil/spring-bed
  // glyph in plan & 3D and the "on elastic foundation" props toggle.
  const gradeBeams = members.filter(mm => mm.kind === "beam" && mm.story === "Story1"
    && /^BX1-/.test(mm.uid)).slice(0, 2);
  for (const gb of gradeBeams) { gb.foundation_ks = 30000; gb.foundation_width = 0.6; }

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
    // v0.22: SH200 ships layered (3-layer sandwich, Σt = elastic thickness)
    SH200: { name: "SH200", material: "CONC", thickness: 0.2,
      layered: { layers: [
        { t: 0.08, material: "CONC", kind: "concrete" },
        { t: 0.04, material: "CONC", kind: "steel" },
        { t: 0.08, material: "CONC", kind: "concrete" },
      ] } },
    SLAB150: { name: "SLAB150", material: "CONC", thickness: 0.15, layered: null },
  };
  const shells = [];
  const wallLen = Math.min(o.bay_width_x, 8);
  for (let s = 0; s < Math.min(2, stories.length); s++) {
    const st = stories[s], zb = st.elevation - st.height, zt = st.elevation;
    shells.push({
      uid: `W${s + 1}`, kind: "wall", behavior: "shell", section: "SH200",
      corners: [[0, 0, zb], [wallLen, 0, zb], [wallLen, 0, zt], [0, 0, zt]],
      mesh_size: 1.0, story: st.name,
      // v0.15: both wall lifts share one pier label → a 2-story pier "P1"
      pier: "P1",
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
      // v0.22: demo area spring (subgrade bed) — hatched overlay in plan
      area_spring: { kz: 30000, compression_only: false },
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
  // v0.8: a demo thermal load on a first-story beam (ΔT +30 °C)
  const firstBeam = members.find(mm => mm.kind === "beam" && mm.story === stories[0].name);
  const patterns = {
    DEAD: {
      name: "DEAD", kind: "dead", member_loads: beamUdls(o.dead_udl),
      area_loads: shells.filter(s => s.kind === "slab").map(s => ({ region_uid: s.uid, q: 2.0 })),
      story_forces: [],
      // v0.8: uniform thermal load on a demo beam
      thermal_loads: firstBeam ? [{ member_uid: firstBeam.uid, dT: 30 }] : [],
    },
    LIVE: {
      name: "LIVE", kind: "live", member_loads: beamUdls(o.live_udl),
      area_loads: shells.filter(s => s.kind === "slab").map(s => ({ region_uid: s.uid, q: 3.0 })),
      story_forces: [],
    },
    // v0.8: EQX ships with accidental torsion enabled (ASCE 7 §12.8.4)
    EQX: { name: "EQX", kind: "quake", member_loads: [], area_loads: [], story_forces: storyForces("x"),
      accidental_torsion: true, ecc: 0.05 },
    EQY: { name: "EQY", kind: "quake", member_loads: [], area_loads: [], story_forces: storyForces("y") },
  };
  // v0.10: a demo notional load pattern (AISC direct-analysis stability):
  // 0.002 × gravity at each level applied laterally, kind "notional".
  {
    const coeff = 0.002;
    patterns["NOTIONAL-X"] = {
      name: "NOTIONAL-X", kind: "notional", member_loads: [], area_loads: [],
      story_forces: stories.map(s => ({
        story: s.name,
        fx: +(coeff * (story_masses[s.name] || 0) * G).toFixed(3), fy: 0,
      })),
      notional: { direction: "X", coeff, gravity_pattern: "DEAD" },
    };
  }

  // v0.3: response-spectrum cases (UBC-style default shape)
  const ubc = [[0, 0.4], [0.11, 1.0], [0.56, 1.0], [0.8, 0.7], [1.0, 0.56],
    [1.5, 0.373], [2.0, 0.28], [3.0, 0.187], [4.0, 0.14]];
  // v0.13: EQ-RS-X references the "EC8-Type1" library spectrum function (its
  // inline points are ignored while `function` is set); EQ-RS-Y stays inline.
  const rs_cases = {
    "EQ-RS-X": {
      name: "EQ-RS-X", direction: "X", spectrum: ubc.map(p => [...p]),
      combo_method: "CQC", damping: 0.05, scale: 1.0, function: "EC8-Type1",
    },
    "EQ-RS-Y": {
      name: "EQ-RS-Y", direction: "Y", spectrum: ubc.map(p => [...p]),
      combo_method: "SRSS", damping: 0.05, scale: 1.0, function: "",
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

  // v0.21: a demo designer section — 600×600 RC column with 8 bars of
  // 500 mm² (5e-4 m²). Its companion FrameSection carries the shoelace
  // properties (the live backend recomputes them server-side).
  const dcol = {
    name: "D-COL600",
    polygons: [{
      vertices: [[-0.3, -0.3], [0.3, -0.3], [0.3, 0.3], [-0.3, 0.3]],
      material: "CONC", hole: false,
    }],
    rebar: [-0.24, 0, 0.24].flatMap(z => [-0.24, 0, 0.24]
      .filter(y => y !== 0 || z !== 0)
      .map(y => ({ y, z, area: 5e-4, material: "CONC" }))),
  };
  const dprops = designerProps(dcol);

  return {
    name: o.name,
    materials: { CONC: { name: "CONC", E: o.E, nu: 0.2, unit_weight: 24 } },
    sections: {
      COL: { name: "COL", material: "CONC", b: o.column_size, h: o.column_size,
        mod_A: 1, mod_I33: 1, mod_I22: 1, mod_J: 1 },
      BEAM: { name: "BEAM", material: "CONC", b: o.beam_b, h: o.beam_h,
        mod_A: 1, mod_I33: 1, mod_I22: 1, mod_J: 1 },
      BRACE: { name: "BRACE", material: "CONC", b: 0.2, h: 0.2,
        mod_A: 1, mod_I33: 1, mod_I22: 1, mod_J: 1 },
      "D-COL600": { name: "D-COL600", material: "CONC", b: 0, h: 0,
        shape: "designer", A: dprops.A, I33: dprops.I33, I22: dprops.I22,
        J: dprops.J, mod_A: 1, mod_I33: 1, mod_I22: 1, mod_J: 1 },
    },
    // v0.21: designer (polygon + rebar) sections — round-trip via POST /api/model
    designer_sections: { "D-COL600": dcol },
    shell_sections, shells,
    grid, grid_systems, stories, members,
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
        function: "",
      },
      "TH-NL-X": {
        name: "TH-NL-X", direction: "X", accel: sine,
        dt: 0.02, damping: 0.05, scale: 1.0,
        // v0.6: nonlinear plastic-hinge run — yield moments on ground columns
        nonlinear: true, gravity: { DEAD: 1.0 }, hinges: "column_base",
        My: { ...pushMy }, default_My: 250, hardening: 0.03,
        function: "",
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
    // v0.10: a demo buckling case (gravity DEAD, 3 modes) + an RS directional
    // combination (100/30) of the two response-spectrum cases.
    buckling_cases: {
      "BUCK-G": { name: "BUCK-G", gravity: { DEAD: 1.0, LIVE: 0.5 }, num_modes: 3 },
    },
    rs_combos: {
      "RS-100/30": { name: "RS-100/30", name_x: "EQ-RS-X", name_y: "EQ-RS-Y", method: "100_30" },
    },
    // v0.13: section cuts — two horizontal (z) planes through Story1: one to
    // read the story shear (FX ≈ base shear under lateral cases) and one to
    // read the gravity landing (FZ ≈ weight under gravity cases).
    section_cuts: [
      { name: "Base Shear", axis: "z", coord: +(o.story_height * 0.5).toFixed(3) },
      { name: "Story1 Gravity", axis: "z", coord: +(o.story_height * 0.4).toFixed(3) },
    ],
    // v0.13: function library — a Eurocode 8 spectrum + a demo ground record.
    spectrum_functions: {
      "EC8-Type1": {
        name: "EC8-Type1", damping: 0.05,
        points: mockEc8Spectrum({ ag: 0.25, S: 1.2 }),
      },
      "UBC-Lib": {
        name: "UBC-Lib", damping: 0.05,
        points: ubc.map(p => [...p]),
      },
    },
    th_functions: {
      "SINE-REC": { name: "SINE-REC", values: sine.slice(), dt: 0.02 },
    },
    diaphragm: "rigid",
    story_diaphragm: {},
    // v0.17: beam–column joint model — "none" (centerline) | "rigid" | "scissors"
    panel_zones: "none",
    // v0.16: serviceability deflection limit (L/x on beam live-load sag)
    deflection_limit: 360,
    // v0.15: model-level "auto-label all walls as piers" flag (off — the demo
    // walls carry an explicit pier label instead)
    auto_pier_walls: false,
    // v0.15: demo link devices at the Story1 diaphragm level along the back
    // edge — a viscous damper and an isolator (distinct glyphs + D/I letters)
    links: (xs.length >= 3 ? [
      {
        uid: "LK1", pi: [xs[0], ys[ys.length - 1], stories[0].elevation],
        pj: [xs[1], ys[ys.length - 1], stories[0].elevation],
        stiffness: [1e5, 1e5, 1e5, 1e4, 1e4, 1e4],
        link_type: "damper", params: { cd: 1500, alpha: 0.5, k: 25000 },
      },
      {
        uid: "LK2", pi: [xs[1], ys[ys.length - 1], stories[0].elevation],
        pj: [xs[2], ys[ys.length - 1], stories[0].elevation],
        stiffness: [1e5, 1e5, 1e5, 1e4, 1e4, 1e4],
        link_type: "isolator", params: { k1: 8e4, k2: 8e3, Fy: 120, kv: 1e6 },
      },
    ] : []),
    // v0.8: two demo spring supports at base grid corners (6-dof)
    spring_supports: [
      { point: [xs[0], ys[0], 0], stiffness: [1.5e5, 1.5e5, 3e5, 0, 0, 0] },
      { point: [xs[xs.length - 1], ys[0], 0], stiffness: [1e5, 1e5, 2.5e5, 0, 0, 0] },
    ],
    // v0.22: auto edge constraints off by default (round-trips as a bool)
    edge_constraints: false,
    // v0.22: a demo line spring — subgrade bed along the west base edge
    line_springs: (ys.length >= 2 ? [
      { p1: [xs[0], ys[0], 0], p2: [xs[0], ys[ys.length - 1], 0],
        kz: 25000, compression_only: true },
    ] : []),
    thermal_alpha: 1.2e-5,
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

  /* ---- v0.16: local transverse BEAM deflections (11 stations, m).
     Gravity: the exact simply-supported-UDL sag shape
     y(t) = δmax·(16/5)(t − 2t³ + t⁴), δmax = 5wL⁴/384EI (downward = negative
     local y). Lateral: a small anti-symmetric frame-sway shape from the end
     moments. One designated beam is amplified so a deflection check goes NG. */
  const _EI = mm => {
    const sec = (model.sections || {})[mm.section] || {};
    const mat = (model.materials || {})[sec.material] || {};
    const E = isFinite(mat.E) && mat.E > 0 ? mat.E : 25e6;                  // kPa
    const I = isFinite(sec.I33) && sec.I33 > 0 ? sec.I33
      : (sec.b || 0.3) * Math.pow(sec.h || 0.6, 3) / 12;                    // m⁴
    return E * I;
  };
  const _ngBeam = (model.members.find(mm => mm.kind === "beam" && /^BX2-/.test(mm.uid))
    || model.members.find(mm => mm.kind === "beam") || {}).uid;
  function buildDeflections(member_forces, wMap) {
    const out = {};
    const NS = 11;
    for (const mm of model.members) {
      if (mm.kind !== "beam") continue;
      const f = member_forces[mm.uid];
      if (!f) continue;
      const L = lenOf[mm.uid] || 6;
      const EI = _EI(mm);
      const x = [], dy = [], dz = [];
      if (wMap) {                                   // gravity: parabolic UDL sag
        const w = wMap[mm.uid] || 0;
        let dmax = 5 * w * Math.pow(L, 4) / (384 * EI);
        if (mm.uid === _ngBeam) dmax *= 9;          // one deliberately NG beam
        for (let k = 0; k < NS; k++) {
          const t = k / (NS - 1);
          x.push(+(t * L).toFixed(3));
          dy.push(+(-dmax * (16 / 5) * (t - 2 * t ** 3 + t ** 4)).toFixed(7));
          dz.push(0);
        }
      } else {                                      // lateral: S-shape from end moments
        const M = Math.max(Math.abs(f[5]), Math.abs(f[11]));
        const amp = M * L * L / (40 * EI);
        for (let k = 0; k < NS; k++) {
          const t = k / (NS - 1);
          x.push(+(t * L).toFixed(3));
          dy.push(+(amp * Math.sin(2 * Math.PI * t) * 0.5).toFixed(7));
          dz.push(0);
        }
      }
      out[mm.uid] = { x, dy, dz };
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
        // v0.16: columns pick up a modest minor-axis share (frame action ⊥ the
        // load) so concrete biaxial (Bresler / load-contour) checks light up
        member_forces[mm.uid] = [n, v, v * 0.3 * jit(0.2), 0, m * 0.3 * jit(0.2), m,
          -n, -v, -v * 0.3, 0, m * 0.24, m * jit(0.15)];
      } else {
        const m = Vst / colsPerStory * stories[si].height * 0.45 * jit(0.25);
        const L = 6, v = 2 * m / L;
        member_forces[mm.uid] = [0, v, 0, 0, 0, m, 0, -v, 0, 0, 0, -m * jit(0.2)];
      }
    });
    const member_stations = buildStations(member_forces, null);
    const member_deflections = buildDeflections(member_forces, null);   // v0.16
    return { node_disp, reactions, base, member_forces, member_stations, member_deflections, story };
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
    const member_deflections = buildDeflections(member_forces, wMap);   // v0.16
    return { node_disp, reactions, base, member_forces, member_stations, member_deflections, story };
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
    const out = { node_disp: {}, reactions: {}, base: { FX: 0, FY: 0, FZ: 0, MX: 0, MY: 0, MZ: 0 }, member_forces: {}, member_stations: {}, member_deflections: {}, story: {} };
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
      // v0.16 — local beam deflections superpose linearly like stations
      for (const [u, md] of Object.entries(c.member_deflections || {})) {
        if (!out.member_deflections[u]) {
          out.member_deflections[u] = { x: md.x.slice() };
          for (const k of ["dy", "dz"])
            out.member_deflections[u][k] = new Array(md.x.length).fill(0);
        }
        for (const k of ["dy", "dz"])
          md[k].forEach((v, i) => out.member_deflections[u][k][i] += f * v);
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
    for (const [u, md] of Object.entries(mx.member_deflections || {}))
      if (mn.member_deflections && mn.member_deflections[u]) mn.member_deflections[u].x = md.x.slice();
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

  /* ---- v0.10: RS directional combinations (ASCE 7 §12.5). The combined
     directional case is a POSITIVE envelope, added into rs_cases so it
     surfaces as an "RS: <name>" entry in every results selector.
     100/30: max(1.0·X + 0.3·Y, 0.3·X + 1.0·Y); SRSS: √(X² + Y²). */
  function combineDirectional(cx, cy, method) {
    const src = cx || cy;
    if (!src) return null;
    const comb = (a, b) => method === "SRSS"
      ? Math.hypot(a, b) : Math.max(a + 0.3 * b, 0.3 * a + b);
    const pair = (arr, k) => (arr || []).map((v, i) => comb(v, (k && k[i]) || 0));
    const out = { node_disp: {}, reactions: {}, member_forces: {}, member_stations: {}, story: {},
      base: {} };
    for (const k of Object.keys(src.base || {}))
      out.base[k] = comb(Math.abs((cx?.base || {})[k] || 0), Math.abs((cy?.base || {})[k] || 0));
    for (const t of Object.keys(src.node_disp || {}))
      out.node_disp[t] = pair((cx?.node_disp || {})[t], (cy?.node_disp || {})[t]);
    for (const t of Object.keys(src.reactions || {}))
      out.reactions[t] = pair((cx?.reactions || {})[t], (cy?.reactions || {})[t]);
    for (const u of Object.keys(src.member_forces || {}))
      out.member_forces[u] = pair((cx?.member_forces || {})[u], (cy?.member_forces || {})[u]);
    for (const [u, st] of Object.entries(src.member_stations || {})) {
      const sx = (cx?.member_stations || {})[u] || {}, sy = (cy?.member_stations || {})[u] || {};
      out.member_stations[u] = { x: st.x.slice() };
      for (const kk of ["N", "V2", "V3", "T", "M2", "M3"])
        out.member_stations[u][kk] = st[kk].map((_, i) => comb(sx[kk]?.[i] || 0, sy[kk]?.[i] || 0));
    }
    for (const s of Object.keys(src.story || {})) {
      out.story[s] = {};
      const a = (cx?.story || {})[s] || {}, b = (cy?.story || {})[s] || {};
      for (const kk of Object.keys(src.story[s]))
        out.story[s][kk] = comb(Math.abs(a[kk] || 0), Math.abs(b[kk] || 0));
    }
    return out;
  }
  for (const [name, rcmb] of Object.entries(model.rs_combos || {})) {
    const combined = combineDirectional(
      rs_cases[rcmb.name_x], rs_cases[rcmb.name_y],
      rcmb.method === "SRSS" ? "SRSS" : "100_30");
    if (combined) rs_cases[name] = combined;
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
    if (!uids.length && pc.hinges !== "asce41")
      warnings.push("no hinges defined — curve is elastic only");
    /* v0.19 — asce41 mode: per-hinge trilinear histories + acceptance
       states for every member flagged hinges === "auto_m3" (both ends).
       thy ~ 6 mrad, plastic bands 1/9/11 thy (compact steel row). */
    const hinges = [];
    if (pc.hinges === "asce41") {
      const flagged = model.members.filter(mm => mm.hinges === "auto_m3");
      for (const mm of flagged) {
        const thy = 0.006 * jit(0.1);
        const My = 600 * jit(0.2);
        const bands = { IO: 1 * thy, LS: 9 * thy, CP: 11 * thy };
        for (const end of ["i", "j"]) {
          const share = end === "i" ? 1.0 : 0.55;    // base end rotates more
          const rot = [], moment = [], state = [];
          for (let i = 0; i <= steps; i++) {
            const r = (target / H) * 2.2 * thy / 0.006 * (i / steps) * share;
            rot.push(+r.toFixed(6));
            moment.push(+Math.min(My * r / thy, My * (1 + 0.05 * (r - thy) / thy)).toFixed(2));
            const p = Math.max(r - thy, 0);
            state.push(p <= 0 ? "elastic" : p <= bands.IO ? "IO"
              : p <= bands.LS ? "LS" : p <= bands.CP ? "CP" : "collapse");
          }
          hinges.push({ uid: mm.uid, end, My: +My.toFixed(1),
            thy: +thy.toFixed(6), a: +(9 * thy).toFixed(6),
            b: +(11 * thy).toFixed(6), c: 0.6, IO: +bands.IO.toFixed(6),
            LS: +bands.LS.toFixed(6), CP: +bands.CP.toFixed(6),
            kind: "steel", rot, moment, state });
          if (!(mm.uid in hinge_rotations))
            hinge_rotations[mm.uid] = rot[rot.length - 1];
        }
      }
    }
    pushover[name] = { roof_disp, base_shear, roof_drift, hinge_rotations,
                       warnings, hinges };
  }

  /* ---- v0.10: linearized buckling — critical load factors λ per mode and
     buckling mode shapes (same shape as modal shapes). λ multiplies the
     applied gravity state; λ < 1 means buckling below the applied load. */
  const buckling = {};
  for (const [name, bc] of Object.entries(model.buckling_cases || {})) {
    const nModes = (isFinite(bc.num_modes) && bc.num_modes >= 1) ? Math.round(bc.num_modes) : 3;
    const seed = [3.2, 5.1, 8.7, 12.4, 16.9, 22.1, 28.0];
    const factors = [];
    for (let i = 0; i < nModes; i++)
      factors.push(+((seed[i] != null ? seed[i] : seed[seed.length - 1] + (i - seed.length + 1) * 5.7) * jit(0.02)).toFixed(3));
    const modes = {};
    for (let i = 1; i <= nModes; i++) {
      const src = shapes[String(i)] || shapes["1"] || {};
      const mm = {};
      for (const [t, d] of Object.entries(src)) mm[t] = d.slice();
      modes[String(i)] = mm;
    }
    const warnings = [];
    if (factors.length && factors[0] < 1)
      warnings.push(`critical load factor λ₁ = ${factors[0]} < 1 — the structure buckles below the applied gravity load`);
    buckling[name] = { factors, modes, gravity: { ...(bc.gravity || {}) }, warnings };
  }

  /* ---- v0.8: center of mass / center of rigidity per story. Present only
     when diaphragms exist (rigid globally or per story). CM drifts with
     height (mass irregularity); CR sits eccentric from CM — the two markers
     and their eccentricity vector drive the Story-tab plan diagram. */
  const story_props = {};
  const gx0 = xs2[0], gx1 = xs2[xs2.length - 1], gy0 = ys2[0], gy1 = ys2[ys2.length - 1];
  const Bx = gx1 - gx0 || 1, By = gy1 - gy0 || 1;
  storyOrder.forEach((s, i) => {
    const eff = (model.story_diaphragm && model.story_diaphragm[s]) || model.diaphragm || "rigid";
    if (eff === "none") return;
    const t = storyOrder.length > 1 ? i / (storyOrder.length - 1) : 0;
    story_props[s] = {
      cm_x: +(cx + Bx * (0.02 + 0.07 * t) * jit(0.08)).toFixed(4),
      cm_y: +(cy + By * (0.015 + 0.02 * t) * jit(0.08)).toFixed(4),
      cr_x: +(cx - Bx * 0.035 * jit(0.08)).toFixed(4),
      cr_y: +(cy - By * 0.012 * jit(0.08)).toFixed(4),
    };
  });

  /* ---- v0.9: per-story lateral stiffness + ASCE 7 §12.3 irregularity checks.
     Present only when the model has diaphragms, and only for LATERAL cases
     (quake load cases + response-spectrum cases). The mock seeds one
     torsionally-EXTREME story, one torsional story, one soft story and one
     extreme-soft story so the Story-tab diagnostics chips show every state. */
  const story_stiffness = {}, irregularity = {};
  const hasDia = (model.diaphragm || "rigid") !== "none" && model.rigid_diaphragms !== false;
  if (hasDia) {
    const nS = storyOrder.length;
    const quakeCases = Object.keys(model.cases || {}).filter(cn => {
      const c = model.cases[cn] || {};
      return Object.keys(c.patterns || {}).some(pn => {
        const p = (model.patterns || {})[pn];
        return p && (p.story_forces || []).some(f => (f.fx || 0) || (f.fy || 0));
      });
    });
    const lateralNames = [...quakeCases, ...Object.keys(model.rs_cases || {})];
    const kbaseX = 4.2e5, kbaseY = 3.9e5;                 // kN/m at the base
    for (const cn of lateralNames) {
      story_stiffness[cn] = {}; irregularity[cn] = {};
      storyOrder.forEach((s, i) => {
        const t = nS > 1 ? i / (nS - 1) : 0;
        story_stiffness[cn][s] = {
          kx: +(kbaseX * (1 - 0.5 * t) * jit(0.04)).toFixed(1),
          ky: +(kbaseY * (1 - 0.5 * t) * jit(0.04)).toFixed(1),
        };
        // torsional irregularity ratio = max/avg story drift across the plan
        let trx = 1.03 + 0.05 * (jit(0.4) - 1) + 0.03, tryy = 1.04 + 0.05 * (jit(0.4) - 1);
        if (i === 0) trx = 1.45;              // extreme torsional (red)
        else if (i === 1) trx = 1.26;         // torsional (amber)
        const maxTr = Math.max(trx, tryy);
        const flag = maxTr >= 1.4 ? "extreme" : maxTr >= 1.2 ? "torsional" : "none";
        // soft-story stiffness ratio (this story / the story above)
        let stiff_ratio = null, soft_flag = "none";
        if (i < nS - 1) {
          stiff_ratio = 1.05 + 0.12 * (jit(0.3) - 1) / 0.3;
          if (i === 1) stiff_ratio = 0.66;          // soft (amber)
          else if (i === 2) stiff_ratio = 0.55;     // extreme soft (red)
          soft_flag = stiff_ratio <= 0.6 ? "extreme_soft" : stiff_ratio <= 0.7 ? "soft" : "none";
          stiff_ratio = +stiff_ratio.toFixed(3);
        }
        irregularity[cn][s] = {
          tors_ratio_x: +trx.toFixed(3), tors_ratio_y: +tryy.toFixed(3),
          flag, stiff_ratio, soft_flag,
        };
      });
    }
  }

  /* ---- v0.11: load takedown — per gravity case/combo, the gravity landing
     at each support with a grid label (A-1 …), a grand-total FZ and a balance
     check (support ΣFZ vs applied gravity). Reaction FZ sums to base.FZ by
     construction, so the mock balance is always ok. */
  const gridLabelFor = p => {
    const g = model.grid || {};
    const xi = (g.x_lines || []).findIndex(v => Math.abs(v - p[0]) < 1e-4);
    const yi = (g.y_lines || []).findIndex(v => Math.abs(v - p[1]) < 1e-4);
    if (xi < 0 || yi < 0) return "";
    const xl = (g.x_labels && g.x_labels[xi]) || String(xi + 1);
    const yl = (g.y_labels && g.y_labels[yi]) || String(yi + 1);
    return `${xl}-${yl}`;
  };
  const buildTakedown = cd => {
    const sups = supports.map(t => {
      const p = nodes[t] || [0, 0, 0];
      const f = (cd.reactions && cd.reactions[t]) || [0, 0, 0, 0, 0, 0];
      return { node: t, grid: gridLabelFor(p),
        x: +p[0].toFixed(3), y: +p[1].toFixed(3),
        FZ: +f[2].toFixed(3), FX: +f[0].toFixed(3), FY: +f[1].toFixed(3) };
    });
    const total_FZ = +sups.reduce((a, s) => a + s.FZ, 0).toFixed(3);
    const applied_FZ = +(((cd.base && cd.base.FZ) != null) ? cd.base.FZ : total_FZ).toFixed(3);
    const balance_ok = Math.abs(total_FZ - applied_FZ) <= Math.max(1e-6, 0.005 * Math.abs(applied_FZ));
    return { supports: sups, total_FZ, applied_FZ, balance_ok };
  };
  const isGravity = cd => {
    if (!cd || cd.min) return false;                 // skip envelope combos
    const b = cd.base || {};
    // dominant downward FZ, negligible lateral base shear
    return (b.FZ || 0) > 50 && Math.abs(b.FX || 0) < 1 && Math.abs(b.FY || 0) < 1;
  };
  const takedown = {};
  for (const [name, cd] of Object.entries(cases)) if (isGravity(cd)) takedown[name] = buildTakedown(cd);
  for (const [name, cd] of Object.entries(combos)) if (isGravity(cd)) takedown[name] = buildTakedown(cd);

  /* ---- v0.16: serviceability deflection checks — per static case + additive
     combo, one row per beam with member_deflections: max |dy| vs L/limit.
     ratio_str is the achieved "L/412" form; ok when max|dy| ≤ L/limit. */
  const deflLimit = (isFinite(model.deflection_limit) && model.deflection_limit > 0)
    ? model.deflection_limit : 360;
  const memByUid16 = {};
  for (const mm of model.members) memByUid16[mm.uid] = mm;
  const buildDeflChecks = cd => {
    const rows = [];
    for (const [uid, md] of Object.entries(cd.member_deflections || {})) {
      const mm = memByUid16[uid];
      if (!mm || mm.kind !== "beam") continue;
      const L = lenOf[uid] || 6;
      const maxAbs = (md.dy || []).reduce((a, v) => Math.max(a, Math.abs(v)), 0);
      const ratio = maxAbs > 1e-12 ? L / maxAbs : Infinity;
      rows.push({
        uid, story: mm.story, L: +L.toFixed(3),
        max_abs_dy: +maxAbs.toFixed(7),
        ratio_str: isFinite(ratio) ? `L/${Math.round(ratio)}` : "—",
        limit: `L/${deflLimit}`,
        ok: maxAbs <= L / deflLimit + 1e-12,
      });
    }
    rows.sort((a, b) => a.uid.localeCompare(b.uid, undefined, { numeric: true }));
    return rows;
  };
  const deflection_checks = {};
  for (const [name, cd] of Object.entries(cases)) {
    const rows = buildDeflChecks(cd);
    if (rows.length) deflection_checks[name] = rows;
  }
  for (const [name, cd] of Object.entries(combos)) {
    if (cd.min) continue;                        // envelope combos: skip
    const rows = buildDeflChecks(cd);
    if (rows.length) deflection_checks[name] = rows;
  }

  /* ---- v0.13: section-cut force resultants. For each defined cut and each
     case/combo/RS case, sum the internal forces of members that strictly cross
     the cutting plane (clipped to optional in-plane ranges). A low horizontal
     (z) cut reads FX ≈ base shear under a lateral case and FZ ≈ weight under a
     gravity case — local V2→X, V3→Y, axial→Z (mock mapping). */
  const secCutDefs = (model.section_cuts || []).filter(c => c && c.name);
  const buildCut = (cd, cut) => {
    const axisIdx = { x: 0, y: 1, z: 2 }[cut.axis] ?? 2;
    const inRange = p => {
      for (const [k, idx] of [["x_range", 0], ["y_range", 1], ["z_range", 2]]) {
        if (idx === axisIdx) continue;
        const rg = cut[k];
        if (rg && !(p[idx] >= rg[0] - 1e-6 && p[idx] <= rg[1] + 1e-6)) return false;
      }
      return true;
    };
    let FX = 0, FY = 0, FZ = 0, MX = 0, MY = 0, MZ = 0, nMem = 0, nShell = 0;
    for (const mm of model.members) {
      const a = mm.pi[axisIdx], b = mm.pj[axisIdx];
      const lo = Math.min(a, b), hi = Math.max(a, b);
      if (!(cut.coord > lo + 1e-6 && cut.coord < hi - 1e-6)) continue;
      const t = (cut.coord - a) / (b - a || 1);
      const cp = [0, 1, 2].map(i => mm.pi[i] + (mm.pj[i] - mm.pi[i]) * t);
      if (!inRange(cp)) continue;
      const f = (cd.member_forces || {})[mm.uid];
      if (!f) continue;
      nMem++;
      FX += f[1]; FY += f[2]; FZ += -f[0];
      MX += f[3]; MY += f[4]; MZ += f[5];
    }
    for (const sh of (model.shells || [])) {
      const zs = sh.corners.map(c => c[axisIdx]);
      const lo = Math.min(...zs), hi = Math.max(...zs);
      if (cut.coord > lo + 1e-6 && cut.coord < hi - 1e-6 &&
          inRange(sh.corners[0])) nShell++;
    }
    const warnings = [];
    if (!nMem && !nShell) warnings.push("no members or shells cross this cut plane");
    const r2 = v => +v.toFixed(2);
    return { FX: r2(FX), FY: r2(FY), FZ: r2(FZ), MX: r2(MX), MY: r2(MY), MZ: r2(MZ),
      n_members: nMem, n_shells: nShell, warnings };
  };
  const section_cuts = {};
  if (secCutDefs.length) {
    const perCase = (name, cd) => {
      const row = {};
      for (const cut of secCutDefs) row[cut.name] = buildCut(cd, cut);
      section_cuts[name] = row;
    };
    for (const [name, cd] of Object.entries(cases)) perCase(name, cd);
    for (const [name, cd] of Object.entries(combos)) perCase(name, cd);
    for (const [name, cd] of Object.entries(rs_cases)) perCase(name, cd);
  }

  /* ---- v0.15: wall-pier design forces. Piers group walls sharing a pier
     label (or every wall when auto_pier_walls, labelled by region uid).
     Physically-sensible mock: the pier picks up a share of the story shear at
     its TOP story and carries it down — V constant over the pier height, the
     in-plane moment M = V × lever growing downward (cantilever wall), and P
     accumulating gravity + the overturning chord force story by story. */
  const pierWalls = {};
  for (const sh of (model.shells || [])) {
    if (sh.kind !== "wall") continue;
    const label = (sh.pier || "").trim() || (model.auto_pier_walls ? sh.uid : "");
    if (!label) continue;
    (pierWalls[label] = pierWalls[label] || []).push(sh);
  }
  const piers = {};
  if (Object.keys(pierWalls).length) {
    const buildPierBlock = cd => {
      const out2 = {};
      for (const [label, walls] of Object.entries(pierWalls)) {
        const sts = [...new Set(walls.map(w => w.story))]
          .filter(s => storyOrder.includes(s))
          .sort((a, b) => storyElev[b] - storyElev[a]);          // top → bottom
        if (!sts.length) continue;
        const zTop = storyElev[sts[0]];
        const shTop = (cd.story || {})[sts[0]] || {};
        const V = +(0.35 * Math.hypot(shTop.shear_x || 0, shTop.shear_y || 0)).toFixed(2);
        const c = walls[0].corners;
        const Lw = Math.max(Math.hypot(c[1][0] - c[0][0], c[1][1] - c[0][1]), 1);
        const perStory = 0.06 * Math.max((cd.base && cd.base.FZ) || 0, 0);
        out2[label] = {};
        sts.forEach((s, i) => {
          const zBot = elevs[storyOrder.indexOf(s)];
          const M = +(V * (zTop - zBot)).toFixed(2);              // grows downward
          const P = +(-(perStory * (i + 1)) - M / (2 * Lw)).toFixed(2);
          out2[label][s] = { P, V, M };
        });
      }
      return out2;
    };
    for (const [name, cd] of Object.entries(cases)) piers[name] = buildPierBlock(cd);
    for (const [name, cd] of Object.entries(combos))
      if (!cd.min) piers[name] = buildPierBlock(cd);
    for (const [name, cd] of Object.entries(rs_cases)) piers[name] = buildPierBlock(cd);
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
  if (Object.keys(buckling).length) out.buckling = buckling;      // v0.10
  if (Object.keys(takedown).length) out.takedown = takedown;      // v0.11
  if (Object.keys(deflection_checks).length) out.deflection_checks = deflection_checks;  // v0.16
  if (Object.keys(section_cuts).length) out.section_cuts = section_cuts;  // v0.13
  if (Object.keys(piers).length) out.piers = piers;               // v0.15
  if (Object.keys(story_props).length) out.story_props = story_props;
  if (Object.keys(story_stiffness).length) out.story_stiffness = story_stiffness;
  if (Object.keys(irregularity).length) out.irregularity = irregularity;
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
   v0.10 — mock POST /api/case/rs-directional and /api/pattern/notional
   ================================================================ */

/** POST /api/case/rs-directional — add an RS directional combination (ASCE 7
    §12.5) of two response-spectrum cases. p: {name, name_x, name_y, method}.
    The combined directional case is produced (positive envelope) by
    mockResults into results.rs_cases[name] at solve time. */
export function mockRsDirectional(model, p = {}) {
  const name = (p.name || "RS-DIR").trim() || "RS-DIR";
  model.rs_combos = model.rs_combos || {};
  model.rs_combos[name] = {
    name,
    name_x: p.name_x || "",
    name_y: p.name_y || "",
    method: p.method === "SRSS" ? "SRSS" : "100_30",
  };
  return model;
}

/** POST /api/pattern/notional — add a notional lateral load pattern (AISC
    direct-analysis stability): F_i = coeff × gravity at each level applied
    laterally. p: {name, direction "X"|"Y", coeff, gravity_pattern}. Kind
    "notional" — renders like other lateral patterns (story_forces). */
export function mockNotionalPattern(model, p = {}) {
  const name = (p.name || "NOTIONAL").trim() || "NOTIONAL";
  const dirX = p.direction !== "Y";
  const coeff = isFinite(p.coeff) && p.coeff > 0 ? p.coeff : 0.002;
  const gravPat = p.gravity_pattern || "DEAD";
  const stories = model.stories || [];
  const sm = model.story_masses || {};
  // gravity at each level: prefer story mass; if the chosen pattern carries
  // per-member gravity UDLs, fold their tributary weight into the story.
  const gp = (model.patterns || {})[gravPat];
  const beamW = {};
  for (const l of (gp && gp.member_loads) || [])
    if ((l.kind || "udl") === "udl") beamW[l.member_uid] = (beamW[l.member_uid] || 0) + (l.w || 0);
  const storyGrav = {};
  for (const s of stories) storyGrav[s.name] = (sm[s.name] || 0) * G;   // kN
  for (const mm of model.members || [])
    if (mm.kind === "beam" && beamW[mm.uid])
      storyGrav[mm.story] = (storyGrav[mm.story] || 0) + beamW[mm.uid] * (mm.length || 0);
  const story_forces = stories.map(s => {
    const F = +(coeff * (storyGrav[s.name] || 0)).toFixed(3);
    return { story: s.name, fx: dirX ? F : 0, fy: dirX ? 0 : F };
  });
  model.patterns = model.patterns || {};
  model.patterns[name] = {
    name, kind: "notional", member_loads: [], area_loads: [], story_forces,
    notional: { direction: dirX ? "X" : "Y", coeff, gravity_pattern: gravPat },
  };
  return model;
}

/* ================================================================
   v0.7 — ASCE 7-16 code helpers (self-weight, auto combos, code RS
   spectrum, ELF). Mirrors skyframe/core/codes.py so the code-tools UI
   is exercisable offline (?mock=1) and previews render before the
   real endpoints run.
   ================================================================ */

/* ASCE 7-16 Table 11.4-1 (Fa) and 11.4-2 (Fv), classes A–E, with the
   Ss / S1 breakpoints. Linear interpolation across columns, clamped at the
   ends. Site class F is not tabulated (site-specific) — callers warn and we
   fall back to D so a preview still draws. */
const _ASCE_SS_BP = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5];
const _ASCE_S1_BP = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6];
const _ASCE_FA = {
  A: [0.8, 0.8, 0.8, 0.8, 0.8, 0.8],
  B: [0.9, 0.9, 0.9, 0.9, 0.9, 0.9],
  C: [1.3, 1.3, 1.2, 1.2, 1.2, 1.2],
  D: [1.6, 1.4, 1.2, 1.1, 1.0, 1.0],
  E: [2.4, 1.7, 1.3, 1.1, 0.9, 0.8],
};
const _ASCE_FV = {
  A: [0.8, 0.8, 0.8, 0.8, 0.8, 0.8],
  B: [0.8, 0.8, 0.8, 0.8, 0.8, 0.8],
  C: [1.5, 1.5, 1.5, 1.5, 1.5, 1.4],
  D: [2.4, 2.2, 2.0, 1.9, 1.8, 1.7],
  E: [4.2, 3.3, 2.8, 2.4, 2.2, 2.0],
};
function _interpTable(bp, row, x) {
  if (x <= bp[0]) return row[0];
  if (x >= bp[bp.length - 1]) return row[row.length - 1];
  for (let i = 1; i < bp.length; i++) {
    if (x <= bp[i]) {
      const t = (x - bp[i - 1]) / (bp[i] - bp[i - 1]);
      return row[i - 1] + t * (row[i] - row[i - 1]);
    }
  }
  return row[row.length - 1];
}

/** (Fa, Fv) from ASCE 7-16 site tables; class F falls back to D. */
export function siteCoefficients(Ss, S1, site = "D") {
  const sc = _ASCE_FA[site] ? site : "D";
  return [
    _interpTable(_ASCE_SS_BP, _ASCE_FA[sc], Ss),
    _interpTable(_ASCE_S1_BP, _ASCE_FV[sc], S1),
  ];
}

/** {SDS, SD1, SMS, SM1, T0, Ts} per ASCE 7-16 §11.4. */
export function spectrumParameters(Ss, S1, site = "D") {
  const [Fa, Fv] = siteCoefficients(Ss, S1, site);
  const SMS = Fa * Ss, SM1 = Fv * S1;
  const SDS = (2 / 3) * SMS, SD1 = (2 / 3) * SM1;
  const T0 = SDS > 0 ? 0.2 * SD1 / SDS : 0;
  const Ts = SDS > 0 ? SD1 / SDS : 0;
  return { SDS, SD1, SMS, SM1, T0, Ts };
}

/** ASCE 7-16 design spectrum [[T, Sa_g], …] — ramp 0.4·SDS→SDS on [0,T0],
    flat SDS on [T0,Ts], SD1/T on [Ts,TL], SD1·TL/T² beyond. Corner points
    sampled exactly; a few points fill the 1/T branch for a smooth preview. */
export function asce7SpectrumPreview(Ss, S1, site = "D", TL = 8.0) {
  const { SDS, SD1, T0, Ts } = spectrumParameters(Ss, S1, site);
  const r = v => +v.toFixed(4);
  if (!(SDS > 0)) return [[0, 0], [1, 0]];
  const pts = [[0, r(0.4 * SDS)], [r(T0), r(SDS)], [r(Ts), r(SDS)]];
  // 1/T descending branch up to TL
  for (const T of [1.0, 1.5, 2.0, 3.0, 4.0, 6.0, TL]) {
    if (T > Ts && T <= TL) pts.push([r(T), r(SD1 / T)]);
  }
  // long-period 1/T² branch beyond TL
  for (const T of [TL * 1.5, TL * 2]) pts.push([r(T), r(SD1 * TL / (T * T))]);
  // dedupe by T (corner collisions) and sort
  const seen = new Set();
  return pts.filter(p => { const k = p[0]; if (seen.has(k)) return false; seen.add(k); return true; })
    .sort((a, b) => a[0] - b[0]);
}

/** Eurocode 8 (EN 1998-1) elastic response spectrum, Type 1 → [[T, Sa_g], …].
    A plausible mock EC8 shape for the function-library EC8 preset button. */
export function mockEc8Spectrum(opts = {}) {
  const ag = isFinite(opts.ag) ? opts.ag : 0.25;      // g
  const S = isFinite(opts.S) ? opts.S : 1.2;          // soil factor
  const TB = isFinite(opts.TB) ? opts.TB : 0.15;
  const TC = isFinite(opts.TC) ? opts.TC : 0.5;
  const TD = isFinite(opts.TD) ? opts.TD : 2.0;
  const zeta = isFinite(opts.damping) ? opts.damping * 100 : 5;   // %
  const eta = Math.max(0.55, Math.sqrt(10 / (5 + zeta)));
  const B = 2.5;
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

/** POST /api/pattern/selfweight — add/replace a self-weight dead pattern.
    Mirrors builder.add_self_weight: kind "dead", self_weight_factor=factor. */
export function mockSelfWeightPattern(model, p = {}) {
  const name = (p.name || "SW").trim() || "SW";
  const factor = isFinite(p.factor) ? p.factor : 1.0;
  model.patterns = model.patterns || {};
  model.patterns[name] = {
    name, kind: "dead", member_loads: [], area_loads: [], story_forces: [],
    self_weight_factor: factor,
  };
  // matching single-pattern case (builder creates one)
  model.cases = model.cases || {};
  if (!model.cases[name])
    model.cases[name] = { name, patterns: { [name]: 1 }, pdelta: false };
  return model;
}

/** POST /api/combos/asce7 — append ASCE 7-16 §2.3/§2.4 combos matched by
    the model's pattern kinds. Returns {model, added:[names]}.
    v0.17: optional SDS folds the vertical seismic component Ev = 0.2·SDS·D
    (§12.4.2.2) into the seismic combos' D factors — strength
    (1.2+0.2·SDS)·D + E and (0.9−0.2·SDS)·D + E; ASD (1.0+0.14·SDS)·D + 0.7E
    and (0.6−0.14·SDS)·D + 0.7E. */
export function mockAsce7Combos(model, standard = "LRFD", SDS) {
  const std = standard === "ASD" ? "ASD" : "LRFD";
  const sds = isFinite(SDS) ? Math.max(SDS, 0) : null;
  const rf = v => +v.toFixed(3);            // factor (dict value)
  const rl = v => String(+v.toFixed(2));    // factor label (combo name)
  // classify existing cases by the kind of their dominant pattern
  const kindOf = c => {
    const pk = Object.keys(c.patterns || {});
    for (const pn of pk) { const p = model.patterns[pn]; if (p) return p.kind; }
    return "other";
  };
  const byKind = { dead: [], live: [], quake: [], wind: [], other: [] };
  for (const [cn, c] of Object.entries(model.cases || {})) {
    const k = kindOf(c);
    (byKind[k] || byKind.other).push(cn);
  }
  const D = byKind.dead[0], L = byKind.live[0];
  const quakes = byKind.quake, winds = byKind.wind.concat(
    // wind patterns are stored kind "other"/"wind"; also treat cases whose
    // pattern name looks like wind
    Object.entries(model.cases || {})
      .filter(([cn]) => /wind|^w[xy]?$/i.test(cn) && !byKind.wind.includes(cn))
      .map(([cn]) => cn));
  const combos = {};
  const add = (name, cases) => { combos[name] = { name, combo_type: "add", cases }; };
  if (D) {
    if (std === "LRFD") {
      add("1.4D", { [D]: 1.4 });
      if (L) add("1.2D + 1.6L", { [D]: 1.2, [L]: 1.6 });
      // v0.17: with SDS, Ev = 0.2·SDS·D shifts the seismic D factors
      const dUp = sds != null ? rf(1.2 + 0.2 * sds) : 1.2;
      const dDn = sds != null ? rf(0.9 - 0.2 * sds) : 0.9;
      for (const q of quakes) {
        for (const s of [1, -1]) {
          const c = { [D]: dUp }; if (L) c[L] = 1.0; c[q] = 1.0 * s;
          add(`${rl(dUp)}D ${L ? "+ 1.0L " : ""}${s > 0 ? "+" : "−"} 1.0${q}`, c);
          add(`${rl(dDn)}D ${s > 0 ? "+" : "−"} 1.0${q}`, { [D]: dDn, [q]: 1.0 * s });
        }
      }
      for (const w of winds) for (const s of [1, -1]) {
        const c = { [D]: 1.2 }; if (L) c[L] = 1.0; c[w] = 1.0 * s;
        add(`1.2D ${L ? "+ 1.0L " : ""}${s > 0 ? "+" : "−"} 1.0${w}`, c);
      }
    } else { // ASD
      add("D", { [D]: 1.0 });
      if (L) add("D + L", { [D]: 1.0, [L]: 1.0 });
      // v0.17: with SDS, Ev shifts the ASD seismic D factors by ±0.14·SDS
      const dUp = sds != null ? rf(1.0 + 0.14 * sds) : 1.0;
      const dDn = sds != null ? rf(0.6 - 0.14 * sds) : 0.6;
      for (const q of quakes) for (const s of [1, -1]) {
        add(`${rl(dUp)}D ${s > 0 ? "+" : "−"} 0.7${q}`, { [D]: dUp, [q]: 0.7 * s });
        const c = { [D]: 1.0 }; if (L) c[L] = 0.75; c[q] = 0.525 * s;
        add(`D ${L ? "+ 0.75L " : ""}${s > 0 ? "+" : "−"} 0.525${q}`, c);
        add(`${rl(dDn)}D ${s > 0 ? "+" : "−"} 0.7${q}`, { [D]: dDn, [q]: 0.7 * s });
      }
      for (const w of winds) for (const s of [1, -1]) {
        add(`D ${s > 0 ? "+" : "−"} 0.6${w}`, { [D]: 1.0, [w]: 0.6 * s });
        add(`0.6D ${s > 0 ? "+" : "−"} 0.6${w}`, { [D]: 0.6, [w]: 0.6 * s });
      }
    }
  }
  model.combos = model.combos || {};
  const added = [];
  for (const [n, c] of Object.entries(combos)) { model.combos[n] = c; added.push(n); }
  return { model, added };
}

/** POST /api/case/rs-code — add a ResponseSpectrumCase from an ASCE 7-16
    design spectrum; the Ie/R reduction is carried on scale. */
export function mockCodeRsCase(model, p = {}) {
  const name = (p.name || "RS-Code").trim() || "RS-Code";
  const Ss = isFinite(p.Ss) ? p.Ss : 1.0;
  const S1 = isFinite(p.S1) ? p.S1 : 0.6;
  const site = _ASCE_FA[p.site_class] ? p.site_class : "D";
  const R = isFinite(p.R) && p.R > 0 ? p.R : 8.0;
  const Ie = isFinite(p.Ie) && p.Ie > 0 ? p.Ie : 1.0;
  const spectrum = asce7SpectrumPreview(Ss, S1, site);
  model.rs_cases = model.rs_cases || {};
  model.rs_cases[name] = {
    name, direction: p.direction === "Y" ? "Y" : "X",
    spectrum, combo_method: "CQC", damping: 0.05, scale: +(Ie / R).toFixed(5),
  };
  return model;
}

/** POST /api/pattern/elf — add an equivalent-lateral-force quake pattern
    (§12.8). Story forces from Cs·W distributed by w·h^k. */
export function mockElfPattern(model, p = {}) {
  const name = (p.name || "EQ-ELF").trim() || "EQ-ELF";
  const SDS = isFinite(p.SDS) ? p.SDS : 1.0;
  const SD1 = isFinite(p.SD1) ? p.SD1 : 0.6;
  const R = isFinite(p.R) && p.R > 0 ? p.R : 8.0;
  const Ie = isFinite(p.Ie) && p.Ie > 0 ? p.Ie : 1.0;
  const dirX = p.direction !== "Y";
  const stories = model.stories || [];
  const sm = model.story_masses || {};
  const hn = stories.length ? stories[stories.length - 1].elevation : 1;
  const Ta = 0.0466 * Math.pow(Math.max(hn, 0.1), 0.9);          // §12.8-7 (Ct steel MRF)
  const Cs = Math.max(
    Math.min(SDS / (R / Ie), SD1 / (Ta * (R / Ie)) || Infinity),
    Math.max(0.044 * SDS * Ie, 0.01));
  const W = Object.values(sm).reduce((a, b) => a + b, 0) * G;    // kN
  const V = Cs * W;
  const k = Ta <= 0.5 ? 1 : Ta >= 2.5 ? 2 : 1 + (Ta - 0.5) / 2;
  const wh = stories.map((s, i) => (sm[s.name] || 0) * G * Math.pow(s.elevation, k));
  const sumWh = wh.reduce((a, b) => a + b, 0) || 1;
  const story_forces = stories.map((s, i) => {
    const F = +(V * wh[i] / sumWh).toFixed(2);
    return { story: s.name, fx: dirX ? F : 0, fy: dirX ? 0 : F };
  });
  model.patterns = model.patterns || {};
  model.patterns[name] = {
    name, kind: "quake", member_loads: [], area_loads: [], story_forces,
    elf: { SDS, SD1, R, Ie, Cs: +Cs.toFixed(4), V: +V.toFixed(2), Ta: +Ta.toFixed(3) },
  };
  return model;
}

/** Client-side Cs readout for the ELF card (matches mockElfPattern math). */
export function elfCs(SDS, SD1, R, Ie, hn) {
  if (!(R > 0) || !(Ie > 0)) return null;
  const Ta = 0.0466 * Math.pow(Math.max(hn || 1, 0.1), 0.9);
  const Cs = Math.max(
    Math.min(SDS / (R / Ie), SD1 / (Ta * (R / Ie)) || Infinity),
    Math.max(0.044 * SDS * Ie, 0.01));
  return { Cs, Ta };
}

/* ================================================================
   v0.23 — NBCC code tools (wind + seismic ELF) and shell wind
   ================================================================ */

/** NBCC exposure factor Ce(z) — NBCC 2015/2020 §4.1.7.1:
    open terrain Ce = (z/10)^0.2 ≥ 0.9; rough terrain Ce = 0.7·(z/12)^0.3
    ≥ 0.7. z in metres. */
function _nbccCe(z, exposure) {
  return exposure === "rough"
    ? Math.max(0.7 * Math.pow(Math.max(z, 0.1) / 12, 0.3), 0.7)
    : Math.max(Math.pow(Math.max(z, 0.1) / 10, 0.2), 0.9);
}

/** POST /api/pattern/nbcc-wind — NBCC static wind procedure (§4.1.7),
    honest simplified form: external pressure p = Iw·q·Ce·Cg·Cp with
    Iw = 1.0 (normal importance), gust factor Cg = 2.0 and the flat-faced
    building coefficients Cp = +0.8 windward / −0.5 leeward. The windward
    leg uses Ce(z) at each story's elevation (power-law exposure profile);
    the leeward suction is constant with height at Ce(h) of the roof, per
    the NBCC figure. Story force F = [q·Ce(z)·Cg·0.8 + q·Ce(h)·Cg·0.5]
    × face width × tributary height. p: {q kPa, exposure "open"|"rough",
    name?, direction "X"|"Y"}. */
export function mockNbccWindPattern(model, p = {}) {
  const name = (p.name || "NBCC-WX").trim() || "NBCC-WX";
  const dirX = p.direction !== "Y";
  const q = isFinite(p.q) && p.q > 0 ? p.q : 0.45;         // kPa reference velocity pressure
  const exp_ = p.exposure === "rough" ? "rough" : "open";
  const Cg = 2.0, CpWw = 0.8, CpLee = 0.5, Iw = 1.0;

  const g = model.grid || { x_lines: [0, 18], y_lines: [0, 12] };
  const width = dirX
    ? (g.y_lines[g.y_lines.length - 1] - g.y_lines[0])     // face ⟂ X wind
    : (g.x_lines[g.x_lines.length - 1] - g.x_lines[0]);
  const stories = model.stories || [];
  const hn = stories.length ? stories[stories.length - 1].elevation : 10;
  const pLee = Iw * q * _nbccCe(hn, exp_) * Cg * CpLee;    // kPa, constant

  const story_forces = stories.map((st, i) => {
    const hAbove = i + 1 < stories.length ? stories[i + 1].height : 0;
    const trib = st.height / 2 + hAbove / 2;               // ground half sheds to base
    const pWw = Iw * q * _nbccCe(st.elevation, exp_) * Cg * CpWw;   // kPa
    const F = +((pWw + pLee) * Math.max(width, 1) * trib).toFixed(2);
    return { story: st.name, fx: dirX ? F : 0, fy: dirX ? 0 : F };
  });

  model.patterns = model.patterns || {};
  model.patterns[name] = {
    name, kind: "other", member_loads: [], area_loads: [], story_forces,
    wind: { code: "NBCC", direction: dirX ? "X" : "Y", q, exposure: exp_ },
  };
  return model;
}

/** NBCC design spectral acceleration S(T) — linear interpolation between the
    site-adjusted Sa(0.2)/Sa(0.5)/Sa(1.0)/Sa(2.0) ordinates, flat at Sa(0.2)
    below 0.2 s and Sa(4.0) = Sa(2.0)/2 beyond 2 s (§4.1.8.4(7)). */
function _nbccS(T, Sa02, Sa05, Sa10, Sa20) {
  const pts = [[0.2, Sa02], [0.5, Sa05], [1.0, Sa10], [2.0, Sa20], [4.0, Sa20 / 2]];
  if (T <= pts[0][0]) return pts[0][1];
  for (let i = 1; i < pts.length; i++) {
    if (T <= pts[i][0]) {
      const [t0, s0] = pts[i - 1], [t1, s1] = pts[i];
      return s0 + (s1 - s0) * (T - t0) / (t1 - t0);
    }
  }
  return pts[pts.length - 1][1];
}

/** Client-side readout for the NBCC ELF card (matches mockNbccElfPattern):
    Ta (§4.1.8.11(3), moment-frame form 0.085·hn^0.75), S(Ta) and the base
    shear coefficient V/W = S(Ta)·Mv·Ie/(Rd·Ro) with the code's upper cap
    max(2/3·S(0.2), S(0.5))·Ie/(RdRo) and lower bound S(4.0)·Ie/(RdRo). */
export function nbccElfInfo(p = {}, hn = 10) {
  const Sa02 = isFinite(p.Sa02) ? p.Sa02 : 0.65;
  const Sa05 = isFinite(p.Sa05) ? p.Sa05 : 0.4;
  const Sa10 = isFinite(p.Sa10) ? p.Sa10 : 0.2;
  const Sa20 = isFinite(p.Sa20) ? p.Sa20 : 0.1;
  const RdRo = isFinite(p.RdRo) && p.RdRo > 0 ? p.RdRo : 4.0;
  const Ie = isFinite(p.Ie) && p.Ie > 0 ? p.Ie : 1.0;
  // Ta by system: steel MRF 0.085·hn^0.75 (default) · concrete MRF 0.075
  // · braced/walls/other 0.05 (§4.1.8.11(3))
  const ct = p.system === "concrete_mrf" ? 0.075
    : (p.system === "braced" || p.system === "wall" || p.system === "other") ? 0.05
    : 0.085;
  const Ta = ct * Math.pow(Math.max(hn || 1, 0.1), 0.75);
  const Mv = 1.0;                                          // mock higher-mode factor
  const S = _nbccS(Ta, Sa02, Sa05, Sa10, Sa20);
  let Cs = S * Mv * Ie / RdRo;
  Cs = Math.min(Cs, Math.max((2 / 3) * Sa02, Sa05) * Ie / RdRo);  // §4.1.8.11(2)(c)
  Cs = Math.max(Cs, (Sa20 / 2) * Mv * Ie / RdRo);                 // ≥ S(4.0) floor
  return { Ta, S, Cs };
}

/** POST /api/pattern/nbcc-elf — NBCC equivalent static force procedure
    (§4.1.8.11). V = S(Ta)·Mv·Ie·W/(Rd·Ro) with the code cap/floor (see
    nbccElfInfo); a top force Ft = 0.07·Ta·V ≤ 0.25·V peels off when
    Ta > 0.7 s and the remainder distributes as Wx·hx/ΣWi·hi (§4.1.8.11(7)).
    p: {Sa02, Sa05, Sa10, Sa20, RdRo, Ie?, system?, name?, direction}. */
export function mockNbccElfPattern(model, p = {}) {
  const name = (p.name || "EQ-NBCC").trim() || "EQ-NBCC";
  const dirX = p.direction !== "Y";
  const stories = model.stories || [];
  const sm = model.story_masses || {};
  const hn = stories.length ? stories[stories.length - 1].elevation : 1;
  const { Ta, S, Cs } = nbccElfInfo(p, hn);
  const W = Object.values(sm).reduce((a, b) => a + b, 0) * G;      // kN
  const V = Cs * W;
  const Ft = Ta > 0.7 ? Math.min(0.07 * Ta * V, 0.25 * V) : 0;     // top force
  const wh = stories.map(s => (sm[s.name] || 0) * G * s.elevation);
  const sumWh = wh.reduce((a, b) => a + b, 0) || 1;
  const story_forces = stories.map((s, i) => {
    let F = (V - Ft) * wh[i] / sumWh;
    if (i === stories.length - 1) F += Ft;                         // Ft at the roof
    F = +F.toFixed(2);
    return { story: s.name, fx: dirX ? F : 0, fy: dirX ? 0 : F };
  });
  model.patterns = model.patterns || {};
  model.patterns[name] = {
    name, kind: "quake", member_loads: [], area_loads: [], story_forces,
    elf: { code: "NBCC", Sa02: p.Sa02, Sa05: p.Sa05, Sa10: p.Sa10, Sa20: p.Sa20,
      RdRo: p.RdRo, Ie: p.Ie ?? 1.0, S: +S.toFixed(4), Ta: +Ta.toFixed(3),
      V: +V.toFixed(2) },
  };
  return model;
}

/** Plan area + unit normal of a shell region (first-two-edges cross product;
    exact for the planar quads the editor draws). */
function _regionAreaNormal(sh) {
  const c = sh.corners || [];
  if (c.length < 3) return { area: 0, n: [0, 0, 1] };
  // shoelace-style: sum of cross products of consecutive edge vectors
  let nx = 0, ny = 0, nz = 0;
  for (let i = 1; i + 1 < c.length; i++) {
    const u = [c[i][0] - c[0][0], c[i][1] - c[0][1], c[i][2] - c[0][2]];
    const v = [c[i + 1][0] - c[0][0], c[i + 1][1] - c[0][1], c[i + 1][2] - c[0][2]];
    nx += u[1] * v[2] - u[2] * v[1];
    ny += u[2] * v[0] - u[0] * v[2];
    nz += u[0] * v[1] - u[1] * v[0];
  }
  const L = Math.hypot(nx, ny, nz);
  return { area: L / 2, n: L > 1e-12 ? [nx / L, ny / L, nz / L] : [0, 0, 1] };
}

/** POST /api/pattern/shell-wind — wind pressure on shell regions carrying a
    wind_cp coefficient. p: {q kPa, name?}. Each tagged region gets an area
    load q·Cp; the mock then SUMS those area loads client-side into story
    forces along each region's plan normal (F = q·Cp·area, walls only — a
    horizontal region's normal has no lateral component). Throws when no
    region carries a Cp so the card can toast a useful error. */
export function mockShellWindPattern(model, p = {}) {
  const name = (p.name || "WIND-SHELL").trim() || "WIND-SHELL";
  const q = isFinite(p.q) && p.q > 0 ? p.q : 0.5;                  // kPa
  const tagged = (model.shells || []).filter(s => s.wind_cp != null && isFinite(s.wind_cp));
  if (!tagged.length)
    throw new Error("no shell region has a Wind Cp — assign one in Model mode → shell properties");
  const area_loads = [];
  const storyF = {};                                               // story -> [fx, fy]
  for (const sh of tagged) {
    const pr = q * sh.wind_cp;                                     // kPa on the region
    area_loads.push({ region_uid: sh.uid, q: +pr.toFixed(4) });
    const { area, n } = _regionAreaNormal(sh);
    const F = pr * area;                                           // kN along the normal
    const st = sh.story || (model.stories && model.stories[0] && model.stories[0].name);
    if (!st) continue;
    if (!storyF[st]) storyF[st] = [0, 0];
    storyF[st][0] += F * n[0];
    storyF[st][1] += F * n[1];
  }
  const story_forces = (model.stories || [])
    .filter(s => storyF[s.name])
    .map(s => ({ story: s.name,
      fx: +storyF[s.name][0].toFixed(2), fy: +storyF[s.name][1].toFixed(2) }))
    .filter(f => Math.abs(f.fx) > 1e-9 || Math.abs(f.fy) > 1e-9);
  model.patterns = model.patterns || {};
  model.patterns[name] = {
    name, kind: "other", member_loads: [], area_loads, story_forces,
    wind: { code: "shell", q },
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

/* ================================================================
   v0.6 — mock design checks (steel / concrete) + model importers
   ================================================================ */

/** Resolve the member-forces map for a requested case/combo from a mock
    results run (design endpoints run a fresh analysis first). */
function _caseForcesFor(model, caseName) {
  const r = mockResults(model);
  const cd = (r.cases && r.cases[caseName]) ||
    (r.combos && r.combos[caseName]) ||
    (r.cases && r.cases[Object.keys(r.cases)[0]]);
  return { forces: (cd && cd.member_forces) || {}, resolved: caseName };
}

/** v0.9 — resolve the design request into a response. When `body.combos` is
    set (true = all combos, or an explicit name list) the checks are ENVELOPED
    over the combos: each member keeps its worst (max-ratio) combo and gains a
    `governing_combo` field. Otherwise a single case/combo is checked. */
function _designEnvelope(model, body, buildForCase) {
  const combosReq = body.combos;
  if (combosReq) {
    const all = Object.keys(model.combos || {});
    const names = (Array.isArray(combosReq) ? combosReq.filter(n => all.includes(n)) : all);
    if (!names.length) {                          // no combos → fall back to a case
      const caseName = body.case || Object.keys(model.cases || {})[0];
      const checks = buildForCase(caseName);
      return { preliminary: true, case: caseName, checks, summary: _summary(checks) };
    }
    const byUid = new Map();
    for (const cn of names) {
      for (const c of buildForCase(cn)) {
        const prev = byUid.get(c.uid);
        const better = !prev ||
          (c.status !== "N/A" && (prev.status === "N/A" || c.ratio > prev.ratio));
        if (better) byUid.set(c.uid, { ...c, governing_combo: cn });
      }
    }
    const checks = [...byUid.values()];
    return {
      preliminary: true, envelope: true, combos: names,
      case: `envelope · ${names.length} combo${names.length > 1 ? "s" : ""}`,
      checks, summary: _summary(checks),
    };
  }
  const caseName = body.case || Object.keys(model.cases || {})[0];
  const checks = buildForCase(caseName);
  return { preliminary: true, case: caseName, checks, summary: _summary(checks) };
}

function _summary(checks, governKey = "ratio") {
  const ok = checks.filter(c => c.status === "OK").length;
  const ng = checks.filter(c => c.status === "NG").length;
  const na = checks.filter(c => c.status === "N/A").length;
  // v0.16: a biaxial column's governing ratio is ratio_biaxial
  const govOf = c => (c.biaxial && isFinite(c.ratio_biaxial)) ? c.ratio_biaxial : c.ratio;
  let max_ratio = 0, governing = null;
  for (const c of checks) {
    if (c.status === "N/A") continue;
    if (govOf(c) > max_ratio) { max_ratio = govOf(c); governing = c.uid; }
  }
  return { n: checks.length, ok, ng, na, max_ratio: +max_ratio.toFixed(3), governing };
}

/* ================================================================
   v0.16 — mock GET /api/live-reduction (ASCE 7 §4.7)
   ================================================================ */

/** Per-column live-load reduction factors: {uid: {KLL, At, R}}.
    KLL = 4 (interior columns, Table 4.7-1); At = the column's tributary plan
    area from the grid spacing (half-bay each side, edges get half);
    R = 0.25 + 4.57/√(KLL·At), clamped to [0.4, 1.0] (§4.7.2, SI form). */
export function mockLiveReduction(model) {
  const g = model.grid || {};
  const xs = Array.isArray(g.x_lines) && g.x_lines.length >= 2 ? g.x_lines : [0, 6];
  const ys = Array.isArray(g.y_lines) && g.y_lines.length >= 2 ? g.y_lines : [0, 6];
  const tribAlong = (lines, c) => {
    let i = 0, bd = Infinity;
    lines.forEach((v, k) => { const d = Math.abs(v - c); if (d < bd) { bd = d; i = k; } });
    const below = i > 0 ? (lines[i] - lines[i - 1]) / 2 : 0;
    const above = i < lines.length - 1 ? (lines[i + 1] - lines[i]) / 2 : 0;
    return Math.max(below + above, 0.5);
  };
  const out = {};
  for (const mm of model.members || []) {
    if (mm.kind !== "column") continue;
    const KLL = 4;
    const At = tribAlong(xs, mm.pi[0]) * tribAlong(ys, mm.pi[1]);
    let R = 0.25 + 4.57 / Math.sqrt(KLL * At);
    R = Math.min(1.0, Math.max(0.4, R));
    out[mm.uid] = { KLL, At: +At.toFixed(2), R: +R.toFixed(3) };
  }
  return out;
}

/** v0.23 — apply a design-code calibration factor to every valid check.
    The EC screening (γM partial factors vs φ resistance factors) runs a
    touch hotter than the US codes in this mock so switching the code select
    visibly changes ratios/statuses. Statuses + summary are recomputed. */
function _applyCodeScale(res, k) {
  for (const c of res.checks || []) {
    if (c.status === "N/A") continue;
    c.ratio = +((c.ratio || 0) * k).toFixed(3);
    if (isFinite(c.ratio_biaxial) && c.ratio_biaxial != null)
      c.ratio_biaxial = +(c.ratio_biaxial * k).toFixed(3);
    const gov = (c.biaxial && isFinite(c.ratio_biaxial)) ? c.ratio_biaxial : c.ratio;
    c.status = gov <= 1.0 ? "OK" : "NG";
  }
  res.summary = _summary(res.checks || []);
  return res;
}

/** POST /api/design/steel — AISC-H1-style interaction screening (mock).
    Accepts {case} or {combos:true|[names]} (v0.9 envelope over combos), the
    v0.16 {live_reduction, live_case} flags (columns get Pu × R) and the
    v0.23 {code: "AISC360"|"EC3"} design-code select (echoed back). */
export function mockDesignSteel(model, body = {}) {
  const Fy = isFinite(body.Fy) && body.Fy > 0 ? body.Fy : 345000;   // kPa (≈345 MPa)
  const code = body.code === "EC3" ? "EC3" : "AISC360";             // v0.23
  const llr = body.live_reduction ? mockLiveReduction(model) : null;
  const res = _designEnvelope(model, body, caseName => _steelChecks(model, caseName, Fy, llr));
  if (body.live_reduction) res.live_reduction = true;
  res.code = code;
  if (code === "EC3") _applyCodeScale(res, 1.05);
  return res;
}

function _steelChecks(model, caseName, Fy, llr) {
  const { forces } = _caseForcesFor(model, caseName);
  const checks = [];
  for (const mm of model.members) {
    if (mm.kind === "brace") continue;
    const f = forces[mm.uid];
    const sec = (model.sections || {})[mm.section];
    if (!f || !sec) {
      checks.push({
        uid: mm.uid, section: mm.section, kind: mm.kind, Pu: 0, Mu33: 0, Mu22: 0,
        phiPn: 0, phiMn33: 0, phiMn22: 0, ratio: 0, equation: "—",
        status: "N/A", notes: "no forces / section", preliminary: true,
      });
      continue;
    }
    const b = sec.b || 0.3, h = sec.h || 0.5;
    const A = isFinite(sec.A) && sec.A > 0 ? sec.A : b * h;
    const Z33 = isFinite(sec.I33) ? sec.I33 / (h / 2) * 1.12 : b * h * h / 4;
    const Z22 = isFinite(sec.I22) ? sec.I22 / (b / 2) * 1.12 : h * b * b / 4;
    let Pu = Math.max(Math.abs(f[0]), Math.abs(f[6]));
    // v0.16: live-load reduction — reduced axial demand on columns
    if (llr && mm.kind === "column" && llr[mm.uid]) Pu *= llr[mm.uid].R;
    const Mu33 = Math.max(Math.abs(f[5]), Math.abs(f[11]));
    const Mu22 = Math.max(Math.abs(f[4]), Math.abs(f[10]));
    const phiPn = +(0.9 * Fy * A).toFixed(1);
    const phiMn33 = +(0.9 * Fy * Z33).toFixed(1);
    const phiMn22 = +(0.9 * Fy * Z22).toFixed(1);
    const pr = phiPn ? Pu / phiPn : 0;
    let ratio, equation;
    if (pr >= 0.2) {
      ratio = pr + (8 / 9) * (Mu33 / (phiMn33 || 1) + Mu22 / (phiMn22 || 1));
      equation = "H1-1a";
    } else {
      ratio = pr / 2 + (Mu33 / (phiMn33 || 1) + Mu22 / (phiMn22 || 1));
      equation = "H1-1b";
    }
    ratio = +ratio.toFixed(3);
    checks.push({
      uid: mm.uid, section: mm.section, kind: mm.kind,
      Pu: +Pu.toFixed(1), Mu33: +Mu33.toFixed(1), Mu22: +Mu22.toFixed(1),
      phiPn, phiMn33, phiMn22, ratio, equation,
      status: ratio <= 1.0 ? "OK" : "NG",
      notes: ratio > 1.0 ? "interaction > 1.0" : "", preliminary: true,
    });
  }
  return checks;
}

/* ================================================================
   v0.12 — mock section optimization (POST /api/design/optimize)
   ================================================================ */

/** Self-weight of a frame section in kg/m: A (m²) × material density (kg/m³),
    density from the material unit weight (kN/m³ → kg/m³ via g). */
function _sectionWeight(model, sec) {
  if (!sec) return 0;
  const A = isFinite(sec.A) && sec.A > 0 ? sec.A
    : (sec.b || 0) * (sec.h || 0);
  const mat = (model.materials || {})[sec.material];
  const uw = mat && isFinite(mat.unit_weight) ? mat.unit_weight : 24;   // kN/m³
  const density = uw * 1000 / 9.80665;                                   // kg/m³
  return A * density;
}

/** Build a lighter, area-scaled variant of a section (area × factor). Rect
    (b/h) sections scale each dimension by √factor; property (A/I) sections
    scale A by the factor and I by factor². */
function _scaledSection(sec, factor, name) {
  const out = { ...sec, name };
  if (isFinite(sec.b) && isFinite(sec.h)) {
    const s = Math.sqrt(factor);
    out.b = +(sec.b * s).toFixed(4);
    out.h = +(sec.h * s).toFixed(4);
  }
  if (isFinite(sec.A)) out.A = +(sec.A * factor).toExponential(4) / 1;
  if (isFinite(sec.I33)) out.I33 = +(sec.I33 * factor * factor).toExponential(4) / 1;
  if (isFinite(sec.I22)) out.I22 = +(sec.I22 * factor * factor).toExponential(4) / 1;
  if (isFinite(sec.J)) out.J = +(sec.J * factor * factor).toExponential(4) / 1;
  return out;
}

/** Compute optimization suggestions for the current model against a target
    D/C ratio. Over-designed members (ratio < target) are downsized to a lighter
    section that keeps the demand under the target; the governing member and
    any member already near/over capacity report "no_section_passes". Members
    with no valid check are "n/a". Returns {suggestions, sections} where
    `sections` maps a suggested-section name to its (scaled) section dict. */
function _optimizeSuggestions(model, caseName, target) {
  const tgt = isFinite(target) && target > 0 ? target : 0.95;
  const checks = _steelChecks(model, caseName, 345000);
  const byUid = new Map(checks.map(c => [c.uid, c]));
  // governing member = the largest valid D/C ratio → never downsized
  let govUid = null, govRatio = -1;
  for (const c of checks) {
    if (c.status !== "N/A" && c.ratio > govRatio) { govRatio = c.ratio; govUid = c.uid; }
  }
  const suggestions = [];
  const sections = {};
  for (const mm of model.members) {
    if (mm.kind === "brace") continue;                 // braces excluded (as in checks)
    const c = byUid.get(mm.uid);
    const sec = (model.sections || {})[mm.section];
    const curW = +_sectionWeight(model, sec).toFixed(1);
    if (!c || c.status === "N/A" || !sec) {
      suggestions.push({
        uid: mm.uid, current_section: mm.section, suggested_section: mm.section,
        current_ratio: c ? c.ratio : 0, suggested_ratio: c ? c.ratio : 0,
        weight_kg_per_m: 0, status: "n/a",
      });
      continue;
    }
    const r0 = c.ratio;
    const aNeeded = r0 / tgt;                           // area fraction to reach target
    if (mm.uid === govUid || aNeeded >= 0.95 || r0 <= 1e-6) {
      // controlling member, near capacity, or unloaded → keep the section
      suggestions.push({
        uid: mm.uid, current_section: mm.section, suggested_section: mm.section,
        current_ratio: r0, suggested_ratio: r0, weight_kg_per_m: 0,
        status: "no_section_passes",
      });
      continue;
    }
    // downsize: pick a lighter area factor with a little margin below target
    const a = Math.min(0.92, Math.max(aNeeded, 0.5));
    const pct = Math.round((1 - a) * 100);
    const name = `${mm.section}-L${pct}`;
    if (!sections[name]) sections[name] = _scaledSection(sec, a, name);
    const newW = +_sectionWeight(model, sections[name]).toFixed(1);
    suggestions.push({
      uid: mm.uid, current_section: mm.section, suggested_section: name,
      current_ratio: r0, suggested_ratio: +(r0 / a).toFixed(3),
      weight_kg_per_m: +(newW - curW).toFixed(1), status: "ok",
    });
  }
  return { suggestions, sections };
}

/** POST /api/design/optimize — auto section optimization (mock).
    apply:false → {suggestions:[...]}; apply:true → the updated model dict with
    members reassigned to their suggested (lighter) sections. */
export function mockOptimize(model, body = {}) {
  const caseName = body.case || Object.keys(model.cases || {})[0];
  const target = isFinite(body.target_ratio) ? body.target_ratio : 0.95;
  const { suggestions, sections } = _optimizeSuggestions(model, caseName, target);
  if (!body.apply) return { suggestions, case: caseName, target_ratio: target };
  // apply:true — clone the model, add the scaled sections, reassign members
  const updated = JSON.parse(JSON.stringify(model));
  updated.sections = updated.sections || {};
  Object.assign(updated.sections, sections);
  const suggMap = new Map(suggestions.map(s => [s.uid, s]));
  for (const mm of updated.members) {
    const s = suggMap.get(mm.uid);
    if (s && s.status === "ok" && s.suggested_section !== mm.section)
      mm.section = s.suggested_section;
  }
  return updated;
}

/** POST /api/design/concrete — flexure/axial screening from a rebar layout.
    Accepts {case} or {combos:true|[names]} (v0.9 envelope over combos) and the
    v0.16 {live_reduction, live_case} flags. Column checks gain the v0.16
    biaxial fields: biaxial, ratio_biaxial, method "bresler"|"contour"|"uniaxial". */
export function mockDesignConcrete(model, body = {}) {
  const fc = isFinite(body.fc) && body.fc > 0 ? body.fc : 30000;   // kPa (≈30 MPa)
  const code = body.code === "EC2" ? "EC2" : "ACI318";             // v0.23
  const rebar = body.rebar || {};
  const llr = body.live_reduction ? mockLiveReduction(model) : null;
  const res = _designEnvelope(model, body,
    caseName => _concreteChecks(model, caseName, fc, rebar, llr));
  if (body.live_reduction) res.live_reduction = true;
  res.code = code;
  if (code === "EC2") _applyCodeScale(res, 1.05);
  return res;
}

function _concreteChecks(model, caseName, fc, rebar, llr) {
  let biaxCount = 0;                              // alternate bresler / contour
  const { forces } = _caseForcesFor(model, caseName);
  const checks = [];
  for (const mm of model.members) {
    if (mm.kind === "brace") continue;
    const f = forces[mm.uid];
    const sec = (model.sections || {})[mm.section];
    const rb = rebar[mm.uid];
    if (!f || !sec || !rb) {
      checks.push({
        uid: mm.uid, section: mm.section, kind: mm.kind, Pu: 0, Mu33: 0, Mu22: 0,
        phiPn: 0, phiMn33: 0, phiMn22: 0, ratio: 0, equation: "—",
        biaxial: false, ratio_biaxial: null, method: "uniaxial",
        status: "N/A", notes: rb ? "no forces/section" : "no rebar assigned",
        preliminary: true,
      });
      continue;
    }
    const b = sec.b || 0.3, h = sec.h || 0.5;
    const fy = isFinite(rb.fy) && rb.fy > 0 ? rb.fy : 420000;       // kPa
    const cover = (isFinite(rb.cover) ? rb.cover : 40) / 1000;      // mm→m
    const dia = (isFinite(rb.bar_dia) ? rb.bar_dia : 20) / 1000;    // mm→m
    const nBot = isFinite(rb.n_bot) ? rb.n_bot : 3;
    const nTop = isFinite(rb.n_top) ? rb.n_top : 2;
    const Abar = Math.PI / 4 * dia * dia;
    const d = h - cover - dia / 2;
    const AsBot = nBot * Abar, AsTop = nTop * Abar;
    // simple singly-reinforced Mn (whitney block), both faces available
    const As = Math.max(AsBot, AsTop);
    const a = As * fy / (0.85 * fc * b);
    const phiMn33 = +(0.9 * As * fy * (d - a / 2)).toFixed(1);
    const phiMn22 = +(phiMn33 * (b / h) * 0.6).toFixed(1);
    const Ag = b * h;
    const phiPn = +(0.65 * 0.80 * (0.85 * fc * (Ag - (AsBot + AsTop)) +
      fy * (AsBot + AsTop))).toFixed(1);
    let Pu = Math.max(Math.abs(f[0]), Math.abs(f[6]));
    // v0.16: live-load reduction — reduced axial demand on columns
    if (llr && mm.kind === "column" && llr[mm.uid]) Pu *= llr[mm.uid].R;
    const Mu33 = Math.max(Math.abs(f[5]), Math.abs(f[11]));
    const Mu22 = Math.max(Math.abs(f[4]), Math.abs(f[10]));
    const rP = Pu / (phiPn || 1), r33 = Mu33 / (phiMn33 || 1), r22 = Mu22 / (phiMn22 || 1);
    let ratio, equation;
    // v0.16: biaxial column interaction — Bresler reciprocal-load / PCA load
    // contour when the minor-axis demand is meaningful; else uniaxial.
    let biaxial = false, method = "uniaxial", ratio_biaxial = null;
    if (mm.kind === "column") {
      ratio = rP + r33 + r22;
      equation = "P-M (screen)";
      if (r22 > 0.02) {
        biaxial = true;
        method = (biaxCount++ % 2 === 0) ? "bresler" : "contour";
        const alpha = method === "contour" ? 1.5 : 1.15;
        ratio_biaxial = +(rP +
          Math.pow(Math.pow(r33, alpha) + Math.pow(r22, alpha), 1 / alpha)).toFixed(3);
        equation = method === "contour" ? "P-M contour" : "P-M Bresler";
      }
    } else {
      ratio = r33;
      equation = "Mn (flexure)";
    }
    ratio = +ratio.toFixed(3);
    const governing = biaxial ? ratio_biaxial : ratio;
    checks.push({
      uid: mm.uid, section: mm.section, kind: mm.kind,
      Pu: +Pu.toFixed(1), Mu33: +Mu33.toFixed(1), Mu22: +Mu22.toFixed(1),
      phiPn, phiMn33, phiMn22, ratio, equation,
      biaxial, ratio_biaxial, method,
      status: governing <= 1.0 ? "OK" : "NG",
      notes: `${nTop}T/${nBot}B ⌀${rb.bar_dia || 20}` + (governing > 1.0 ? " · over" : ""),
      preliminary: true,
    });
  }
  return checks;
}

/* ---- mock model importers: parse a tiny built-in fixture → small model. */

/** Build a small quick-model and tag warnings, echoing importer behavior. */
function _importModel(name, opts = {}) {
  const m = mockModel({
    name, stories: opts.stories || 2, bays_x: opts.bays_x || 2,
    bays_y: opts.bays_y || 1, bay_width_x: 6, bay_width_y: 5,
  });
  // importers deliver bare geometry — strip demo analysis extras
  m.rs_cases = {}; m.th_cases = {}; m.pushover_cases = {};
  m.staged_cases = {}; m.shells = [];
  for (const p of Object.values(m.patterns)) p.area_loads = [];
  return m;
}

export function mockImport(fmt, body = {}) {
  const text = String(body.text || "");
  const warnings = [];
  if (!text.trim()) warnings.push("empty file — nothing to import");
  if (fmt === "dxf") {
    const stories = Array.isArray(body.stories) && body.stories.length
      ? body.stories : [3.2, 3.2];
    const nLines = text.split(/\r?\n/).length;
    const model = _importModel("Imported DXF", { stories: stories.length });
    // apply requested story heights
    let elev = 0;
    model.stories = stories.map((h, i) => {
      elev += (isFinite(h) && h > 0) ? h : 3.2;
      return { name: `Story${i + 1}`, height: (isFinite(h) && h > 0) ? h : 3.2, elevation: elev };
    });
    if (body.column_section) warnings.push(`columns mapped to section “${body.column_section}”`);
    if (body.beam_section) warnings.push(`beams mapped to section “${body.beam_section}”`);
    warnings.push(`${nLines} DXF entities scanned; 2 unsupported entity types skipped`);
    warnings.push(`unit scale ${body.unit_scale === "mm" ? "1 mm → 0.001 m" : "1:1 (m)"}`);
    return { model, warnings };
  }
  if (fmt === "e2k") {
    const model = _importModel("Imported E2K", { stories: 3, bays_x: 3, bays_y: 2 });
    warnings.push("3 stories, section list mapped to defaults");
    warnings.push("SPRINGPROP lines ignored (unsupported)");
    return { model, warnings };
  }
  if (fmt === "ifc") {
    const model = _importModel("Imported IFC", { stories: 2, bays_x: 2, bays_y: 2 });
    warnings.push("IfcBeam / IfcColumn extracted; IfcSlab meshing skipped");
    warnings.push("2 IfcWallStandardCase converted to shell placeholders");
    return { model, warnings };
  }
  throw new Error(`unknown import format “${fmt}”`);
}

/* ================================================================
   v0.18 — mock POST /api/design/wall · /api/design/punching ·
   /api/results/virtual-work
   ================================================================ */

/** POST /api/design/wall — RC wall-pier PMM + in-plane shear screening.
    body {combos?: [names], rho_v?, rho_h? (reinforcement RATIOS, not %),
    fy?, fc_prime? (kPa)} →
    {piers: [{pier, story, P, V, M, ratio_pmm, ratio_shear, phiVn,
      boundary_required, sigma_max, status, combo}], params: {…echo}}.
    Deterministic demo intent: two piers × up to 3 stories — the second
    (squat) pier's base lift lands NG on PMM and the first pier's base
    needs boundary elements (σmax > 0.2·f'c per the ACI 318 §18.10.6
    stress screen). Everything scales with the ρ / fy / f'c inputs. */
export function mockDesignWall(model, body = {}) {
  const rnd = mulberry32(4242);
  const jit = a => 1 + (rnd() - 0.5) * 2 * a;
  const rho_v = body.rho_v > 0 ? body.rho_v : 0.0025;
  const rho_h = body.rho_h > 0 ? body.rho_h : 0.0025;
  const fy = body.fy > 0 ? body.fy : 420000;              // kPa
  const fc = body.fc_prime > 0 ? body.fc_prime : 30000;   // kPa
  const all = Object.keys(model.combos || {});
  const combos = Array.isArray(body.combos) && body.combos.length
    ? body.combos.filter(n => all.includes(n)) : all;
  const eqCombo = combos.find(n => /E[XY]|EQ/i.test(n)) || combos[0] ||
    Object.keys(model.cases || {})[0] || "EQX";
  const grvCombo = combos.find(n => !/E[XY]|EQ|W\b/i.test(n)) || eqCombo;

  const wallShells = (model.shells || []).filter(s => s.kind === "wall");
  const pierNames = [...new Set(wallShells.filter(s => s.pier).map(s => s.pier))];
  if (!pierNames.length) pierNames.push("P1");
  if (pierNames.length < 2) pierNames.push(pierNames.includes("P2") ? "P2b" : "P2");
  pierNames.sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));

  const stories = (model.stories || []).slice(0, 3);      // bottom → up
  const n = stories.length || 1;
  const Htot = stories.length ? stories[stories.length - 1].elevation : 9.6;

  const geomOf = (pier, pi) => {
    const sh = wallShells.find(s => s.pier === pier);
    const t = sh ? (((model.shell_sections || {})[sh.section] || {}).thickness || 0.2) : 0.2;
    let Lw = sh ? (Math.hypot(sh.corners[1][0] - sh.corners[0][0],
      sh.corners[1][1] - sh.corners[0][1]) || 6) : 6;
    if (pi > 0) Lw = Math.max(Lw * 0.45, 2.4);            // fabricated squat pier
    return { t, Lw };
  };

  const piers = [];
  pierNames.slice(0, 2).forEach((pier, pi) => {
    const { t, Lw } = geomOf(pier, pi);
    const Ag = t * Lw, S = t * Lw * Lw / 6;               // m², m³
    const As = rho_v * Ag;
    // capacities (screening-level): φPn axial, φMn in-plane with axial assist,
    // φVn = φ(0.17√f'c + ρh·fy)·0.8Lw·t  (kPa·m² → kN)
    const phiPn = 0.65 * (0.85 * fc * (Ag - As) + fy * As);
    const phiVn = +(0.75 * (0.17 * Math.sqrt(fc / 1000) * 1000 + rho_h * fy)
      * 0.8 * Lw * t).toFixed(1);
    stories.forEach((st, si) => {
      const hFac = (n - si) / n;                          // 1 at base → 1/n at top
      const P = 2200 * Ag * hFac * jit(0.04);             // kN (compression +)
      const V = (pi === 0 ? 520 : 515) * Ag * hFac * jit(0.05);
      let M = V * 0.62 * Htot * hFac * jit(0.04);         // kN·m
      if (pi === 0 && si === 0) M *= 1.35;                // P1 base: boundary screen trips
      const phiMn = 0.9 * (fy * (As / 2) + 0.5 * P) * 0.8 * Lw;
      const ratio_pmm = +(P / phiPn + M / (phiMn || 1)).toFixed(3);
      const ratio_shear = +(V / (phiVn || 1)).toFixed(3);
      const sigma_max = +(P / Ag + M / (S || 1)).toFixed(0);          // kPa
      const boundary_required = sigma_max > 0.2 * fc;
      piers.push({
        pier, story: st.name,
        P: +P.toFixed(1), V: +V.toFixed(1), M: +M.toFixed(1),
        ratio_pmm, ratio_shear, phiVn,
        boundary_required, sigma_max,
        status: (ratio_pmm > 1 || ratio_shear > 1) ? "NG" : "OK",
        combo: si === n - 1 ? grvCombo : eqCombo,
      });
    });
  });
  return { piers, params: { combos, rho_v, rho_h, fy, fc_prime: fc } };
}

/** POST /api/design/punching — two-way (punching) shear at slab–column
    connections. body {case?, fc_prime? (kPa), cover? (m)} →
    {columns: [{uid, story, Vu, vu, phi_vc, b0, d, ratio, status, case}]}.
    Four columns of the slab story; the interior one lands at D/C ≈ 1.15.
    φvc = 0.75·0.33·√f'c (MPa→kPa); b0 = 4(c+d) at d/2 from the face. */
export function mockDesignPunching(model, body = {}) {
  const rnd = mulberry32(1717);
  const jit = a => 1 + (rnd() - 0.5) * 2 * a;
  const fc = body.fc_prime > 0 ? body.fc_prime : 30000;   // kPa
  const coverMm = body.cover > 0 ? body.cover * 1000 : 40;   // m → mm
  const caseName = body.case || Object.keys(model.combos || {})[0] ||
    Object.keys(model.cases || {})[0] || "DEAD";

  const slab = (model.shells || []).find(s => s.kind === "slab");
  const th = slab ? (((model.shell_sections || {})[slab.section] || {}).thickness || 0.15) : 0.15;
  const story = (slab && slab.story) || ((model.stories || [])[0] || {}).name;
  const cols = (model.members || []).filter(m => m.kind === "column" && m.story === story);
  if (!cols.length) return { columns: [] };
  // interior columns first: sort by distance to the plan centroid
  const cx = cols.reduce((a, m) => a + m.pi[0], 0) / cols.length;
  const cy = cols.reduce((a, m) => a + m.pi[1], 0) / cols.length;
  const picked = [...cols].sort((a, b) =>
    Math.hypot(a.pi[0] - cx, a.pi[1] - cy) - Math.hypot(b.pi[0] - cx, b.pi[1] - cy))
    .slice(0, 4);

  const d = Math.max(th - coverMm / 1000 - 0.008, 0.06);  // avg effective depth, m
  const phi_vc = +(0.75 * 0.33 * Math.sqrt(fc / 1000) * 1000).toFixed(1);   // kPa
  // interior column deliberately over (≈1.15); the rest comfortably under
  const targets = [1.15, 0.86, 0.71, 0.58];
  const columns = picked.map((mm, i) => {
    const sec = (model.sections || {})[mm.section] || {};
    const c = sec.b || 0.5;                               // square column, m
    const b0 = +(4 * (c + d)).toFixed(3);                 // m
    const ratio = +(targets[i % targets.length] * jit(0.03)).toFixed(3);
    const vu = +(ratio * phi_vc).toFixed(1);              // kPa
    const Vu = +(vu * b0 * d).toFixed(1);                 // kN
    return {
      uid: mm.uid, story: mm.story, Vu, vu, phi_vc, b0, d: +d.toFixed(3),
      ratio, status: ratio > 1 ? "NG" : "OK", case: caseName,
    };
  });
  return { columns };
}

/** POST /api/design/seismic341 — AISC 341 seismic joint screening (mock).
    body {combo?, columns?: "auto"|[uids]} → {joints: [{point: [x,y,z],
    scwb_ratio, pz_demand, pz_capacity, pz_ratio, status}], combo}.
    Strong-column/weak-beam ΣM*pc/ΣM*pb ≥ 1.0 (E3-1) and panel-zone shear
    demand vs φRv. Deterministic demo intent: interior joints of the lowest
    two stories; one joint fails SCWB (< 1.0) and one runs the panel zone
    over 1.0 so both failure modes render. */
export function mockDesignSeismic341(model, body = {}) {
  const rnd = mulberry32(341);
  const jit = a => 1 + (rnd() - 0.5) * 2 * a;
  const all = Object.keys(model.combos || {});
  const combo = (body.combo && (all.includes(body.combo) ||
      (model.cases || {})[body.combo])) ? body.combo
    : all.find(n => /E[XY]|EQ/i.test(n)) || all[0] ||
      Object.keys(model.cases || {})[0] || "EQX";

  const stories = (model.stories || []).slice(0, 2);      // lowest two
  const names = new Set(stories.map(s => s.name));
  let cols = (model.members || [])
    .filter(m => m.kind === "column" && names.has(m.story));
  if (Array.isArray(body.columns) && body.columns.length) {
    const want = new Set(body.columns);
    cols = cols.filter(m => want.has(m.uid));
  }
  // interior joints first (columns nearest the plan centroid), up to 8
  const cx = cols.length ? cols.reduce((a, m) => a + m.pi[0], 0) / cols.length : 0;
  const cy = cols.length ? cols.reduce((a, m) => a + m.pi[1], 0) / cols.length : 0;
  const picked = [...cols].sort((a, b) =>
    Math.hypot(a.pi[0] - cx, a.pi[1] - cy) - Math.hypot(b.pi[0] - cx, b.pi[1] - cy))
    .slice(0, 8);

  // SCWB / panel-zone intents — one SCWB fail (0.87), one PZ fail (1.12)
  const scwbT = [0.87, 1.12, 1.24, 1.38, 1.05, 1.51, 1.19, 1.62];
  const pzT = [0.94, 1.12, 0.66, 0.58, 0.81, 0.47, 0.73, 0.52];
  const joints = picked.map((mm, i) => {
    const zTop = Math.max(mm.pi[2], mm.pj[2]);
    const scwb_ratio = +(scwbT[i % scwbT.length] * jit(0.03)).toFixed(3);
    const pz_capacity = +(1450 * jit(0.06)).toFixed(1);   // kN — φ·0.6·Fy·dc·tw-ish
    const pz_ratio = +(pzT[i % pzT.length] * jit(0.03)).toFixed(3);
    const pz_demand = +(pz_ratio * pz_capacity).toFixed(1);
    return {
      point: [mm.pi[0], mm.pi[1], +zTop.toFixed(3)],
      scwb_ratio, pz_demand, pz_capacity, pz_ratio,
      status: (scwb_ratio < 1.0 || pz_ratio > 1.0) ? "NG" : "OK",
    };
  });
  return { joints, combo };
}

/** POST /api/results/virtual-work — member contributions to the roof drift
    (virtual work of a unit roof load). body {case, direction "X"|"Y"} →
    {contributions: {uid: m}, total, roof_disp} with total = Σ contributions
    = roof_disp exactly. Twelve members with descending shares — lower-story
    columns dominate, braces next, drift-direction beams after that. */
export function mockVirtualWork(model, body = {}) {
  const rnd = mulberry32(31415);
  const jit = a => 1 + (rnd() - 0.5) * 2 * a;
  const dirY = body.direction === "Y";
  const r = mockResults(model);
  const caseName = body.case &&
    ((r.cases && r.cases[body.case]) || (r.combos && r.combos[body.case]))
    ? body.case : (dirY ? "EQY" : "EQX");
  const cd = (r.cases && r.cases[caseName]) || (r.combos && r.combos[caseName]) || {};
  const top = r.story_order[r.story_order.length - 1];
  const st = (cd.story && cd.story[top]) || {};
  let roof = Math.abs(dirY ? st.uy || 0 : st.ux || 0);
  if (!(roof > 1e-9)) roof = Math.abs(st.ux || 0) + Math.abs(st.uy || 0) || 0.02;

  const storyIdx = {};
  (model.stories || []).forEach((s, i) => { storyIdx[s.name] = i; });
  const weighted = (model.members || []).map(mm => {
    const si = storyIdx[mm.story] ?? 0;                    // 0 = bottom
    const hFac = Math.pow(0.55, si);
    const kFac = mm.kind === "column" ? 1 : mm.kind === "brace" ? 0.8 : 0.28;
    const dx = Math.abs(mm.pj[0] - mm.pi[0]), dy = Math.abs(mm.pj[1] - mm.pi[1]);
    const dirFac = mm.kind === "beam"
      ? (dirY ? (dy > dx ? 1 : 0.3) : (dx > dy ? 1 : 0.3)) : 1;
    return [mm.uid, hFac * kFac * dirFac * jit(0.25)];
  }).sort((a, b) => b[1] - a[1]).slice(0, 12);

  const wSum = weighted.reduce((a, [, w]) => a + w, 0) || 1;
  const contributions = {};
  let acc = 0;
  weighted.forEach(([uid, w], i) => {
    let v = i === weighted.length - 1
      ? roof - acc                                         // exact closure
      : +(w / wSum * roof).toFixed(7);
    contributions[uid] = +v.toFixed(7);
    acc = +(acc + contributions[uid]).toFixed(7);
  });
  const total = +Object.values(contributions).reduce((a, b) => a + b, 0).toFixed(7);
  return { contributions, total, roof_disp: total };
}

/* ==========================================================================
   v0.19 — pattern live loading, auto sequence, performance point
   ========================================================================== */

/** POST /api/loads/pattern-live — local mirror of the backend split:
    beams grouped by line + chained spans, odd/even member loads, PLL_*
    factored cases (1.2D + 1.6L) and the PATTERN-LL envelope combo. */
export function mockPatternLive(model, livePattern) {
  const pat = (model.patterns || {})[livePattern];
  if (!pat || pat.kind !== "live") throw new Error(`no live pattern ${livePattern}`);
  // span indices along collinear chained runs (matches core/patterning.py)
  const beams = model.members.filter(mm => mm.kind === "beam");
  const lines = new Map();
  for (const mm of beams) {
    const dx = mm.pj.map((v, i) => v - mm.pi[i]);
    const L = Math.hypot(...dx);
    let u = dx.map(v => v / L);
    if (u[0] < 0 || (Math.abs(u[0]) < 1e-9 && u[1] < 0)) u = u.map(v => -v);
    const proj = mm.pi.reduce((a, p, i) => a + p * u[i], 0);
    const off = mm.pi.map((p, i) => +(p - proj * u[i]).toFixed(6)).join(",");
    const key = u.map(v => +v.toFixed(6)).join(",") + "|" + off;
    if (!lines.has(key)) lines.set(key, []);
    lines.get(key).push([proj, mm, L]);
  }
  const span = {};
  for (const entries of lines.values()) {
    entries.sort((a, b) => a[0] - b[0]);
    let idx = 0, prevEnd = null;
    for (const [proj, mm, L] of entries) {
      if (prevEnd !== null && Math.abs(proj - prevEnd) > 1e-6) idx = 0;
      span[mm.uid] = idx++;
      prevEnd = proj + L;
    }
  }
  const odd = { name: `${livePattern}__ODD`, kind: "live", member_loads: [],
    member_udls: [], nodal_loads: [], story_forces: [], area_loads: [], thermal_loads: [] };
  const even = { ...JSON.parse(JSON.stringify(odd)), name: `${livePattern}__EVEN` };
  for (const ml of (pat.member_loads || [])) {
    const s = span[ml.member_uid];
    if (s === undefined) continue;
    (s % 2 === 0 ? odd : even).member_loads.push({ ...ml });
  }
  model.patterns[odd.name] = odd;
  model.patterns[even.name] = even;
  // factored cases + envelope combo (needs a dead-classified case)
  const deadCase = Object.values(model.cases || {}).find(cc => {
    const ps = Object.keys(cc.patterns || {});
    return ps.length === 1 && (model.patterns[ps[0]] || {}).kind === "dead";
  });
  if (deadCase) {
    for (const [cname, lp] of [["PLL_ALL", livePattern],
        ["PLL_ODD", odd.name], ["PLL_EVEN", even.name]]) {
      const pats = {};
      for (const [p, f] of Object.entries(deadCase.patterns)) pats[p] = 1.2 * f;
      pats[lp] = (pats[lp] || 0) + 1.6;
      model.cases[cname] = { name: cname, patterns: pats, pdelta: false };
    }
    model.combos["PATTERN-LL"] = { name: "PATTERN-LL", combo_type: "envelope",
      cases: { PLL_ALL: 1.0, PLL_ODD: 1.0, PLL_EVEN: 1.0 } };
  }
  return model;
}

/** POST /api/case/auto-sequence — staged per-story case, local mirror. */
export function mockAutoSequence(model, name = "SEQ", pattern) {
  const dead = pattern || (Object.values(model.patterns || {})
    .find(p => p.kind === "dead") || {}).name;
  if (!dead) throw new Error("no dead pattern");
  model.staged_cases = model.staged_cases || {};
  model.staged_cases[name] = { name, pattern: dead, stages: "per_story",
                               include_live: {} };
  return model;
}

/** POST /api/results/performance-point — ASCE 41-17 §7.4.3 on the MOCK
    capacity curve: real bilinearization (equal-area + 0.6Vy secant, exact
    on piecewise-linear curves) and the real coefficient formulas, with
    Ti taken from the mock modal results' dominant mode. */
export function mockPerformancePoint(model, pd, body) {
  // drop any leading zero point before re-anchoring at the origin (the
  // mock curve includes u = 0; the live engine curve starts at step 1)
  let rd = pd.roof_disp || [], bs = pd.base_shear || [];
  while (rd.length && rd[0] <= 0) { rd = rd.slice(1); bs = bs.slice(1); }
  const disp = [0, ...rd], shear = [0, ...bs];
  if (disp.length < 3) throw new Error("capacity curve too short");
  let ip = 0;
  for (let i = 0; i < shear.length; i++) if (shear[i] >= shear[ip]) ip = i;
  const d = disp.slice(0, ip + 1), v = shear.slice(0, ip + 1);
  const du = d[d.length - 1], Vu = v[v.length - 1];
  const Ki = v[1] / d[1];
  const area = d.slice(1).reduce((a, x, i) => a + (v[i] + v[i + 1]) * (x - d[i]) / 2, 0);
  const interpD = vq => {
    for (let i = 0; i + 1 < d.length; i++)
      if ((v[i] - vq) * (v[i + 1] - vq) <= 0 && v[i + 1] !== v[i])
        return d[i] + (d[i + 1] - d[i]) * (vq - v[i]) / (v[i + 1] - v[i]);
    return du * vq / Vu;
  };
  let Vy = Vu, Ke = Ki;
  for (let it = 0; it < 100; it++) {
    Ke = 0.6 * Vy / interpD(0.6 * Vy);
    const den = du / 2 - Vu / (2 * Ke);
    const next = (area - du * Vu / 2) / den;
    if (Math.abs(next - Vy) < 1e-9 * Math.max(1, Vy)) { Vy = next; break; }
    Vy = next;
  }
  Ke = 0.6 * Vy / interpD(0.6 * Vy);
  const SDS = body.SDS || 1.0, SD1 = body.SD1 || 0.6;
  const site = (body.site_class || "D").toUpperCase();
  const dir = ((model.pushover_cases || {})[body.case] || {}).direction || "X";
  const key = dir === "X" ? "ux" : "uy";
  const modes = mockResults(model).modal?.participation || [];
  const Ti = modes.length
    ? modes.reduce((a, b) => ((b[key] || 0) > (a[key] || 0) ? b : a)).T : 0.5;
  const Te = Ti * Math.sqrt(Ki / Ke);
  const Ts = SD1 / SDS, T0 = 0.2 * Ts;
  const Sa = Te < T0 ? SDS * (0.4 + 0.6 * Te / T0) : Te <= Ts ? SDS : SD1 / Te;
  const stories = (model.stories || []).length || 1;
  const C0T = [[1, 1.0], [2, 1.2], [3, 1.3], [5, 1.4], [10, 1.5]];
  let C0 = 1.5;
  for (let i = 0; i + 1 < C0T.length; i++) {
    const [n1, c1] = C0T[i], [n2, c2] = C0T[i + 1];
    if (stories <= n2) { C0 = stories <= n1 ? c1 : c1 + (c2 - c1) * (stories - n1) / (n2 - n1); break; }
  }
  const W = (model.stories || []).length * 500 * 9.81;   // mock seismic weight
  const mu = Sa / (Vy / W);
  const aF = { A: 130, B: 130, C: 90, D: 60, E: 60, F: 60 }[site] || 60;
  const C1 = Te >= 1 ? 1 : 1 + (mu - 1) / (aF * Math.max(Te, 0.2) ** 2);
  const C2 = Te > 0.7 ? 1 : 1 + ((mu - 1) / Te) ** 2 / 800;
  const delta_t = C0 * C1 * C2 * Sa * (Te / (2 * Math.PI)) ** 2 * 9.81;
  let step = 0, bd = Infinity;
  (pd.roof_disp || []).forEach((u, i) => {
    const e = Math.abs(u - delta_t);
    if (e < bd) { bd = e; step = i; }
  });
  const summary = { elastic: 0, IO: 0, LS: 0, CP: 0, collapse: 0 };
  for (const h of (pd.hinges || []))
    summary[h.state[Math.min(step, h.state.length - 1)] || "elastic"]++;
  return { Ki, Ke, Vy, dy: Vy / Ke, du, Vu, area, Te, Ti, Sa, mu, Cm: 1,
    C0, C1, C2, W, site_class: site, delta_t, case: body.case,
    direction: dir, step, roof_disp_at_step: (pd.roof_disp || [])[step],
    hinge_summary: summary };
}

/* ==========================================================================
   v0.20 — composite beam design, slab flexural design, floor vibration
   ========================================================================== */

/** POST /api/design/composite — composite steel beam screening (AISC I3).
    body {combos?: [names], fc_prime? (kPa), t_slab? (m), hr? (deck rib
    height, m), stud_d? (m), rib_spacing? (m), shored?: bool} →
    {beams: [{uid, story, applicable, reason?, beff, tc, phiMn_full,
      n_studs, sumQn, ratio_composite, phiMn_partial, Mu, ratio,
      precomp_ratio, I_equiv, defl_LL, defl_limit_ok, status}], params}.
    Deterministic demo intent: six beams — five on slab stories (one lands
    NG at D/C ≈ 1.08), one on a slab-less story reported n/a with reason
    "no slab above". Stud strength ΣQn (and with it the % composite)
    scales with the fc' / stud Ø / rib spacing inputs. */
export function mockDesignComposite(model, body = {}) {
  const rnd = mulberry32(2020);
  const jit = a => 1 + (rnd() - 0.5) * 2 * a;
  const fc = body.fc_prime > 0 ? body.fc_prime : 30000;          // kPa
  const t_slab = body.t_slab > 0 ? body.t_slab : 0.13;           // m
  const hr = body.hr >= 0 ? body.hr : 0.075;                     // m
  const stud_d = body.stud_d > 0 ? body.stud_d : 0.019;          // m
  const rib_s = body.rib_spacing > 0 ? body.rib_spacing : 0.3;   // m
  const shored = !!body.shored;
  const all = Object.keys(model.combos || {});
  const combos = Array.isArray(body.combos) && body.combos.length
    ? body.combos.filter(n => all.includes(n)) : all;

  const slabStories = new Set((model.shells || [])
    .filter(s => s.kind === "slab").map(s => s.story));
  const beams = (model.members || []).filter(m => m.kind === "beam");
  const withSlab = beams.filter(m => slabStories.has(m.story)).slice(0, 5);
  const noSlab = beams.find(m => !slabStories.has(m.story));
  const picked = [...withSlab, ...(noSlab ? [noSlab] : [])];

  const Fy = 345000, Fu = 450000;                                // kPa
  const Asa = Math.PI / 4 * stud_d * stud_d;                     // m²
  const Ec = 4700 * Math.sqrt(fc / 1000) * 1000;                 // kPa
  // one-stud strength — concrete leg scales with √fc', steel leg caps it
  const Qn = Math.min(0.5 * Asa * Math.sqrt(fc * Ec), 0.75 * Asa * Fu);  // kN
  const AsFy = 0.0076 * Fy;                                      // steel yield force, kN
  const targets = [0.62, 0.71, 1.08, 0.55, 0.83];                // D/C intents
  const E = model.E || 25_000_000;
  const out = picked.map((mm, i) => {
    if (!slabStories.has(mm.story))
      return { uid: mm.uid, story: mm.story, applicable: false,
        reason: "no slab above", status: "n/a" };
    const L = mm.length || 6;
    const beff = +Math.min(L / 4, 6).toFixed(3);                 // m
    const tc = +Math.max(t_slab - hr, 0.05).toFixed(3);          // above-rib slab
    const n_studs = Math.max(2, Math.floor(L / rib_s));
    const sumQn = +(n_studs * Qn).toFixed(1);
    const ratio_composite = +Math.min(1, sumQn / AsFy).toFixed(3);
    const w = (model.dead_udl ?? 25) * 1.2 + (model.live_udl ?? 10) * 1.6;
    const Mu = +(w * L * L / 8 * jit(0.06)).toFixed(1);
    const ratio = targets[i % targets.length];       // D/C intent — NG lands at 1.08
    const phiMn_partial = +(Mu / ratio).toFixed(1);
    const phiMn_full = +(phiMn_partial * (1.12 + 0.25 * (1 - ratio_composite))).toFixed(1);
    const precomp_ratio = +((shored ? 0.32 : 0.74) * jit(0.10)).toFixed(3);
    const I_equiv = +(1.8e-3 * (0.75 + 0.5 * ratio_composite) * jit(0.05)).toFixed(6);
    const defl_LL = +(5 * (model.live_udl ?? 10) * L ** 4 / (384 * E * I_equiv)).toFixed(5);
    const defl_limit_ok = defl_LL <= L / 360 + 1e-12;
    // v0.23 — recommended camber (m): unshored beams get ~80 % of the bare
    // steel wet-concrete deflection rounded to 5 mm; stiff spans (< 10 mm
    // estimate — every other beam in the demo intent) camber 0, so the dim
    // "—" rendering is exercised. Shored construction never cambers.
    const camT = [0.020, 0, 0.025, 0.015, 0];
    const camber = shored ? 0 : camT[i % camT.length];
    return { uid: mm.uid, story: mm.story, applicable: true,
      beff, tc, phiMn_full, n_studs, sumQn, ratio_composite,
      phiMn_partial, Mu, ratio, precomp_ratio, I_equiv, defl_LL, defl_limit_ok,
      camber,
      status: (ratio > 1 || precomp_ratio > 1 || !defl_limit_ok) ? "NG" : "OK" };
  });
  return { beams: out, params: { combos, fc_prime: fc, t_slab, hr,
    stud_d, rib_spacing: rib_s, shored } };
}

/** POST /api/design/slab — two-way slab flexural design (direct-design
    strip method). body {case?, fc_prime? (kPa), bar_d? (m), cover? (m)} →
    {regions: [{uid, directions: [{dir: "x"|"y", strips: [{strip:
    "column"|"middle", sections: [{x, Mu, As_req, As_min, spacing,
    status}]}]}]}], params}.
    Mu in kN·m/m (negative at supports), As in mm²/m, spacing in mm.
    One region × 2 directions × 2 strips × 3 sections; the x-direction
    column-strip support section is deliberately under-reinforced (NG). */
export function mockDesignSlab(model, body = {}) {
  const rnd = mulberry32(808);
  const jit = a => 1 + (rnd() - 0.5) * 2 * a;
  const fc = body.fc_prime > 0 ? body.fc_prime : 30000;          // kPa
  const bar_d = body.bar_d > 0 ? body.bar_d : 0.016;             // m
  const cover = body.cover > 0 ? body.cover : 0.025;             // m
  const caseName = body.case || Object.keys(model.combos || {})[0] ||
    Object.keys(model.cases || {})[0] || "DEAD";
  const slabs = (model.shells || []).filter(s => s.kind === "slab").slice(0, 1);
  const fy = 420000;                                             // kPa
  const barArea = Math.PI / 4 * (bar_d * 1000) ** 2;             // mm²
  // cheap fc' respect — a-depth shrinks a little as fc' grows
  const fcFac = 1 + 0.03 * (30000 / fc - 1);

  const regions = slabs.map(sh => {
    const c = sh.corners || [];
    const Lx = c.length >= 2 ? (Math.abs(c[1][0] - c[0][0]) || 6) : 6;
    const Ly = c.length >= 3 ? (Math.abs(c[2][1] - c[1][1]) || 6) : 6;
    const th = (((model.shell_sections || {})[sh.section] || {}).thickness) || 0.15;
    const d = Math.max(th - cover - bar_d / 2, 0.05);            // m
    const As_min = +(0.0018 * 1000 * th * 1000).toFixed(0);      // mm²/m
    const q = 1.2 * 5.6 + 1.6 * 3.0;                             // kPa demo panel load
    const mkSections = (Lspan, share, bump) => [0, 0.5, 1].map((t, si) => {
      const x = +(t * Lspan).toFixed(2);
      const Mo = q * Lspan * Lspan / 8;                          // kN·m/m
      let Mu = (si === 1 ? 0.35 : -0.65) * Mo * share * jit(0.05);
      if (bump && si === 0) Mu *= 3.1;                           // trip the NG screen
      Mu = +Mu.toFixed(2);
      let As_req = +(Math.abs(Mu) / (0.9 * fy * 0.9 * d) * 1e6 * fcFac).toFixed(0);
      const gov = Math.max(As_req, As_min);
      const spacing = +Math.min(barArea / gov * 1000, 3 * th * 1000, 450).toFixed(0);
      // mock ρmax screen — over ~1500 mm²/m the 150 slab is under-reinforced
      const status = As_req > 1500 ? "NG" : "OK";
      return { x, Mu, As_req, As_min, spacing, status };
    });
    return { uid: sh.uid, directions: ["x", "y"].map(dir => ({
      dir,
      strips: ["column", "middle"].map(strip => ({
        strip,
        sections: mkSections(dir === "x" ? Lx : Ly,
          strip === "column" ? 0.72 : 0.38,
          dir === "x" && strip === "column"),      // the deliberate NG
      })),
    })) };
  });
  return { regions, params: { case: caseName, fc_prime: fc, bar_d, cover } };
}

/** POST /api/results/vibration — floor vibration screening (AISC DG11
    walking excitation). body {live_factor?, beta?, ap_limit?} →
    {beams: [{uid, story, fn, delta_mid, W_eff, ap_over_g, limit,
    status}], params}. fn = 0.18·√(g/Δ); ap/g = P0·e^(−0.35fn)/(β·W).
    Six beams; one lands at ap/g ≈ 0.0062 (NG vs the 0.005 office limit).
    β and the live factor feed the formula directly. */
export function mockVibration(model, body = {}) {
  const lf = (isFinite(body.live_factor) && body.live_factor >= 0)
    ? body.live_factor : 0.5;
  const beta = body.beta > 0 ? body.beta : 0.03;
  const limit = body.ap_limit > 0 ? body.ap_limit : 0.005;
  const beams = (model.members || []).filter(m => m.kind === "beam").slice(0, 6);
  const Po = 0.29;                                               // kN walking force
  // per-beam midspan deflection (mm) + participating weight (kN) at lf = 0.5
  const specs = [{ d: 5.2, W: 260 }, { d: 8.35, W: 180 }, { d: 4.1, W: 320 },
    { d: 6.3, W: 240 }, { d: 3.6, W: 350 }, { d: 7.1, W: 210 }];
  const wFac = (25 + lf * 10) / 30;               // included live load adds mass
  const rows = beams.map((mm, i) => {
    const s = specs[i % specs.length];
    const delta_mid = +(s.d * wFac / 1000).toFixed(5);           // m
    const W_eff = +(s.W * wFac).toFixed(1);                      // kN
    const fn = +(0.18 * Math.sqrt(9.81 / delta_mid)).toFixed(2); // Hz
    const ap_over_g = +(Po * Math.exp(-0.35 * fn) / (beta * W_eff)).toFixed(5);
    return { uid: mm.uid, story: mm.story, fn, delta_mid, W_eff,
      ap_over_g, limit, status: ap_over_g > limit ? "NG" : "OK" };
  });
  return { beams: rows, params: { live_factor: lf, beta, ap_limit: limit } };
}

/* ================================================================
   v0.21 — SECTION DESIGNER (polygon + rebar fiber sections)
   ================================================================ */
const cloneModel = m => JSON.parse(JSON.stringify(m));

/** POST /api/sections/designer {action:"upsert", section} — store the
    designer section, refresh its companion FrameSection with shoelace
    properties (the live backend computes the true values, incl. torsion J;
    here J is the polar approximation) and echo the full model dict. */
export function mockDesignerUpsert(model, section) {
  const sec = JSON.parse(JSON.stringify(section));
  model.designer_sections = model.designer_sections || {};
  model.designer_sections[sec.name] = sec;
  const pr = designerProps(sec);
  const prev = model.sections[sec.name] || {};
  model.sections[sec.name] = {
    name: sec.name,
    material: (sec.polygons[0] && sec.polygons[0].material) || prev.material || "CONC",
    A: pr.A, I33: pr.I33, I22: pr.I22, J: pr.J,
    b: 0, h: 0, shape: "designer",
    mod_A: prev.mod_A ?? 1, mod_I33: prev.mod_I33 ?? 1,
    mod_I22: prev.mod_I22 ?? 1, mod_J: prev.mod_J ?? 1,
  };
  return cloneModel(model);
}

/** POST /api/sections/designer {action:"delete", section:{name}} — remove
    the designer section (and its companion FrameSection when unused) and
    echo the full model dict. */
export function mockDesignerDelete(model, name) {
  if (model.designer_sections) delete model.designer_sections[name];
  const used = (model.members || []).some(mm => mm.section === name);
  if (!used) delete model.sections[name];
  return cloneModel(model);
}

/** POST /api/sections/designer/pmm {name, axis?} — analytic rectangular
    interaction diagram: a convex curve through (0, φP0), (Mb, Pb) and
    (M0, 0) plus a linear tension tail to (0, −φTn). fc' 30 MPa / fy 420 MPa;
    unreinforced sections assume 1 % steel. Points are [[φMn kN·m, φPn kN]]
    ordered tension → compression. */
export function mockDesignerPmm(model, { name, axis = "33" } = {}) {
  const ds = (model.designer_sections || {})[name];
  if (!ds) throw new Error(`unknown designer section "${name}"`);
  const pr = designerProps(ds);
  let ymin = Infinity, ymax = -Infinity, zmin = Infinity, zmax = -Infinity;
  for (const p of ds.polygons) {
    if (p.hole) continue;
    for (const [y, z] of p.vertices) {
      ymin = Math.min(ymin, y); ymax = Math.max(ymax, y);
      zmin = Math.min(zmin, z); zmax = Math.max(zmax, z);
    }
  }
  const depth = Math.max(axis === "22" ? (ymax - ymin) : (zmax - zmin), 0.05);
  const Ast = (ds.rebar || []).reduce((s, b) => s + b.area, 0);
  const Ag = Math.max(pr.A - Ast, 1e-6);
  const fc = 30000, fy = 420000;                   // kPa (30 / 420 MPa)
  const As = Ast > 0 ? Ast : 0.01 * Ag;            // unreinforced → assume 1 %
  const P0 = 0.65 * (0.85 * fc * Ag + fy * As);    // φPn at zero moment (kN)
  const Pt = 0.9 * fy * As;                        // φTn — pure tension (kN)
  const M0 = 0.9 * (As / 2) * fy * 0.8 * depth;    // φMn at P = 0 (kN·m)
  const Pb = 0.38 * P0;                            // balanced point
  const Mb = 1.35 * M0;
  const points = [];
  const N = 12;
  for (let i = N; i >= 1; i--) {                   // tension tail: −φTn → 0⁻
    const u = i / N;
    points.push([+(M0 * (1 - u)).toFixed(2), +(-Pt * u).toFixed(1)]);
  }
  for (let i = 0; i <= N; i++) {                   // 0 → balanced (bulges out)
    const t = i / N;
    points.push([+(M0 + (Mb - M0) * Math.sin(t * Math.PI / 2)).toFixed(2),
                 +(Pb * t).toFixed(1)]);
  }
  for (let i = 1; i <= N; i++) {                   // balanced → φP0 cap
    const u = i / N;
    points.push([+(Mb * Math.cos(u * Math.PI / 2)).toFixed(2),
                 +(Pb + (P0 - Pb) * u).toFixed(1)]);
  }
  return {
    points,
    properties: { A: pr.A, I33: pr.I33, I22: pr.I22, J: pr.J,
      cy: pr.cy, cz: pr.cz },
  };
}
