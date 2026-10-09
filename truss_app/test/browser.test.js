/* Real-browser test (headless Chromium via playwright-core).
 *
 * jsdom can't render CSS faithfully (it reports the old, broken Help modal as
 * hidden too), so this drives the app in a real engine: true CSS cascade,
 * real mouse clicks at pixel positions, real layout — and saves screenshots.
 *
 * Dev-only. Skips cleanly if playwright-core or a Chromium is unavailable.
 *   PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install --no-save playwright-core
 *   node test/browser.test.js [page.html] [screenshot-dir]
 * Chromium is taken from $CHROMIUM_PATH, else $PLAYWRIGHT_BROWSERS_PATH/chromium,
 * else playwright's own download.
 */
let chromium;
try { ({ chromium } = require('playwright-core')); }
catch (_) { console.log('SKIP: playwright-core not installed (dev-only).'); process.exit(0); }

const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');

const page_ = process.argv[2] ? path.resolve(process.argv[2]) : path.join(__dirname, '..', 'index.html');
const shots = process.argv[3] ? path.resolve(process.argv[3]) : null;
if (shots) fs.mkdirSync(shots, { recursive: true });

function chromePath() {
  const cands = [process.env.CHROMIUM_PATH,
    process.env.PLAYWRIGHT_BROWSERS_PATH && path.join(process.env.PLAYWRIGHT_BROWSERS_PATH, 'chromium')];
  return cands.find((p) => p && fs.existsSync(p));
}

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('  ok  ' + name); }
  else { fail++; console.log('  FAIL ' + name + (extra ? ' -> ' + extra : '')); }
}

(async () => {
  let browser;
  try {
    browser = await chromium.launch({ executablePath: chromePath(), args: ['--no-sandbox'] });
  } catch (e) {
    console.log('SKIP: no Chromium available (' + e.message.split('\n')[0] + ')');
    process.exit(0);
  }
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('dialog', (d) => d.accept()); // "Clear the whole truss?" -> OK

  await page.goto(pathToFileURL(page_).href);
  await page.waitForFunction(() => window.TrussApp && window.TrussApp.result);
  const shot = async (name) => { if (shots) await page.screenshot({ path: path.join(shots, name) }); };

  // Helpers: world coords -> page pixels; real mouse clicks.
  const toPage = (wx, wy) => page.evaluate(([x, y]) => {
    const [sx, sy] = window.TrussApp.view.toScreen(x, y);
    const r = document.getElementById('canvas').getBoundingClientRect();
    return [r.left + sx, r.top + sy];
  }, [wx, wy]);
  const clickWorld = async (wx, wy) => { const [x, y] = await toPage(wx, wy); await page.mouse.click(x, y); };
  const app = (fn, arg) => page.evaluate(fn, arg);

  console.log('— layout & startup');
  ok('Pratt preset solved on load', await app(() => window.TrussApp.result.ok && window.TrussApp.model.members.length === 17));
  const canvasBox = await page.locator('#canvas').boundingBox();
  ok('canvas has real size', canvasBox.width > 500 && canvasBox.height > 400, JSON.stringify(canvasBox));
  ok('no horizontal page scroll', await app(() => document.documentElement.scrollWidth <= window.innerWidth));
  ok('ranking bars actually fill (rendered width > 0)', await app(() => {
    const fills = [...document.querySelectorAll('.bar-fill')];
    return fills.length > 0 && fills.every((f) => f.getBoundingClientRect().width > 1);
  }));
  ok('top ranking bar spans its full track', await app(() => {
    const f = document.querySelector('.bar-fill');
    return Math.abs(f.getBoundingClientRect().width - f.parentElement.clientWidth) <= 1;
  }));
  ok('tension members drawn blue, compression red',
    await app(() => {
      const t = document.querySelector('.member.tension'), c = document.querySelector('.member.compression');
      return t && c && getComputedStyle(t).stroke !== getComputedStyle(c).stroke;
    }));
  await shot('1-pratt-insights.png');

  console.log('— help modal (real CSS)');
  ok('help hidden on load', !(await page.locator('#helpModal').isVisible()));
  await page.click('#helpBtn');
  ok('? opens help', await page.locator('#helpModal').isVisible());
  await shot('2-help.png');
  await page.click('#helpClose');
  ok('✕ closes help', !(await page.locator('#helpModal').isVisible()));
  await page.click('#helpBtn');
  await page.mouse.click(20, 450); // backdrop, outside the card
  ok('backdrop click closes help', !(await page.locator('#helpModal').isVisible()));
  await page.click('#helpBtn'); await page.keyboard.press('Escape');
  ok('Esc closes help', !(await page.locator('#helpModal').isVisible()));

  console.log('— draw with the real mouse');
  await page.click('#clearBtn');
  ok('Clear empties model and panels agree',
    await app(() => window.TrussApp.model.nodes.length === 0 && !window.TrussApp.result.ok &&
      !/m=17/.test(document.getElementById('insightCards').textContent)));
  await page.click('.tool[data-tool="node"]');
  for (const [x, y] of [[0, 0], [4, 0], [8, 0], [2, 3], [6, 3]]) await clickWorld(x, y);
  ok('5 joints placed by clicking', await app(() => window.TrussApp.model.nodes.length === 5));
  await page.click('.tool[data-tool="member"]');
  for (const [x, y] of [[0, 0], [4, 0], [8, 0], [6, 3], [2, 3], [0, 0]]) await clickWorld(x, y);
  await page.keyboard.press('Escape');
  await clickWorld(2, 3); await clickWorld(4, 0); await clickWorld(6, 3); await page.keyboard.press('Escape');
  ok('7 members drawn by clicking', await app(() => window.TrussApp.model.members.length === 7));
  await page.click('.tool[data-tool="support"]');
  await clickWorld(0, 0); await clickWorld(8, 0); await clickWorld(8, 0);
  ok('pin + roller placed', await app(() =>
    window.TrussApp.model.supportKind(0) === 'pin' && window.TrussApp.model.supportKind(2) === 'roller-x'));
  await page.click('.tool[data-tool="load"]');
  await clickWorld(2, 3);
  await page.fill('#npFy', '-40'); await page.press('#npFy', 'Enter'); await page.locator('#npFy').blur();
  await clickWorld(6, 3);
  await page.fill('#npFy', '-20'); await page.locator('#npFy').blur();
  ok('drawn Warren truss solves, determinate', await app(() =>
    window.TrussApp.result.ok && window.TrussApp.result.determinacy.verdict === 'determinate'));
  await page.click('.tab[data-tab="insights"]');
  await page.click('#fitBtn');
  await shot('3-drawn-truss.png');

  console.log('— drag a joint with the mouse');
  await page.click('.tool[data-tool="select"]');
  const [ax, ay] = await toPage(2, 3);
  const [bx, by] = await toPage(2, 4);
  await page.mouse.move(ax, ay); await page.mouse.down();
  await page.mouse.move(bx, by, { steps: 6 }); await page.mouse.up();
  ok('joint dragged to new grid point', await app(() => window.TrussApp.model.nodes[3].y === 4));
  await page.keyboard.press('Control+z');
  ok('Ctrl+Z puts it back', await app(() => window.TrussApp.model.nodes[3].y === 3));

  console.log('— probe, deflected shape, AISC, units');
  await page.click('.tab[data-tab="insights"]'); // picker lives on the Insights tab
  await page.selectOption('#vwNode', '1'); await page.selectOption('#vwDir', 'vertical');
  ok('probe on Joint 2 vertical', await app(() => window.TrussApp.result.virtualWork.targetNode === 1));
  await page.check('#tgDefl');
  ok('deflected shape visible', await page.locator('.member-deflected').first().isVisible());
  await page.locator('.member[data-member="0"]').click({ force: true });
  await page.selectOption('#mpSection', 'W8x31');
  ok('AISC W8x31 applied to member 1', await app(() => window.TrussApp.model.members[0].section === 'W8x31'));
  await shot('4-aisc-properties.png');
  await page.selectOption('#unitSelect', 'US');
  ok('US units render kip labels', /kip/.test(await page.locator('.load-label').first().textContent()));
  await page.click('.tab[data-tab="insights"]');
  await shot('5-us-units-deflected.png');
  await page.click('.tab[data-tab="results"]');
  ok('results tables render', (await page.locator('#resultsTables table').count()) === 3);
  ok('no floating-point noise (e-15/e-16) shown in tables',
    !/e-1[0-9]/.test(await page.locator('#resultsTables').textContent()));
  await shot('6-results.png');

  console.log('— zoom & pan');
  const s0 = await app(() => window.TrussApp.view.scale);
  await page.mouse.move(canvasBox.x + 400, canvasBox.y + 300);
  await page.mouse.wheel(0, -400);
  ok('mouse wheel zooms', (await app(() => window.TrussApp.view.scale)) > s0);
  await page.keyboard.press('f');

  console.log('— presets');
  const keys = await app(() => Object.keys(window.TRUSS_EXAMPLES));
  for (const k of keys) {
    await page.selectOption('#exampleSelect', k);
    ok(`preset ${k} solves`, await app(() => window.TrussApp.result.ok));
  }

  // Edit a preset, then pick the SAME preset again -> it must reload.
  await page.selectOption('#exampleSelect', 'triangle');
  await app(() => { const A = window.TrussApp; A.model.commit(); A.model.deleteMember(0); A.recompute(); });
  await page.selectOption('#exampleSelect', 'triangle');
  ok('re-picking the same preset reloads it', await app(() => window.TrussApp.model.members.length === 3));
  ok('preset dropdown returns to placeholder', (await page.locator('#exampleSelect').inputValue()) === '');

  ok('no page errors in a real browser', errors.length === 0, errors.slice(0, 3).join(' | '));
  await browser.close();
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(async (e) => { console.error('THREW:', e); process.exit(1); });
