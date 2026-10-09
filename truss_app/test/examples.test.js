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

/* ---- Each preset's TEACHING CLAIM holds (what its blurb tells students) -- */
function solved(key) {
  const model = new Model();
  model.loadFrom(EX[key].build(new Units('SI')));
  const r = S.analyze({ nodes: model.nodes, members: model.members, supports: model.supports, loads: model.loads });
  const yMax = Math.max(...model.nodes.map((n) => n.y));
  const supported = new Set(model.supports.map((s) => s.node));
  const tagged = r.members.map((m) => {
    const a = model.nodes[m.i], b = model.nodes[m.j];
    const kind = Math.abs(a.y - b.y) < 1e-9 ? (Math.abs(a.y - yMax) < 1e-9 ? 'top' : 'bottom')
      : Math.abs(a.x - b.x) < 1e-9 ? 'vert' : 'diag';
    return { ...m, kind, a, b, atSupport: supported.has(m.i) || supported.has(m.j) };
  });
  return { r, tagged, model };
}
const all = (arr, f) => arr.length > 0 && arr.every(f);

(function () {
  const { r, tagged } = solved('pratt');
  ok('Pratt: determinate', r.determinacy.verdict === 'determinate');
  ok('Pratt: every diagonal in tension', all(tagged.filter((m) => m.kind === 'diag'), (m) => m.state === 'tension'));
  ok('Pratt: verticals in compression (or zero)', all(tagged.filter((m) => m.kind === 'vert'), (m) => m.N <= 0));
  ok('Pratt: top chord in compression', all(tagged.filter((m) => m.kind === 'top'), (m) => m.state === 'compression'));
  ok('Pratt: bottom chord in tension (or zero)', all(tagged.filter((m) => m.kind === 'bottom'), (m) => m.N >= 0));
  const top = tagged.filter((m) => m.kind === 'top').sort((p, q) => q.N - p.N); // most compressive last
  ok('Pratt: largest chord force near midspan', Math.abs(top[top.length - 1].a.x + top[top.length - 1].b.x - 12) <= 6);
})();

(function () {
  const { r, tagged } = solved('howe');
  ok('Howe: determinate', r.determinacy.verdict === 'determinate');
  ok('Howe: web diagonals in compression',
    all(tagged.filter((m) => m.kind === 'diag' && Math.min(m.a.y, m.b.y) === 0 && Math.max(m.a.y, m.b.y) > 0 &&
      !(m.a.x === 0 || m.b.x === 0 || m.a.x === 12 || m.b.x === 12)), (m) => m.state === 'compression'));
  const king = tagged.find((m) => m.kind === 'vert' && m.a.x === 6);
  ok('Howe: king post in tension', king && king.state === 'tension');
  ok('Howe: sloping top chords in compression',
    all(tagged.filter((m) => m.kind === 'diag' && (m.a.x === 0 || m.b.x === 0 || m.a.x === 12 || m.b.x === 12 ||
      (m.a.y > 0 && m.b.y > 0))), (m) => m.state === 'compression'));
  ok('Howe: bottom tie in tension', all(tagged.filter((m) => m.kind === 'bottom'), (m) => m.state === 'tension'));
})();

(function () {
  const { tagged } = solved('warrenLoaded');
  const d = tagged.filter((m) => m.kind === 'diag');
  ok('Warren: diagonals include both tension and compression',
    d.some((m) => m.state === 'tension') && d.some((m) => m.state === 'compression'));
  ok('Warren: top chord compression, bottom chord tension',
    all(tagged.filter((m) => m.kind === 'top'), (m) => m.state === 'compression') &&
    all(tagged.filter((m) => m.kind === 'bottom'), (m) => m.state === 'tension'));
})();

(function () {
  const { r, tagged } = solved('cantilever');
  ok('Cantilever: determinate (no dead wall member)', r.determinacy.verdict === 'determinate',
    JSON.stringify(r.determinacy));
  const maxF = tagged.slice().sort((p, q) => Math.abs(q.N) - Math.abs(p.N))[0];
  ok('Cantilever: most-loaded member is at the wall', maxF.atSupport);
  ok('Cantilever: deflection controlled by a member at the wall', tagged[r.virtualWork.ranked[0].index].atSupport);
  ok('Cantilever: top chord tension, bottom chord compression (hogging)',
    all(tagged.filter((m) => m.kind === 'top' && m.N !== 0), (m) => m.state === 'tension') &&
    all(tagged.filter((m) => m.kind === 'bottom'), (m) => m.state === 'compression'));
})();

(function () {
  const { tagged } = solved('triangle');
  ok('Triangle: rafters in compression, tie in tension',
    all(tagged.filter((m) => m.kind === 'diag'), (m) => m.state === 'compression') &&
    all(tagged.filter((m) => m.kind === 'bottom'), (m) => m.state === 'tension'));
})();

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
