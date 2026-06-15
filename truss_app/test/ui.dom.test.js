/* DOM smoke test — loads the real index.html with all scripts and exercises
 * the UI: page init, solving, the Help modal open/close (regression test for
 * the .modal[hidden] specificity bug), tool switching, and the what-if loop.
 *
 * Requires jsdom (dev-only, not a runtime dependency). Skips gracefully if
 * jsdom is not installed:  npm install --no-save jsdom && node test/ui.dom.test.js
 */
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (_) { console.log('SKIP: jsdom not installed (dev-only).'); process.exit(0); }

const path = require('path');

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('  ok  ' + name); }
  else { fail++; console.log('  FAIL ' + name + (extra ? ' -> ' + extra : '')); }
}

(async function () {
  const dom = await JSDOM.fromFile(path.join(__dirname, '..', 'index.html'), {
    runScripts: 'dangerously',
    resources: 'usable',
    pretendToBeVisual: true,
  });
  const { window } = dom;

  // Wait for external scripts + load event.
  await new Promise((res) => {
    if (window.document.readyState === 'complete' && window.TrussApp) return res();
    window.addEventListener('load', () => setTimeout(res, 50));
  });

  const doc = window.document;
  const App = window.TrussApp;
  const click = (el) => el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));

  // 1. App initialised and auto-loaded the Pratt preset, solved ok.
  ok('App initialised', !!App);
  ok('preset loaded members', App.model.members.length > 0, `${App.model.members.length}`);
  ok('preset solved ok', !!(App.result && App.result.ok));
  ok('insight cards rendered', doc.getElementById('insightCards').children.length > 0);
  ok('ranking bars rendered', doc.getElementById('rankingBox').innerHTML.includes('bar-row'));

  // 2. Help modal: starts closed, opens, and — the bug — closes again.
  const modal = doc.getElementById('helpModal');
  ok('modal starts hidden (attribute)', modal.hidden === true);
  click(doc.getElementById('helpBtn'));
  ok('modal opens on ? click', modal.hidden === false);
  click(doc.getElementById('helpClose'));
  ok('modal CLOSES on ✕ click (regression)', modal.hidden === true);
  // backdrop click closes too
  click(doc.getElementById('helpBtn'));
  modal.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); // target=modal
  ok('modal closes on backdrop click', modal.hidden === true);

  // 2b. CSS specificity: computed display must be 'none' when hidden, not 'grid'.
  try {
    const cs = window.getComputedStyle(modal);
    if (cs && cs.display) {
      ok('computed display is none when hidden', cs.display === 'none', `display=${cs.display}`);
    } else {
      console.log('  --  (jsdom did not resolve computed display; attribute check stands)');
    }
  } catch (_) {
    console.log('  --  (getComputedStyle unsupported in this jsdom build)');
  }

  // 3. Tool switching.
  click(doc.querySelector('.tool[data-tool="member"]'));
  ok('member tool active', App.tool === 'member');
  click(doc.querySelector('.tool[data-tool="select"]'));
  ok('select tool active', App.tool === 'select');

  // 4. What-if loop: select member 0, change its area, re-solve, no throw.
  App.select('member', 0);
  ok('member properties shown', doc.getElementById('memberProps').hidden === false);
  const before = App.result.members[0].A;
  const areaInput = doc.getElementById('mpArea');
  areaInput.value = String((+areaInput.value || 1) * 2);
  areaInput.dispatchEvent(new window.Event('change', { bubbles: true }));
  ok('area edit re-solved ok', !!(App.result && App.result.ok));
  ok('area actually changed', Math.abs(App.result.members[0].A - before) > 1e-12);

  // 5. AISC section pick sets area + steel E.
  const sectionSel = doc.getElementById('mpSection');
  sectionSel.value = 'W12x26';
  if (sectionSel.value === 'W12x26') {
    sectionSel.dispatchEvent(new window.Event('change', { bubbles: true }));
    ok('AISC section applied + solved', !!(App.result && App.result.ok));
    ok('AISC section recorded on member', App.model.members[0].section === 'W12x26');
  } else {
    ok('AISC option W12x26 exists in dropdown', false, 'option not found');
  }

  // 6. Unit switch does not break solving.
  const unitSel = doc.getElementById('unitSelect');
  unitSel.value = 'US';
  unitSel.dispatchEvent(new window.Event('change', { bubbles: true }));
  ok('unit switch to US still ok', !!(App.result && App.result.ok));

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.error('THREW:', e); process.exit(1); });
