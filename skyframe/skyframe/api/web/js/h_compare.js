/* SkyFrame Wave H — View › Windows › Compare Side by Side… (beyond ETABS).

   Two linked 3D windows with ONE camera (orbit / pan / zoom together):

     Two cases / steps     any two results states of this run (case, combo,
                           RS, mode, TH / pushover / staged step, envelope)
     Two analysis runs     the same or different cases of an earlier run of
                           this session (Compare Results run list,
                           js/wc_compare.js) and the current run — a model
                           version WITH results; joints matched by tag / by
                           coordinates
     Model version         a Model History checkpoint (js/mhistory.js,
                           core/model_diff.py) against the current model:
                           checkpoint geometry left, current right

   plus an optional third DIFFERENCE window: B − A drawn by the Wave G layer —
   contours of the joint-displacement difference (on the deformed difference
   shape) or the frame-force difference diagram; for model versions the
   structural diff (added green / removed red / changed amber). Field
   differences come from js/h_compare_core.js, the exact mirror of
   core/compare_view.py; the live app also asks POST
   /api/results/compare/fields (Compare Results engine: family scales,
   flag count) and shows its summary in the difference window.
   "End Compare" returns to the previous window layout. */

import * as HC from "./h_compare_core.js";
import { wcRuns as hcRuns } from "./wc_compare.js";
import { el as hcEl, esc as hcEsc, dkForm as hcForm, dkGroup as hcGroup, dkDialog as hcDialog, dkNote as hcNote } from "./dlgkit.js";
import hcU from "./units.js";

const COL = { added: "#3fbf6a", removed: "#e05555", changed: "#e0a020" };
const DIFF_QTY = [["U", "Displacement |U| (contour)"], ["UX", "Displacement Ux (contour)"], ["UY", "Displacement Uy (contour)"], ["UZ", "Displacement Uz (contour)"],
  ["M3", "Moment M3 (diagram)"], ["M2", "Moment M2 (diagram)"], ["V2", "Shear V2 (diagram)"], ["V3", "Shear V3 (diagram)"], ["N", "Axial P (diagram)"], ["T", "Torsion T (diagram)"]];

export function initHCompare(sky, dock) {
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 3500) => sky.toast && sky.toast(t, m, k, ms);
  const R = () => sky.results;
  const st = { prev: null, on: false, spec: null, summary: null, diffMemo: null, ckptModels: new Map() };
  const D = { mode: "cases", a: "", b: "", stepA: 1, stepB: 1, runA: "", runB: "current", ckpt: "", diff: true, qty: "U", link: true, dir: "row", mhList: [] };

  /* ---------------------------------------------------------------- states */
  const label = key => { const e = R() && R().entry(key); return e ? e.label : key; };
  function runResults(id) { try { return id === "current" ? S.results : hcRuns.results(sky, id); } catch { return null; } }
  /** Display state of one compare side (keys of the CURRENT results' joints). */
  function sideState(side) {
    const api = R();
    if (!api || !side) return null;
    if (side.src === "case") return api.withView({ sel: side.sel }, () => api.state());
    if (side.src === "run") {
      const r = runResults(side.run);
      if (!r) return null;
      if (r === S.results) return api.withView({ sel: side.sel }, () => api.state());
      const memo = side._memo || (side._memo = {});
      if (memo.r === r && memo.cur === S.results && memo.key === side.sel.key && memo.step === side.sel.step) return memo.st;
      const raw = api.core.extractState(r, side.sel, null, {});
      const nmap = HC.hcNodeMap(S.results, r);
      Object.assign(memo, { r, cur: S.results, key: side.sel.key, step: side.sel.step, st: raw ? HC.hcRekey(raw, nmap) : null, nmap });
      return memo.st;
    }
    return null;
  }
  function diffState(spec) {
    const a = sideState(spec.a), b = sideState(spec.b);
    if (!a || !b) return null;
    const m = st.diffMemo;
    if (m && m.a === a && m.b === b) return m.st;
    const diff = st.server && st.server.key === keyOf(spec) && st.server.out ? st.server.out : HC.hcFieldDifference(a, b, null);
    const out = HC.hcDiffState(a, diff, spec.a.label, spec.b.label);
    st.diffMemo = { a, b, st: out, diff };
    st.summary = diff;
    paintSummary();
    return out;
  }
  const keyOf = spec => JSON.stringify([spec.a.src, spec.a.run || "", spec.a.sel && spec.a.sel.key, spec.a.sel && spec.a.sel.step, spec.b.src, spec.b.run || "", spec.b.sel && spec.b.sel.key, spec.b.sel && spec.b.sel.step]);

  /** Re-attach the live parts of a compare window after (re)building it. */
  function hydrate(w) {
    const c = w.cmp;
    if (!c || !st.spec) return;
    const api = R(), v = w.gView();
    if (c.role === "diff" && v) {
      if (st.spec.mode === "ckpt") { v.stateFn = null; installModelDiff(w); return; }
      v.sel = { ...st.spec.a.sel };
      v.stateFn = () => diffState(st.spec);
    } else if ((c.role === "a" || c.role === "b") && v) {
      const side = st.spec[c.role];
      if (side.src === "ckpt") {
        v.stateFn = () => null;
        const mdl = st.ckptModels.get(side.id);
        if (mdl && w.v && w.v.model !== mdl) { const cam = w.camera(); w.v.hFixedModel = mdl; w.v.setModel(mdl); w.v.setResults(null); w.setCamera(cam); }
        installModelDiff(w, side.id === "current" ? null : "a");
      } else if (side.src === "run" && runResults(side.run) !== S.results) { v.sel = { ...side.sel }; v.stateFn = () => sideState(side); }
      else { v.sel = { ...side.sel }; v.stateFn = null; }
    }
    if (api) api.redraw();
  }

  /* ---------------------------------------------------------------- model-version highlight */
  function installModelDiff(w, role) {
    if (!w.v) return;
    const items = (st.mdiff && st.mdiff.items || []).filter(it => it.geom && (role === "a" ? it.status !== "added" : role === "b" ? it.status !== "removed" : true));
    w.v.extraOverlay = (ctx, P, viewer) => {
      if (!items.length) return;
      const proj = p => { const c = P.toCam(p); return c[2] < P.near ? null : P.proj(c); };
      ctx.save();
      for (const it of items) {
        const g = it.geom, col = COL[it.status], ghost = it.status === "removed";
        ctx.strokeStyle = col; ctx.fillStyle = col; ctx.setLineDash(ghost ? [6, 4] : []);
        if (g.corners) {
          const pts = g.corners.map(proj); if (pts.some(x => !x)) continue;
          ctx.globalAlpha = ghost ? 0.12 : 0.28; ctx.beginPath(); pts.forEach((q, i) => (i ? ctx.lineTo(q.x, q.y) : ctx.moveTo(q.x, q.y))); ctx.closePath(); ctx.fill();
          ctx.globalAlpha = 0.9; ctx.lineWidth = 2; ctx.stroke();
        } else if (g.pi && g.pj) {
          const a = proj(g.pi), b = proj(g.pj); if (!a || !b) continue;
          ctx.globalAlpha = ghost ? 0.6 : 0.95; ctx.lineWidth = ghost ? 2.2 : 3.4; ctx.lineCap = "round";
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        } else if (g.point) {
          const q = proj(g.point); if (!q) continue;
          ctx.globalAlpha = 0.9; ctx.beginPath(); ctx.arc(q.x, q.y, 4, 0, Math.PI * 2); if (ghost) ctx.stroke(); else ctx.fill();
        }
      }
      ctx.restore();
      w.hDiffDrawn = (w.hDiffDrawn || 0) + 1;
    };
    w.v._dirty = true;
  }

  /* ---------------------------------------------------------------- summary chip (difference window) */
  function paintSummary() {
    const w = dock.wins().find(x => x.cmp && x.cmp.role === "diff");
    if (!w) return;
    let chip = w.head.querySelector(".h-cmp-sum");
    if (!chip) { chip = hcEl("span", { class: "h-cmp-sum", "data-h-cmp-sum": "1" }); w.head.insertBefore(chip, w.head.querySelector(".toolbar-spacer")); }
    const s = st.summary;
    if (st.spec && st.spec.mode === "ckpt") {
      const it = (st.mdiff && st.mdiff.items) || [];
      const n = k => it.filter(x => x.status === k).length;
      chip.innerHTML = `<b>Model diff</b> <i style="color:${COL.added}">+${n("added")}</i> <i style="color:${COL.removed}">−${n("removed")}</i> <i style="color:${COL.changed}">~${n("changed")}</i>`;
      return;
    }
    if (!s || !s.stats) { chip.textContent = "Δ —"; return; }
    const nd = s.stats.node_disp || {};
    const ms = (s.stats.member_stations || {})[st.spec.qty] || null;
    const parts = [];
    if (nd.max_trans != null) parts.push(`max |ΔU| ${hcU.fmt("disp", nd.max_trans, 3)} ${hcU.label("disp")}${nd.at_trans ? ` @ ${hcEsc(nd.at_trans)}` : ""}`);
    if (ms) parts.push(`max |Δ${st.spec.qty}| ${hcU.fmt(/^M|^T/.test(st.spec.qty) ? "moment" : "force", ms.max_abs, 2)}`);
    parts.push(`${s.n_flagged || 0} flagged`);
    chip.textContent = "Δ " + parts.join(" · ");
    chip.title = `B − A, flagged where |Δ| > rtol·S (Compare Results rule, rtol ${s.rtol})${st.serverUsed ? " — POST /api/results/compare/fields" : ""}`;
  }
  async function askServer(spec) {
    st.server = null; st.serverUsed = false;
    if (S.mock || spec.mode === "ckpt") return;
    const multi = side => { const e = R() && R().entry(side.sel.key); return !e || (e.multi && e.kind !== "modal"); };
    if (multi(spec.a) || multi(spec.b)) return;                         // steps / envelopes: the browser holds them (JS mirror)
    const ck = k => (k === "modal" ? null : k);
    const ka = spec.a.sel.key === "modal" ? `modal:${(spec.a.sel.step || 0) + 1}` : ck(spec.a.sel.key);
    const kb = spec.b.sel.key === "modal" ? `modal:${(spec.b.sel.step || 0) + 1}` : ck(spec.b.sel.key);
    if (!ka || !kb || /^(st|nl|th|po|bk):/.test(ka + kb)) return;
    const envOf = side => (side.sel.env === "min" ? "min" : "max");
    const ra = spec.a.src === "run" && spec.a.run !== "current" ? runResults(spec.a.run) : "current";
    const rb = spec.b.src === "run" && spec.b.run !== "current" ? runResults(spec.b.run) : "current";
    try {
      const res = await fetch("/api/results/compare/fields", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ a: ra, b: rb, case_a: ka, case_b: kb, env: envOf(spec.a) }) });
      const out = await res.json();
      if (!res.ok) throw new Error(out.error || res.statusText);
      if (st.spec !== spec) return;
      st.server = { key: keyOf(spec), out }; st.serverUsed = true; st.diffMemo = null;
      if (R()) R().redraw();
      paintSummary();
    } catch (e) { console.warn("compare fields", e.message); }
  }

  /* ---------------------------------------------------------------- start / end */
  async function start(o) {
    const api = R();
    if (!S.model) throw new Error("No model.");
    const mode = o.mode || "cases";
    const side = (k, step, extra = {}) => { const e = api && api.entry(k); return { src: "case", sel: { key: k, step: e && e.multi ? Math.max(0, (step || 1) - 1) : (e && (e.kind === "modal" || e.kind === "buckling") ? 0 : -1), env: "step" }, label: label(k) + (e && e.multi ? ` #${step || 1}` : ""), ...extra }; };
    let spec;
    if (mode === "cases" || mode === "runs") {
      if (!S.results || !api) throw new Error("Run the analysis first (or use Model version).");
      if (!api.entry(o.a) || !api.entry(o.b)) throw new Error("Pick a case for A and for B.");
      const A = side(o.a, o.stepA), B = side(o.b, o.stepB);
      if (mode === "runs") {
        for (const [s, run] of [[A, o.runA], [B, o.runB]]) {
          const r = runResults(run);
          if (!r) throw new Error("Pick two analysis runs (earlier runs of this session are kept by Tools › Compare Results).");
          if (r !== S.results && !api.core.caseCatalog(r).some(e => e.key === s.sel.key)) throw new Error(`${label(s.sel.key)} is not in ${run}.`);
          if (r !== S.results && api.entry(s.sel.key).multi && api.entry(s.sel.key).kind !== "modal") throw new Error("Earlier runs compare case / combo / RS states and modes (steps need the current run).");
          Object.assign(s, { src: "run", run, label: `${s.label} (${run === "current" ? "current run" : run})` });
        }
      }
      spec = { mode, a: A, b: B, qty: o.qty || "U", diff: o.diff !== false, link: o.link !== false, dir: o.dir || "row" };
    } else {
      if (!sky.mhistory) throw new Error("Model History is not available.");
      if (!o.ckpt) throw new Error("Pick a checkpoint.");
      const g = await sky.mhistory.transport.get(o.ckpt);
      const mdl = g && g.model;
      if (!mdl) throw new Error("Checkpoint not found.");
      st.ckptModels.set(o.ckpt, mdl);
      st.mdiff = await sky.mhistory.compare(o.ckpt, "current");
      spec = { mode, a: { src: "ckpt", id: o.ckpt, label: `Checkpoint ${(g.checkpoint && (g.checkpoint.name || g.checkpoint.id)) || o.ckpt}` }, b: { src: "ckpt", id: "current", label: "Current model" },
        diff: o.diff !== false, link: o.link !== false, dir: o.dir || "row", qty: "U" };
    }
    if (!st.on) st.prev = dock.capture();
    st.spec = spec; st.on = true; st.diffMemo = null; st.summary = null;
    // the compare layout: A | B (| difference), 3D windows, one camera
    const ref = dock.active() && dock.active().v ? dock.active().camera() : (sky.viewer ? { yaw: sky.viewer.yaw, pitch: sky.viewer.pitch, dist: sky.viewer.dist, target: sky.viewer.target.slice() } : null);
    const L = { v: 1, root: null, wins: {}, active: "w1", next: 1, links: [], popped: {} };
    const mk = (role, title) => { const id = "w" + L.next++; L.wins[id] = { id, kind: "3d", title, cmp: { role, mode }, camera: ref || undefined }; return id; };
    const ia = mk("a", "A · " + spec.a.label), ib = mk("b", "B · " + spec.b.label);
    const ids = [ia, ib];
    if (spec.diff) ids.push(mk("diff", mode === "ckpt" ? "Model diff (A → B)" : `Δ B − A`));
    L.root = { t: "split", dir: spec.dir === "col" ? "col" : "row", sizes: ids.map(() => 1 / ids.length), kids: ids.map(id => ({ t: "tabs", wins: [id], cur: id })) };
    if (spec.link) L.links = [ids.slice()];
    L.active = ia;
    dock.restore(L);
    // window results displays
    const api2 = R();
    for (const w of dock.wins()) {
      if (!w.cmp) continue;
      const v = w.gView();
      if (v && mode !== "ckpt") {
        const isDisp = /^U/.test(spec.qty);
        const patch = isDisp ? { deformed: true, contour: spec.qty, onDeformed: true, diagram: "" } : { diagram: spec.qty, values: "fill", deformed: false, contour: "" };
        Object.assign(v.opts, patch);
      }
      if (v && mode === "ckpt") Object.assign(v.opts, { deformed: false, contour: "", diagram: "" });
      hydrate(w);
      w.syncResBox();
    }
    if (api2) api2.redraw();
    askServer(spec);
    setTimeout(paintSummary, 50);
    document.dispatchEvent(new CustomEvent("sky:h-compare", { detail: { on: true, mode } }));
    return dock.wins().map(w => w.id);
  }
  function end() {
    if (!st.on) return false;
    st.on = false; st.spec = null; st.summary = null; st.diffMemo = null; st.server = null;
    dock.restore(st.prev || null);
    st.prev = null;
    document.dispatchEvent(new CustomEvent("sky:h-compare", { detail: { on: false } }));
    return true;
  }

  /* ---------------------------------------------------------------- dialog */
  async function open() {
    const api = R();
    const cat = api && S.results ? api.catalog() : [];
    if (!D.a && cat[0]) D.a = cat[0].key;
    if (!D.b && cat[1]) D.b = cat[1].key; else if (!D.b && cat[0]) D.b = cat[0].key;
    let runs = [];
    try { runs = hcRuns.options(sky); } catch { runs = []; }
    try { const l = sky.mhistory ? await sky.mhistory.list() : null; D.mhList = ((l && l.checkpoints) || []).map(c => [c.id, `${c.name || c.reason || c.kind || "checkpoint"} · ${c.created ? new Date(c.created * 1000).toLocaleString() : c.id}`]); } catch { D.mhList = []; }
    if (!D.ckpt && D.mhList[0]) D.ckpt = D.mhList[0][0];
    if (!D.runA) D.runA = (runs.find(r => r[0] !== "current") || runs[0] || [""])[0];
    const F = hcForm();
    const caseOpts = () => cat.map(e => [e.key, e.label]);
    const multi = k => { const e = api && api.entry(k); return !!(e && e.multi); };
    const modeRow = F.radio(D, "mode", [["cases", "Two cases / steps of this run"], ["runs", "Two analysis runs (earlier run vs current)"], ["ckpt", "Model version: checkpoint vs current model"]],
      { label: "Compare", onChange: () => sync() });
    const gA = hcGroup("Window A", [F.select(D, "a", caseOpts, { label: "Case / combo", id: "hCmpA", onChange: () => sync() }),
      F.num(D, "stepA", { label: "Step / mode", id: "hCmpStepA", min: 1, max: 100000, step: 1, kind: "none", hint: "multi-step cases: TH, pushover, staged, modes" }),
      F.select(D, "runA", () => runs, { label: "Run", id: "hCmpRunA" })]);
    const gB = hcGroup("Window B", [F.select(D, "b", caseOpts, { label: "Case / combo", id: "hCmpB", onChange: () => sync() }),
      F.num(D, "stepB", { label: "Step / mode", id: "hCmpStepB", min: 1, max: 100000, step: 1, kind: "none" }),
      F.select(D, "runB", () => runs, { label: "Run", id: "hCmpRunB" })]);
    const gK = hcGroup("Model version", [F.select(D, "ckpt", () => (D.mhList.length ? D.mhList : [["", "— no checkpoints (Tools › Model History) —"]]), { label: "Checkpoint (A)", id: "hCmpCkpt" }),
      hcNote("Window A shows the checkpoint geometry, window B the current model; the difference window colours added / removed / changed objects (core/model_diff.py).")]);
    const gD = hcGroup("Windows", [F.check(D, "diff", { label: "Difference window (B − A)", id: "hCmpDiff" }),
      F.select(D, "qty", DIFF_QTY, { label: "Difference shows", id: "hCmpQty" }),
      F.check(D, "link", { label: "Link cameras (orbit / pan / zoom together)", id: "hCmpLink" }),
      F.select(D, "dir", [["row", "Side by side"], ["col", "Stacked"]], { label: "Arrangement", id: "hCmpDir" })]);
    const endB = hcEl("button", { type: "button", class: "btn", id: "hCmpEnd", disabled: st.on ? null : true, title: "Close the compare windows and restore the previous layout" }, ["End Compare"]);
    endB.addEventListener("click", () => { end(); d.close(); });
    function sync() {
      const m = D.mode;
      gA.hidden = gB.hidden = m === "ckpt";
      gK.hidden = m !== "ckpt";
      for (const [g, k] of [[gA, "a"], [gB, "b"]]) {
        const stepRow = g.querySelector(`#hCmpStep${k.toUpperCase()}`); if (stepRow) stepRow.closest(".dk-row").hidden = !multi(D[k]);
        const runRow = g.querySelector(`#hCmpRun${k.toUpperCase()}`); if (runRow) runRow.closest(".dk-row").hidden = m !== "runs";
      }
      const q = gD.querySelector("#hCmpQty"); if (q) q.closest(".dk-row").hidden = m === "ckpt";
    }
    const d = hcDialog({ id: "hCompareDlg", title: "Compare Side by Side", iconId: "menu-view", size: "md", form: F, extra: [endB],
      intro: "Two windows with one camera showing two results states or two model versions, plus an optional difference window (beyond ETABS).",
      body: [modeRow, gA, gB, gK, gD],
      onOk: async () => {
        try { await start({ ...D }); toast("Compare side by side", "Windows linked — orbit any of them; View › Windows › Compare Side by Side… › End Compare restores the layout.", "info", 4000); return true; }
        catch (e) { return e.message; }
      } });
    sync();
    return d;
  }

  const api = { open, start, end, active: () => st.on, spec: () => st.spec, summary: () => st.summary, hydrate, sideState, diffState: () => (st.spec ? diffState(st.spec) : null), state: st };
  sky.hCompare = api;
  return api;
}
