/* SkyFrame Wave H — pop-out windows and main ↔ pop-out sync.

   Pop Out (window ⋯ menu, View › Windows › Pop Out Active Window, or drag a
   tab outside the window area) moves a docked window into its own OS window:

     desktop app   a second pywebview window through the js_api bridge
                   (skyframe/desktop_windows.py open_window) — pywebview
                   sends a page's window.open to the system browser, so the
                   shell opens the window itself; same local server URL
     browser       window.open(same URL, popup features)

   The pop-out loads the same app with ?hpop=<window id>&hs=<channel>, hides
   the chrome and renders that ONE window (js/h_dock.js popout mode).

   Channel: a BroadcastChannel("skyframe-h-<channel>") (same origin, same
   browser profile: Chromium / Edge WebView2 windows of one pywebview process
   share it), with an automatic fallback to the local server relay
   (POST / GET /api/hsync/<channel>, long-poll; skyframe/api/h_sync_api.py)
   when the pop-out gets no answer over BroadcastChannel within 1.5 s — e.g.
   two WebView2 windows that do not share a profile, or a pop-out the OS
   opened in another browser. ?hsync=bc|server forces one transport.
   Messages are deduplicated by id (js/h_sync_core.js).

   Synced both ways: model edits (per top-level key, the undo unit), the
   selection, the results toolbar state and the window's own results state,
   linked cameras; undo / redo in a pop-out run in the main window (one
   history). The main window sends new analysis results. Closing the main
   window closes every pop-out (pagehide "bye"; desktop: the shell closes
   them); closing a pop-out docks its window back. Pop-out positions and
   sizes are remembered per monitor setup (localStorage) and restored, the
   ETABS v22.5 behaviour. */

import * as HS from "./h_sync_core.js";
import * as HD from "./h_dock_core.js";

const LS_RECTS = "skyframe.h.popouts.v1";
const Q = (() => { try { return new URLSearchParams(location.search); } catch { return new URLSearchParams(""); } })();

export function initHSync(sky, dock) {
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 3500) => sky.toast && sky.toast(t, m, k, ms);
  const popId = Q.get("hpop");
  const isPop = !!(popId && /^w\d{1,4}$/.test(popId));
  const me = isPop ? popId : "main";
  const ch = isPop ? String(Q.get("hs") || "") : "m" + Math.random().toString(36).slice(2, 12);
  const force = Q.get("hsync");
  const desk = () => (window.pywebview && window.pywebview.api && typeof window.pywebview.api.open_window === "function" ? window.pywebview.api : null);
  const seen = HS.hsSeen();
  const stats = { sent: 0, recv: 0, bc: 0, server: 0, last: null };
  let applying = false;

  /* ================================================================ transports */
  let bc = null;
  if (force !== "server" && typeof BroadcastChannel !== "undefined" && /^[A-Za-z0-9_-]{4,64}$/.test(ch)) {
    try { bc = new BroadcastChannel("skyframe-h-" + ch); bc.onmessage = e => receive(e.data, "bc"); } catch { bc = null; }
  }
  const relay = { on: false, after: 0, ctl: null, fails: 0 };
  function relayStart() {
    if (relay.on || force === "bc" || !/^[A-Za-z0-9_-]{4,64}$/.test(ch)) return;
    relay.on = true;
    (async () => {
      try { const r = await fetch(`/api/hsync/${ch}?after=0&wait=0`); if (r.ok) relay.after = isPop ? 0 : (await r.json()).seq || 0; } catch { /* no server */ }
      while (relay.on) {
        try {
          relay.ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
          const r = await fetch(`/api/hsync/${ch}?after=${relay.after}&wait=20`, relay.ctl ? { signal: relay.ctl.signal } : undefined);
          if (!r.ok) throw new Error(String(r.status));
          const d = await r.json();
          relay.fails = 0;
          for (const it of d.msgs || []) { relay.after = Math.max(relay.after, it.seq); receive(it.msg, "server"); }
          relay.after = Math.max(relay.after, d.seq || 0);
        } catch (e) {
          if (!relay.on) break;
          relay.fails++;
          await new Promise(res => setTimeout(res, Math.min(5000, 400 * relay.fails)));
          if (relay.fails > 12 && !Object.keys(pops).length && isPop === false) { relay.on = false; break; }
        }
      }
    })();
  }
  function relayStop() { relay.on = false; try { relay.ctl && relay.ctl.abort(); } catch { /* ignore */ } }
  // pop-out: once the relay runs (no BroadcastChannel answer); main: only for pop-outs that reached it over the relay
  // send on the relay too whenever it runs: in WebView2 / some browsers BroadcastChannel can work one way only
  const useRelay = () => force === "server" || (relay.on && (isPop || Object.keys(pops).length > 0));
  function send(type, body = {}, to = "*") {
    const m = HS.hsMake(ch, me, type, body, to);
    seen(m.id);
    stats.sent++;
    if (bc && force !== "server") { try { bc.postMessage(m); } catch (e) { console.warn("window sync (BroadcastChannel)", e.message); } }
    if (useRelay()) {
      fetch(`/api/hsync/${ch}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ msg: m }), keepalive: type === "bye" || type === "dock" })
        .catch(() => { /* relay unavailable */ });
    }
    return m;
  }
  function receive(m, via) {
    const bad = HS.hsCheck(m, ch);
    if (bad || !HS.hsForMe(m, me) || seen(m.id)) return;
    stats.recv++; stats[via]++; stats.last = m.type;
    try { (isPop ? onPop : onMain)(m, via); } catch (e) { console.error("window sync", m.type, e); }
  }

  /* ================================================================ shared appliers */
  let modelSnap = null, rev = 0;
  function applyModelDelta(delta) {
    if (!S.model || !delta) return false;
    const cur = HS.hsSnapshot(S.model);
    const set = {};
    for (const [k, v] of Object.entries(delta.set || {})) if (cur[k] !== v) set[k] = v;
    const del = (delta.del || []).filter(k => k in S.model);
    if (!Object.keys(set).length && !del.length) return false;
    applying = true;
    try {
      HS.hsApply(S.model, { set, del });
      S.modelEdited = true;
      if (sky.markDirty) sky.markDirty();
      try { sky.rebuildStorySelect && sky.rebuildStorySelect(); } catch { /* optional */ }
      try { sky.planEditor && sky.planEditor.refresh(); sky.elevEditor && sky.elevEditor.refresh(); } catch { /* optional */ }
      try { if (S.mode === "analyze" && sky.viewer) { sky.viewer.setModel(S.model); S.modelEdited = false; } } catch { /* optional */ }
    } finally { applying = false; }
    modelSnap = HS.hsSnapshot(S.model);
    return true;
  }
  let selSig = HS.hsSelSig(S.selection);
  function applySel(sel) {
    const sig = HS.hsSelSig(sel);
    if (sig === HS.hsSelSig(S.selection)) { selSig = sig; return; }
    applying = true;
    try { sky.handleSelect(Array.isArray(sel) ? sel.map(r => ({ ...r })) : [], false); } finally { applying = false; }
    selSig = HS.hsSelSig(S.selection);
    for (const w of dock.wins()) w.syncHighlight();
    if (sky.viewer) sky.viewer._dirty = true;
  }
  let gSig = "";
  function applyG(g) {
    const R = sky.results;
    if (!R || !g || !g.key || HS.hsResSig(g) === HS.hsResSig(R.globalSel())) { gSig = HS.hsResSig(g); return; }
    if (!R.entry(g.key)) return;
    applying = true;
    try { R.setGlobalSel(g); } finally { applying = false; }
    gSig = HS.hsResSig(R.globalSel());
  }
  const gNow = () => { const R = sky.results; return R && R.globalSel ? R.globalSel() : null; };

  /* ================================================================ monitors + rectangles */
  let screens = null;
  function monitorKey() {
    if (screens && screens.length) return HD.hMonitorKey(screens);
    const s = window.screen || {};
    return HD.hMonitorKey({ x: s.availLeft || 0, y: s.availTop || 0, width: s.width || 0, height: s.height || 0 });
  }
  const loadRects = () => { try { return JSON.parse(localStorage.getItem(LS_RECTS) || "{}") || {}; } catch { return {}; } };
  function saveRect(id, kind, rect) {
    if (!rect || !(rect.w > 0 && rect.h > 0)) return;
    const all = loadRects(), mk = monitorKey();
    all[mk] = all[mk] || {};
    all[mk][id] = rect; if (kind) all[mk]["kind:" + kind] = rect;
    try { localStorage.setItem(LS_RECTS, JSON.stringify(all)); } catch { /* storage blocked */ }
  }
  function rectFor(id, kind) {
    const m = loadRects()[monitorKey()] || {};
    const r = m[id] || m["kind:" + kind] || null;
    const scr = screens && screens.length ? screens : [{ x: (window.screen && window.screen.availLeft) || 0, y: (window.screen && window.screen.availTop) || 0, width: (window.screen && window.screen.availWidth) || 1280, height: (window.screen && window.screen.availHeight) || 800 }];
    if (r) return HD.hClampRect(r, scr);
    const n = Object.keys(pops).length;
    return HD.hClampRect({ x: (window.screenX || 0) + 80 + 30 * n, y: (window.screenY || 0) + 80 + 30 * n, w: 900, h: 620 }, scr);
  }
  (async () => { const d = desk(); if (d && d.screens) { try { screens = await d.screens(); } catch { screens = null; } } })();
  window.addEventListener("pywebviewready", async () => { const d = desk(); if (d && d.screens) { try { screens = await d.screens(); } catch { /* ignore */ } } });

  /* ================================================================ MAIN side */
  const pops = {};              // id → {handle, desktop, kind, seen, ready, via, rect, state}
  function popUrl(id) {
    const q = new URLSearchParams();
    q.set("hpop", id); q.set("hs", ch);
    if (S.mock || Q.has("mock")) q.set("mock", "1");
    for (const k of ["renderer", "hsync"]) if (Q.get(k)) q.set(k, Q.get(k));
    if (!q.has("renderer") && sky.gpu3d && sky.gpu3d.mode) { const md = sky.gpu3d.mode(); if (md === "gpu" || md === "classic") q.set("renderer", md); }   // same 3D renderer as the main window
    if (Q.get("hselftest")) q.set("hselftest", "1");
    return { url: location.pathname + "?" + q.toString(), query: Object.fromEntries(q.entries()) };
  }
  /** Pop window `id` out of the docking layout into its own OS window. */
  async function popout(id) {
    if (isPop) return false;
    const w = dock.win(id);
    if (!w) return false;
    const st = w.state();
    dock._reduce({ type: "patch", id, patch: st });
    dock._reduce({ type: "popout", id });
    const rect = rectFor(id, st.kind);
    const { url, query } = popUrl(id);
    const title = `SkyFrame — Window ${id.slice(1)} (${(dock.KIND_LABEL || {})[st.kind] || st.kind})`;
    const p = pops[id] = { kind: st.kind, desktop: false, handle: null, seen: Date.now(), ready: false, rect, state: st };
    if (force !== "bc") relayStart();   // listen on the server relay as well as BroadcastChannel (duplicates are dropped by id)
    const d = desk();
    if (d) {
      p.desktop = true;
      try {
        const r = await d.open_window(id, query, title, rect.x, rect.y, rect.w, rect.h);
        if (!r || !r.ok) throw new Error((r && r.error) || "the shell refused the window");
      } catch (e) { delete pops[id]; dock._reduce({ type: "dock", id }); toast("Pop out", `Could not open a window: ${e.message}`, "error"); return false; }
    } else {
      const feat = `popup=yes,left=${rect.x},top=${rect.y},width=${rect.w},height=${rect.h}`;
      let h = null;
      try { h = window.open(url, `skyframe_${ch}_${id}`, feat); } catch { h = null; }
      if (!h) { delete pops[id]; dock._reduce({ type: "dock", id }); toast("Pop out", "The browser blocked the new window — allow pop-ups for this page.", "error", 6000); return false; }
      p.handle = h;
    }
    watch();
    document.dispatchEvent(new CustomEvent("sky:h-popout", { detail: { id } }));
    return true;
  }
  function dockIn(id, state, rect) {
    const p = pops[id];
    if (rect) saveRect(id, (state && state.kind) || (p && p.kind), rect);
    delete pops[id];
    if (dock.L().popped && dock.L().popped[id]) dock._reduce({ type: "dock", id, state: state || (p && p.state) || null });
    if (!Object.keys(pops).length && !useRelayForever()) relayStop();
    document.dispatchEvent(new CustomEvent("sky:h-dock", { detail: { id } }));
  }
  const useRelayForever = () => force === "server";
  function closePop(id) {
    const p = pops[id];
    if (!p) return;
    send("close", { win: id }, id);
    try { if (p.handle && !p.handle.closed) p.handle.close(); } catch { /* ignore */ }
    const d = desk();
    if (p.desktop && d && d.close_window) { try { d.close_window(id); } catch { /* ignore */ } }
    delete pops[id];
  }
  function closeAll() { for (const id of Object.keys(pops)) closePop(id); }
  function dockAll() { for (const id of Object.keys(pops)) { const p = pops[id]; closePop(id); if (dock.L().popped[id]) dock._reduce({ type: "dock", id, state: p.state }); } }
  let watchT = 0;
  function watch() {
    if (watchT) return;
    watchT = setInterval(async () => {
      const ids = Object.keys(pops);
      if (!ids.length) { clearInterval(watchT); watchT = 0; return; }
      const d = desk();
      let open = null;
      if (d && d.list_windows && ids.some(id => pops[id].desktop)) { try { open = await d.list_windows(); } catch { open = null; } }
      for (const id of ids) {
        const p = pops[id];
        if (!p) continue;
        const gone = p.handle ? p.handle.closed : (p.desktop && open && !open.includes(id) && Date.now() - p.seen > 3000);
        const silent = p.ready && Date.now() - p.seen > 15000;
        if (gone || silent) dockIn(id, p.state, p.rect);
      }
    }, 700);
  }
  function snapFor(id) {
    const L = dock.L();
    return { win: id, state: (L.wins || {})[id] || (pops[id] && pops[id].state) || { id, kind: "3d" }, model: HS.hsSnapshot(S.model), results: S.results || null,
      sel: S.selection || [], g: gNow(), rev, mode: S.mode, linked: HD.hLinkedWith(L, id), units: (S.model && S.model.display_units) || null };
  }
  function onMain(m, via) {
    const id = m.from, p = pops[id];
    if (p) { p.seen = Date.now(); p.via = via; }
    switch (m.type) {
      case "hello":
        if (!p && !(dock.L().popped || {})[id]) { send("close", { win: id }, id); return; }
        if (via === "server") relayStart();
        send("snap", snapFor(id), id);
        break;
      case "welcome": if (p) p.ready = true; document.dispatchEvent(new CustomEvent("sky:h-ready", { detail: { id, via } })); break;
      case "edit":
        if (sky.history && sky.history.label) sky.history.label(m.body.label || `Edit in window ${id.slice(1)}`);
        if (applyModelDelta(m.body)) { rev++; send("model", { set: m.body.set || {}, del: m.body.del || [], rev }); }
        break;
      case "sel": applySel(m.body.sel); send("sel", { sel: S.selection || [] }); break;
      case "res":
        applyG(m.body.g);
        if (m.body.win && m.body.wres !== undefined && dock.L().wins[m.body.win]) dock.L().wins[m.body.win].res = m.body.wres || undefined;
        if (p && m.body.wres !== undefined) p.state = { ...p.state, res: m.body.wres || undefined };
        send("res", { g: gNow() });
        break;
      case "cmd":
        if (sky.history) { if (m.body.cmd === "undo") sky.history.undo(); else if (m.body.cmd === "redo") sky.history.redo(); }
        break;
      case "cam": dock.camIn(m.body.win, m.body.cam); break;
      case "ping": if (p) { p.rect = m.body.rect || p.rect; if (m.body.state) p.state = m.body.state; if (p.rect) saveRect(id, p.kind, p.rect); } break;
      case "dock": dockIn(id, m.body.state, m.body.rect); break;
      default: break;
    }
  }
  // outgoing (main)
  let mT = 0;
  document.addEventListener("sky:model-changed", () => {
    if (isPop) { popEdit(); return; }
    if (!Object.keys(pops).length) { modelSnap = null; return; }
    if (applying) return;
    clearTimeout(mT);
    mT = setTimeout(() => {
      const cur = HS.hsSnapshot(S.model);
      const d = modelSnap ? HS.hsDelta(modelSnap, cur) : { set: cur, del: [] };
      modelSnap = cur;
      if (d) { rev++; send("model", { ...d, rev }); }
    }, 30);
  });
  document.addEventListener("sky:model-replaced", () => { modelSnap = null; if (!isPop && Object.keys(pops).length) { rev++; send("model", { set: HS.hsSnapshot(S.model), del: [], rev, replace: true }); } });
  document.addEventListener("sky:results-changed", () => { if (!isPop && Object.keys(pops).length) send("results", { results: S.results || null }); });
  setInterval(() => {
    if (isPop ? !popReady : !Object.keys(pops).length) return;   // nothing to sync (no cost without pop-outs)
    const sig = HS.hsSelSig(S.selection);
    if (sig === selSig) return;
    selSig = sig;
    if (applying) return;
    if (isPop ? popReady : Object.keys(pops).length) send("sel", { sel: S.selection || [] });
  }, 150);
  if (sky.results && sky.results.subscribe) sky.results.subscribe(() => resOut(false));
  function resOut(forceSend) {
    if (applying) return;
    const sig = HS.hsResSig(gNow());
    if (isPop) {
      if (!popReady) return;
      const w = dock.win(popId), wres = w ? w.resState() : null, wsig = JSON.stringify(wres);
      if (!forceSend && sig === gSig && wsig === lastWres) return;
      gSig = sig; lastWres = wsig;
      send("res", { g: gNow(), win: popId, wres });
    } else if (Object.keys(pops).length && (forceSend || sig !== gSig)) { gSig = sig; send("res", { g: gNow() }); }
  }
  let lastWres = "null";
  window.addEventListener("pagehide", () => { if (!isPop && Object.keys(pops).length) send("bye", {}); else if (isPop && popReady && !docking) send("dock", { win: popId, state: popState(), rect: myRect() }); });
  function camOut(id, cam, peers) { if (!isPop && peers && peers.length) send("cam", { win: id, cam }); }

  /* ================================================================ POP-OUT side */
  let popReady = false, docking = false, linked = [];
  const myRect = () => ({ x: window.screenX || 0, y: window.screenY || 0, w: window.outerWidth || window.innerWidth, h: window.outerHeight || window.innerHeight });
  const popState = () => { const w = dock.win(popId); return w ? w.state() : null; };
  function popEdit() {
    if (applying || !popReady) return;
    clearTimeout(mT);
    mT = setTimeout(() => {
      const cur = HS.hsSnapshot(S.model);
      const d = modelSnap ? HS.hsDelta(modelSnap, cur) : null;
      modelSnap = cur;
      if (d) send("edit", { ...d, label: (sky.history && sky.history.state && sky.history.state().undoLabel) || "" }, "main");
      S.dirty = false;
    }, 30);
  }
  function onPop(m) {
    switch (m.type) {
      case "snap": {
        if (popReady && m.body.win !== popId) return;
        clearInterval(helloT);
        if (m.body.units && S.model) S.model.display_units = m.body.units;
        applyModelDelta({ set: m.body.model, del: Object.keys(HS.hsSnapshot(S.model)).filter(k => !(k in m.body.model)) });
        modelSnap = HS.hsSnapshot(S.model);
        S.dirty = false;
        rev = m.body.rev || 0;
        if (sky.hAdoptResults && (m.body.results || S.results)) sky.hAdoptResults(m.body.results || null);
        linked = m.body.linked || [];
        const st = { ...m.body.state, id: popId };
        const L = HD.hEmpty();
        L.wins[popId] = HD.hWinState(popId, st);
        L.root = { t: "tabs", wins: [popId], cur: popId };
        L.active = popId; L.next = +popId.slice(1) + 1;
        dock.setL(L);
        applySel(m.body.sel || []);
        if (m.body.g) applyG(m.body.g);
        document.title = `SkyFrame — Window ${popId.slice(1)}`;
        popReady = true;
        lastWres = JSON.stringify((dock.win(popId) || { resState: () => null }).resState());
        send("welcome", { win: popId }, "main");
        document.dispatchEvent(new CustomEvent("sky:h-ready", { detail: { id: popId } }));
        break;
      }
      case "model": if (popReady) { applyModelDelta(m.body); rev = m.body.rev || rev; S.dirty = false; } break;
      case "results": if (popReady && sky.hAdoptResults) sky.hAdoptResults(m.body.results || null); break;
      case "sel": if (popReady) { applySel(m.body.sel); if (selftest.on) selftest.gotSel(m.body.sel); } break;
      case "res": if (popReady) applyG(m.body.g); break;
      case "cam": if (popReady && (linked.includes(m.body.win))) { const w = dock.win(popId); if (w) { w.setCamera(m.body.cam); lastCam = JSON.stringify(w.camera()); } } break;
      case "bye": case "close": closeSelf(); break;
      default: break;
    }
  }
  /** Close this pop-out OS window (desktop: the shell closes the pywebview window; WebView2 ignores window.close()). */
  function closeSelf() {
    docking = true;
    const d = desk();
    if (d && d.close_window) { try { d.close_window(popId); } catch { /* fall through */ } }
    try { window.close(); } catch { /* ignore */ }
  }
  /** Pop-out: send the window back to the main window's layout and close. */
  function dockBack() {
    if (!isPop) return;
    docking = true;
    send("dock", { win: popId, state: popState(), rect: myRect() }, "main");
    setTimeout(closeSelf, 120);
  }
  let helloT = 0, lastCam = "";
  if (isPop) {
    document.documentElement.classList.add("h-popout");
    const hello = () => send("hello", { win: popId }, "main");
    const t0 = Date.now();
    helloT = setInterval(() => {
      if (popReady) { clearInterval(helloT); return; }
      if (Date.now() - t0 > 1500 && !relay.on && force !== "bc") relayStart();   // no answer over BroadcastChannel → server relay
      hello();
    }, 500);
    if (force !== "bc") relayStart();   // both transports from the start; duplicates are dropped by id
    setTimeout(hello, 0);
    // undo / redo run in the main window (one history)
    window.addEventListener("keydown", e => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
      const k = e.key.toLowerCase();
      const cmd = k === "z" && !e.shiftKey ? "undo" : (k === "y" || (k === "z" && e.shiftKey)) ? "redo" : null;
      if (!cmd) return;
      const tag = (e.target && e.target.tagName || "").toLowerCase();
      if (["input", "textarea"].includes(tag)) return;
      e.preventDefault(); e.stopImmediatePropagation();
      send("cmd", { cmd }, "main");
    }, true);
    if (sky.history) { for (const f of ["undo", "redo"]) { const o = sky.history[f]; sky.history[f] = () => { if (popReady) { send("cmd", { cmd: f }, "main"); return true; } return o(); }; } }
    setInterval(() => {
      if (!popReady) return;
      send("ping", { win: popId, rect: myRect(), state: popState() }, "main");
    }, 2000);
    const camLoop = () => {
      if (popReady && linked.length) { const w = dock.win(popId), c = w && w.camera(); const sig = c ? JSON.stringify(c) : ""; if (sig && sig !== lastCam) { lastCam = sig; send("cam", { win: popId, cam: c }, "main"); } }
      requestAnimationFrame(camLoop);
    };
    requestAnimationFrame(camLoop);
    setTimeout(() => { try { sky.ux && sky.ux.start && sky.ux.start.isOpen() && sky.ux.start.close(); } catch { /* ignore */ } }, 300);
  }

  /* ================================================================ self-test (desktop --selftest-windows; also usable in a browser) */
  const selftest = { on: !!Q.get("hselftest"), gotSel: () => {} };
  if (selftest.on) runSelftest();
  async function runSelftest() {
    const t0 = Date.now();
    const until = async (fn, ms) => { const t = Date.now(); while (Date.now() - t < ms) { const v = fn(); if (v) return v; await new Promise(r => setTimeout(r, 100)); } return null; };
    const report = rep => {
      window.__hSelftest = rep;
      const d = window.pywebview && window.pywebview.api;
      if (!isPop && d && d.selftest_result) { try { d.selftest_result(rep); } catch { /* ignore */ } }
    };
    if (isPop) {
      // the pop-out answers the main window's selection with its own (second member)
      selftest.gotSel = sel => {
        const m = (S.model && S.model.members) || [];
        if (sel && sel.length === 1 && m[0] && sel[0].uid === m[0].uid && m[1]) setTimeout(() => { sky.handleSelect([{ type: "member", uid: m[1].uid }], false); }, 50);
      };
      return;
    }
    try {
      if (!(await until(() => S.model && dock && sky.results, 30000))) throw new Error("app did not boot");
      if (!dock.L().root) dock.add({ kind: "3d" });
      const id = dock.add({ kind: "3d" });
      const ok = await popout(id);
      if (!ok) throw new Error("pop-out window did not open");
      const ready = await until(() => pops[id] && pops[id].ready, 30000);
      if (!ready) throw new Error("no handshake from the pop-out window");
      const m = (S.model && S.model.members) || [];
      if (m.length < 2) throw new Error("model has fewer than 2 members");
      sky.handleSelect([{ type: "member", uid: m[0].uid }], false);
      const back = await until(() => (S.selection || []).length === 1 && S.selection[0].uid === m[1].uid, 15000);
      if (!back) throw new Error("selection did not come back from the pop-out window");
      const via = pops[id] && pops[id].via;
      closePop(id);
      dock._reduce({ type: "dock", id });
      report({ ok: true, transport: via || "bc", ms: Date.now() - t0, desktop: !!desk() });
    } catch (e) {
      report({ ok: false, error: e.message, ms: Date.now() - t0, desktop: !!desk(), stats });
    }
  }

  const api = {
    channel: ch, me, isPopout: isPop, popout, dockBack, closeAll, dockAll, closePop, pops: () => ({ ...pops }), monitorKey, rectFor, saveRect,
    camOut, resOut, stats: () => ({ ...stats, relay: relay.on, bc: !!bc }), send, ready: () => (isPop ? popReady : true),
  };
  sky.hSync = api;
  return api;
}
