/* Integration test: every preset builds a solvable, stable truss.
 * Run: node truss_app/test/examples.test.js
 */
const fs = require('fs');
const path = require('path');
function load(f) { eval(fs.readFileSync(path.join(__dirname, '..', 'js', f), 'utf8')); }
['solver.js', 'units.js', 'sections.js', 'model.js', 'examples.js'].forEach(load);

const S = globalThis.TrussSolver;
const Units = globalThis.Units;
const Model = globalThis.TrussModel;
const EX = globalThis.TRUSS_EXAMPLES;

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('  ok  ' + name); }
  else { fail++; console.log('  FAIL ' + name + (extra ? ' -> ' + extra : '')); }
}

for (const sys of ['SI', 'US']) {
  const units = new Units(sys);
  for (const key in EX) {
    const built = EX[key].build(units);
    const model = new Model();
    model.loadFrom(built);
    const r = S.analyze({
      nodes: model.nodes, members: model.members,
      supports: model.supports, loads: model.loads,
    });
    ok(`[${sys}] ${key} solves`, r.ok,
      r.ok ? '' : (r.messages || []).join('; '));
    if (r.ok) {
      const allFinite = r.members.every((m) => Number.isFinite(m.N));
      ok(`[${sys}] ${key} member forces finite`, allFinite);
      if (r.virtualWork) {
        const node = r.virtualWork.targetNode;
        const dir = r.virtualWork.dir;
        const disp = r.displacements[node].ux * dir.x + r.displacements[node].uy * dir.y;
        const close = Math.abs(r.virtualWork.total - disp) <= 1e-6 * Math.max(1, Math.abs(disp));
        ok(`[${sys}] ${key} virtual-work matches stiffness`, close,
          `${r.virtualWork.total} vs ${disp}`);
      }
    }
  }
}

// Specifically: the X-braced panel must be indeterminate.
(function () {
  const units = new Units('SI');
  const model = new Model();
  model.loadFrom(EX.indeterminate.build(units));
  const r = S.analyze({ nodes: model.nodes, members: model.members, supports: model.supports, loads: model.loads });
  ok('X-braced example is indeterminate', r.ok && r.determinacy.verdict === 'indeterminate',
    JSON.stringify(r.determinacy));
})();

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
