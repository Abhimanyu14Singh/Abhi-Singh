/* Run every test suite and print one summary.  node tools/test-all.js
 *
 * The engine/preset suites need only Node. The UI suites need dev-only tools
 * and report SKIP if they're missing:
 *   npm install --no-save jsdom                                          (DOM suite)
 *   PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install --no-save playwright-core   (browser suite)
 * If dist/TrussLab.html exists, the UI suites also run against the built file.
 */
'use strict';
const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const dist = path.join(ROOT, 'dist', 'TrussLab.html');
const suites = [
  ['Engine (solver, closed-form checks)', ['test/solver.test.js']],
  ['Presets (stability + teaching claims)', ['test/examples.test.js']],
  ['UI, simulated DOM (index.html)', ['test/ui.dom.test.js']],
  ['UI, real Chromium (index.html)', ['test/browser.test.js']],
];
if (fs.existsSync(dist)) {
  suites.push(['UI, simulated DOM (dist/TrussLab.html)', ['test/ui.dom.test.js', dist]]);
  suites.push(['UI, real Chromium (dist/TrussLab.html)', ['test/browser.test.js', dist]]);
}

let failed = 0;
for (const [label, args] of suites) {
  const r = spawnSync(process.execPath, args, { cwd: ROOT, encoding: 'utf8' });
  const out = (r.stdout || '') + (r.stderr || '');
  const summary = (out.match(/(\d+) passed, (\d+) failed/) || [])[0];
  const skipped = /^SKIP/m.test(out);
  const status = skipped ? 'SKIP' : r.status === 0 ? 'PASS' : 'FAIL';
  if (status === 'FAIL') {
    failed++;
    console.log(out.split('\n').filter((l) => /FAIL|THREW|Error/.test(l)).slice(0, 10).join('\n'));
  }
  console.log(`${status.padEnd(5)} ${label.padEnd(44)} ${skipped ? out.trim().split('\n')[0] : summary || ''}`);
}
process.exit(failed ? 1 : 0);
