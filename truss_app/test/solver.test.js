/* Node smoke tests for the truss solver (no browser, no deps).
 * Run: node truss_app/test/solver.test.js
 */
const fs = require('fs');
const path = require('path');

// Load the IIFE module into this (global) scope.
const src = fs.readFileSync(path.join(__dirname, '..', 'js', 'solver.js'), 'utf8');
// eslint-disable-next-line no-eval
eval(src);
const S = globalThis.TrussSolver;

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('  ok  ' + name); }
  else { fail++; console.log('  FAIL ' + name + (extra ? '  -> ' + extra : '')); }
}
function close(a, b, tol) { return Math.abs(a - b) <= (tol || 1e-6) * Math.max(1, Math.abs(b)); }

/* ---- 1. Single axial bar: u = P L /(EA), N = P ------------------------- */
(function () {
  const E = 2.0e8, A = 0.002, L = 3, P = 100;
  const m = {
    nodes: [{ x: 0, y: 0 }, { x: L, y: 0 }],
    members: [{ i: 0, j: 1, E, A }],
    supports: [{ node: 0, dx: true, dy: true }, { node: 1, dx: false, dy: true }],
    loads: [{ node: 1, fx: P, fy: 0 }],
  };
  const r = S.analyze(m);
  ok('single bar solves', r.ok);
  const uExpected = (P * L) / (E * A);
  ok('single bar displacement = PL/EA', close(r.displacements[1].ux, uExpected),
    `${r.displacements[1].ux} vs ${uExpected}`);
  ok('single bar force = P (tension)', close(r.members[0].N, P), `${r.members[0].N}`);
})();

/* ---- 2. Determinacy classification ------------------------------------ */
(function () {
  const c = S.classifyDeterminacy(3, 3, 3); // triangle, 3 reactions
  ok('triangle is determinate (m+r=2j)', c.verdict === 'determinate', JSON.stringify(c));
  const ind = S.classifyDeterminacy(4, 6, 3); // X-braced square: m+r=9 > 2j=8
  ok('extra diagonal -> indeterminate deg 1',
    ind.verdict === 'indeterminate' && ind.degree === 1, JSON.stringify(ind));
  const un = S.classifyDeterminacy(4, 2, 3);
  ok('too few members -> unstable', un.verdict === 'unstable', JSON.stringify(un));
})();

/* ---- 3. Virtual work total == stiffness displacement (self-consistency) */
(function () {
  const E = 2.0e8, A = 0.003;
  // Simple supported truss: 2 bottom + apex.
  const m = {
    nodes: [{ x: 0, y: 0 }, { x: 4, y: 0 }, { x: 2, y: 2.5 }, { x: 6, y: 0 }],
    members: [
      { i: 0, j: 1, E, A }, { i: 1, j: 3, E, A },
      { i: 0, j: 2, E, A }, { i: 2, j: 1, E, A },
      { i: 2, j: 3, E, A },
    ],
    supports: [{ node: 0, dx: true, dy: true }, { node: 3, dx: false, dy: true }],
    loads: [{ node: 2, fx: 0, fy: -50 }],
  };
  const r = S.analyze(m);
  ok('truss solves', r.ok);
  if (r.ok && r.virtualWork) {
    const node = r.virtualWork.targetNode;
    const dir = r.virtualWork.dir;
    const dispAlongDir = r.displacements[node].ux * dir.x + r.displacements[node].uy * dir.y;
    ok('virtual-work total == stiffness displacement',
      close(r.virtualWork.total, dispAlongDir, 1e-9),
      `${r.virtualWork.total} vs ${dispAlongDir}`);
    const sum = r.virtualWork.contributions.reduce((a, c) => a + c.contribution, 0);
    ok('sum of member contributions == total',
      close(sum, r.virtualWork.total, 1e-9), `${sum} vs ${r.virtualWork.total}`);
  }
})();

/* ---- 4. Determinate truss: forces independent of area scaling ---------- */
(function () {
  const mk = (A) => ({
    nodes: [{ x: 0, y: 0 }, { x: 4, y: 0 }, { x: 2, y: 3 }],
    members: [{ i: 0, j: 1, E: 2e8, A }, { i: 1, j: 2, E: 2e8, A }, { i: 2, j: 0, E: 2e8, A }],
    supports: [{ node: 0, dx: true, dy: true }, { node: 1, dx: false, dy: true }],
    loads: [{ node: 2, fx: 0, fy: -20 }],
  });
  const r1 = S.analyze(mk(0.002));
  const r2 = S.analyze(mk(0.004));
  let same = true;
  for (let k = 0; k < r1.members.length; k++) {
    if (!close(r1.members[k].N, r2.members[k].N, 1e-9)) same = false;
  }
  ok('determinate forces unchanged when area doubles', same);
  // ...but deflection should roughly halve.
  const d1 = Math.abs(r1.virtualWork.total), d2 = Math.abs(r2.virtualWork.total);
  ok('determinate deflection halves when area doubles', close(d1, 2 * d2, 1e-6),
    `${d1} vs ${2 * d2}`);
})();

/* ---- 5. Mechanism detection ------------------------------------------- */
(function () {
  const m = {
    nodes: [{ x: 0, y: 0 }, { x: 4, y: 0 }],
    members: [{ i: 0, j: 1, E: 2e8, A: 0.002 }],
    supports: [{ node: 0, dx: true, dy: true }], // node 1 totally free -> mechanism
    loads: [{ node: 1, fx: 0, fy: -10 }],
  };
  const r = S.analyze(m);
  ok('unconstrained mechanism flagged not ok', !r.ok);
})();

/* ---- 6. Closed form: symmetric two-bar truss ---------------------------
 * Apex at (0,h), pins at (-a,0) and (a,0), load P down at the apex.
 * Statics: N = -P/(2 sinθ) in each bar. Unit-load: δ_down = P L /(2 sin²θ EA). */
(function () {
  const E = 2e8, A = 0.0015, a = 3, h = 4, P = 60;
  const L = Math.hypot(a, h), s = h / L;
  const r = S.analyze({
    nodes: [{ x: -a, y: 0 }, { x: a, y: 0 }, { x: 0, y: h }],
    members: [{ i: 0, j: 2, E, A }, { i: 1, j: 2, E, A }],
    supports: [{ node: 0, dx: true, dy: true }, { node: 1, dx: true, dy: true }],
    loads: [{ node: 2, fx: 0, fy: -P }],
  }, { target: { node: 2, mode: 'vertical' } });
  ok('two-bar solves', r.ok);
  ok('two-bar member force = -P/(2 sinθ)', close(r.members[0].N, -P / (2 * s)) && close(r.members[1].N, -P / (2 * s)),
    `${r.members[0].N} vs ${-P / (2 * s)}`);
  const dExp = (P * L) / (2 * s * s * E * A);
  ok('two-bar vertical deflection = PL/(2 sin²θ EA)', close(r.virtualWork.total, dExp, 1e-9),
    `${r.virtualWork.total} vs ${dExp}`);
  ok('vertical mode: δ equals -uy (down = +)', close(r.virtualWork.total, -r.displacements[2].uy, 1e-9));
})();

/* ---- 7. Closed form: classic INDETERMINATE three-bar truss --------------
 * Central vertical bar (length L) + two bars at ±α, all EA, meeting at a
 * loaded joint. N1 = P/(1+2cos³α), N2 = P cos²α/(1+2cos³α), δ = N1 L/EA. */
function threeBar(Acentre) {
  const E = 2e8, A = 0.002, L = 3, alpha = Math.PI / 6, P = 100;
  const w = L * Math.tan(alpha);
  return {
    params: { E, A, L, alpha, P },
    r: S.analyze({
      nodes: [{ x: -w, y: L }, { x: 0, y: L }, { x: w, y: L }, { x: 0, y: 0 }],
      members: [{ i: 1, j: 3, E, A: Acentre || A }, { i: 0, j: 3, E, A }, { i: 2, j: 3, E, A }],
      supports: [0, 1, 2].map((n) => ({ node: n, dx: true, dy: true })),
      loads: [{ node: 3, fx: 0, fy: -P }],
    }, { target: { node: 3, mode: 'vertical' } }),
  };
}
(function () {
  const { params: { E, A, L, alpha, P }, r } = threeBar();
  const c3 = Math.cos(alpha) ** 3;
  const N1 = P / (1 + 2 * c3), N2 = (P * Math.cos(alpha) ** 2) / (1 + 2 * c3);
  ok('three-bar is indeterminate', r.determinacy.verdict === 'indeterminate', JSON.stringify(r.determinacy));
  ok('three-bar centre force = P/(1+2cos³α)', close(r.members[0].N, N1, 1e-9), `${r.members[0].N} vs ${N1}`);
  ok('three-bar side force = P cos²α/(1+2cos³α)', close(r.members[1].N, N2, 1e-9) && close(r.members[2].N, N2, 1e-9),
    `${r.members[1].N} vs ${N2}`);
  ok('three-bar deflection = N1 L/EA', close(r.virtualWork.total, (N1 * L) / (E * A), 1e-9),
    `${r.virtualWork.total} vs ${(N1 * L) / (E * A)}`);
  // Stiffer path attracts more force (indeterminate only).
  const stiff = threeBar(2 * A).r;
  ok('stiffening centre bar attracts more force to it', stiff.members[0].N > r.members[0].N + 1e-6,
    `${stiff.members[0].N} vs ${r.members[0].N}`);
})();

/* ---- 8. Equilibrium of reactions + Maxwell–Betti reciprocity ---------- */
(function () {
  const E = 2e8, A = 0.002;
  const geom = {
    nodes: [{ x: 0, y: 0 }, { x: 3, y: 0 }, { x: 6, y: 0 }, { x: 1.5, y: 2 }, { x: 4.5, y: 2 }],
    members: [[0, 1], [1, 2], [3, 4], [0, 3], [3, 1], [1, 4], [4, 2]].map(([i, j]) => ({ i, j, E, A })),
    supports: [{ node: 0, dx: true, dy: true }, { node: 2, dx: false, dy: true }],
  };
  const loads = [{ node: 3, fx: 7, fy: -20 }, { node: 4, fx: 0, fy: -35 }];
  const r = S.analyze({ ...geom, loads });
  let sx = 0, sy = 0, mz = 0;
  for (const l of loads) { const n = geom.nodes[l.node]; sx += l.fx; sy += l.fy; mz += n.x * l.fy - n.y * l.fx; }
  for (const re of r.reactions) {
    const n = geom.nodes[re.node]; const rx = re.rx || 0, ry = re.ry || 0;
    sx += rx; sy += ry; mz += n.x * ry - n.y * rx;
  }
  ok('reactions satisfy ΣFx = 0', Math.abs(sx) < 1e-6, `${sx}`);
  ok('reactions satisfy ΣFy = 0', Math.abs(sy) < 1e-6, `${sy}`);
  ok('reactions satisfy ΣM = 0', Math.abs(mz) < 1e-6, `${mz}`);
  // δ at joint 4 (vertical) from unit load at joint 3 == δ at 3 from unit load at 4.
  const d43 = S.analyze({ ...geom, loads: [{ node: 3, fx: 0, fy: -1 }] }).displacements[4].uy;
  const d34 = S.analyze({ ...geom, loads: [{ node: 4, fx: 0, fy: -1 }] }).displacements[3].uy;
  ok('Maxwell–Betti reciprocity δ43 = δ34', close(d43, d34, 1e-9), `${d43} vs ${d34}`);
})();

/* ---- 9. Robustness: bad inputs give clear messages, never NaN ----------- */
(function () {
  const E = 2e8, A = 0.002;
  // Slanted collinear bars meeting at a free joint: count says determinate,
  // geometry is a mechanism. (Round-off makes the pivot ~1e-11, not 0.)
  const col = S.analyze({
    nodes: [{ x: 0, y: 0 }, { x: 3, y: 4 }, { x: 6, y: 8 }],
    members: [{ i: 0, j: 1, E, A }, { i: 1, j: 2, E, A }],
    supports: [{ node: 0, dx: true, dy: true }, { node: 2, dx: true, dy: true }],
    loads: [{ node: 1, fx: 4, fy: -3 }],
  });
  ok('slanted collinear mechanism detected', !col.ok, col.ok ? `uy=${col.displacements[1].uy}` : '');
  ok('collinear: count said determinate', col.determinacy.verdict === 'determinate');
  ok('collinear: arrangement hint given', col.messages.some((m) => /arrangement/.test(m)));

  const zero = S.analyze({
    nodes: [{ x: 0, y: 0 }, { x: 0, y: 0 }, { x: 2, y: 2 }],
    members: [{ i: 0, j: 1, E, A }, { i: 1, j: 2, E, A }, { i: 0, j: 2, E, A }],
    supports: [{ node: 0, dx: true, dy: true }], loads: [],
  });
  ok('zero-length member reported', !zero.ok && /zero length/.test(zero.messages.join(' ')));

  const bad = S.analyze({
    nodes: [{ x: 0, y: 0 }, { x: 2, y: 0 }],
    members: [{ i: 0, j: 5, E, A }], supports: [], loads: [],
  });
  ok('bad joint reference reported', !bad.ok && /does not exist/.test(bad.messages.join(' ')));

  const loose = S.analyze({
    nodes: [{ x: 0, y: 0 }, { x: 4, y: 0 }, { x: 2, y: 3 }, { x: 9, y: 9 }],
    members: [{ i: 0, j: 1, E, A }, { i: 1, j: 2, E, A }, { i: 2, j: 0, E, A }],
    supports: [{ node: 0, dx: true, dy: true }, { node: 1, dx: false, dy: true }],
    loads: [{ node: 2, fx: 0, fy: -10 }],
  });
  ok('unconnected joint named in hint', !loose.ok && /Joint 4 is not connected/.test(loose.messages.join(' ')),
    loose.messages.join(' | '));
})();

/* ---- 10. Probe target selection --------------------------------------- */
(function () {
  const E = 2e8, A = 0.002;
  const m = {
    nodes: [{ x: 0, y: 0 }, { x: 4, y: 0 }, { x: 2, y: 3 }],
    members: [{ i: 0, j: 1, E, A }, { i: 1, j: 2, E, A }, { i: 2, j: 0, E, A }],
    supports: [{ node: 0, dx: true, dy: true }, { node: 1, dx: false, dy: true }],
    loads: [{ node: 2, fx: 5, fy: -20 }],
  };
  const h = S.analyze(m, { target: { node: 1, mode: 'horizontal' } });
  ok('horizontal probe at roller: δ = ux', close(h.virtualWork.total, h.displacements[1].ux, 1e-9));
  ok('horizontal probe targets chosen joint', h.virtualWork.targetNode === 1 && !h.virtualWork.auto);
  const p = S.analyze(m, { target: { node: 0, mode: 'vertical' } });
  ok('probe at a pinned joint is flagged restrained, δ = 0', p.virtualWork.restrained && p.virtualWork.total === 0);
  const none = S.analyze({ ...m, loads: [] });
  ok('no load: solves, all forces zero, no virtual-work target',
    none.ok && none.members.every((x) => x.N === 0) && !none.virtualWork);
})();

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
