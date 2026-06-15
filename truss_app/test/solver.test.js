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

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
