/* SkyFrame Wave G — results in every view (controller).

   ONE results model (js/g_core.js: case → step / time or envelope →
   component) shared by every view: the main 3D view (Classic and GPU
   renderers), every View › Windows pane, the plan and the elevation. Each
   view keeps its OWN display choice (deformed shape, frame / pier /
   spandrel / link diagrams, displacement or shell contours, reactions,
   hinge states, scales, tower / group filter); the case, step / time,
   envelope and the animation clock are shared, like the ETABS results
   toolbar driving every window.

     sky.results = { state, selection, catalog, select, setStep, step,
       setEnv, play, pause, toggle, isPlaying, setSpeed, setCycles,
       loadSteps, stepData, viewIds, viewOpts, setViewOpts, activeView,
       subscribe, redraw, captureView, applyView,
       openDetail (js/g_detail.js), pin / probes / openWatch
       (js/g_probe.js), openSelectByResult / selectByResult (js/g_probe.js),
       openOptions (js/g_results_bar.js) }

   Step states of time-history, pushover, nonlinear static and staged
   cases come from POST /api/results/steps (js/mock_g_steps.js in ?mock=1);
   without them a TH case still animates from its recorded animation frames
   or the story-translation frame source of the video export
   (sky.wcVideo.thFrames — js/wc_video.js), which keeps working unchanged.
   Response-spectrum results stay positive envelopes (no sign is made up).
   Hooks: app.js (import + init line), viewer3d.js (gResOverlay),
   gpu/renderer.js (gResGL), wb_views.js (named views), etabs.js (Select ›
   More Criteria submenu). */

import gRU from "./units.js";
import * as gC from "./g_core.js";
import { gMockSteps as gResMockSteps } from "./mock_g_steps.js";
import { gInitOverlay as gResInitOverlay } from "./g_overlay.js";
import { gInitBar as gResInitBar } from "./g_results_bar.js";
import { gInitDetail as gResInitDetail } from "./g_detail.js";
import { gInitProbe as gResInitProbe } from "./g_probe.js";
import { uhCaseList as gUhCaseList, uhSteps as gUhSteps } from "./uhcore.js";

export const G_DEFAULT_VIEW = Object.freeze({
  deformed: false, wire: true, cubic: true, dscaleMode: "auto", dscale: 1, dscaleUser: 100,
  diagram: "", values: "fill", vscaleMode: "auto", vscale: 1, vscaleUser: 0.01,
  contour: "", onDeformed: true, crange: null, palette: "rainbow",
  reactions: false, piers: false, spandrels: false, links: false, hinges: false, shell: false,
  towers: [], group: "",
});
const VIEW_KEYS = Object.keys(G_DEFAULT_VIEW);

export function initGResults(sky) {
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 4000) => sky.toast && sky.toast(t, m, k, ms);
  const R = {
    sel: { key: null, step: -1, env: "step" },
    steps: {}, loading: {}, errors: {},
    frames: 40,
    play: { on: false, speed: 1, cycles: 0, t: 0, acc: 0, cycle: 0, phase: 1, last: 0, raf: 0 },
    views: new Map(),                 // id → {id, kind, obj, opts, redraw}
    listeners: new Set(),
    memo: { r: null, cat: [], key: null, st: null, sig: null },
    refCache: new Map(),
  };
  const results = () => S.results || null;

  /* ---------------------------------------------------------------- catalogue / state */
  function catalog() {
    const r = results();
    if (R.memo.r !== r) { R.memo.r = r; R.memo.cat = gC.caseCatalog(r); R.memo.st = null; R.refCache.clear(); }
    return R.memo.cat;
  }
  const entry = (key = R.sel.key) => catalog().find(e => e.key === key) || null;
  const stepDataOf = e => (e && R.steps[e.name] && R.steps[e.name].case === e.name ? R.steps[e.name] : null);
  const fmt = (kind, v) => (v == null || !isFinite(v) ? "—" : `${gRU.fmt(kind, v, kind === "period" ? 2 : 1)} ${gRU.label(kind)}`.trim());
  const thFrames = (r, n) => (sky.wcVideo && sky.wcVideo.thFrames ? sky.wcVideo.thFrames(r, n) : null);
  function state() {
    const r = results(), e = entry();
    const sd = stepDataOf(e);
    const sig = [r, R.sel.key, R.sel.step, R.sel.env, sd];
    if (R.memo.st !== null && R.memo.sig && sig.every((x, i) => x === R.memo.sig[i])) return R.memo.st;
    R.memo.sig = sig;
    R.memo.st = r && e ? gC.extractState(r, R.sel, sd, { fmt, thFrames }) : null;
    // lazily served station blocks (large models): fetch the shown step (+ the next ones while playing)
    const st = R.memo.st;
    if (st && sd && sd.members_lazy && st.axis && st.axis.source === "steps" && R.sel.env === "step" && !st.member_stations) {
      const ids = st.axis.ids, i = st.stepIndex;
      const want = [ids[i]];
      if (R.play.on) for (let k = 1; k <= 8; k++) want.push(ids[(i + k) % ids.length]);
      fetchMemberSteps(e, sd, want);
    }
    return R.memo.st;
  }
  const memberJobs = new Set();
  async function fetchMemberSteps(e, sd, ids) {
    const need = ids.filter(id => id != null && !(sd.member_stations && sd.member_stations[String(id)]) && !memberJobs.has(e.name + "|" + id));
    if (!need.length) return;
    need.forEach(id => memberJobs.add(e.name + "|" + id));
    try {
      const res = await fetch("/api/results/steps", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ case: e.name, frames: sd.frames || R.frames, envelope: !!sd.envelope, members: "lazy", member_steps: need }) });
      const data = await res.json();
      if (!res.ok) throw new Error((data && data.error) || res.statusText);
      if (R.steps[e.name] !== sd) return;                 // results / step data replaced meanwhile
      sd.member_stations = Object.assign(sd.member_stations || {}, data.member_stations || {});
      R.memo.st = null;
      notify();
    } catch (err) {
      R.errors[e.name] = err.message || String(err);
      notify();
    } finally { need.forEach(id => memberJobs.delete(e.name + "|" + id)); }
  }
  function axis() { const st = state(); return (st && st.axis) || { n: 0, ids: [], labels: [], values: [] }; }

  /** Fixed deformed-shape reference (peak translation over every available
      step of the case) so an animation keeps one scale. */
  function deformedRef() {
    const r = results(), e = entry(), st = state();
    if (!r || !e || !st) return 0;
    const sd = stepDataOf(e);
    const ck = `d|${e.key}|${R.sel.env}|${sd ? 1 : 0}|${e.multi ? "" : R.sel.env}`;
    if (R.refCache.has(ck)) return R.refCache.get(ck);
    let ref = 0;
    if (!e.multi || e.kind === "modal" || e.kind === "buckling") ref = gC.peakDisp(st.node_disp);
    else if (sd) {
      for (const m of Object.values(sd.node_disp || {})) ref = Math.max(ref, gC.peakDisp(m));
      if (sd.envelope) ref = Math.max(ref, gC.peakDisp(sd.envelope.max.node_disp), gC.peakDisp(sd.envelope.min.node_disp));
    } else if (st.axis && st.axis.source === "frames") {
      for (const m of Object.values(r.th_cases[e.name].frames.node_disp || {})) ref = Math.max(ref, gC.peakDisp(m));
    } else if (st.axis && st.axis.source === "story" && st.axis.frames) {
      for (const f of st.axis.frames) ref = Math.max(ref, gC.peakDisp(f.disp));
    } else ref = gC.peakDisp(st.node_disp);
    if (!(e.kind === "modal" || e.kind === "buckling")) R.refCache.set(ck, ref);
    return ref;
  }
  /** Fixed diagram reference: max |comp| over the steps / envelope of the case. */
  function diagramRef(comp, filterFn, compOf, cacheKey) {
    const e = entry(), st = state();
    if (!e || !st) return 0;
    const sd = stepDataOf(e);
    const ck = cacheKey ? `v|${cacheKey}|${comp}|${e.key}|${R.sel.env}|${e.multi && sd ? "steps" : R.sel.step}|${sd ? 1 : 0}` : null;
    if (ck && R.refCache.has(ck)) return R.refCache.get(ck);
    const blocks = [];
    if (e.multi && sd && sd.member_peaks) {
      const keys = comp === "MIN" || comp === "VIN" ? (comp === "MIN" ? ["M2", "M3"] : ["V2", "V3"]) : [comp];
      let v = 0;
      for (const pk of Object.values(sd.member_peaks)) for (const k of keys) v = Math.max(v, +pk[k] || 0);
      if (sd.envelope) for (const b of [sd.envelope.max.member_stations, sd.envelope.min.member_stations]) for (const s of Object.values(b || {})) for (const k of keys) for (const x of (s[k] || [])) v = Math.max(v, Math.abs(x));
      if (ck) R.refCache.set(ck, v);
      return v;
    }
    if (e.multi && sd && sd.member_stations) {
      blocks.push(...Object.values(sd.member_stations));
      if (sd.envelope) blocks.push(sd.envelope.max.member_stations, sd.envelope.min.member_stations);
    } else { blocks.push(st.member_stations); if (st.member_stations_min) blocks.push(st.member_stations_min); }
    let v = 0;
    const keys = comp === "MIN" || comp === "VIN" ? (comp === "MIN" ? ["M2", "M3"] : ["V2", "V3"]) : [comp];
    for (const b of blocks) for (const [uid, s] of Object.entries(b || {})) {
      if (filterFn && !filterFn(uid)) continue;
      const ks = compOf ? [compOf(uid) || keys[0]] : keys;
      for (const k of ks) for (const x of (s[k] || [])) { const a = Math.abs(x); if (a > v) v = a; }
    }
    if (ck && (e.multi && sd)) R.refCache.set(ck, v);
    return v;
  }

  /* ---------------------------------------------------------------- notify */
  let pending = 0;
  function notify(immediate) {
    const run = () => {
      pending = 0;
      for (const v of R.views.values()) { try { v.redraw && v.redraw(); } catch (e) { console.error("results view redraw", e); } }
      for (const fn of R.listeners) { try { fn(state()); } catch (e) { console.error("results listener", e); } }
    };
    if (immediate) { if (pending) cancelAnimationFrame(pending); run(); return; }
    if (!pending) pending = requestAnimationFrame(run);
  }
  const subscribe = fn => { R.listeners.add(fn); return () => R.listeners.delete(fn); };

  /* ---------------------------------------------------------------- step data */
  async function loadSteps(opts = {}) {
    const e = opts.key ? entry(opts.key) : entry();
    if (!e || !gC.needsStepData(e)) return null;
    const have = stepDataOf(e);
    const env = !!opts.envelope;
    if (have && (!env || have.envelope) && !opts.force) return have;
    const key = e.name + (env ? "|env" : "");
    if (R.loading[key]) return R.loading[key];
    const req = { case: e.name, frames: opts.frames || R.frames, members: S.mock ? true : "lazy", envelope: env };
    const job = (async () => {
      try {
        let pay;
        if (S.mock) { await new Promise(res => setTimeout(res, 40)); pay = gResMockSteps(S.model, results(), req); }
        else {
          const res = await fetch("/api/results/steps", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(req) });
          let data = null;
          try { data = await res.json(); } catch { /* non-JSON */ }
          if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
          pay = data;
        }
        if (entry(e.key) && results()) {
          R.steps[e.name] = pay;
          delete R.errors[e.name];
          R.refCache.clear();
          R.memo.st = null;
          if (R.sel.key === e.key && R.sel.step >= pay.steps.length) R.sel.step = pay.steps.length - 1;
          notify(true);
        }
        return pay;
      } catch (err) {
        R.errors[e.name] = err.message || String(err);
        notify();
        return null;
      } finally { delete R.loading[key]; }
    })();
    R.loading[key] = job;
    notify();
    return job;
  }

  /* ---------------------------------------------------------------- selection */
  function syncAppCase(key) {
    const r = results();
    if (!r || !key) return;
    let name = null;
    if (key.startsWith("c:") || key.startsWith("k:")) name = key.slice(2);
    else if (key.startsWith("rs:")) name = key;
    else if (key.startsWith("st:")) name = "staged:" + key.slice(3);
    else if (key.startsWith("nl:")) name = key;
    const sel = document.getElementById("caseSelect");
    if (!name || !sel || S.caseName === name || ![...sel.options].some(o => o.value === name)) return;
    sel.value = name;
    syncingApp = true;
    try { sel.dispatchEvent(new Event("change", { bubbles: true })); } finally { syncingApp = false; }
  }
  let syncingApp = false;
  function select(key, { step = null, env = null, quiet = false } = {}) {
    const e = entry(key);
    if (!e) return false;
    const changed = R.sel.key !== key;
    R.sel.key = key;
    if (changed) { R.sel.step = e.kind === "modal" || e.kind === "buckling" ? 0 : -1; R.sel.env = "step"; }
    if (step != null) R.sel.step = step;
    if (env != null) R.sel.env = env;
    if (changed && R.play.on) pause();
    R.memo.st = null;
    if (!quiet) syncAppCase(key);
    if (gC.needsStepData(e) && !stepDataOf(e)) loadSteps();
    syncHinges();
    notify(true);
    return true;
  }
  function setStep(i) {
    const ax = axis();
    if (!ax.n) return;
    R.sel.step = Math.max(0, Math.min(ax.n - 1, Math.round(i)));
    if (R.sel.env !== "step" && entry() && entry().multi) R.sel.env = "step";
    R.memo.st = null;
    syncHinges();
    notify(true);
  }
  const curStep = () => { const ax = axis(); return ax.n ? (R.sel.step < 0 ? ax.n - 1 : Math.min(R.sel.step, ax.n - 1)) : -1; };
  function stepBy(d) { const ax = axis(); if (!ax.n) return; let k = curStep() + d; if (k < 0) k = ax.n - 1; if (k >= ax.n) k = 0; setStep(k); }
  async function setEnv(env) {
    const e = entry();
    if (!e) return;
    R.sel.env = env;
    R.memo.st = null;
    if (env !== "step" && gC.needsStepData(e) && e.kind !== "staged") {
      const sd = stepDataOf(e);
      if (!sd || !sd.envelope) await loadSteps({ envelope: true, force: !!sd });
    }
    notify(true);
  }

  /* ---------------------------------------------------------------- hinge sync */
  function syncHinges() {
    const hr = sky.hingeResults, e = entry();
    if (!hr || !e) return;
    const wants = [...R.views.values()].some(v => v.opts.hinges);
    const hk = e.kind === "pushover" ? "po:" + e.name : e.kind === "nl" ? "nl:" + e.name : e.kind === "th" ? "th:" + e.name : null;
    if (!wants || !hk) return;
    try {
      const c = gUhCaseList(results()).find(x => x.key === hk);
      if (!c) return;
      const n = gUhSteps(c).n;
      if (!n) return;
      const ax = axis(), i = curStep();
      let k;
      if (stepDataOf(e) && ax.ids[i] != null) k = ax.ids[i] - (e.kind === "nl" ? 0 : 1);
      else k = Math.round((ax.n > 1 ? i / (ax.n - 1) : 1) * (n - 1));
      hr.state.key = hk;
      hr.setActive(true);
      hr.setStep(Math.max(0, Math.min(n - 1, k)));
    } catch (err) { /* hinge results are optional */ }
  }

  /* ---------------------------------------------------------------- animation */
  const cyclic = () => { const e = entry(); return !!e && (!e.multi || e.kind === "modal" || e.kind === "buckling"); };
  function frame(now) {
    const P = R.play;
    if (!P.on) return;
    const dt = Math.min(0.1, Math.max(0, (now - (P.last || now)) / 1000));
    P.last = now;
    if (cyclic()) {
      P.t += dt;
      const period = 1.8 / Math.max(0.05, P.speed);
      P.phase = Math.sin(2 * Math.PI * P.t / period);
      if (P.cycles > 0 && P.t >= P.cycles * period) { pause(); return; }
      for (const v of R.views.values()) { try { v.animate && v.animate(); } catch (e) { /* never break */ } }
    } else {
      const ax = axis();
      if (!ax.n) { pause(); return; }
      P.acc += dt * 8 * P.speed;
      let moved = false;
      while (P.acc >= 1) {
        P.acc -= 1;
        let k = curStep() + 1;
        if (k >= ax.n) {
          P.cycle++;
          if (P.cycles > 0 && P.cycle >= P.cycles) { R.sel.step = ax.n - 1; R.memo.st = null; pause(); notify(); return; }
          k = 0;
        }
        R.sel.step = k; moved = true;
      }
      if (moved) { R.sel.env = "step"; R.memo.st = null; syncHinges(); notify(true); }
    }
    P.raf = requestAnimationFrame(frame);
  }
  async function play() {
    const e = entry();
    if (!e) { toast("Results", "Pick a case on the results toolbar first."); return false; }
    if (gC.needsStepData(e) && !stepDataOf(e)) await loadSteps();
    const P = R.play;
    if (P.on) return true;
    P.on = true; P.t = 0; P.acc = 0; P.cycle = 0; P.last = 0; P.phase = 1;
    if (!cyclic() && R.sel.env !== "step") R.sel.env = "step";
    if (!cyclic() && curStep() >= axis().n - 1) R.sel.step = 0;
    R.memo.st = null;
    notify(true);
    P.raf = requestAnimationFrame(frame);
    return true;
  }
  function pause() {
    const P = R.play;
    if (P.raf) cancelAnimationFrame(P.raf);
    P.on = false; P.raf = 0; P.phase = 1;
    for (const v of R.views.values()) { try { v.animate && v.animate(); } catch (e) { /* never break */ } }
    notify();
  }
  const phase = () => (R.play.on && cyclic() ? R.play.phase : 1);

  /* ---------------------------------------------------------------- views */
  function registerView(id, kind, obj, hooks = {}) {
    let v = R.views.get(id);
    if (!v) { v = { id, kind, obj, opts: { ...G_DEFAULT_VIEW, towers: [] } }; R.views.set(id, v); }
    Object.assign(v, { kind, obj }, hooks);
    return v;
  }
  const viewIds = () => [...R.views.keys()];
  const viewOpts = id => { const v = R.views.get(id || activeView()); return v ? { ...v.opts } : { ...G_DEFAULT_VIEW }; };
  function setViewOpts(id, patch, { all = false } = {}) {
    const targets = all ? [...R.views.values()] : [R.views.get(id || activeView())].filter(Boolean);
    const clean = {};
    for (const [k, val] of Object.entries(patch || {})) if (VIEW_KEYS.includes(k)) clean[k] = val;
    for (const v of targets) { Object.assign(v.opts, clean); v.optsChanged && v.optsChanged(); }
    if ("hinges" in clean && clean.hinges) syncHinges();
    if ("hinges" in clean && !clean.hinges && ![...R.views.values()].some(v => v.opts.hinges) && sky.hingeResults) { try { sky.hingeResults.setActive(false); } catch { /* optional */ } }
    notify(true);
  }
  function activeView() {
    const W = sky.wb && sky.wb.windows;
    if (W && W.layout && W.layout() !== "1") {
      const ps = W.panes();
      const i = Math.max(0, ps.findIndex(p => p.el && p.el.classList.contains("is-focus")));
      if (R.views.has("pane" + i)) return "pane" + i;
    }
    if (S.mode === "model") return S.view === "elev" ? "elev" : "plan";
    return "main3d";
  }

  /* ---------------------------------------------------------------- named views */
  function captureView() {
    if (!R.sel.key) return null;
    const id = activeView();
    const o = viewOpts(id);
    const out = { case: R.sel.key, step: R.sel.step, env: R.sel.env, view: {} };
    for (const k of VIEW_KEYS) if (JSON.stringify(o[k]) !== JSON.stringify(G_DEFAULT_VIEW[k])) out.view[k] = o[k];
    return out;
  }
  function applyView(rv) {
    if (!rv || typeof rv !== "object") return false;
    if (rv.case && entry(rv.case)) select(rv.case, { step: rv.step != null ? rv.step : null, env: rv.env || "step" });
    const id = activeView();
    setViewOpts(id, { ...G_DEFAULT_VIEW, towers: [], ...(rv.view || {}) });
    if (rv.env && rv.env !== "step") setEnv(rv.env);
    return true;
  }

  /* ---------------------------------------------------------------- events */
  function resetForResults() {
    pause();
    R.steps = {}; R.errors = {}; R.loading = {};
    R.memo.st = null; R.refCache.clear();
    const r = results();
    if (!r) { R.sel = { key: null, step: -1, env: "step" }; notify(); return; }
    const cat = catalog();
    if (!R.sel.key || !cat.some(e => e.key === R.sel.key)) {
      const k = gC.keyFromStoreCase(r, S.caseName);
      R.sel = { key: k || (cat[0] && cat[0].key) || null, step: -1, env: "step" };
    }
    const e = entry();
    if (e && (e.kind === "modal" || e.kind === "buckling") && R.sel.step < 0) R.sel.step = 0;
    if (e && gC.needsStepData(e) && [...R.views.values()].some(v => v.opts.deformed || v.opts.diagram || v.opts.contour)) loadSteps();
    notify();
  }
  document.addEventListener("sky:results-changed", resetForResults);
  document.addEventListener("sky:model-changed", () => notify());
  document.addEventListener("change", ev => {
    if (syncingApp || !ev.target || ev.target.id !== "caseSelect") return;
    const k = gC.keyFromStoreCase(results(), ev.target.value);
    if (k && k !== R.sel.key) select(k, { quiet: true });
  }, true);
  if (gRU.onUnitsChange) gRU.onUnitsChange(() => notify());

  const api = {
    core: gC, defaults: G_DEFAULT_VIEW,
    state, selection: () => ({ ...R.sel, step: curStep() }), catalog, entry: k => entry(k || R.sel.key), axis,
    select, setStep, step: stepBy, setEnv, play, pause, toggle: () => (R.play.on ? (pause(), false) : play()),
    isPlaying: () => R.play.on, phase, cyclic,
    setSpeed: s => { R.play.speed = Math.max(0.05, Math.min(10, +s || 1)); notify(); },
    setCycles: c => { R.play.cycles = Math.max(0, Math.round(+c || 0)); notify(); },
    playState: () => ({ on: R.play.on, speed: R.play.speed, cycles: R.play.cycles, phase: R.play.phase }),
    loadSteps, stepData: name => (name ? R.steps[name] : stepDataOf(entry())) || null,
    loading: () => Object.keys(R.loading).length > 0, error: () => { const e = entry(); return e ? R.errors[e.name] || "" : ""; },
    setFrames: n => { R.frames = Math.max(1, Math.min(400, Math.round(+n || 40))); },
    deformedRef, diagramRef,
    registerView, viewIds, viewOpts, setViewOpts, activeView, view: id => R.views.get(id) || null,
    subscribe, redraw: () => notify(),
    captureView, applyView,
    _R: R,
  };
  const prevApi = sky.results;
  if (prevApi) for (const k of Object.keys(prevApi)) if (!(k in api)) api[k] = prevApi[k];
  sky.results = api;                       // sub-modules extend this same object

  // views, toolbar, member detail window, probes / select-by-result
  try { gResInitOverlay(sky, api); } catch (e) { console.error("results overlay init failed", e); }
  try { gResInitDetail(sky, api); } catch (e) { console.error("member detail init failed", e); }
  try { gResInitProbe(sky, api); } catch (e) { console.error("result probes init failed", e); }
  try { gResInitBar(sky, api); } catch (e) { console.error("results toolbar init failed", e); }
  if (results()) resetForResults();
  return sky.results;
}
