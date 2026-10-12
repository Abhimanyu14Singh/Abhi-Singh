/* SkyFrame Wave C — periodic auto-save with recovery on reopen.

   * Options › Auto Save…: on / off, interval (minutes, default 5), Save Now,
     the list of backup versions (newest first) with Recover….
   * Every interval, when the working model has unsaved edits that differ
     from the last auto-save, it is posted to POST /api/autosave (the
     backend keeps a ring of 5 versions in <models dir>/.autosave and
     validates the model; the working model on the server is untouched).
     Leaving the page with unsaved edits also auto-saves (sendBeacon).
   * Saving the model (or discarding the edits) clears the auto-save.
   * On reopen, an auto-save that differs from the opened model prompts
     "Recover Auto-Saved Model": Recover → POST /api/autosave/recover
     (the recovered model's sha256 equals the one recorded at save time) and
     the model is adopted with unsaved edits; Discard → DELETE /api/autosave;
     Later keeps it.
   ?mock=1: the same ring in localStorage ("sky.wc.autosave.mock").
   Undo / redo (js/history.js) is unchanged.
   CONTRACT "Wave C interface …" § Auto-save. */

import { el as aEl, esc as aEsc, dkForm as aForm, dkGroup as aGroup, dkDialog as aDialog, dkNote as aNote } from "./dlgkit.js";

const PKEY = "sky.wc.autosave";
const MKEY = "sky.wc.autosave.mock";
const KEEP = 5;

/** Canonical JSON (sorted keys) — the mock hash input. */
export function canonical(o) {
  if (Array.isArray(o)) return "[" + o.map(canonical).join(",") + "]";
  if (o && typeof o === "object") return "{" + Object.keys(o).sort().map(k => JSON.stringify(k) + ":" + canonical(o[k])).join(",") + "}";
  return JSON.stringify(o);
}
export async function sha256(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");
}

export function initWcAutosave(sky) {
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 4000) => sky.toast && sky.toast(t, m, k, ms);
  let pref = { on: true, minutes: 5 };
  try { const o = JSON.parse(localStorage.getItem(PKEY) || "null"); if (o && typeof o === "object") pref = { ...pref, ...o }; } catch { /* ignore */ }
  const savePref = () => { try { localStorage.setItem(PKEY, JSON.stringify(pref)); } catch { /* ignore */ } };
  const st = { lastT: Date.now(), lastSnap: null, saved: false, last: null, recovered: null };
  const clean = () => { const m = JSON.parse(JSON.stringify(S.model)); delete m._mock_params; return m; };

  /* ---------------- transport (live / mock) */
  const mockRing = () => { try { const r = JSON.parse(localStorage.getItem(MKEY) || "[]"); return Array.isArray(r) ? r : []; } catch { return []; } };
  const mockPut = r => { try { localStorage.setItem(MKEY, JSON.stringify(r.slice(0, KEEP))); } catch { /* quota: ignore */ } };
  async function api(method, path, body) {
    const res = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new Error((data && data.error) || `${res.status}`);
    return data;
  }
  async function post(model, reason) {
    if (S.mock) {
      const hash = await sha256(canonical(model));
      const ring = mockRing();
      ring.unshift({ saved_at: Date.now() / 1000, hash, file_name: S.fileName || null, reason, model });
      mockPut(ring);
      return { ok: true, hash, saved_at: ring[0].saved_at, versions: Math.min(ring.length, KEEP) };
    }
    return api("POST", "/api/autosave", { model, file_name: S.fileName || null, reason });
  }
  async function list() {
    if (S.mock) {
      const ring = mockRing();
      const cur = S.model ? await sha256(canonical(clean())) : null;
      return { exists: !!ring.length, current_hash: cur, versions: ring.map((r, k) => ({ version: k, saved_at: r.saved_at, file_name: r.file_name, reason: r.reason, hash: r.hash,
        stories: (r.model.stories || []).length, members: (r.model.members || []).length, shells: (r.model.shells || []).length })) };
    }
    return api("GET", "/api/autosave");
  }
  async function fetchRecover(version = 0) {
    if (S.mock) {
      const r = mockRing()[version];
      if (!r) throw new Error("no auto-saved model");
      return { model: r.model, hash: await sha256(canonical(r.model)), saved_hash: r.hash, saved_at: r.saved_at, file_name: r.file_name };
    }
    return api("POST", "/api/autosave/recover", { version });
  }
  async function clear() {
    if (S.mock) { const n = mockRing().length; mockPut([]); try { localStorage.removeItem(MKEY); } catch { /* ignore */ } return { ok: true, removed: n }; }
    return api("DELETE", "/api/autosave");
  }

  /* ---------------- save */
  async function saveNow(reason = "manual") {
    if (!S.model) return null;
    const m = clean();
    const r = await post(m, reason);
    st.lastT = Date.now(); st.lastSnap = JSON.stringify(m); st.saved = true; st.last = r;
    syncInfo();
    return r;
  }
  async function tick() {
    if (!pref.on || !S.model) return;
    if (S.dirty) {
      if (Date.now() - st.lastT < pref.minutes * 60000) return;
      const snap = JSON.stringify(S.model);
      if (snap === st.lastSnap) { st.lastT = Date.now(); return; }
      try { await saveNow("interval"); } catch (e) { console.warn("auto-save failed:", e.message); st.lastT = Date.now(); }
    } else if (st.saved) {                             // saved / discarded → no recovery needed
      st.saved = false; st.lastSnap = null;
      try { await clear(); } catch { /* ignore */ }
    }
  }
  const timer = setInterval(() => { tick(); }, 5000);
  window.addEventListener("pagehide", () => {
    if (!pref.on || !S.dirty || !S.model || S.mock) return;
    try {
      const snap = JSON.stringify(S.model);
      if (snap === st.lastSnap) return;
      navigator.sendBeacon("/api/autosave", new Blob([JSON.stringify({ model: clean(), file_name: S.fileName || null, reason: "close" })], { type: "application/json" }));
    } catch { /* ignore */ }
  });

  /* ---------------- recovery */
  const when = t => (t ? new Date(t * 1000).toLocaleString() : "—");
  async function recover(version = 0) {
    const r = await fetchRecover(version);
    sky.adoptModel(r.model, r.file_name || null);
    if (sky.markDirty) sky.markDirty();                // recovered edits are unsaved
    st.recovered = { hash: r.hash, saved_hash: r.saved_hash, version, saved_at: r.saved_at };
    st.lastSnap = JSON.stringify(S.model); st.saved = true; st.lastT = Date.now();
    toast("Model recovered", `Auto-save of ${when(r.saved_at)} restored${r.hash === r.saved_hash ? " (same model hash)" : ""}. Save it to keep it.`, "info", 6000);
    return st.recovered;
  }
  function openRecover(info, { startup = false } = {}) {
    const vs = info.versions || [];
    if (!vs.length) { toast("Auto Save", "There is no auto-saved model.", "info"); return null; }
    const V = { version: String(vs[0].version) };
    const F = aForm();
    const opts = vs.map(v => [String(v.version), `${when(v.saved_at)}${v.file_name ? " · " + v.file_name : ""}`, `${v.stories} stories · ${v.members} frames · ${v.shells} shells · ${v.reason || "interval"}`]);
    const discard = aEl("button", { type: "button", class: "btn btn-small", id: "wcAsDiscard", text: "Discard Auto-Save" });
    const body = [aGroup("Auto-Saved Versions", [F.radio(V, "version", opts, { idPrefix: "wcAsVer_" })]),
      aNote(startup ? "SkyFrame closed with unsaved edits. Recover the auto-saved model? (Later keeps it for the next start.)" : "Recovering replaces the working model; it opens with unsaved edits.")];
    const dlg = aDialog({ id: "wcRecoverDlg", title: "Recover Auto-Saved Model", iconId: "menu-file", size: "md", form: F, body, okLabel: "Recover",
      cancelLabel: startup ? "Later" : "Cancel", extra: [discard],
      onOk: async () => { try { await recover(+V.version); } catch (e) { return e.message; } } });
    discard.addEventListener("click", async () => { try { await clear(); } catch { /* ignore */ } dlg.close(); toast("Auto Save", "Auto-saved versions discarded.", "info", 2500); });
    return dlg;
  }
  async function checkStartup() {
    if (!pref.on) return null;
    try {
      const info = await list();
      if (!info.exists || !info.versions.length) return null;
      if (info.current_hash && info.versions[0].hash === info.current_hash) return null;   // nothing new to recover
      return openRecover(info, { startup: true });
    } catch (e) { console.warn("auto-save check failed:", e.message); return null; }
  }

  /* ---------------- Options › Auto Save… */
  let infoEl = null;
  function syncInfo() { if (infoEl) infoEl.textContent = st.last ? `Last auto-save: ${when(st.last.saved_at)} · ${st.last.versions} version(s) kept` : "No auto-save in this session yet."; }
  function openOptions() {
    const V = { on: !!pref.on, minutes: pref.minutes };
    const F = aForm();
    infoEl = aEl("p", { class: "muted", id: "wcAsInfo" });
    syncInfo();
    const now = aEl("button", { type: "button", class: "btn btn-small", id: "wcAsNow", text: "Save Now" });
    now.addEventListener("click", async () => { try { await saveNow("manual"); toast("Auto Save", "Model auto-saved."); } catch (e) { toast("Auto Save", e.message, "error"); } });
    const rec = aEl("button", { type: "button", class: "btn btn-small", id: "wcAsRecover", text: "Recover…" });
    rec.addEventListener("click", async () => { try { dlg.close(); openRecover(await list()); } catch (e) { toast("Auto Save", e.message, "error"); } });
    const body = [aGroup("Auto Save", [
      F.check(V, "on", { label: "Save a backup of the model automatically", id: "wcAsOn" }),
      F.num(V, "minutes", { label: "Every", unit: "min", int: true, min: 1, max: 120, id: "wcAsMin", hint: "only when there are unsaved edits" }),
    ]), infoEl, aNote(`The last ${KEEP} versions are kept next to the saved models; saving the model clears them. Undo / redo is unchanged.`)];
    const dlg = aDialog({ id: "wcAutosaveDlg", title: "Auto Save", iconId: "menu-file", size: "sm", form: F, body, extra: [now, rec],
      onOk: () => { pref.on = !!V.on; pref.minutes = V.minutes; savePref(); st.lastT = Math.min(st.lastT, Date.now()); } });
    return dlg;
  }
  if (sky.menu) sky.menu.add("Options", { label: "Auto Save…", act: "opt-wc-autosave", hint: () => (pref.on ? `every ${pref.minutes} min` : "off"), fn: openOptions });

  sky.wc.autosave = { saveNow, list, recover, clear, openRecover, openOptions, checkStartup, tick, canonical, sha256,
    pref: () => ({ ...pref }), setPref: (k, v) => { pref[k] = v; savePref(); }, state: () => ({ ...st, lastSnap: !!st.lastSnap }), stop: () => clearInterval(timer) };
  setTimeout(() => { checkStartup(); }, 600);
  void aEsc;
}
