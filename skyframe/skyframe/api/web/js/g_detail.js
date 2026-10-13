/* SkyFrame Wave G — member detail window (ETABS "Diagrams for Frame Object").

   A floating, draggable window opened by sky.results.openDetail(obj) — the
   right-click "Show results…" of the selection / context menus, the Member
   panel's "Detail…" button, the results toolbar (Member…) and Display ›
   Force Diagrams › Diagrams for Frame Object… (dis-g-detail).

   Frame object: case / step / envelope selectors (follow the results
   toolbar or pick independently), panels for the equivalent applied loads
   (from the station shears — every case and step) with the listed member
   loads, V2 + M3, V3 + M2, P + T and the deflection (absolute or relative,
   cubic from the joint displacements; the exact recovered deflection of a
   static case when available). Scroll for Values: move the cursor along any
   panel or type a location — values are exact at the output stations and
   linear between them (ETABS); Show Max marks the extreme of every panel;
   📌 pins a value to the watch list. Max / min pairs draw both curves.

   Shell objects: the region's mesh; pick a point (element) to read F11 …
   V23 and the top / bottom stresses (S = F/t ± 6M/t², the SkyFrame shell
   convention); piers / spandrels: per-story forces; links: forces and
   deformations. Values through js/units.js; nothing in the model changes. */

import gDU from "./units.js";
import * as gD from "./g_core.js";
import { el as gdEl, esc as gdEsc } from "./dlgkit.js";
import { frameLocalAxes as gDFrameAxes } from "./frameaxes.js";

const CSS = `
.g-detail { position: absolute; top: 56px; right: 14px; z-index: 37; width: 520px; max-width: calc(100% - 28px); max-height: calc(100% - 140px);
  display: flex; flex-direction: column; background: rgba(17,22,29,0.97); border: 1px solid var(--border-soft, #2a3340); border-radius: 8px;
  box-shadow: 0 10px 28px rgba(0,0,0,.45); color: var(--text-2, #c8d0da); font-size: 11.5px; resize: both; overflow: hidden; }
.g-detail.hidden { display: none; }
.g-detail header { display: flex; align-items: center; gap: 8px; padding: 6px 10px; cursor: move; border-bottom: 1px solid var(--border-soft, #2a3340); user-select: none; }
.g-detail header h3 { margin: 0; font-size: 12.5px; color: var(--text-1, #e8edf3); font-weight: 650; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.g-detail header button, .g-detail .g-d-row button { background: var(--bg-2, #141a22); color: var(--text-1, #e8edf3); border: 1px solid var(--border-soft, #2a3340); border-radius: 5px; height: 22px; padding: 0 7px; cursor: pointer; font-size: 11px; }
.g-detail .g-d-body { overflow: auto; padding: 8px 10px 10px; }
.g-detail .g-d-row { display: flex; flex-wrap: wrap; align-items: center; gap: 5px 10px; margin-bottom: 6px; }
.g-detail select, .g-detail input[type=text] { background: var(--bg-2, #141a22); color: var(--text-1, #e8edf3); border: 1px solid var(--border-soft, #2a3340); border-radius: 5px; height: 22px; line-height: 20px; padding: 0 4px; font-size: 11px; max-width: 200px; }
.g-detail input[type=text] { width: 70px; padding: 0 4px; }
.g-detail .g-d-meta { color: var(--text-3, #99a3b0); margin-bottom: 6px; }
.g-detail .g-d-tabs { display: inline-flex; }
.g-detail .g-d-tabs button { border-radius: 0; margin-left: -1px; }
.g-detail .g-d-tabs button.is-on { background: rgba(53,181,229,.2); border-color: var(--accent, #35b5e5); }
.g-detail canvas { display: block; width: 100%; height: 92px; margin: 2px 0 6px; background: rgba(8,12,18,.6); border-radius: 4px; cursor: crosshair; }
.g-detail .g-d-ct { display: flex; justify-content: space-between; color: var(--text-1, #e8edf3); font-weight: 600; margin-top: 4px; }
.g-detail table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; margin-top: 4px; }
.g-detail td, .g-detail th { padding: 2px 6px; border-bottom: 1px solid rgba(255,255,255,.05); text-align: right; }
.g-detail th { color: var(--text-3, #99a3b0); font-weight: 600; }
.g-detail td:first-child, .g-detail th:first-child { text-align: left; }
.g-detail .g-pin { background: none; border: none; cursor: pointer; padding: 0 2px; font-size: 11px; }
.g-detail .g-d-note { color: var(--warn, #e0b050); margin: 4px 0; }
.g-detail svg.g-d-mesh { width: 100%; height: 170px; background: rgba(8,12,18,.6); border-radius: 4px; cursor: crosshair; }
`;

const PAIRS = { major: [["V2", "Shear V2"], ["M3", "Moment M3"]], minor: [["V3", "Shear V3"], ["M2", "Moment M2"]], axial: [["N", "Axial P"], ["T", "Torsion T"]] };
const KIND = gD.COMP_KIND;

export function gInitDetail(sky, api) {
  const S = sky.store;
  if (!document.getElementById("gDetailCss")) document.head.appendChild(gdEl("style", { id: "gDetailCss", text: CSS }));
  const host = document.getElementById("etabsCanvas") || document.body;
  const win = gdEl("section", { class: "g-detail hidden", id: "gDetail", role: "dialog", "aria-label": "Diagrams for Frame Object" });
  const title = gdEl("h3", { id: "gDetailTitle", text: "Diagrams for Frame Object" });
  const closeB = gdEl("button", { type: "button", id: "gDetailClose", title: "Close", "aria-label": "Close member detail" }, ["×"]);
  const body = gdEl("div", { class: "g-d-body", id: "gDetailBody" });
  win.append(gdEl("header", {}, [title, closeB]), body);
  host.appendChild(win);
  closeB.addEventListener("click", () => close());
  // drag by the header
  (() => {
    const head = win.querySelector("header");
    let d = null;
    head.addEventListener("pointerdown", e => {
      if (e.target.closest("button")) return;
      const r = win.getBoundingClientRect(), hr = host.getBoundingClientRect();
      d = { dx: e.clientX - r.left, dy: e.clientY - r.top, hr };
      head.setPointerCapture(e.pointerId);
    });
    head.addEventListener("pointermove", e => {
      if (!d) return;
      win.style.left = Math.max(0, e.clientX - d.hr.left - d.dx) + "px";
      win.style.top = Math.max(0, e.clientY - d.hr.top - d.dy) + "px";
      win.style.right = "auto";
    });
    head.addEventListener("pointerup", () => { d = null; });
  })();

  const D = { obj: null, follow: true, sel: null, tab: "major", rel: false, showMax: true, x: null, stateMemo: null, pick: null };

  /* ---------------------------------------------------------------- object resolution */
  function resolve(obj) {
    const m = S.model || {};
    const mem = uid => (m.members || []).find(x => x.uid === uid);
    if (obj == null) {
      const uid = S.selectedMemberUid || ((S.selection || []).find(r => r.type === "member") || {}).uid;
      if (uid && mem(uid)) return { type: "member", uid };
      const sh = (S.selection || []).find(r => r.type === "shell");
      if (sh) return { type: "shell", uid: sh.uid };
      return (m.members || [])[0] ? { type: "member", uid: m.members[0].uid } : null;
    }
    if (typeof obj === "string") return mem(obj) ? { type: "member", uid: obj } : (m.shells || []).some(s => s.uid === obj) ? { type: "shell", uid: obj } : (m.links || []).some(l => l.uid === obj) ? { type: "link", uid: obj } : null;
    const t = String(obj.type || obj.kind || "").toLowerCase();
    const uid = obj.uid || obj.label || obj.name;
    if (["member", "frame", "beam", "column", "brace"].includes(t) || (!t && uid && mem(uid))) return mem(uid) ? { type: "member", uid } : null;
    if (["shell", "area", "wall", "slab", "floor"].includes(t)) return { type: "shell", uid };
    if (t === "pier") return { type: "pier", label: uid };
    if (t === "spandrel") return { type: "spandrel", label: uid };
    if (t === "link" || t === "spring") return { type: "link", uid };
    if (t === "joint" || t === "point" || t === "node") return { type: "joint", point: obj.point || (typeof uid === "string" ? uid.split(",").map(Number) : null) };
    if (uid && mem(uid)) return { type: "member", uid };
    return null;
  }
  function open(obj) {
    if (!S.results) { sky.toast && sky.toast("Member detail", "Run the analysis first (F5).", "info", 3000); return null; }
    const o = resolve(obj);
    if (!o) { sky.toast && sky.toast("Member detail", "Select a frame, shell, pier or link first.", "info", 3000); return null; }
    D.obj = o; D.x = null; D.pick = null;
    if (!D.sel) D.sel = { ...api.selection() };
    const watch = document.getElementById("gWatch");
    if (!win.style.left) win.style.right = watch && !watch.classList.contains("hidden") ? "356px" : "14px";   // beside the docked watch list
    win.classList.remove("hidden");
    render();
    return api._detail;
  }
  function close() { win.classList.add("hidden"); D.obj = null; }

  /* ---------------------------------------------------------------- state */
  function curState() {
    if (D.follow) return api.state();
    const sd = api.stepData(D.sel && api.entry(D.sel.key) ? api.entry(D.sel.key).name : null);
    const k = [S.results, D.sel.key, D.sel.step, D.sel.env, sd];
    if (D.stateMemo && k.every((x, i) => x === D.stateMemo.k[i])) return D.stateMemo.st;
    const st = gD.extractState(S.results, D.sel, sd, { fmt: (kind, v) => `${gDU.fmt(kind, v, 2)} ${gDU.label(kind)}`, thFrames: (r, n) => (sky.wcVideo && sky.wcVideo.thFrames ? sky.wcVideo.thFrames(r, n) : null) });
    D.stateMemo = { k, st };
    return st;
  }

  /* ---------------------------------------------------------------- chart */
  function chart(canvas, xs, series, kind, { L, cursorX, showMax, zeroLine = true, bars = null }) {
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.clientWidth || 480, H = canvas.clientHeight || 92;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const pad = 14, top = 12, bot = 12;
    const X = x => pad + (x / (L || 1)) * (W - 2 * pad);
    let vmax = 0;
    for (const s of series) for (const v of s.vals || []) vmax = Math.max(vmax, Math.abs(v));
    if (bars) for (const b of bars) vmax = Math.max(vmax, Math.abs(b.w != null ? b.w : b.P));
    vmax = vmax || 1;
    const Y = v => top + (H - top - bot) / 2 - v / vmax * (H - top - bot) / 2;
    if (zeroLine) { ctx.strokeStyle = "rgba(154,167,180,.55)"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(pad, Y(0)); ctx.lineTo(W - pad, Y(0)); ctx.stroke(); }
    if (bars) {
      for (const b of bars) {
        if (b.kind === "dist") {
          ctx.fillStyle = b.w >= 0 ? "rgba(53,181,229,.35)" : "rgba(229,103,103,.35)";
          ctx.fillRect(X(b.x0), Math.min(Y(0), Y(b.w)), Math.max(1, X(b.x1) - X(b.x0)), Math.abs(Y(b.w) - Y(0)));
        } else {
          ctx.strokeStyle = "rgba(229,165,10,.95)"; ctx.lineWidth = 2;
          ctx.beginPath(); ctx.moveTo(X(b.x), Y(0)); ctx.lineTo(X(b.x), Y(b.P)); ctx.stroke();
        }
      }
    }
    series.forEach((s, si) => {
      if (!s.vals) return;
      if (si === 0 && !s.noFill) {
        ctx.beginPath(); ctx.moveTo(X(xs[0]), Y(0));
        xs.forEach((x, i) => ctx.lineTo(X(x), Y(s.vals[i])));
        ctx.lineTo(X(xs[xs.length - 1]), Y(0)); ctx.closePath();
        ctx.fillStyle = "rgba(53,181,229,.22)"; ctx.fill();
      }
      ctx.setLineDash(s.dashed ? [5, 4] : []);
      ctx.strokeStyle = s.color || "rgba(53,181,229,.95)"; ctx.lineWidth = 1.5;
      ctx.beginPath(); xs.forEach((x, i) => (i ? ctx.lineTo(X(x), Y(s.vals[i])) : ctx.moveTo(X(x), Y(s.vals[i])))); ctx.stroke();
      ctx.setLineDash([]);
      if (showMax) {
        const mx = gD.maxOf(xs, s.vals);
        if (mx) {
          ctx.fillStyle = s.color || "#35b5e5";
          ctx.beginPath(); ctx.arc(X(mx.x), Y(mx.v), 3.2, 0, Math.PI * 2); ctx.fill();
          ctx.font = "600 10px -apple-system, 'Segoe UI', sans-serif"; ctx.textAlign = X(mx.x) > W - 80 ? "right" : "left"; ctx.textBaseline = mx.v >= 0 ? "bottom" : "top";
          ctx.fillStyle = "rgba(232,237,243,.95)";
          ctx.fillText(`${s.dashed ? "min " : "max "}${gDU.fmt(kind, mx.v, 2)} @ ${gDU.fmt("length", mx.x, 2)}`, X(mx.x) + (ctx.textAlign === "left" ? 5 : -5), Y(mx.v) + (mx.v >= 0 ? -2 : 2));
        }
      }
    });
    if (cursorX != null) {
      ctx.strokeStyle = "rgba(240,190,80,.95)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(X(cursorX), 2); ctx.lineTo(X(cursorX), H - 2); ctx.stroke();
    }
    canvas._toX = px => Math.max(0, Math.min(L, (px - pad) / (W - 2 * pad) * L));
  }

  /* ---------------------------------------------------------------- frame */
  function deflectionOf(mm, st) {
    const r = S.results;
    const rm = (r.members || []).find(x => x.uid === mm.uid);
    const md = st.member_deflections && st.member_deflections[mm.uid];
    let axes;
    try { const a = gDFrameAxes(mm, S.model); axes = { x: a.x, y: a.y, z: a.z }; } catch { axes = gD.defaultAxes(mm.pi, mm.pj); }
    if (md && Array.isArray(md.x) && Array.isArray(md.dy)) return { x: md.x, d2: md.dy, d3: md.dz || md.x.map(() => 0), d1: md.x.map(() => 0), exact: true };
    if (!rm || !st.node_disp) return null;
    const Pi = r.nodes[rm.ni], Pj = r.nodes[rm.nj], di = st.node_disp[rm.ni], dj = st.node_disp[rm.nj];
    if (!Pi || !Pj || !di || !dj) return null;
    return { ...gD.memberDeflection(Pi, Pj, di, dj, axes, 21), exact: false };
  }
  function memberLoadsText(mm, st) {
    const m = S.model, e = api.entry(D.follow ? null : D.sel.key) || api.entry();
    if (!e) return "";
    const pats = {};
    if (e.kind === "static") Object.assign(pats, ((m.cases || {})[e.name] || {}).patterns || {});
    else if (e.kind === "combo" || e.kind === "minmax") for (const [cn, f] of Object.entries(((m.combos || {})[e.name] || {}).cases || {})) for (const [p, g] of Object.entries(((m.cases || {})[cn] || {}).patterns || {})) pats[p] = (pats[p] || 0) + f * g;
    const rows = [];
    for (const [p, f] of Object.entries(pats)) {
      const pat = (m.patterns || {})[p];
      if (!pat) continue;
      for (const u of pat.member_udls || []) if (u.member_uid === mm.uid) rows.push(`${p}×${+f}: uniform ${gDU.fmtU("line_force", u.w * f, 2)} (gravity)`);
      for (const l of pat.member_loads || []) if (l.member_uid === mm.uid) rows.push(`${p}×${+f}: ${l.kind} ${l.kind === "point" ? gDU.fmtU("force", l.w * f, 2) : gDU.fmtU("line_force", l.w * f, 2)} (${l.direction || "gravity"})`);
    }
    return rows.join(" · ");
  }
  function renderFrame() {
    const m = S.model, r = S.results, mm = (m.members || []).find(x => x.uid === D.obj.uid);
    if (!mm) { body.textContent = "Member not found."; return; }
    const st = curState();
    const L = Math.hypot(mm.pj[0] - mm.pi[0], mm.pj[1] - mm.pi[1], mm.pj[2] - mm.pi[2]);
    title.textContent = `Diagrams for Frame Object — ${mm.uid}`;
    body.textContent = "";
    // selectors
    const memSel = gdEl("select", { id: "gDetailMember", "aria-label": "Frame object" }, (m.members || []).map(x => gdEl("option", { value: x.uid, selected: x.uid === mm.uid || null }, [x.uid])));
    memSel.addEventListener("change", () => { D.obj = { type: "member", uid: memSel.value }; D.x = null; render(); });
    body.appendChild(gdEl("div", { class: "g-d-row" }, ["Object", memSel, ...caseControls()]));
    body.appendChild(gdEl("div", { class: "g-d-meta", text: `${mm.kind} · ${mm.section} · ${mm.story || ""} · L = ${gDU.fmtU("length", L, 3)}${st ? " · " + st.label + (st.stepLabel ? " · " + st.stepLabel : "") : ""}` }));
    if (!st) { body.appendChild(gdEl("div", { class: "g-d-note", text: "No results for this selection." })); return; }
    if (st.note) body.appendChild(gdEl("div", { class: "g-d-note", text: st.note }));
    const s = (st.member_stations || {})[mm.uid];
    const smin = st.member_stations_min ? st.member_stations_min[mm.uid] : null;
    // tabs + options
    const tabs = gdEl("span", { class: "g-d-tabs" }, [["major", "Major (V2 / M3)"], ["minor", "Minor (V3 / M2)"], ["axial", "Axial / Torsion (P / T)"]].map(([k, t]) => {
      const b = gdEl("button", { type: "button", "data-tab": k, class: D.tab === k ? "is-on" : "" }, [t]);
      b.addEventListener("click", () => { D.tab = k; render(); });
      return b;
    }));
    const maxCb = gdEl("input", { type: "checkbox", id: "gDetailShowMax" }); maxCb.checked = D.showMax;
    maxCb.addEventListener("change", () => { D.showMax = maxCb.checked; render(); });
    const relSel = gdEl("select", { id: "gDetailDefl", "aria-label": "Deflection" }, [["abs", "Absolute"], ["rel", "Relative"]].map(([v, t]) => gdEl("option", { value: v, selected: (D.rel ? "rel" : "abs") === v || null }, [t])));
    relSel.addEventListener("change", () => { D.rel = relSel.value === "rel"; render(); });
    const loc = gdEl("input", { type: "text", id: "gDetailLoc", value: D.x == null ? "" : gDU.inputValue("length", D.x), title: "Location from end I (Scroll for Values)" });
    loc.addEventListener("change", () => { const v = gDU.parse ? gDU.parse("length", loc.value) : gDU.fromDisplay("length", +loc.value); if (isFinite(v)) { D.x = Math.max(0, Math.min(L, v)); render(); } });
    body.appendChild(gdEl("div", { class: "g-d-row" }, [tabs, gdEl("label", {}, [maxCb, "Show Max"]), gdEl("label", {}, ["Deflection", relSel]),
      gdEl("label", {}, ["Location", loc, gdEl("span", { text: gDU.label("length") })])]));
    if (!s) { body.appendChild(gdEl("div", { class: "g-d-note", text: "No station forces for this member in this state (load the step data on the results toolbar)." })); }
    const xs = s ? s.x : [0, L];
    const canvases = [];
    const addChart = (titleTxt, kind, series, opt = {}) => {
      const cv = gdEl("canvas", { "data-chart": titleTxt });
      const valAt = D.x != null && series[0] && series[0].vals ? gD.valueAt(opt.xs || xs, series[0].vals, D.x) : null;
      body.appendChild(gdEl("div", { class: "g-d-ct" }, [gdEl("span", { text: `${titleTxt} (${gDU.label(kind)})` }), gdEl("span", { class: "g-d-at", text: valAt == null ? "" : `@ ${gDU.fmt("length", D.x, 3)}: ${gDU.fmt(kind, valAt, 3)}` })]));
      body.appendChild(cv);
      canvases.push([cv, () => chart(cv, opt.xs || xs, series, kind, { L, cursorX: D.x, showMax: D.showMax, bars: opt.bars || null })]);
    };
    // equivalent loads (from shear) + listed loads
    if (s) {
      const vk = D.tab === "minor" ? "V3" : "V2";
      const eq = gD.equivalentLoads(s.x, s[vk]);
      addChart(`Equivalent loads (local ${vk === "V2" ? 2 : 3}, from ${vk})`, "line_force", [], { bars: eq });
      const lt = memberLoadsText(mm, st);
      if (lt) body.appendChild(gdEl("div", { class: "g-d-meta", text: "Applied: " + lt }));
      for (const [k, t] of PAIRS[D.tab]) addChart(t, KIND[k], [{ vals: s[k] }, ...(smin ? [{ vals: smin[k], dashed: true, color: "rgba(229,165,10,.95)", noFill: true }] : [])]);
    }
    const df = deflectionOf(mm, st);
    if (df) {
      const dv = D.tab === "minor" ? df.d3 : D.tab === "axial" ? df.d1 : df.d2;
      const vv = D.rel ? gD.relativeDeflection(df.x, dv) : dv;
      addChart(`Deflection ${D.tab === "minor" ? "local 3" : D.tab === "axial" ? "axial" : "local 2"} (${D.rel ? "relative" : "absolute"}${df.exact ? "" : ", cubic"})`, "disp", [{ vals: vv, color: "rgba(229,165,10,.95)" }], { xs: df.x });
      D.defl = { x: df.x, v: vv };
    } else D.defl = null;
    // readout table at the cursor (all components)
    const tbl = gdEl("table", { id: "gDetailReadout" });
    tbl.appendChild(gdEl("tr", {}, [gdEl("th", { text: "Component" }), gdEl("th", { text: D.x == null ? "max |value|" : `at ${gDU.fmt("length", D.x, 3)} ${gDU.label("length")}` }), gdEl("th", { text: "unit" }), gdEl("th", { text: "" })]));
    if (s) for (const k of gD.ST_KEYS) {
      const v = D.x == null ? (gD.maxOf(s.x, s[k]) || {}).v : gD.valueAt(s.x, s[k], D.x);
      const pin = gdEl("button", { type: "button", class: "g-pin", title: "Pin to the watch list", "data-pin": k }, ["📌"]);
      pin.addEventListener("click", () => api.pin && api.pin(D.x == null ? { type: "member_max", uid: mm.uid, comp: k } : { type: "member_at", uid: mm.uid, comp: k, x: D.x }));
      tbl.appendChild(gdEl("tr", { "data-comp": k }, [gdEl("td", { text: gD.COMP_LABEL[k] }), gdEl("td", { class: "g-d-val", text: v == null ? "—" : gDU.fmt(KIND[k], v, 3) }), gdEl("td", { text: gDU.label(KIND[k]) }), gdEl("td", {}, [pin])]));
    }
    if (D.defl && D.x != null) tbl.appendChild(gdEl("tr", { "data-comp": "defl" }, [gdEl("td", { text: "Deflection" }), gdEl("td", { class: "g-d-val", text: gDU.fmt("disp", gD.valueAt(D.defl.x, D.defl.v, D.x), 3) }), gdEl("td", { text: gDU.label("disp") }), gdEl("td")]));
    body.appendChild(tbl);
    requestAnimationFrame(() => {
      for (const [cv, draw] of canvases) {
        draw();
        const onMove = e => { const rc = cv.getBoundingClientRect(); if (!cv._toX) return; D.x = cv._toX(e.clientX - rc.left); updateCursor(); };
        cv.addEventListener("pointermove", onMove);
        cv.addEventListener("click", onMove);
      }
    });
    D.updateCursor = () => { for (const [, draw] of canvases) draw(); };
  }
  let cursorRaf = 0;
  function updateCursor() {
    if (cursorRaf) return;
    cursorRaf = requestAnimationFrame(() => { cursorRaf = 0; render(); });
  }

  function caseControls() {
    const follow = gdEl("input", { type: "checkbox", id: "gDetailFollow" }); follow.checked = D.follow;
    follow.addEventListener("change", () => { D.follow = follow.checked; if (!D.follow) D.sel = { ...api.selection() }; render(); });
    const out = [gdEl("label", { title: "Use the case / step of the results toolbar" }, [follow, "Toolbar case"])];
    if (D.follow) return out;
    const cat = api.catalog();
    const cs = gdEl("select", { id: "gDetailCase", "aria-label": "Case" }, cat.map(e => gdEl("option", { value: e.key, selected: e.key === D.sel.key || null }, [e.label])));
    cs.addEventListener("change", async () => {
      D.sel = { key: cs.value, step: -1, env: "step" };
      const e = api.entry(cs.value);
      if (e && api.core.needsStepData(e) && !api.stepData(e.name)) { const cur = api.selection().key; api.select(cs.value, { quiet: true }); await api.loadSteps(); if (cur) api.select(cur, { quiet: true }); }
      render();
    });
    out.push(cs);
    const st = curState();
    if (st && st.axis && st.axis.n > 1) {
      const ss = gdEl("select", { id: "gDetailStep", "aria-label": "Step" }, st.axis.labels.map((l, i) => gdEl("option", { value: String(i), selected: i === st.stepIndex || null }, [l])));
      ss.addEventListener("change", () => { D.sel.step = +ss.value; D.sel.env = "step"; render(); });
      out.push(ss);
    }
    if (st && st.envAvailable) {
      const es = gdEl("select", { id: "gDetailEnv" }, [["step", "Step"], ["max", "Max"], ["min", "Min"], ["maxmin", "Max/Min"]].map(([v, t]) => gdEl("option", { value: v, selected: D.sel.env === v || null }, [t])));
      es.addEventListener("change", () => { D.sel.env = es.value; render(); });
      out.push(es);
    }
    return out;
  }

  /* ---------------------------------------------------------------- shell */
  function renderShell() {
    const m = S.model, r = S.results, sh = (m.shells || []).find(x => x.uid === D.obj.uid);
    title.textContent = `Shell Results — ${D.obj.uid}`;
    body.textContent = "";
    if (!sh) { body.textContent = "Shell not found."; return; }
    const st = curState();
    body.appendChild(gdEl("div", { class: "g-d-row" }, ["Object", gdEl("b", { text: sh.uid }), ...caseControls()]));
    const t = gD.shellThickness(m, sh.uid);
    body.appendChild(gdEl("div", { class: "g-d-meta", text: `${sh.kind} · ${sh.section || ""} · t = ${t ? gDU.fmtU("dim", t, 3) : "—"}${st ? " · " + st.label + (st.stepLabel ? " · " + st.stepLabel : "") : ""}` }));
    const quads = [];
    (r.shell_quads || []).forEach((q, i) => { if (q.region === sh.uid) quads.push([i, q]); });
    if (!quads.length) { body.appendChild(gdEl("div", { class: "g-d-note", text: "No meshed elements for this region (membrane slab or not meshed)." })); return; }
    // plane frame of the region
    const P = q => q.nodes.map(tg => r.nodes[tg]);
    const c0 = sh.corners;
    const e1 = gD.V3.unit(gD.V3.sub(c0[1], c0[0])) || [1, 0, 0];
    const nrm = gD.V3.unit(gD.V3.cross(gD.V3.sub(c0[1], c0[0]), gD.V3.sub(c0[c0.length - 1], c0[0]))) || [0, 0, 1];
    const e2 = gD.V3.cross(nrm, e1);
    const to2 = p => [gD.V3.dot(gD.V3.sub(p, c0[0]), e1), gD.V3.dot(gD.V3.sub(p, c0[0]), e2)];
    const pts2 = quads.map(([, q]) => P(q).map(to2));
    let lo = [Infinity, Infinity], hi = [-Infinity, -Infinity];
    for (const ps of pts2) for (const p of ps) { lo = [Math.min(lo[0], p[0]), Math.min(lo[1], p[1])]; hi = [Math.max(hi[0], p[0]), Math.max(hi[1], p[1])]; }
    const W = 480, H = 170, sc = Math.min((W - 20) / Math.max(hi[0] - lo[0], 1e-6), (H - 20) / Math.max(hi[1] - lo[1], 1e-6));
    const X = p => 10 + (p[0] - lo[0]) * sc, Y = p => H - 10 - (p[1] - lo[1]) * sc;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "g-d-mesh"); svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.id = "gDetailMesh";
    const sf = st && st.shell_forces;
    const vals = quads.map(([i]) => { const f = gD.quadForces(sf, i); return f ? gD.shellComponent(f, t, "SVM", "top") : null; });
    const rng = gD.rangeOf(vals.filter(v => v != null), null, false);
    quads.forEach(([i], k) => {
      const poly = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
      poly.setAttribute("points", pts2[k].map(p => `${X(p)},${Y(p)}`).join(" "));
      poly.setAttribute("fill", vals[k] == null ? "rgba(154,167,180,.2)" : gD.valueColor(vals[k], rng, "rainbow"));
      poly.setAttribute("stroke", D.pick === i ? "#fff" : "rgba(13,17,23,.6)"); poly.setAttribute("stroke-width", D.pick === i ? "2" : "0.7");
      poly.dataset.quad = String(i);
      poly.addEventListener("click", () => { D.pick = i; render(); });
      svg.appendChild(poly);
    });
    body.appendChild(gdEl("div", { class: "g-d-ct" }, [gdEl("span", { text: "Pick a point (element) — colour: SVM top" })]));
    body.appendChild(svg);
    if (!sf) { body.appendChild(gdEl("div", { class: "g-d-note", text: "No shell forces in this state (static cases and add combinations carry them; TH snapshots via Display › Shell Stresses / Forces)." })); return; }
    const qi = D.pick != null ? D.pick : quads[0][0];
    const f = gD.quadForces(sf, qi);
    const fmin = st.shell_forces_min ? gD.quadForces(st.shell_forces_min, qi) : null;
    const tbl = gdEl("table", { id: "gDetailShell" });
    tbl.appendChild(gdEl("tr", {}, ["Component", "Value", fmin ? "Min" : "", "unit", ""].map(h => gdEl("th", { text: h }))));
    const row = (k, v, vmin, kind, probe) => {
      const pin = gdEl("button", { type: "button", class: "g-pin", title: "Pin to the watch list" }, ["📌"]);
      pin.addEventListener("click", () => api.pin && api.pin(probe));
      tbl.appendChild(gdEl("tr", { "data-comp": k }, [gdEl("td", { text: k }), gdEl("td", { text: v == null ? "—" : gDU.fmt(kind, v, 3) }), gdEl("td", { text: vmin == null ? "" : gDU.fmt(kind, vmin, 3) }), gdEl("td", { text: gDU.label(kind) }), gdEl("td", {}, [pin])]));
    };
    for (const [k, i] of Object.entries(gD.SHELL_FORCE_IDX)) row(k, f ? f[i] : null, fmin ? fmin[i] : null, i >= 3 && i <= 5 ? "line_moment" : "line_force", { type: "shell", region: sh.uid, quad: qi, comp: k });
    for (const face of ["top", "bot"]) {
      const s = gD.shellStresses(f, t, face), smin = fmin ? gD.shellStresses(fmin, t, face) : null;
      if (!s) continue;
      for (const k of ["S11", "S22", "S12", "SMax", "SMin", "SVM"]) row(`${k} ${face === "top" ? "top" : "bottom"}`, s[k], smin ? smin[k] : null, "stress", { type: "shell", region: sh.uid, quad: qi, comp: k, face });
    }
    body.appendChild(gdEl("div", { class: "g-d-meta", text: `Element ${qi} (element-centre values, local axes). Stresses S = F/t ± 6M/t² (top = +3 face).` }));
    body.appendChild(tbl);
  }

  /* ---------------------------------------------------------------- pier / spandrel / link / joint */
  function renderPier() {
    const st = curState();
    title.textContent = `Pier Forces — ${D.obj.label}`;
    body.textContent = "";
    body.appendChild(gdEl("div", { class: "g-d-row" }, ["Pier", gdEl("b", { text: D.obj.label }), ...caseControls()]));
    const blk = st && st.piers && st.piers[D.obj.label];
    if (!blk) { body.appendChild(gdEl("div", { class: "g-d-note", text: "No pier forces for this pier in this state." })); return; }
    const tbl = gdEl("table", { id: "gDetailPier" });
    tbl.appendChild(gdEl("tr", {}, ["Story", "P", "V2", "M3", "min P", "min V2", "min M3"].map(h => gdEl("th", { text: h }))));
    for (const s of [...(S.model.stories || [])].reverse()) {
      const e = blk[s.name];
      if (!e) continue;
      const pin = gdEl("button", { type: "button", class: "g-pin", title: "Pin V2" }, ["📌"]);
      pin.addEventListener("click", () => api.pin && api.pin({ type: "pier", label: D.obj.label, story: s.name, comp: "V" }));
      tbl.appendChild(gdEl("tr", {}, [gdEl("td", {}, [s.name, " ", pin]), ...["P", "V", "M"].map(k => gdEl("td", { text: gDU.fmt(k === "M" ? "moment" : "force", +e[k] || 0, 2) })),
        ...["P", "V", "M"].map(k => gdEl("td", { text: e.min ? gDU.fmt(k === "M" ? "moment" : "force", +e.min[k] || 0, 2) : "" }))]));
    }
    body.appendChild(gdEl("div", { class: "g-d-meta", text: `Units: ${gDU.label("force")}, ${gDU.label("moment")}` }));
    body.appendChild(tbl);
  }
  function renderSpandrel() {
    const st = curState();
    title.textContent = `Spandrel Forces — ${D.obj.label}`;
    body.textContent = "";
    body.appendChild(gdEl("div", { class: "g-d-row" }, ["Spandrel", gdEl("b", { text: D.obj.label }), ...caseControls()]));
    const blk = st && st.spandrels && st.spandrels[D.obj.label];
    if (!blk) { body.appendChild(gdEl("div", { class: "g-d-note", text: "No spandrel forces in this state." })); return; }
    const tbl = gdEl("table", { id: "gDetailSpandrel" });
    tbl.appendChild(gdEl("tr", {}, ["Story", "End", "P", "V2", "M3"].map(h => gdEl("th", { text: h }))));
    for (const [s, e] of Object.entries(blk)) for (const end of ["Left", "Right"]) if (e[end]) tbl.appendChild(gdEl("tr", {}, [gdEl("td", { text: s }), gdEl("td", { text: end }), ...["P", "V2", "M3"].map(k => gdEl("td", { text: gDU.fmt(k === "M3" ? "moment" : "force", +e[end][k] || 0, 2) }))]));
    body.appendChild(tbl);
  }
  function renderLink() {
    const st = curState();
    const lk = (S.model.links || []).find(x => x.uid === D.obj.uid);
    title.textContent = `Link Results — ${D.obj.uid}`;
    body.textContent = "";
    body.appendChild(gdEl("div", { class: "g-d-row" }, ["Link", gdEl("b", { text: D.obj.uid }), ...caseControls()]));
    if (!lk || !st || !st.node_disp) { body.appendChild(gdEl("div", { class: "g-d-note", text: "No joint displacements in this state." })); return; }
    const r = S.results, ti = gD.tagAt(r, lk.pi), tj = gD.tagAt(r, lk.pj);
    const di = st.node_disp[ti] || [0, 0, 0, 0, 0, 0], dj = st.node_disp[tj] || [0, 0, 0, 0, 0, 0];
    const F = (api.linkForces && api.linkForces()) || {};
    const tbl = gdEl("table", { id: "gDetailLink" });
    tbl.appendChild(gdEl("tr", {}, ["DOF", "Deformation", "Force", ""].map(h => gdEl("th", { text: h }))));
    ["U1", "U2", "U3", "R1", "R2", "R3"].forEach((k, i) => {
      const def = (dj[i] || 0) - (di[i] || 0), f = F[lk.uid] ? F[lk.uid][i] : null;
      const pin = gdEl("button", { type: "button", class: "g-pin", title: "Pin" }, ["📌"]);
      pin.addEventListener("click", () => api.pin && api.pin({ type: "link", uid: lk.uid, comp: k }));
      tbl.appendChild(gdEl("tr", {}, [gdEl("td", { text: k }), gdEl("td", { text: gDU.fmt(i < 3 ? "disp" : "rotation", def, 4) }), gdEl("td", { text: f == null ? "— (nonlinear / not elastic)" : gDU.fmt(i < 3 ? "force" : "moment", f, 2) }), gdEl("td", {}, [pin])]));
    });
    body.appendChild(gdEl("div", { class: "g-d-meta", text: `Global axes · ${lk.link_type || "elastic"} link` }));
    body.appendChild(tbl);
  }
  function renderJoint() {
    const st = curState(), r = S.results;
    const p = D.obj.point;
    const t = p ? gD.tagAt(r, p) : null;
    title.textContent = `Joint Results — ${p ? p.map(v => gDU.fmt("length", v, 2)).join(", ") : "?"}`;
    body.textContent = "";
    body.appendChild(gdEl("div", { class: "g-d-row" }, [...caseControls()]));
    if (t == null || !st || !st.node_disp) { body.appendChild(gdEl("div", { class: "g-d-note", text: "No result joint at this point." })); return; }
    const d = st.node_disp[t] || [], rc = (st.reactions || {})[t];
    const tbl = gdEl("table", { id: "gDetailJoint" });
    tbl.appendChild(gdEl("tr", {}, ["DOF", "Displacement", "Reaction", ""].map(h => gdEl("th", { text: h }))));
    ["UX", "UY", "UZ", "RX", "RY", "RZ"].forEach((k, i) => {
      const pin = gdEl("button", { type: "button", class: "g-pin", title: "Pin" }, ["📌"]);
      pin.addEventListener("click", () => api.pin && api.pin({ type: "joint_disp", point: p, comp: k }));
      tbl.appendChild(gdEl("tr", {}, [gdEl("td", { text: k }), gdEl("td", { text: gDU.fmt(i < 3 ? "disp" : "rotation", d[i] || 0, 4) }), gdEl("td", { text: rc ? gDU.fmt(i < 3 ? "force" : "moment", rc[i] || 0, 2) : "" }), gdEl("td", {}, [pin])]));
    });
    body.appendChild(tbl);
  }

  function render() {
    if (!D.obj || win.classList.contains("hidden")) return;
    if (!S.results) { close(); return; }
    try {
      if (D.obj.type === "member") renderFrame();
      else if (D.obj.type === "shell") renderShell();
      else if (D.obj.type === "pier") renderPier();
      else if (D.obj.type === "spandrel") renderSpandrel();
      else if (D.obj.type === "link") renderLink();
      else if (D.obj.type === "joint") renderJoint();
    } catch (e) { console.error("member detail", e); body.textContent = "Could not show the results for this object."; }
  }
  api.subscribe(() => { if (D.follow) render(); });
  document.addEventListener("sky:results-changed", () => { if (!S.results) close(); else render(); });
  gDU.onUnitsChange && gDU.onUnitsChange(() => render());

  // Member panel (Analyze mode) → Detail… button (additive DOM, no app.js edit)
  const mph = document.querySelector("#memberPanel .member-panel-head");
  if (mph && !document.getElementById("gMemberDetailBtn")) {
    const b = gdEl("button", { type: "button", class: "chip", id: "gMemberDetailBtn", title: "Diagrams for Frame Object — scroll for values, Show Max, steps" }, ["Detail…"]);
    b.addEventListener("click", () => open({ type: "member", uid: S.selectedMemberUid }));
    mph.insertBefore(b, document.getElementById("memberPanelClose"));
  }

  api._detail = { open, close, state: D, render, setLocation: x => { D.x = x; render(); }, isOpen: () => !win.classList.contains("hidden") };
  api.openDetail = obj => open(obj);
  api.closeDetail = close;
  if (sky.menu && sky.menu.add)
    sky.menu.add("Display/Force Diagrams", { label: "Diagrams for Frame Object…", act: "dis-g-detail", needs: "results",
      hint: "V / M / P / T / deflection · scroll for values · Show Max", fn: () => open(null) });
}
