/* SkyFrame — Tools › Model History… (beyond ETABS; CONTRACT "Model history
   and visual diff").

     checkpoints  named (manual) or automatic: on save and before analysis
                  (server hooks), before bulk edits (Replicate / Move /
                  Extrude / Merge / Divide / Join / Delete of ≥ 10 objects,
                  database edit, model repair; js/history.js hook) and
                  before a restore.  Stored next to the saved models
                  (<models dir>/.history/<file name>/, gzip), bounded with
                  pruning; ?mock=1 keeps them in localStorage.
     compare      any two checkpoints, or a checkpoint against the current
                  model: per-type summary (joints, frames, shells, links,
                  supports, sections, materials, loads, cases, combos,
                  stories, grids, options) and a change table with
                  field-level differences.
     views        "Show in views" colours the changes in 3D, plan and
                  elevation: added green, removed ghosted red (dashed),
                  changed amber — through the existing overlay hooks
                  (viewer.extraOverlay / editor.grpOverlay, chained).
     restore      a whole checkpoint, or the checked rows of the change
                  table, as ONE undo step (dlgkit dkCommit).

   API: window.__sky.mhistory = { open, checkpoint(name, note), compare(a, b),
   restore(id, items?), list(), key(), clearHighlight(), beforeBulk(reason) }. */

import { el as mhEl, esc as mhEsc, dkForm as mhForm, dkGroup as mhGroup, dkDialog as mhDialog, dkNote as mhNote,
  dkCommit as mhCommit, dkConfirm as mhConfirm, dkList as mhList } from "./dlgkit.js";
import { mhDiff as mhMockDiff, mhRestore as mhMockRestore, mhMockStore as mhStore, MH_TYPES as MH_ALL_TYPES } from "./mock_mhistory.js";

const TYPE_LABEL = { joints: "Joints", frames: "Frames", shells: "Shells", links: "Links", supports: "Supports",
  sections: "Sections", materials: "Materials", loads: "Loads", cases: "Load cases", combos: "Combinations",
  stories: "Stories", grids: "Grids", options: "Options" };
const COL = { added: "#3fbf6a", removed: "#e05555", changed: "#e0a020" };
const REASON_LABEL = { save: "On save", "before analysis": "Before analysis" };
const BULK_LABEL = /^(Replicate|Move|Extrude|Merge joints|Divide frames|Join frames|Align|Delete|Erase|Interactive database edit|Repair model)/i;
const BULK_MIN = 10;

const CSS = `
.dk-dlg .mh-list .dk-list-table { max-height: 210px; }
.dk-dlg .mh-kind { font-size: 10.5px; padding: 1px 6px; border-radius: 8px; background: var(--surface-3, #2a313b); }
.dk-dlg .mh-cmp-row { display: flex; gap: 8px; align-items: end; flex-wrap: wrap; }
.dk-dlg .mh-cmp-row .dk-row { margin: 0; flex: 1 1 200px; }
.dk-dlg .mh-sum { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0 6px; font-size: 11.5px; }
.dk-dlg .mh-chip { border: 1px solid var(--border-soft, #333); border-radius: 10px; padding: 1px 8px; cursor: pointer; user-select: none; }
.dk-dlg .mh-chip.is-off { opacity: .45; }
.dk-dlg .mh-chip b { font-weight: 600; }
.dk-dlg .mh-a { color: ${COL.added}; } .dk-dlg .mh-r { color: ${COL.removed}; } .dk-dlg .mh-c { color: ${COL.changed}; }
.dk-dlg .mh-wrap { max-height: 260px; overflow: auto; border: 1px solid var(--border-soft, #333); border-radius: 4px; }
.dk-dlg .mh-table { width: 100%; border-collapse: collapse; font-size: 11.5px; }
.dk-dlg .mh-table th, .dk-dlg .mh-table td { padding: 3px 6px; border-bottom: 1px solid rgba(128,128,128,.18); text-align: left; vertical-align: top; }
.dk-dlg .mh-table th { position: sticky; top: 0; background: var(--panel, #1b2129); }
.dk-dlg .mh-table td.mh-f { font-family: var(--mono, monospace); font-size: 11px; white-space: normal; word-break: break-word; }
.dk-dlg .mh-table tr.mh-st-added td:nth-child(3) { color: ${COL.added}; }
.dk-dlg .mh-table tr.mh-st-removed td:nth-child(3) { color: ${COL.removed}; }
.dk-dlg .mh-table tr.mh-st-changed td:nth-child(3) { color: ${COL.changed}; }
.dk-dlg .mh-table tr[data-ref] { cursor: pointer; }
.dk-dlg .mh-btns { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }
.dk-dlg .mh-msg { font-size: 12px; margin: 6px 0; }
.mh-legend { position: fixed; left: 50%; transform: translateX(-50%); bottom: 34px; z-index: 40; display: flex; gap: 10px; align-items: center;
  background: var(--panel, #1b2129); border: 1px solid var(--border-soft, #333); border-radius: 14px; padding: 3px 10px; font-size: 11.5px; box-shadow: 0 2px 10px rgba(0,0,0,.3); }
.mh-legend.hidden { display: none; }
.mh-legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; vertical-align: -1px; }
.mh-legend button { background: none; border: 0; color: inherit; cursor: pointer; font-size: 14px; padding: 0 2px; }
`;
function ensureCss() {
  if (document.getElementById("mhCss")) return;
  const s = document.createElement("style"); s.id = "mhCss"; s.textContent = CSS; document.head.appendChild(s);
}

export function initMHistory(sky) {
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 4000) => sky.toast && sky.toast(t, m, k, ms);
  const st = { a: "", b: "current", diff: null, hl: null, hlOn: true, off: new Set(), checked: new Set(), list: [], lastKey: null, cmpLabel: "" };
  const clean = m => { const o = JSON.parse(JSON.stringify(m || {})); delete o._mock_params; return o; };
  const key = () => String(S.fileName || (S.model && S.model.name) || "Untitled");

  /* ---------------------------------------------------------------- transport */
  async function api(method, path, body) {
    const res = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    let data = null;
    try { data = await res.json(); } catch { /* non-JSON */ }
    if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
    return data;
  }
  const q = () => `?key=${encodeURIComponent(key())}`;
  const T = {
    list: () => S.mock ? mhStore.list(key()) : api("GET", "/api/history" + q()),
    add: (o) => S.mock ? mhStore.add(key(), o.model || clean(S.model), o)
      : api("POST", "/api/history/checkpoint", { key: key(), ...o, model: o.model || clean(S.model) }),
    get: id => S.mock ? mhStore.get(key(), id) : api("GET", `/api/history/checkpoint/${encodeURIComponent(id)}` + q()),
    update: (id, patch) => S.mock ? mhStore.update(key(), id, patch) : api("PATCH", `/api/history/checkpoint/${encodeURIComponent(id)}`, { key: key(), ...patch }),
    remove: id => S.mock ? mhStore.remove(key(), id) : api("DELETE", `/api/history/checkpoint/${encodeURIComponent(id)}` + q()),
    async compare(a, b) {
      if (S.mock) {
        const side = async x => x === "current" ? clean(S.model) : (await T.get(x)).model;
        return mhMockDiff(await side(a), await side(b), { max_items: 5000 });
      }
      const side = x => x === "current" ? clean(S.model) : x;
      return api("POST", "/api/history/compare", { key: key(), a: side(a), b: side(b), max_items: 5000 });
    },
    async restore(from, items) {
      if (S.mock) {
        const ref = (await T.get(from)).model;
        if (!items) return { model: ref, report: { restored: "all", skipped: [] } };
        return mhMockRestore(clean(S.model), ref, items);
      }
      return api("POST", "/api/history/restore", { key: key(), from, into: clean(S.model), items: items || undefined });
    },
    settings: o => S.mock ? Promise.resolve({ settings: mhStore.list(key()).settings, pruned: [] }) : api("POST", "/api/history/settings", { key: key(), ...o }),
    syncKey: () => S.mock ? Promise.resolve() : api("POST", "/api/history/key", { key: key() }).catch(() => {}),
  };

  /* ---------------------------------------------------------------- checkpoints */
  async function checkpoint(name = "", note = "", kind = "manual", reason = "", model = null) {
    if (!S.model) throw new Error("no model");
    const r = await T.add({ name, note, kind, reason, model: model || undefined });
    document.dispatchEvent(new CustomEvent("sky:history-checkpoint", { detail: r }));
    return r;
  }
  /** Auto checkpoint of the CURRENT working model before a bulk change. */
  function beforeBulk(reason) {
    return checkpoint("", "", "auto", String(reason || "before bulk edit")).catch(e => console.warn("model history", e.message));
  }
  // js/history.js hook: called for every new undo entry with the PREVIOUS
  // snapshot (key → JSON); a bulk edit checkpoints that previous state.
  sky.mhOnHistoryEntry = (entry, prevSnap) => {
    if (!entry || !prevSnap || /^Restore/.test(entry.label || "")) return;
    const cnt = s => (s ? (s.match(/"uid":/g) || []).length : 0);
    let delta = 0;
    for (const k of ["members", "shells", "links"]) if (k in entry.after) delta += Math.abs(cnt(entry.after[k]) - cnt(entry.before[k]));
    const touchesObjects = ["members", "shells", "links"].some(k => k in entry.after);
    if (!(delta >= BULK_MIN || (touchesObjects && BULK_LABEL.test(entry.label || "") && delta >= 2) || /^(Interactive database edit|Repair model)/.test(entry.label || ""))) return;
    let model;
    try { model = JSON.parse("{" + Object.entries(prevSnap).filter(([k, v]) => v !== undefined && k !== "_mock_params").map(([k, v]) => JSON.stringify(k) + ":" + v).join(",") + "}"); }
    catch { return; }
    checkpoint("", "", "auto", `before ${entry.label}`, model).catch(e => console.warn("model history", e.message));
  };

  /* ---------------------------------------------------------------- highlight */
  function setHighlight(diff) {
    st.diff = diff;
    st.hl = diff ? diff.items.filter(it => it.geom && !st.off.has(it.type)) : null;
    redraw();
    syncLegend();
  }
  function clearHighlight() { st.hl = null; redraw(); syncLegend(); }
  function redraw() {
    try { sky.viewer && sky.viewer._dirty !== undefined && (sky.viewer._dirty = true); } catch { /* ignore */ }
    try { sky.viewer && sky.viewer.render && sky.viewer.render(); } catch { /* ignore */ }
    try { sky.planEditor && sky.planEditor.refresh && sky.planEditor.refresh(); } catch { /* ignore */ }
    try { sky.elevEditor && sky.elevEditor.refresh && sky.elevEditor.refresh(); } catch { /* ignore */ }
  }
  let legend = null;
  function syncLegend() {
    if (!legend) {
      legend = mhEl("div", { class: "mh-legend hidden", id: "mhLegend", role: "status" });
      document.body.appendChild(legend);
      legend.addEventListener("click", e => { if (e.target.closest("button")) clearHighlight(); });
    }
    const items = st.hl || [];
    legend.classList.toggle("hidden", !items.length);
    if (!items.length) return;
    const n = s => items.filter(i => i.status === s).length;
    legend.innerHTML = `<span class="muted">Model diff · ${mhEsc(st.cmpLabel)}</span>` +
      `<span><i style="background:${COL.added}"></i>Added ${n("added")}</span>` +
      `<span><i style="background:${COL.removed}"></i>Removed ${n("removed")}</span>` +
      `<span><i style="background:${COL.changed}"></i>Changed ${n("changed")}</span>` +
      `<button type="button" title="Clear the highlight" aria-label="Clear the model-diff highlight" id="mhLegendClose">×</button>`;
  }
  /* 3D: chained onto viewer.extraOverlay (Check Model keeps its own slot) */
  function draw3d(ctx, P, viewer) {
    if (!st.hl || !st.hl.length) return;
    st.drawn3d = (st.drawn3d || 0) + 1;            // render counter (tests)
    const proj = p => { const c = P.toCam(p); return c[2] < P.near ? null : P.proj(c); };
    ctx.save();
    for (const it of st.hl) {
      const g = it.geom, col = COL[it.status];
      const ghost = it.status === "removed";
      ctx.strokeStyle = col; ctx.fillStyle = col;
      ctx.setLineDash(ghost ? [6, 4] : []);
      if (g.corners) {
        const pts = g.corners.map(proj);
        if (pts.some(x => !x)) continue;
        ctx.globalAlpha = ghost ? 0.12 : 0.28;
        ctx.beginPath(); pts.forEach((q, i) => i ? ctx.lineTo(q.x, q.y) : ctx.moveTo(q.x, q.y)); ctx.closePath(); ctx.fill();
        ctx.globalAlpha = ghost ? 0.6 : 0.95; ctx.lineWidth = 2; ctx.stroke();
      } else if (g.pi && g.pj) {
        const sg = viewer._projSeg ? viewer._projSeg(P, g.pi, g.pj) : null;
        if (!sg) continue;
        ctx.globalAlpha = ghost ? 0.6 : 0.95; ctx.lineWidth = ghost ? 2.2 : 3.4; ctx.lineCap = "round";
        ctx.beginPath(); ctx.moveTo(sg.a.x, sg.a.y); ctx.lineTo(sg.b.x, sg.b.y); ctx.stroke();
        if (it.geom_a && it.geom_a.pi) {         // moved: old position ghosted
          const so = viewer._projSeg(P, it.geom_a.pi, it.geom_a.pj);
          if (so) { ctx.setLineDash([4, 4]); ctx.globalAlpha = 0.45; ctx.lineWidth = 1.6; ctx.beginPath(); ctx.moveTo(so.a.x, so.a.y); ctx.lineTo(so.b.x, so.b.y); ctx.stroke(); }
        }
      } else if (g.point) {
        const q = proj(g.point);
        if (!q) continue;
        ctx.globalAlpha = ghost ? 0.6 : 0.95;
        ctx.beginPath(); ctx.arc(q.x, q.y, it.type === "joints" ? 3.2 : 5, 0, Math.PI * 2);
        if (ghost) { ctx.lineWidth = 1.5; ctx.stroke(); } else ctx.fill();
      }
    }
    ctx.restore();
  }
  function chain(obj, prop, mine) {
    if (!obj || obj.__mhChained && obj.__mhChained[prop]) return;
    let other = obj[prop];
    const composite = function (...args) {
      if (other) { try { other.apply(this, args); } catch (e) { /* the other hook never breaks ours */ } }
      try { mine.apply(this, args); } catch (e) { /* never break the render */ }
    };
    try {
      Object.defineProperty(obj, prop, { configurable: true, enumerable: true, get: () => composite, set: f => { other = f === composite ? other : f; } });
      (obj.__mhChained = obj.__mhChained || {})[prop] = true;
    } catch { /* non-configurable: skip the overlay */ }
  }
  /* plan / elevation (SVG, world coordinates) */
  function svg(editor, kind) {
    if (!st.hl || !st.hl.length || !editor || !editor.gElems) return;
    const NS = "http://www.w3.org/2000/svg";
    const layer = document.createElementNS(NS, "g");
    layer.setAttribute("class", "mh-hl-layer"); layer.setAttribute("pointer-events", "none");
    let elev = null;
    if (kind === "plan") {
      const sn = editor.opts && editor.opts.getStory ? editor.opts.getStory() : null;
      const s = ((S.model && S.model.stories) || []).find(x => x.name === sn);
      elev = s ? s.elevation : 0;
    }
    const xy = p => {
      if (!p) return null;
      if (kind === "plan") return Math.abs(p[2] - elev) < 1e-6 ? [p[0], p[1]] : null;
      return editor.inPlane && editor.inPlane(p) ? [editor.sOf(p), p[2]] : null;
    };
    const r = 5 / (editor.scale || 40);
    const add = (tag, attrs) => { const n = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); layer.appendChild(n); };
    for (const it of st.hl) {
      const g = it.geom, col = COL[it.status], ghost = it.status === "removed";
      const common = { stroke: col, "vector-effect": "non-scaling-stroke", opacity: ghost ? "0.7" : "0.95", "data-mh": it.status };
      if (ghost) common["stroke-dasharray"] = "6 4";
      if (g.corners) {
        const pts = g.corners.map(xy);
        if (pts.some(x => !x)) continue;
        add("polygon", { ...common, points: pts.map(p => p.join(",")).join(" "), fill: col, "fill-opacity": ghost ? "0.08" : "0.22", "stroke-width": "2" });
      } else if (g.pi && g.pj) {
        const a = xy(g.pi), b = xy(g.pj);
        if (a && b) add("line", { ...common, x1: a[0], y1: a[1], x2: b[0], y2: b[1], "stroke-width": ghost ? "2" : "3.5", "stroke-linecap": "round" });
        else if (kind === "plan") {        // a column ending at this story: a marker
          const top = g.pi[2] > g.pj[2] ? g.pi : g.pj;
          const p = xy(top);
          if (p && Math.abs(g.pi[0] - g.pj[0]) < 1e-6 && Math.abs(g.pi[1] - g.pj[1]) < 1e-6)
            add("circle", { ...common, cx: p[0], cy: p[1], r: r * 1.6, fill: ghost ? "none" : col, "fill-opacity": "0.5", "stroke-width": "2" });
        }
      } else if (g.point) {
        const p = xy(g.point);
        if (p) add("circle", { ...common, cx: p[0], cy: p[1], r, fill: ghost ? "none" : col, "stroke-width": "1.5" });
      }
    }
    editor.gElems.appendChild(layer);
  }
  const install = () => {
    if (sky.viewer) chain(sky.viewer, "extraOverlay", function (ctx, P, viewer) { draw3d(ctx, P, viewer || sky.viewer); });
    if (sky.planEditor) chain(sky.planEditor, "grpOverlay", function (ed) { svg(ed || sky.planEditor, "plan"); });
    if (sky.elevEditor) chain(sky.elevEditor, "grpOverlay", function (ed) { svg(ed || sky.elevEditor, "elev"); });
  };
  install();

  /* ---------------------------------------------------------------- restore */
  async function restore(id, items = null) {
    const r = await T.restore(id, items);
    if (!r || !r.model) throw new Error("restore returned no model");
    await beforeBulk(items ? "before restore of selected objects" : "before restore");
    const draft = JSON.parse(JSON.stringify(r.model));
    delete draft._mock_params;
    if (S.model && S.model._mock_params) draft._mock_params = S.model._mock_params;
    const norm = sky.ME && sky.ME.normalizeModel ? sky.ME.normalizeModel(draft) : draft;
    if (sky.history && sky.history.label) sky.history.label(items ? `Restore ${items.length} object${items.length === 1 ? "" : "s"}` : "Restore checkpoint");
    const changed = mhCommit(sky, norm);
    if (sky.history && sky.history.flush) sky.history.flush();
    try { sky.rebuildStorySelect && sky.rebuildStorySelect(); } catch { /* optional */ }
    return { changed, report: r.report };
  }

  /* ---------------------------------------------------------------- dialog */
  const fmtTime = s => { try { return new Date(s * 1000).toLocaleString(); } catch { return String(s); } };
  const cpLabel = c => c ? (c.name || (c.kind === "auto" ? (REASON_LABEL[c.reason] || c.reason || "Auto") : "Checkpoint")) : "";
  const cpOption = c => [c.id, `${cpLabel(c)} · ${fmtTime(c.created)}${c.pinned ? " · pinned" : ""}`];
  function fmtVal(v) {
    if (v === null || v === undefined) return "—";
    if (typeof v === "number") return String(+v.toPrecision(8));
    if (typeof v === "string") return v;
    return JSON.stringify(v);
  }
  function fieldsText(it) {
    if (it.status === "added") return "new object";
    if (it.status === "removed") return "deleted";
    const f = it.fields || [];
    const shown = f.slice(0, 4).map(x => `${x.path || "value"}: ${fmtVal(x.present_a === false ? "(none)" : x.a)} → ${fmtVal(x.present_b === false ? "(none)" : x.b)}`);
    return shown.join("; ") + (f.length > 4 ? ` (+${f.length - 4} more)` : "") + (it.match && it.match !== "key" ? ` [matched by ${it.match}]` : "");
  }

  async function openDialog() {
    ensureCss();
    install();
    let listing;
    try { listing = await T.list(); }
    catch (e) { toast("Model History", e.message, "error", 6000); return null; }
    st.list = listing.checkpoints || [];
    if (!st.list.some(c => c.id === st.a)) st.a = st.list[0] ? st.list[0].id : "";
    if (st.b !== "current" && !st.list.some(c => c.id === st.b)) st.b = "current";
    const F = mhForm();
    const msg = mhEl("div", { class: "mh-msg muted", id: "mhMsg" });
    const out = mhEl("div", { class: "mh-out", id: "mhOut" });
    const reload = async (sel) => {
      try { listing = await T.list(); st.list = listing.checkpoints || []; } catch (e) { msg.textContent = e.message; return; }
      if (sel) list.select(sel); else list.render();
      F.refresh();
      foot();
    };
    const foot = () => {
      const kb = (listing.total_bytes || 0) / 1024;
      d && d.setNote(`${st.list.length} checkpoint${st.list.length === 1 ? "" : "s"} · ${kb < 1024 ? kb.toFixed(0) + " KB" : (kb / 1024).toFixed(1) + " MB"} · <span title="${mhEsc(listing.dir || "")}">${S.mock ? "browser storage (mock)" : "stored with the model files"}</span>`);
    };
    const list = mhEl("div");
    const listEl = mhList({
      id: "mhList",
      items: () => st.list.map(c => ({ ...c, key: c.id })),
      columns: [
        { label: "Checkpoint", get: c => cpLabel(c) + (c.pinned ? " 📌" : "") },
        { label: "Kind", get: c => c.kind === "auto" ? "Auto" : "Manual" },
        { label: "Saved", get: c => fmtTime(c.created) },
        { label: "Frames / Shells", get: c => c.counts ? `${c.counts.frames} / ${c.counts.shells}` : "" },
        { label: "Note", get: c => (c.note || "").slice(0, 60) },
      ],
      empty: "No checkpoints yet — click <b>Save Checkpoint…</b> (checkpoints are also made automatically on save and before analysis).",
      onOpen: k => { st.a = k; st.b = "current"; F.refresh(); doCompare(); },
      actions: [
        { label: "Save Checkpoint…", id: "mhAdd", fn: () => openSave(async () => reload()) },
        { label: "Rename / Note…", id: "mhEdit", needsSel: true, fn: k => openEdit(k, async () => reload(k)) },
        { label: "Pin / Unpin", id: "mhPin", needsSel: true, fn: async k => { const c = st.list.find(x => x.id === k); try { await T.update(k, { pinned: !(c && c.pinned) }); } catch (e) { msg.textContent = e.message; } reload(k); } },
        { label: "Compare with Current", id: "mhCmpCur", needsSel: true, fn: k => { st.a = k; st.b = "current"; F.refresh(); doCompare(); } },
        { label: "Restore Checkpoint…", id: "mhRestoreAll", needsSel: true, fn: k => confirmRestoreAll(k) },
        { label: "Delete", id: "mhDel", needsSel: true, fn: async k => { try { await T.remove(k); } catch (e) { msg.textContent = e.message; } reload(); } },
      ],
    });
    listEl.classList.add("mh-list");
    list.appendChild(listEl);
    Object.assign(list, { render: () => listEl.render(), select: k => listEl.select(k) });

    const sideOpts = (withCurrent) => [...(withCurrent ? [["current", "Current model"]] : []), ...st.list.map(cpOption)];
    const cmpBtn = mhEl("button", { type: "button", class: "btn btn-small", id: "mhCompare" }, ["Compare"]);
    cmpBtn.addEventListener("click", () => doCompare());
    const body = [
      mhGroup("Checkpoints", [list]),
      mhGroup("Compare", [
        mhEl("div", { class: "mh-cmp-row" }, [
          F.select(st, "a", () => sideOpts(true), { label: "From (A)", id: "mhA" }),
          F.select(st, "b", () => sideOpts(true), { label: "To (B)", id: "mhB" }),
          cmpBtn,
        ]),
        out,
      ]),
      msg,
    ];
    const d = mhDialog({
      id: "mhDlg", title: "Model History", size: "lg", form: F, body, noCancel: true,
      intro: "Named checkpoints of the model. Compare two of them (or one with the current model), see what changed in the views, and restore a whole checkpoint or selected objects.",
      extra: [(() => { const b = mhEl("button", { type: "button", class: "btn btn-small", id: "mhSettings" }, ["Storage…"]); b.addEventListener("click", () => openSettings(listing, () => reload())); return b; })()],
    });
    foot();
    if (st.diff && st.lastKey === key()) renderDiff(out, st.diff);

    async function doCompare() {
      msg.textContent = "";
      if (!st.a || !st.b) { msg.textContent = "Pick two checkpoints (or one and the current model)."; return; }
      if (st.a === st.b) { msg.textContent = "Pick two different sides."; return; }
      out.textContent = "Comparing…";
      try {
        const res = await T.compare(st.a, st.b);
        const nm = x => x === "current" ? "current model" : cpLabel(st.list.find(c => c.id === x));
        st.cmpLabel = `${nm(st.a)} → ${nm(st.b)}`;
        st.lastKey = key();
        st.checked.clear();
        renderDiff(out, res);
        setHighlight(st.hlOn ? res : null);
        if (!st.hlOn) st.diff = res;
      } catch (e) { out.textContent = ""; msg.textContent = e.message; }
    }
    function renderDiff(box, res) {
      box.textContent = "";
      const sum = mhEl("div", { class: "mh-sum", id: "mhSummary" });
      const changedTypes = MH_ALL_TYPES.filter(t => res.summary[t] && (res.summary[t].added + res.summary[t].removed + res.summary[t].changed));
      if (res.equal) sum.appendChild(mhEl("span", { class: "mh-a", text: `Identical — no structural differences (${res.elapsed_ms} ms).` }));
      for (const t of changedTypes) {
        const s = res.summary[t];
        const chip = mhEl("span", { class: "mh-chip" + (st.off.has(t) ? " is-off" : ""), "data-type": t, title: "Click to hide / show this type in the table and the views",
          html: `<b>${mhEsc(TYPE_LABEL[t])}</b> ${s.added ? `<span class="mh-a">+${s.added}</span> ` : ""}${s.removed ? `<span class="mh-r">−${s.removed}</span> ` : ""}${s.changed ? `<span class="mh-c">~${s.changed}</span>` : ""}` });
        chip.addEventListener("click", () => { st.off.has(t) ? st.off.delete(t) : st.off.add(t); renderDiff(box, res); if (st.hl) setHighlight(res); });
        sum.appendChild(chip);
      }
      box.appendChild(sum);
      if (res.equal) return;
      const rows = res.items.filter(it => !st.off.has(it.type));
      const tbl = mhEl("table", { class: "mh-table", id: "mhTable" });
      tbl.innerHTML = `<thead><tr><th><input type="checkbox" id="mhAll" aria-label="Select all rows"></th><th>Type</th><th>Change</th><th>Object</th><th>Fields (A → B)</th></tr></thead>`;
      const tb = mhEl("tbody");
      rows.slice(0, 1500).forEach((it, i) => {
        const id = `${it.type}\u0000${it.key}`;
        const tr = mhEl("tr", { class: "mh-st-" + it.status, "data-i": String(i), ...(it.ref && it.ref.uid ? { "data-ref": `${it.ref.kind}:${it.ref.uid}` } : {}) });
        tr.innerHTML = `<td><input type="checkbox" aria-label="Select ${mhEsc(it.label)}" ${st.checked.has(id) ? "checked" : ""}></td><td>${mhEsc(TYPE_LABEL[it.type] || it.type)}</td><td>${mhEsc(it.status)}</td><td>${mhEsc(it.label)}</td><td class="mh-f">${mhEsc(fieldsText(it))}</td>`;
        tr.querySelector("input").addEventListener("change", e => { e.target.checked ? st.checked.add(id) : st.checked.delete(id); syncBtns(); });
        tr.addEventListener("click", e => { if (e.target.closest("input")) return; selectObject(it); });
        tb.appendChild(tr);
      });
      tbl.appendChild(tb);
      tbl.querySelector("#mhAll").addEventListener("change", e => {
        for (const it of rows) { const id = `${it.type}\u0000${it.key}`; e.target.checked ? st.checked.add(id) : st.checked.delete(id); }
        tb.querySelectorAll("input").forEach(x => { x.checked = e.target.checked; });
        syncBtns();
      });
      box.appendChild(mhEl("div", { class: "mh-wrap" }, [tbl]));
      if (res.truncated || rows.length > 1500) box.appendChild(mhNote(`First ${Math.min(rows.length, 1500)} of ${res.n_items} changes shown.`));
      const showB = mhEl("button", { type: "button", class: "btn btn-small", id: "mhShow" }, [st.hl ? "Hide in Views" : "Show in Views"]);
      showB.addEventListener("click", () => { st.hlOn = !st.hl; setHighlight(st.hlOn ? res : null); if (!st.hlOn) st.diff = res; showB.textContent = st.hl ? "Hide in Views" : "Show in Views"; });
      const restB = mhEl("button", { type: "button", class: "btn btn-small", id: "mhRestoreSel" }, ["Restore Selected from A"]);
      restB.title = "Put the checked objects back to their state in checkpoint A (one undo step)";
      restB.addEventListener("click", () => doRestoreSel(res));
      const btns = mhEl("div", { class: "mh-btns" }, [showB, restB]);
      box.appendChild(btns);
      function syncBtns() {
        const n = st.checked.size;
        restB.disabled = !n || st.a === "current";
        restB.textContent = n ? `Restore ${n} Selected from A` : "Restore Selected from A";
      }
      syncBtns();
    }
    function selectObject(it) {
      const ref = it.ref || {};
      if (!ref.uid || it.status === "removed") return;
      const type = ref.kind === "member" ? "member" : ref.kind === "shell" ? "shell" : ref.kind === "link" ? "link" : null;
      if (!type) return;
      S.selection = [{ type, uid: ref.uid }];
      try { sky.renderProps && sky.renderProps(); } catch { /* ignore */ }
      redraw();
      document.dispatchEvent(new CustomEvent("sky:selection-changed"));
    }
    async function doRestoreSel(res) {
      const items = res.items.filter(it => st.checked.has(`${it.type}\u0000${it.key}`)).map(it => ({ type: it.type, key: it.key_b || it.key }));
      if (!items.length || st.a === "current") return;
      try {
        const r = await restore(st.a, items);
        const sk = (r.report && r.report.skipped) || [];
        msg.textContent = `Restored ${items.length - sk.length} object${items.length - sk.length === 1 ? "" : "s"} from A as one undo step${sk.length ? ` · ${sk.length} skipped (${sk[0].reason})` : ""}.`;
        st.b = "current"; F.refresh();
        await doCompare();
        reload();
      } catch (e) { msg.textContent = e.message; }
    }
    function confirmRestoreAll(id) {
      const c = st.list.find(x => x.id === id);
      mhConfirm({ id: "mhRestoreConfirm", title: "Restore Checkpoint", okLabel: "Restore",
        html: `Replace the working model with <b>${mhEsc(cpLabel(c))}</b> (${mhEsc(fmtTime(c.created))})?<br>The current model is checkpointed first, and Edit › Undo brings it back.` })
        .then(async ok => {
          if (!ok) return;
          try {
            const r = await restore(id);
            msg.textContent = r.changed.length ? `Checkpoint restored (${r.changed.length} model sections changed) — one undo step.` : "The working model already equals this checkpoint.";
            clearHighlight();
            reload();
          } catch (e) { msg.textContent = e.message; }
        });
    }
    return d;
  }

  function openSave(after) {
    const o = { name: "", note: "" };
    const F = mhForm();
    mhDialog({ id: "mhSaveDlg", title: "Save Checkpoint", size: "sm", form: F,
      intro: "Save the current model as a named checkpoint.",
      body: [mhGroup("Checkpoint", [
        F.text(o, "name", { label: "Name", id: "mhSaveName", placeholder: "e.g. before re-sizing columns", required: true, requiredMsg: "Enter a name." }),
        F.text(o, "note", { label: "Note", id: "mhSaveNote", required: false, wide: true }),
      ])],
      onOk: async () => {
        try { const r = await checkpoint(o.name, o.note, "manual"); toast("Checkpoint saved", `“${o.name}”${r.pruned && r.pruned.length ? ` · ${r.pruned.length} old auto checkpoint(s) pruned` : ""}`, "info", 3000); }
        catch (e) { return e.message; }
        after && after();
        return true;
      } });
  }
  function openEdit(id, after) {
    const c = st.list.find(x => x.id === id);
    if (!c) return;
    const o = { name: c.name || cpLabel(c), note: c.note || "" };
    const F = mhForm();
    mhDialog({ id: "mhEditDlg", title: "Checkpoint Name and Note", size: "sm", form: F,
      body: [mhGroup("Checkpoint", [
        F.text(o, "name", { label: "Name", id: "mhEditName" }),
        F.text(o, "note", { label: "Note", id: "mhEditNote", required: false, wide: true }),
      ])],
      onOk: async () => {
        try { await T.update(id, { name: o.name, note: o.note }); } catch (e) { return e.message; }
        after && after();
        return true;
      } });
  }
  function openSettings(listing, after) {
    const s = listing.settings || {};
    const o = { max_checkpoints: s.max_checkpoints, max_auto: s.max_auto, max_mb: (s.max_bytes || 0) / 1048576 };
    const F = mhForm();
    mhDialog({ id: "mhSetDlg", title: "Model History Storage", size: "sm", form: F,
      intro: S.mock ? "Mock mode keeps up to 20 checkpoints (4 MB) in browser storage." : `Folder: <code>${mhEsc(listing.dir || "")}</code>`,
      body: [mhGroup("Limits", [
        F.num(o, "max_checkpoints", { label: "Checkpoints kept", int: true, min: 2, max: 10000, id: "mhSetMax", readonly: S.mock }),
        F.num(o, "max_auto", { label: "Automatic checkpoints kept", int: true, min: 0, max: 10000, id: "mhSetAuto", readonly: S.mock }),
        F.num(o, "max_mb", { label: "Storage limit", unit: "MB", min: 0.0625, max: 65536, id: "mhSetMb", readonly: S.mock }),
      ], { note: "When a limit is exceeded the oldest unpinned automatic checkpoints are removed first, then the oldest unpinned named ones." })],
      onOk: async () => {
        if (S.mock) return true;
        try {
          const r = await T.settings({ max_checkpoints: Math.round(o.max_checkpoints), max_auto: Math.round(o.max_auto), max_bytes: Math.round(o.max_mb * 1048576) });
          if (r.pruned && r.pruned.length) toast("Model History", `${r.pruned.length} checkpoint(s) pruned`, "info", 3000);
        } catch (e) { return e.message; }
        after && after();
        return true;
      } });
  }

  /* ---------------------------------------------------------------- wiring */
  let lastKey = null;
  const sync = () => { const k = key(); if (k !== lastKey) { lastKey = k; T.syncKey(); } };
  document.addEventListener("sky:model-replaced", () => { clearHighlight(); st.diff = null; sync(); });
  document.addEventListener("sky:model-synced", sync);
  setTimeout(sync, 0);

  const apiObj = {
    open: openDialog, checkpoint: (name, note) => checkpoint(name, note, "manual"), beforeBulk,
    list: () => T.list(), compare: (a, b) => T.compare(a, b), restore, key, clearHighlight,
    highlight: () => st.hl, state: st, transport: T,
  };
  sky.mhistory = apiObj;
  return apiObj;
}
