/* SkyFrame Wave C — model lock after a run (ETABS Analyze › Lock / Unlock
   Model).

   * After an analysis the model is LOCKED (Options › Lock Model After Run,
     on by default; Analyze › Lock / Unlock Model toggles it by hand).
   * Starting an editing command while locked — an Edit / Draw / Assign menu
     command, a draw tool of the palette, or the Define dialogs' OK —
     prompts "Unlock the model? Unlocking deletes the analysis results.":
       Unlock  → the results are deleted (__sky.clearResults) and the command
                 runs / the edit stays;
       Cancel  → nothing runs; an edit that already reached the model (Define
                 dialog OK, keyboard draw shortcuts, scripts) is undone (one
                 undo step, js/history.js).
   * Status bar: a "Locked" chip (click = unlock prompt).
   Per-browser preference (localStorage "sky.wc.lock"); never model data.
   CONTRACT "Wave C interface …" § Model lock. */

import { el as lkEl, dkDialog as lkDialog } from "./dlgkit.js";

const KEY = "sky.wc.lock";
const EDIT_PREFIX = /^(edit|draw|asn)-/;
const SAFE = new Set(["draw-select", "draw-plan", "draw-elev", "draw-w8-snap", "edit-copy", "edit-dbedit"]);
const DRAW_TOOLS = new Set(["column", "beam", "brace", "wall", "slab", "link", "spring", "linespring", "erase"]);

export function initWcLock(sky) {
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 4000) => sky.toast && sky.toast(t, m, k, ms);
  let pref = { afterRun: true };
  try { const o = JSON.parse(localStorage.getItem(KEY) || "null"); if (o && typeof o === "object") pref = { ...pref, ...o }; } catch { /* ignore */ }
  const savePref = () => { try { localStorage.setItem(KEY, JSON.stringify(pref)); } catch { /* ignore */ } };
  const st = { locked: false, prompting: null, bypass: 0, lastResults: S.results || null };

  /* ---------------- status chip */
  const chip = lkEl("button", { type: "button", class: "sb-chip wc-lock-chip hidden", id: "wcLockChip", title: "The model is locked: analysis results exist. Click to unlock (deletes the results)." }, ["🔒 Locked"]);
  chip.addEventListener("click", () => requestUnlock("Unlock Model"));
  const status = sky.etabs && sky.etabs.el && sky.etabs.el.status;
  if (status) status.appendChild(chip);
  const sync = () => {
    chip.classList.toggle("hidden", !st.locked);
    if (sky.menu && sky.menu.refresh) try { sky.menu.refresh(); } catch { /* ignore */ }
  };
  function setLocked(on) { st.locked = !!on; sync(); }

  /** Unlock = delete the results. Resolves true when unlocked. */
  function unlock() {
    st.locked = false;
    if (S.results && sky.clearResults) sky.clearResults();
    st.lastResults = null;
    sync();
    toast("Model unlocked", "The analysis results were deleted; run the analysis again (F5) after editing.", "info", 4500);
    return true;
  }
  function requestUnlock(what) {
    if (!st.locked) return Promise.resolve(true);
    if (st.prompting) return st.prompting;
    st.prompting = new Promise(resolve => {
      let done = false;
      lkDialog({ id: "wcLockDlg", title: "Model Is Locked", iconId: "menu-analyze", size: "sm", okLabel: "Unlock",
        body: [lkEl("p", { class: "dk-msg", html: `The model is locked because analysis results exist${what ? ` (<b>${what}</b>)` : ""}.<br>Unlock it to edit? <b>Unlocking deletes the analysis results.</b>` })],
        onOk: () => { done = true; unlock(); resolve(true); },
        onClose: () => { st.prompting = null; if (!done) resolve(false); } });
    });
    return st.prompting;
  }

  /* ---------------- lock on results */
  document.addEventListener("sky:results-changed", () => {
    if (S.results && S.results !== st.lastResults) { st.lastResults = S.results; if (pref.afterRun) setLocked(true); }
    if (!S.results) { st.lastResults = null; setLocked(false); }
  });
  document.addEventListener("sky:model-replaced", () => { st.lastResults = S.results || null; setLocked(false); });

  /* ---------------- pre-emptive: menu / palette / context menu commands */
  const editingAct = act => !!act && EDIT_PREFIX.test(act) && !SAFE.has(act) && !/^edit-(undo|redo)$/.test(act);
  window.addEventListener("click", e => {
    if (!st.locked || st.bypass) return;
    const t = e.target && e.target.closest ? e.target : null;
    if (!t) return;
    const item = t.closest("#etabsMenubar [data-act], .wf-ctx [data-act], #etabsPalette [data-tool], .tool-btn[data-tool]");
    if (!item) return;
    const act = item.dataset.act, tool = item.dataset.tool;
    if (act ? !editingAct(act) : !DRAW_TOOLS.has(tool)) return;
    e.stopImmediatePropagation(); e.preventDefault();
    requestUnlock(act ? (item.textContent || act).trim().replace(/\s+/g, " ").slice(0, 40) : `Draw ${tool}`).then(ok => {
      if (!ok) return;
      st.bypass++;
      try { if (act) { if (sky.etabs && sky.etabs.clickItem && !sky.etabs.clickItem(act)) item.click(); } else item.click(); }
      finally { st.bypass--; }
    });
  }, true);
  if (sky.etabs && sky.etabs.clickItem) {               // palette / shortcuts / scripts
    const orig = sky.etabs.clickItem;
    sky.etabs.clickItem = act => {
      if (st.locked && !st.bypass && editingAct(act)) {
        requestUnlock(act).then(ok => { if (ok) { st.bypass++; try { orig(act); } finally { st.bypass--; } } });
        return true;
      }
      return orig(act);
    };
  }

  /* ---------------- post-hoc: any edit that reached the model */
  document.addEventListener("sky:model-changed", () => {
    if (!st.locked || st.bypass || !S.dirty) return;
    // let the history record this step first, then ask
    setTimeout(() => requestUnlock("model edit").then(ok => {
      if (ok) return;
      if (sky.edit && sky.edit.undo) { st.bypass++; try { sky.edit.undo(); } finally { st.bypass--; } }
      toast("Model locked", "The edit was undone — the model stays locked with its results.", "info", 4000);
    }), 0);
  });

  /* ---------------- menus */
  if (sky.menu) {
    sky.menu.add("Analyze", { label: "Lock / Unlock Model", act: "an-wc-lock", hint: "locked after a run; unlocking deletes the results",
      check: () => st.locked, fn: () => { if (st.locked) requestUnlock("Unlock Model"); else { setLocked(true); toast("Model locked", "Editing now asks to unlock (which deletes the results)."); } }, before: "an-run" });
    sky.menu.add("Options", { label: "Lock Model After Run", act: "opt-wc-lockrun", check: () => !!pref.afterRun,
      fn: () => { pref.afterRun = !pref.afterRun; savePref(); sync(); } });
  }
  if (S.results && pref.afterRun) { st.lastResults = S.results; setLocked(true); }
  sync();

  sky.wc.lock = { locked: () => st.locked, lock: () => setLocked(true), unlock, requestUnlock, pref: () => ({ ...pref }),
    setPref: (k, v) => { pref[k] = v; savePref(); sync(); } };
}
