/* ============================================================================
 * examples.js — Preset trusses
 *
 * Each builder returns a plain model object {nodes, members, supports, loads}
 * in BASE units (m, kN). Geometry is laid out on the grid using the current
 * unit spacing `u` (metres per grid square) so presets look identical whether
 * the user is in metric or imperial mode. Loads are given in the user's force
 * unit and converted to base kN.
 * ==========================================================================*/

(function (global) {
  'use strict';

  function build(units, fn) {
    const u = units.gridBase; // metres per grid square
    const E = units.defaultE;
    const A = units.defaultA;
    const P = (kipsOrKN) => units.forceToBase(kipsOrKN); // display force -> base kN
    return fn({ u, E, A, P });
  }

  // Parallel-chord truss with verticals + one diagonal per panel.
  // Determinate and stable: m + r = 2j.
  function parallelChord({ u, E, A, P }, panels, depth, span, loadEachKN, diagDir) {
    const p = span / panels; // panel length
    const h = depth;
    const nodes = [];
    const idxB = [], idxT = [];
    for (let k = 0; k <= panels; k++) { idxB.push(nodes.length); nodes.push({ x: k * p, y: 0 }); }
    for (let k = 0; k <= panels; k++) { idxT.push(nodes.length); nodes.push({ x: k * p, y: h }); }
    const members = [];
    const M = (i, j) => members.push({ i, j, E, A });
    for (let k = 0; k < panels; k++) M(idxB[k], idxB[k + 1]); // bottom chord
    for (let k = 0; k < panels; k++) M(idxT[k], idxT[k + 1]); // top chord
    for (let k = 0; k <= panels; k++) M(idxB[k], idxT[k]); // verticals
    for (let k = 0; k < panels; k++) {
      if (diagDir === 'up') M(idxB[k], idxT[k + 1]);
      else M(idxT[k], idxB[k + 1]);
    }
    const supports = [
      { node: idxB[0], dx: true, dy: true }, // pin
      { node: idxB[panels], dx: false, dy: true }, // roller
    ];
    const loads = [];
    for (let k = 1; k < panels; k++) loads.push({ node: idxB[k], fx: 0, fy: -P(loadEachKN) });
    void u;
    return { nodes, members, supports, loads };
  }

  const EXAMPLES = {
    triangle: {
      name: 'Starter triangle',
      blurb: 'The simplest stable truss — three pin-jointed bars. A great first ' +
        'experiment: one load, three members, fully determinate.',
      build: (units) => build(units, ({ u, E, A, P }) => ({
        nodes: [
          { x: 0, y: 0 }, { x: 4 * u, y: 0 }, { x: 2 * u, y: 3 * u },
        ],
        members: [
          { i: 0, j: 1, E, A }, { i: 1, j: 2, E, A }, { i: 2, j: 0, E, A },
        ],
        supports: [
          { node: 0, dx: true, dy: true },
          { node: 1, dx: false, dy: true },
        ],
        loads: [{ node: 2, fx: 0, fy: -P(20) }],
      })),
    },

    pratt: {
      name: 'Pratt truss (parallel chord)',
      blurb: 'A simply-supported parallel-chord truss. Watch the bottom chord ' +
        'go into tension and the top chord into compression, with the largest ' +
        'chord forces near midspan.',
      build: (units) => build(units, (ctx) =>
        parallelChord(ctx, 4, 3 * ctx.u, 12 * ctx.u, 20, 'down')),
    },

    warrenLoaded: {
      name: 'Warren truss',
      blurb: 'Equal-length diagonals zig-zag between the chords. Diagonals ' +
        'alternate tension/compression as they ferry shear to the supports.',
      build: (units) => build(units, ({ u, E, A, P }) => {
        // Triangular Warren: bottom nodes, top nodes at panel midpoints.
        const span = 12 * u, h = 3 * u, panels = 4, p = span / panels;
        const nodes = [];
        const B = [], T = [];
        for (let k = 0; k <= panels; k++) { B.push(nodes.length); nodes.push({ x: k * p, y: 0 }); }
        for (let k = 0; k < panels; k++) { T.push(nodes.length); nodes.push({ x: (k + 0.5) * p, y: h }); }
        const members = [];
        const M = (i, j) => members.push({ i, j, E, A });
        for (let k = 0; k < panels; k++) M(B[k], B[k + 1]); // bottom chord
        for (let k = 0; k < panels - 1; k++) M(T[k], T[k + 1]); // top chord
        for (let k = 0; k < panels; k++) { M(B[k], T[k]); M(T[k], B[k + 1]); } // diagonals
        const supports = [
          { node: B[0], dx: true, dy: true },
          { node: B[panels], dx: false, dy: true },
        ];
        const loads = T.map((t) => ({ node: t, fx: 0, fy: -P(15) }));
        return { nodes, members, supports, loads };
      }),
    },

    cantilever: {
      name: 'Cantilever truss',
      blurb: 'Fixed at the wall, loaded at the free tip. The members nearest ' +
        'the support carry the most force — and the tip deflection is ' +
        'dominated by those same members.',
      build: (units) => build(units, ({ u, E, A, P }) => {
        const panels = 3, p = 3 * u, h = 3 * u;
        const nodes = [];
        const B = [], T = [];
        for (let k = 0; k <= panels; k++) { B.push(nodes.length); nodes.push({ x: k * p, y: 0 }); }
        for (let k = 0; k <= panels; k++) { T.push(nodes.length); nodes.push({ x: k * p, y: h }); }
        const members = [];
        const M = (i, j) => members.push({ i, j, E, A });
        for (let k = 0; k < panels; k++) M(B[k], B[k + 1]);
        for (let k = 0; k < panels; k++) M(T[k], T[k + 1]);
        for (let k = 0; k <= panels; k++) M(B[k], T[k]);
        for (let k = 0; k < panels; k++) M(T[k], B[k + 1]); // diagonals
        const supports = [
          { node: B[0], dx: true, dy: true }, // pin at wall
          { node: T[0], dx: true, dy: true }, // pin at wall (top)
        ];
        const loads = [{ node: B[panels], fx: 0, fy: -P(15) }];
        return { nodes, members, supports, loads };
      }),
    },

    indeterminate: {
      name: 'X-braced panel (indeterminate)',
      blurb: 'Two diagonals cross in one panel, making it 1° statically ' +
        'indeterminate. Now stiffness matters: change a diagonal’s area and ' +
        'watch the force redistribute — the stiffer path attracts more load.',
      build: (units) => build(units, ({ u, E, A, P }) => {
        const w = 4 * u, h = 4 * u;
        const nodes = [
          { x: 0, y: 0 }, { x: w, y: 0 }, { x: w, y: h }, { x: 0, y: h },
        ];
        const members = [
          { i: 0, j: 1, E, A }, { i: 1, j: 2, E, A },
          { i: 2, j: 3, E, A }, { i: 3, j: 0, E, A },
          { i: 0, j: 2, E, A }, // diagonal 1
          { i: 1, j: 3, E, A }, // diagonal 2 (the redundant)
        ];
        const supports = [
          { node: 0, dx: true, dy: true },
          { node: 1, dx: false, dy: true },
        ];
        const loads = [{ node: 2, fx: P(20), fy: -P(10) }];
        return { nodes, members, supports, loads };
      }),
    },

    howe: {
      name: 'Howe roof truss',
      blurb: 'A pitched (gable) roof truss. Sloping top chords in compression, ' +
        'bottom tie in tension — the classic roof load path.',
      build: (units) => build(units, ({ u, E, A, P }) => {
        const span = 12 * u, rise = 3 * u;
        const xs = [0, 3 * u, 6 * u, 9 * u, 12 * u];
        const apexY = (x) => (x <= span / 2 ? (rise * x) / (span / 2) : (rise * (span - x)) / (span / 2));
        const nodes = [];
        // bottom chord
        const B = xs.map((x) => { const id = nodes.length; nodes.push({ x, y: 0 }); return id; });
        // top chord (apex line) at interior + apex
        const T = [];
        [3 * u, 6 * u, 9 * u].forEach((x) => { const id = nodes.length; nodes.push({ x, y: apexY(x) }); T.push(id); });
        const members = [];
        const M = (i, j) => members.push({ i, j, E, A });
        // bottom tie
        for (let k = 0; k < B.length - 1; k++) M(B[k], B[k + 1]);
        // top chords: B0 - T0 - T1 - T2 - B4
        M(B[0], T[0]); M(T[0], T[1]); M(T[1], T[2]); M(T[2], B[4]);
        // verticals/webs from top nodes to bottom nodes
        M(T[0], B[1]); M(T[1], B[2]); M(T[2], B[3]);
        // diagonals
        M(B[1], T[1]); M(B[3], T[1]);
        const supports = [
          { node: B[0], dx: true, dy: true },
          { node: B[4], dx: false, dy: true },
        ];
        const loads = T.map((t) => ({ node: t, fx: 0, fy: -P(12) }));
        return { nodes, members, supports, loads };
      }),
    },
  };

  global.TRUSS_EXAMPLES = EXAMPLES;
})(typeof window !== 'undefined' ? window : globalThis);
