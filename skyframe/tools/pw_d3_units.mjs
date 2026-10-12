/* D3 audit — Playwright check: section / material editors follow the display
   units (labels switch with the dialog Units selector, kip-in and kN-mm entry
   store identical section properties, closing restores the model units, OK
   with defaults leaves the model byte-identical, zero console errors).

   Run a backend first (any free port), then:
     SKYFRAME_URL=http://127.0.0.1:PORT node tools/pw_d3_units.mjs mock|live
   (PW_MODULE / PW_CHROMIUM override the playwright module / browser paths.) */
const { chromium } = await import(process.env.PW_MODULE || "/opt/node22/lib/node_modules/playwright/index.mjs");

const BASE = process.env.SKYFRAME_URL || "http://127.0.0.1:8763";
const MODE = process.argv[2] || "mock";
const results = [];
const ok = (name, cond, extra = "") => { results.push([name, !!cond, extra]); console.log((cond ? "PASS " : "FAIL ") + name + (extra ? "  " + extra : "")); };

const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM || "/opt/pw-browsers/chromium" });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errors = [];
page.on("console", m => { if (m.type() === "error") errors.push(m.text()); });
page.on("pageerror", e => errors.push(String(e)));

if (MODE === "live") {
  await page.request.post(BASE + "/api/model/quick", { data: {} });
}
await page.goto(BASE + (MODE === "mock" ? "/?mock=1" : "/"));
await page.waitForFunction(() => window.__sky && window.__sky.store && window.__sky.store.model, null, { timeout: 30000 });
await page.waitForTimeout(800);

const units = () => page.evaluate(() => window.__sky.U.getUnits());
const modelUnits = () => page.evaluate(() => window.__sky.store.model.display_units || "kN-m");
const snap0 = await page.evaluate(() => JSON.stringify(window.__sky.store.model));
ok("model units default", (await units()) === "kN-m", await units());

/* ---------------- Section Designer ---------------- */
await page.evaluate(() => window.__sky.openSectionDesigner());
await page.waitForSelector("#dsUnits");
ok("designer: selector defaults to model units", (await page.$eval("#dsUnits", s => s.value)) === "kN-m");
const lab = id => page.$eval(`label[for="${id}"] .unit`, e => e.textContent);
ok("designer: kN-m grid label m", (await lab("dsSnap")) === "m", await lab("dsSnap"));
await page.selectOption("#dsUnits", "kip-in");
await page.waitForTimeout(100);
ok("designer: kip-in grid label in", (await lab("dsSnap")) === "in", await lab("dsSnap"));
ok("designer: kip-in bar label in²", (await lab("dsBarArea")) === "in²", await lab("dsBarArea"));
ok("designer: entry unit in", (await page.$eval('label[for="dsEntryY"] .unit', e => e.textContent)) === "in");

async function drawRect(name, pts) {
  await page.click("#dsNew");
  await page.fill("#dsName", name);
  await page.dispatchEvent("#dsName", "change");
  for (const [y, z] of pts) {
    await page.fill("#dsEntryY", String(y));
    await page.fill("#dsEntryZ", String(z));
    await page.click("#dsEntryAdd");
  }
  await page.click("#dsEntryClose");
  await page.click("#dsSave");
  await page.waitForFunction(n => (window.__sky.store.model.sections || {})[n], name, { timeout: 20000 });
}
// kip-in: 10 x 20 in rectangle centred on the origin
await drawRect("RTIN", [[-5, -10], [5, -10], [5, 10], [-5, 10]]);
const propsTxt = await page.$eval("#dsProps", e => e.textContent);
ok("designer: kip-in property readouts in in² / in⁴", propsTxt.includes("in²") && propsTxt.includes("in⁴"), propsTxt.replace(/\s+/g, " ").slice(0, 120));
ok("designer: no m² left in kip-in", !/\bm²/.test(propsTxt));
// kN-mm: the same section, 254 x 508 mm
await page.selectOption("#dsUnits", "kN-mm");
await page.waitForTimeout(100);
ok("designer: kN-mm grid label mm", (await lab("dsSnap")) === "mm");
await drawRect("RTMM", [[-127, -254], [127, -254], [127, 254], [-127, 254]]);
const cmp = await page.evaluate(() => {
  const S = window.__sky.store.model.sections, a = S.RTIN, b = S.RTMM;
  const out = {};
  for (const k of ["A", "I33", "I22", "J"]) out[k] = [a[k], b[k], Math.abs(a[k] - b[k]) / Math.abs(a[k])];
  const D = window.__sky.store.model.designer_sections;
  out.verts = [D.RTIN.polygons[0].vertices, D.RTMM.polygons[0].vertices];
  return out;
});
for (const k of ["A", "I33", "I22", "J"])
  ok(`designer round trip ${k} kip-in == kN-mm`, cmp[k][2] < 1e-12, JSON.stringify(cmp[k]));
ok("designer: A = 200 in² = 0.129032 m²", Math.abs(cmp.A[0] - 0.129032) < 1e-12, String(cmp.A[0]));
ok("designer: I33 = 10*20^3/12 in⁴", Math.abs(cmp.I33[0] - 10 * 20 ** 3 / 12 * 0.0254 ** 4) / cmp.I33[0] < 1e-12, String(cmp.I33[0]));
await page.click("#designerClose");
await page.waitForTimeout(150);
ok("designer: close restores model units", (await units()) === (await modelUnits()), await units());

/* ---------------- Built-up editor ---------------- */
async function builtupPlate(name, set, b, t) {
  await page.evaluate(() => window.__sky.openBuiltup());
  await page.waitForSelector("#buUnits");
  const def = await page.$eval("#buUnits", s => s.value);
  await page.selectOption("#buUnits", set);
  await page.waitForTimeout(100);
  const gu = await page.$eval("#buGridUnit", e => e.textContent.trim());
  await page.fill("#buName", name);
  await page.dispatchEvent("#buName", "input");
  await page.click("#buAddPlate");
  await page.fill("#buDim_b", String(b)); await page.dispatchEvent("#buDim_b", "change");
  await page.fill("#buDim_t", String(t)); await page.dispatchEvent("#buDim_t", "change");
  await page.waitForTimeout(150);
  const aUnit = await page.$eval("#buPA", td => td.nextElementSibling.textContent);
  await page.click("#buOk");
  await page.waitForTimeout(250);
  return { def, gu, aUnit };
}
const b1 = await builtupPlate("BUIN", "kip-in", 10, 1);
ok("builtup: selector defaults to model units", b1.def === "kN-m", b1.def);
ok("builtup: kip-in grid unit + A unit", b1.gu === "in" && b1.aUnit === "in²", JSON.stringify(b1));
const b2 = await builtupPlate("BUMM", "kN-mm", 254, 25.4);
ok("builtup: kN-mm grid unit + A unit", b2.gu === "mm" && b2.aUnit === "mm²", JSON.stringify(b2));
const bcmp = await page.evaluate(() => {
  const S = window.__sky.store.model.sections, a = S.BUIN, b = S.BUMM;
  if (!a || !b) return null;
  return Object.fromEntries(["A", "I33", "I22", "J"].map(k => [k, [a[k], b[k], Math.abs(a[k] - b[k]) / Math.abs(a[k])]]));
});
ok("builtup: both saved", !!bcmp);
if (bcmp) for (const k of ["A", "I33", "I22"]) ok(`builtup round trip ${k}`, bcmp[k][2] < 1e-12, JSON.stringify(bcmp[k]));
ok("builtup: close restores model units", (await units()) === "kN-m", await units());

/* ---------------- Frame Section Property Data (parametric + preview) ---------------- */
await page.evaluate(() => window.__sky.std.frameSections());
await page.waitForSelector("#dsSecAdd");
await page.click("#dsSecAdd");
await page.waitForSelector("#dsFrameSectionDataUnits");
ok("frame section data: selector default", (await page.$eval("#dsFrameSectionDataUnits", s => s.value)) === "kN-m");
const hM = await page.$eval("#dsSecH", i => i.value);
await page.selectOption("#dsFrameSectionDataUnits", "kip-in");
await page.waitForTimeout(150);
const hIn = await page.$eval("#dsSecH", i => i.value);
ok("frame section data: h 0.6 m → 23.622 in", hM === "0.6" && Math.abs(parseFloat(hIn) - 23.622) < 1e-3, `${hM} → ${hIn}`);
const units2 = await page.$$eval("#dsFrameSectionData .dk-unit", us => us.map(u => u.textContent));
ok("frame section data: readout labels in in² / in⁴", units2.includes("in²") && units2.includes("in⁴"), units2.filter(Boolean).slice(0, 8).join(","));
ok("frame section data: no m² / m⁴ labels left", !units2.includes("m²") && !units2.includes("m⁴"));
// shape → steel I, dims in inches
const shapes = await page.$$eval("#dsSecShape option", os => os.map(o => o.value));
if (shapes.includes("I")) {
  await page.selectOption("#dsSecShape", "I");
  await page.waitForTimeout(150);
  const dimU = await page.$$eval("#ssShapeForm .dk-unit", us => us.map(u => u.textContent));
  ok("parametric I: dimension + property units follow kip-in", dimU.includes("in") && dimU.includes("in³") && dimU.includes("in⁶"), dimU.filter(Boolean).slice(0, 10).join(","));
}
await page.click("#dsFrameSectionDataCancel");
await page.waitForTimeout(150);
await page.click("#dsFrameSectionsCancel").catch(() => {});
await page.waitForTimeout(150);
ok("frame section data: close restores model units", (await units()) === "kN-m", await units());

/* ---------------- Material library ---------------- */
await page.evaluate(() => window.__sky.matLib.open());
await page.waitForSelector("#mlMatLibUnits");
await page.waitForTimeout(600);
const th0 = await page.$$eval("#mlMatLib th", t => t.map(x => x.textContent).join("|"));
await page.selectOption("#mlMatLibUnits", "kip-in");
await page.waitForTimeout(200);
const th1 = await page.$$eval("#mlMatLib th", t => t.map(x => x.textContent).join("|"));
ok("matlib: headers switch kPa → ksi", th0.includes("kPa") && th1.includes("ksi"), `${th0} || ${th1}`);
await page.click("#mlMatLibCancel");
await page.waitForTimeout(150);
ok("matlib: close restores model units", (await units()) === "kN-m");

/* ---------------- OK with defaults: model unchanged ---------------- */
const before = await page.evaluate(() => JSON.stringify(window.__sky.store.model));
await page.evaluate(() => window.__sky.std.frameSections());
await page.waitForSelector("#dsSecModify");
await page.click("#dsSecList tbody tr");
await page.click("#dsSecModify");
await page.waitForSelector("#dsFrameSectionDataUnits");
await page.selectOption("#dsFrameSectionDataUnits", "kN-mm");
await page.click("#dsFrameSectionDataOk");
await page.waitForTimeout(150);
await page.click("#dsFrameSectionsOk");
await page.waitForTimeout(200);
const after = await page.evaluate(() => JSON.stringify(window.__sky.store.model));
ok("OK with defaults (units switched) leaves the model byte-identical", before === after);
ok("display_units never written", (await modelUnits()) === "kN-m");

ok("zero console errors", errors.length === 0, errors.slice(0, 5).join(" || "));
await browser.close();
const nf = results.filter(r => !r[1]).length;
console.log(`${MODE}: ${results.length - nf}/${results.length} passed`);
process.exit(nf ? 1 : 0);
