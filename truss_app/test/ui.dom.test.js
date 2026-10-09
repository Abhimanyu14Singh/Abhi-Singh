/* DOM test — loads the real page (or a built single-file copy) with all
 * scripts and drives every UI operation the way a user would: drawing tools
 * via pointer events, dragging/joining/panning/zooming, the property panels,
 * the deflection-probe picker, units, AISC shapes, save/load, keyboard
 * shortcuts, the Help modal, and every preset.
 *
 * Dev-only dependency: jsdom. Skips cleanly if it is not installed.
 *   npm install --no-save jsdom
 *   node test/ui.dom.test.js                 # tests index.html
 *   node test/ui.dom.test.js dist/TrussLab.html   # tests the built file
 */
let JSDOM, VirtualConsole;
try { ({ JSDOM, VirtualConsole } = require('jsdom')); }
catch (_) { console.log('SKIP: jsdom not installed (dev-only).'); process.exit(0); }

const path = require('path');
const target = process.argv[2]
  ? path.resolve(process.argv[2])
  : path.join(__dirname, '..', 'index.html');

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('  ok  ' + name); }
  else { fail++; console.log('  FAIL ' + name + (extra ? ' -> ' + extra : '')); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async function () {
  // Collect real script errors; ignore jsdom's "not implemented" noise
  // (navigation on <a download>, layout APIs).
  const scriptErrors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', (e) => { if (!/Not implemented/.test(e.message)) scriptErrors.push(e.message); });
  vc.on('error', (e) => scriptErrors.push(String(e)));

  const dom = await JSDOM.fromFile(target, {
    runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true, virtualConsole: vc,
  });
  const { window } = dom;
  await new Promise((res) => {
    if (window.document.readyState === 'complete' && window.TrussApp) return res();
    window.addEventListener('load', () => setTimeout(res, 50));
  });

  const doc = window.document;
  const App = window.TrussApp;
  const $ = (id) => doc.getElementById(id);
  const alerts = [];
  window.alert = (m) => alerts.push(String(m));
  window.confirm = () => true;

  const PE = window.PointerEvent || window.MouseEvent;
  const svg = $('canvas');
  const fire = (type, x, y, extra = {}) =>
    svg.dispatchEvent(new PE(type, { bubbles: true, clientX: x, clientY: y, pointerId: 1, ...extra }));
  const tapScreen = (x, y) => { fire('pointerdown', x, y); fire('pointerup', x, y); };
  const tapWorld = (wx, wy) => { const [x, y] = App.view.toScreen(wx, wy); tapScreen(x, y); };
  const click = (el) => el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  const change = (el, v) => { el.value = String(v); el.dispatchEvent(new window.Event('change', { bubbles: true })); };
  const key = (k, opts = {}) => doc.dispatchEvent(new window.KeyboardEvent('keydown', { key: k, bubbles: true, ...opts }));
  const setTool = (t) => click(doc.querySelector(`.tool[data-tool="${t}"]`));
  const panelsText = () => $('insightCards').innerHTML + $('rankingBox').innerHTML +
    $('summaryStrip').innerHTML + $('resultsTables').innerHTML + svg.innerHTML;
  const noJunk = (label) => {
    const t = panelsText();
    const bad = ['NaN', 'undefined', 'Infinity'].filter((w) => t.includes(w));
    ok(`${label}: no NaN/undefined/Infinity rendered`, bad.length === 0, bad.join(','));
  };

  /* ---------- 1. Startup ------------------------------------------------ */
  console.log('— startup');
  ok('app initialised', !!App);
  ok('Pratt preset loaded & solved', App.model.members.length === 17 && App.result.ok);
  ok('insight cards + ranking rendered', $('insightCards').children.length > 2 &&
    $('rankingBox').innerHTML.includes('bar-row'));
  ok('probe arrow drawn on canvas', !!svg.querySelector('.probe'));
  ok('Help modal closed on start', $('helpModal').hidden && window.getComputedStyle($('helpModal')).display === 'none');
  noJunk('startup');

  /* ---------- 2. Help modal -------------------------------------------- */
  console.log('— help modal');
  click($('helpBtn'));
  ok('? opens help', !$('helpModal').hidden && window.getComputedStyle($('helpModal')).display !== 'none');
  const toolBefore = App.tool;
  key('m');
  ok('tool shortcuts ignored while help is open', App.tool === toolBefore);
  click($('helpClose'));
  ok('✕ closes help', $('helpModal').hidden && window.getComputedStyle($('helpModal')).display === 'none');
  click($('helpBtn')); $('helpModal').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  ok('backdrop click closes help', $('helpModal').hidden);
  click($('helpBtn')); key('Escape');
  ok('Esc closes help', $('helpModal').hidden);

  /* ---------- 3. Draw a truss from scratch with the tools --------------- */
  console.log('— drawing tools');
  click($('clearBtn'));
  ok('Clear empties the model', App.model.nodes.length === 0 && App.model.members.length === 0);
  ok('empty model shows guidance, no crash', !App.result.ok && $('insightCards').textContent.length > 0);

  setTool('node');
  tapWorld(0, 0); tapWorld(4, 0); tapWorld(2, 3);
  ok('Joint tool adds 3 joints', App.model.nodes.length === 3);
  tapWorld(4, 0);
  ok('clicking an existing spot does not duplicate a joint', App.model.nodes.length === 3);
  ok('joints snap to the grid', App.model.nodes.every((n) => Number.isInteger(n.x) && Number.isInteger(n.y)),
    JSON.stringify(App.model.nodes));
  ok('drawing keeps the Insights tab open', doc.querySelector('.tab.active').dataset.tab === 'insights');

  setTool('member');
  tapWorld(0, 0);
  ok('first click marks the start joint', App.view.opts.pendingNode === 0 &&
    !!svg.querySelector('circle.node.pending'));
  tapWorld(4, 0); tapWorld(2, 3); tapWorld(0, 0);
  ok('chained clicks draw 3 members', App.model.members.length === 3);
  key('Escape');
  ok('Esc cancels the pending member', App.memberStart === -1 && App.view.opts.pendingNode === null);
  tapWorld(0, 0); tapWorld(4, 0);
  ok('duplicate member is not created', App.model.members.length === 3);
  key('Escape');

  setTool('support');
  tapWorld(0, 0);
  ok('Support tool: 1st click = pin', App.model.supportKind(0) === 'pin');
  tapWorld(4, 0);
  ok('Support tool: pin on joint 2', App.model.supportKind(1) === 'pin');
  tapWorld(4, 0);
  ok('Support tool cycles pin -> roller', App.model.supportKind(1) === 'roller-x');
  ok('stable but unloaded -> "add a load" tip', App.result.ok && /Add a load/.test($('insightCards').textContent));

  setTool('load');
  tapWorld(2, 3);
  ok('Load tool selects joint & opens Properties', App.sel.type === 'node' && App.sel.index === 2 &&
    doc.querySelector('.tab.active').dataset.tab === 'properties');
  change($('npFy'), -30);
  ok('typed load is applied', App.model.loadFor(2) && Math.abs(App.model.loadFor(2).fy + 30) < 1e-9);
  ok('loaded triangle solves', App.result.ok && App.result.determinacy.verdict === 'determinate');
  // Hand check: symmetric triangle, P=30 at apex (2,3), base 0..4.
  // Rafters: half-base 2, rise 3, length √13, sinθ = 3/√13.
  //   N = -P/(2 sinθ) = -5√13 ≈ -18.028 ;  tie = P/(2 tanθ) = 30·2/(2·3) = +10.
  const Ns = App.result.members.map((m) => m.N);
  const rafter = -5 * Math.sqrt(13);
  ok('member forces match hand calc (+10 tie, -18.03 rafters)',
    Ns.filter((n) => Math.abs(n - 10) < 1e-6).length === 1 &&
    Ns.filter((n) => Math.abs(n - rafter) < 1e-6).length === 2, JSON.stringify(Ns));
  ok('load label drawn in kN', /30 kN/.test(svg.querySelector('.load-label').textContent));
  noJunk('hand-drawn truss');

  /* ---------- 4. Properties panel edits -------------------------------- */
  console.log('— properties');
  App.select('node', 2);
  change($('npX'), 3);
  ok('editing joint X moves the joint', App.model.nodes[2].x === 3 && App.result.ok);
  change($('npSupport'), 'pin');
  ok('support dropdown sets a support', App.model.supportKind(2) === 'pin');
  change($('npSupport'), 'none');
  ok('support dropdown removes it', App.model.supportKind(2) === 'none');
  change($('npX'), 2);

  App.select('member', 0);
  ok('member panel shows force + contribution', /Force:/.test($('mpReadout').innerHTML) &&
    /contribution/.test($('mpReadout').innerHTML));
  const dBefore = Math.abs(App.result.virtualWork.total);
  const forcesBefore = App.result.members.map((m) => m.N);
  change($('mpArea'), (+$('mpArea').value) * 4);
  ok('determinate: bigger area leaves forces unchanged',
    App.result.members.every((m, k) => Math.abs(m.N - forcesBefore[k]) < 1e-9));
  ok('determinate: bigger area reduces deflection', Math.abs(App.result.virtualWork.total) < dBefore);
  // Picker: Family -> Search -> Shape.
  const famOpts = Array.from($('mpFamily').options).map((o) => o.value);
  ok('AISC family list: 10 families + "All"', famOpts.length === 11 && famOpts[0] === 'W' && famOpts.includes('ALL'),
    famOpts.join(','));
  const shapeCount = () => $('mpSection').querySelectorAll('option[value]:not([value=""])').length;
  ok('W family lists all 283 W shapes', $('mpFamily').value === 'W' && shapeCount() === 283, String(shapeCount()));
  $('mpSearch').value = 'w12'; $('mpSearch').dispatchEvent(new window.Event('input', { bubbles: true }));
  const w12 = Array.from($('mpSection').options).filter((o) => o.value).map((o) => o.value);
  ok('search "w12" narrows to W12 shapes only', w12.length > 10 && w12.length < 40 && w12.every((v) => v.startsWith('W12X')),
    `${w12.length}: ${w12.slice(0, 3)}`);
  ok('match count shown', /match/.test($('mpAiscCount').textContent), $('mpAiscCount').textContent);
  $('mpSearch').value = ''; $('mpSearch').dispatchEvent(new window.Event('input', { bubbles: true }));
  change($('mpFamily'), 'ALL');
  ok('"All families" lists all 1,589 shapes', shapeCount() === 1589, String(shapeCount()));
  change($('mpFamily'), 'HSSR');
  change($('mpSection'), 'HSS6X6X3/8');
  ok('AISC shape sets area (7.58 in²) and E (29,000 ksi)',
    Math.abs(App.model.members[0].A - 7.58 * 0.00064516) < 1e-12 &&
    Math.abs(App.model.members[0].E - 29000 * 6894.757293) < 1e-3);
  ok('AISC shape name kept on member', App.model.members[0].section === 'HSS6X6X3/8');
  ok('member readout names the shape', /HSS6X6X3\/8/.test($('mpReadout').textContent));
  // A shape from every family can be applied and solved.
  const perFamily = { W: 'W14X90', HSSC: 'HSS5.000X0.250', PIPE: 'Pipe6STD', L: 'L6X6X1/2', '2L': '2L4X4X3/8',
    T: 'WT8X28.5', C: 'C15X50', HP: 'HP14X117', MS: 'S24X121' };
  for (const [fam, name] of Object.entries(perFamily)) {
    change($('mpFamily'), fam); change($('mpSection'), name);
    const it = window.AISC.find(name);
    ok(`pick ${name} (${fam}) -> area ${it && it.areaIn2} in², solves`, App.model.members[0].section === name &&
      Math.abs(App.model.members[0].A - it.areaIn2 * 0.00064516) < 1e-12 && App.result.ok);
  }
  // Re-selecting the member re-opens the family its shape belongs to.
  change($('mpFamily'), 'W'); App.select('member', 1); App.select('member', 0);
  ok('re-selecting a member shows its shape (family follows it)',
    $('mpFamily').value === 'MS' && $('mpSection').value === 'S24X121');
  change($('mpFamily'), 'HSSR'); change($('mpSection'), 'HSS6X6X3/8');
  change($('mpE'), 150);
  ok('manual E edit clears the shape label', App.model.members[0].section === null &&
    Math.abs(App.model.members[0].E - 150e6) < 1e-3);

  /* ---------- 5. Deflection probe picker -------------------------------- */
  console.log('— deflection probe');
  ok('picker lists every joint', $('vwNode').options.length === App.model.nodes.length + 1);
  change($('vwNode'), 1); change($('vwDir'), 'horizontal');
  ok('probe moves to chosen joint/direction', App.result.virtualWork.targetNode === 1 &&
    App.result.virtualWork.mode === 'horizontal');
  ok('probe δ equals the joint\'s horizontal movement',
    Math.abs(App.result.virtualWork.total - App.result.displacements[1].ux) < 1e-12);
  ok('ranking heading follows the probe', /Joint 2/.test($('rankingBox').innerHTML));
  change($('vwNode'), 0); change($('vwDir'), 'vertical');
  ok('probe on a pinned joint explains why δ = 0', /doesn't move that way/.test($('insightCards').innerHTML) &&
    $('rankingBox').innerHTML === '');
  change($('vwNode'), -1); change($('vwDir'), 'motion');
  ok('back to auto (most-displaced joint)', App.result.virtualWork.auto === true);

  /* ---------- 6. Drag, join, delete, undo/redo -------------------------- */
  console.log('— editing');
  setTool('select');
  const [ax, ay] = App.view.toScreen(2, 3);
  const [bx, by] = App.view.toScreen(2, 4);
  fire('pointerdown', ax, ay); fire('pointermove', bx, by);
  ok('dragging re-solves live', App.model.nodes[2].y === 4 && App.result.ok);
  fire('pointerup', bx, by);
  key('z', { ctrlKey: true });
  ok('Ctrl+Z undoes the whole drag in one step', App.model.nodes[2].y === 3);
  key('y', { ctrlKey: true });
  ok('Ctrl+Y redoes it', App.model.nodes[2].y === 4);
  key('z', { ctrlKey: true });

  // Leaving the canvas mid-drag still finishes it.
  fire('pointerdown', ax, ay); fire('pointermove', bx, by); fire('pointerleave', bx, by);
  ok('pointer leaving the canvas ends the drag', App.drag === null && App.result.ok);
  key('z', { ctrlKey: true });

  // Drop one joint onto another -> they join.
  setTool('node'); tapWorld(6, 0);
  setTool('member'); tapWorld(4, 0); tapWorld(6, 0); key('Escape');
  const nBefore = App.model.nodes.length;
  setTool('select');
  const [cx, cy] = App.view.toScreen(6, 0);
  const [dx, dy] = App.view.toScreen(4, 0);
  fire('pointerdown', cx, cy); fire('pointermove', dx, dy); fire('pointerup', dx, dy);
  ok('dropping a joint onto another joins them', App.model.nodes.length === nBefore - 1 &&
    App.model.members.length === 3 && App.result.ok, `${App.model.nodes.length} joints, ${App.model.members.length} members`);

  setTool('delete');
  const [mx, my] = App.view.toScreen(1, 1.5); // midpoint of member 0-2
  tapScreen(mx, my);
  ok('Delete tool removes a member', App.model.members.length === 2);
  ok('…which makes it unstable, explained not crashed', !App.result.ok &&
    /mechanism/.test($('insightCards').textContent));
  tapWorld(2, 3);
  ok('Delete tool removes a joint (and its members)', App.model.nodes.length === 2 && App.model.members.length === 1);
  key('z', { ctrlKey: true }); key('z', { ctrlKey: true });
  ok('undo restores deleted joint and member', App.model.nodes.length === 3 && App.model.members.length === 3 && App.result.ok);
  App.select('member', 0); key('Delete');
  ok('Delete key removes the selected member', App.model.members.length === 2);
  key('z', { ctrlKey: true });

  /* ---------- 7. View: pan, zoom, fit, toggles -------------------------- */
  console.log('— view');
  setTool('select');
  const ox0 = App.view.ox;
  fire('pointerdown', 500, 500); fire('pointermove', 560, 520); fire('pointerup', 560, 520);
  ok('dragging empty space pans', Math.abs(App.view.ox - ox0 - 60) < 1e-9);
  App.select('node', 0);
  fire('pointerdown', 500, 500); fire('pointerup', 500, 500);
  ok('plain click on empty space deselects', App.sel.type === null);
  const s0 = App.view.scale;
  svg.dispatchEvent(new window.WheelEvent('wheel', { deltaY: -200, clientX: 300, clientY: 300, bubbles: true, cancelable: true }));
  ok('scroll wheel zooms in', App.view.scale > s0);
  click($('fitBtn'));
  ok('Fit view re-frames', App.view.scale !== 0 && Number.isFinite(App.view.ox));
  $('tgDefl').checked = true; $('tgDefl').dispatchEvent(new window.Event('input', { bubbles: true }));
  ok('deflected-shape toggle draws the deformed truss', svg.querySelectorAll('.member-deflected').length === App.model.members.length);
  $('tgForces').checked = false; $('tgForces').dispatchEvent(new window.Event('change', { bubbles: true }));
  ok('forces toggle off removes colouring', !svg.querySelector('.member.tension, .member.compression'));
  $('tgForces').checked = true; $('tgForces').dispatchEvent(new window.Event('change', { bubbles: true }));
  key('f');
  ok('F key fits the view', Number.isFinite(App.view.scale));

  /* ---------- 8. Units ------------------------------------------------- */
  console.log('— units');
  change($('unitSelect'), 'US');
  ok('US units: still solves', App.result.ok);
  ok('US units: load label in kip', /kip/.test(svg.querySelector('.load-label').textContent),
    svg.querySelector('.load-label').textContent);
  ok('US units: grid follows 1 ft snap', Math.abs(App.view.gridStep - 0.3048) < 1e-12);
  ok('US units: deflection reported in inches', /\bin\b/.test($('summaryStrip').textContent));
  noJunk('US units');
  change($('unitSelect'), 'SI');

  /* ---------- 9. Save / load ------------------------------------------- */
  console.log('— save / load');
  App.select('member', 1); change($('mpFamily'), 'W'); change($('mpSection'), 'W12X26');
  let savedBlob = null;
  window.URL.createObjectURL = (b) => { savedBlob = b; return 'blob:test'; };
  window.URL.revokeObjectURL = () => {};
  click($('saveBtn'));
  ok('Save produces a file', !!savedBlob);
  const savedText = await new Promise((res) => {
    const fr = new window.FileReader(); fr.onload = () => res(fr.result); fr.readAsText(savedBlob);
  });
  const saved = JSON.parse(savedText);
  ok('saved file keeps the AISC shape', saved.members.some((m) => m.section === 'W12X26'));

  const loadFile = async (text) => {
    const file = new window.File([text], 'truss.json', { type: 'application/json' });
    Object.defineProperty($('fileInput'), 'files', { value: [file], configurable: true });
    $('fileInput').dispatchEvent(new window.Event('change', { bubbles: true }));
    await sleep(80);
  };
  click($('clearBtn'));
  await loadFile(savedText);
  ok('Load restores the truss', App.model.members.length === saved.members.length && App.result.ok);
  ok('Load restores the AISC shape', App.model.members.some((m) => m.section === 'W12X26'));

  // Files saved by v1.1 used other spellings ("W12x26", "Pipe 4 Std").
  const legacy = JSON.parse(savedText);
  legacy.members[0].section = 'W12x26'; legacy.members[0].A = 7.65 * 0.00064516;
  legacy.members[1].section = 'L5x5x3/8'; legacy.members[1].A = 3.61 * 0.00064516; // pre-v15 area
  await loadFile(JSON.stringify(legacy));
  ok('old file: "W12x26" maps to W12X26', App.model.members[0].section === 'W12X26');
  ok('old file: label with a different saved area is dropped, area kept',
    App.model.members[1].section === null && Math.abs(App.model.members[1].A - 3.61 * 0.00064516) < 1e-12);
  await loadFile(savedText);

  const before = JSON.stringify(App.model.toJSON());
  await loadFile('{ "nodes": [ {"x":0,"y":0} ], "members": [ {"i":0,"j":7} ] }');
  ok('bad file: friendly error shown', alerts.some((a) => /does not exist/.test(a)), alerts.join(' | '));
  ok('bad file: current truss left untouched', JSON.stringify(App.model.toJSON()) === before);
  await loadFile('this is not json');
  ok('non-JSON file: friendly error, no crash', alerts.length >= 2 && App.result.ok);

  /* ---------- 10. Every preset, both unit systems ---------------------- */
  console.log('— presets');
  for (const sys of ['SI', 'US']) {
    change($('unitSelect'), sys);
    for (const opt of Array.from($('exampleSelect').options).filter((o) => o.value)) {
      change($('exampleSelect'), opt.value);
      ok(`[${sys}] ${opt.value}: solves & explains`, App.result.ok &&
        $('insightCards').children.length >= 3 && !!svg.querySelector('.probe'));
      noJunk(`[${sys}] ${opt.value}`);
    }
  }
  change($('unitSelect'), 'SI');
  change($('exampleSelect'), 'indeterminate');
  ok('indeterminate preset says so', /indeterminate to degree 1/.test($('insightCards').innerHTML));
  App.select('member', 5);
  const shareBefore = App.result.members[5].N;
  change($('mpArea'), (+$('mpArea').value) * 5);
  ok('indeterminate: stiffer diagonal attracts more force', Math.abs(App.result.members[5].N) > Math.abs(shareBefore) + 1e-6,
    `${shareBefore} -> ${App.result.members[5].N}`);

  /* ---------- 11. Results tab ------------------------------------------ */
  console.log('— results tab');
  click(doc.querySelector('.tab[data-tab="results"]'));
  const tables = $('resultsTables').querySelectorAll('table');
  ok('results tab: forces, reactions, virtual-work tables', tables.length === 3);
  click($('resultsTables').querySelector('tr[data-member="2"]'));
  ok('clicking a results row selects that member', App.sel.type === 'member' && App.sel.index === 2);

  ok('no uncaught script errors during the whole session', scriptErrors.length === 0, scriptErrors.slice(0, 3).join(' | '));

  console.log(`\n${pass} passed, ${fail} failed`);
  window.close();
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.error('THREW:', e); process.exit(1); });
