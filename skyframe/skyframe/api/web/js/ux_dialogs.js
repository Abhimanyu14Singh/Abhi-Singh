/* SkyFrame UX — shared dialog enhancer.
   Upgrades EVERY existing dialog (.modal-backdrop > .modal: the static ones in
   index.html that toggle .hidden and the dynamic ones built by analysisdlg /
   casedlg / combodlg / assigndlg / nls_ui / tables / b9 …) at open time —
   without editing any of them:
     · max-height + scrollable body + sticky footer (CSS, .ux-dlg)
     · draggable header, resize grip, remembered size/position per dialog id
       (localStorage, try/catch; double-click the header to reset)
     · "?" help button → short ETABS-equivalent description (js/helpdocs.js)
     · keyboard: Enter = OK (primary button) unless focus is in a textarea /
       button / link; Esc = Cancel / close for dialogs that do not handle it;
       stray single-key app shortcuts (tool keys, R = run, Delete…) no longer
       leak to the workspace behind an open dialog; focus moves into a dialog
       when it opens.
   UX pass 2:
     · footer normalisation — ETABS order "OK · Cancel · Apply" and one
       button style in every dialog (buttons are moved, never rebuilt; a
       footer a module re-renders is normalised again)
     · focus trap (Tab / Shift+Tab cycle inside the top-most dialog), focus
       returns to the opener on close, role / aria-modal / aria-labelledby
     · Enter commits the field being typed in (blur → "change") before OK
   Detection is by MutationObserver (childList of #app/body + the class
   attribute of each backdrop) — no polling. */

import { helpForDialog as uxHelpForDialog } from "./helpdocs.js";

const LS_PREFIX = "skyframe.ux.dlg.";
const lsGet = k => { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : null; } catch { return null; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage blocked */ } };
const lsDel = k => { try { localStorage.removeItem(k); } catch { /* storage blocked */ } };

const isShown = b => b && b.isConnected && !b.classList.contains("hidden") && b.style.display !== "none";
const PRIMARY_RE = /^(ok|done|save|import|check|apply & close|create|close)$/i;
const CANCEL_RE = /^(cancel|close)$/i;

/** Visible dialogs, top-most last (confirm modal always counts as top). */
export function visibleDialogs() {
  const list = [...document.querySelectorAll(".modal-backdrop")].filter(isShown);
  const conf = document.getElementById("confirmModal");
  if (conf && isShown(conf)) { list.splice(list.indexOf(conf), 1); list.push(conf); }
  return list;
}
export const topDialog = () => { const l = visibleDialogs(); return l[l.length - 1] || null; };

const dlgKey = back => {
  if (back.id) return back.id;
  const h = back.querySelector(".modal-head h2, .modal-head");
  return "t-" + (h ? h.textContent : "dialog").trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 40);
};
const dlgTitle = back => {
  const h = back.querySelector(".modal-head h2");
  return h ? h.textContent.trim() : "";
};

function footButtons(modal) {
  const foot = modal.querySelector(":scope > .modal-foot, :scope > footer") || modal.querySelector(".modal-foot");
  return foot ? [...foot.querySelectorAll("button")].filter(b => b.offsetParent !== null) : [];
}
/** The dialog's OK-equivalent: an "OK" button, else the accent (.btn-run /
    .btn-primary) footer button, else a lone Done/Close. */
export function primaryButton(modal) {
  const bs = footButtons(modal);
  return bs.find(b => /^ok$/i.test(b.textContent.trim()))
    || bs.find(b => b.matches(".btn-run, .btn-primary, .btn-accent") && !CANCEL_RE.test(b.textContent.trim()))
    || (bs.length === 1 && PRIMARY_RE.test(bs[0].textContent.trim()) ? bs[0] : null);
}
function cancelButton(modal) {
  const bs = footButtons(modal);
  return bs.find(b => /^cancel$/i.test(b.textContent.trim()))
    || modal.querySelector(".modal-head .icon-btn:not(.ux-help-btn)")
    || bs.find(b => CANCEL_RE.test(b.textContent.trim())) || null;
}

/* ---------------- footer normalisation (UX audit D2) ----------------
   ETABS order everywhere: [note] [other actions…] OK · Cancel · Apply, with
   one consistent style: the commit button (OK / Save / Import / Create…) is
   the accent button, Cancel / Apply are neutral; a lone Done / Close is the
   accent button. Buttons are MOVED (never re-created), so every module's
   listeners and ids keep working. Only buttons that already share one parent
   are reordered; a non-standard footer otherwise keeps its layout. */
const COMMIT_RE = /^(ok|save|import|create|apply & close|apply and close|assign|open|check|generate|select|show)$/i;
const btnText = b => (b.textContent || "").trim().replace(/\s+/g, " ").replace(/\s*\((enter|esc)\)$/i, "");
export function normalizeFooter(modal) {
  const foot = modal.querySelector(":scope > .modal-foot, :scope > footer") || modal.querySelector(".modal-foot");
  if (!foot) return null;
  const all = [...foot.querySelectorAll("button")].filter(b => !b.closest(".ux-empty-cta"));
  const vis = all.filter(b => !b.classList.contains("hidden") && b.style.display !== "none");
  const ok = vis.find(b => /^ok$/i.test(btnText(b))) || vis.find(b => COMMIT_RE.test(btnText(b)));
  // "Close" next to an OK is that dialog's Cancel
  const cancel = vis.find(b => /^cancel$/i.test(btnText(b))) || (ok ? vis.find(b => b !== ok && /^close$/i.test(btnText(b))) : null);
  const apply = vis.find(b => /^apply$/i.test(btnText(b)));
  const lone = vis.length === 1 && /^(done|close)$/i.test(btnText(vis[0])) ? vis[0] : null;
  const order = [ok, cancel, apply].filter(Boolean);
  if (order.length > 1) {
    const parent = order[0].parentElement;
    if (order.every(b => b.parentElement === parent)) {
      // already in order at the end → no DOM writes (keeps observers quiet)
      const kids = [...parent.children].filter(k => k.tagName === "BUTTON");
      const tail = kids.slice(-order.length);
      if (!order.every((b, i) => tail[i] === b)) {
        const keep = document.activeElement;
        order.forEach(b => parent.appendChild(b));
        if (keep && keep !== document.activeElement && keep.isConnected) { try { keep.focus({ preventScroll: true }); } catch { /* ignore */ } }
      }
    }
  }
  const mark = (b, cls) => { if (b && !b.classList.contains(cls)) b.classList.add(cls); };
  mark(ok || lone, "ux-btn-primary");
  mark(cancel, "ux-btn-secondary");
  mark(apply, "ux-btn-secondary");
  foot.classList.add("ux-foot");
  return { ok, cancel, apply, lone };
}

const FOCUSABLE = "button:not([disabled]), [href], input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";
export function focusables(root) {
  return [...root.querySelectorAll(FOCUSABLE)].filter(n => n.offsetParent !== null && !n.closest(".hidden, [hidden], .ux-grip") && getComputedStyle(n).visibility !== "hidden");
}

export function installDialogs(sky) {
  const vw = () => window.innerWidth, vh = () => window.innerHeight;

  /* ---------------- geometry (drag / resize / remember) ---------------- */
  function applyGeom(back, modal) {
    const g = lsGet(LS_PREFIX + dlgKey(back));
    modal.style.transform = ""; modal.style.width = ""; modal.style.height = "";
    if (!g) return;
    if (g.w) modal.style.width = Math.min(g.w, vw() - 16) + "px";
    if (g.h) modal.style.height = Math.min(g.h, vh() - 16) + "px";
    if (g.dx || g.dy) {
      // clamp so the header stays reachable at the current viewport size
      const r = modal.getBoundingClientRect();
      const cx = r.left, cy = r.top;
      const dx = Math.max(-cx + 8, Math.min(g.dx || 0, vw() - cx - 120));
      const dy = Math.max(-cy + 8, Math.min(g.dy || 0, vh() - cy - 48));
      modal.style.transform = `translate(${dx}px, ${dy}px)`;
      modal.dataset.uxDx = dx; modal.dataset.uxDy = dy;
    } else { modal.dataset.uxDx = 0; modal.dataset.uxDy = 0; }
  }
  function saveGeom(back, modal, patch) {
    const k = LS_PREFIX + dlgKey(back);
    lsSet(k, { ...(lsGet(k) || {}), ...patch });
  }

  function wireDrag(back, modal, head) {
    head.classList.add("ux-drag");
    head.addEventListener("pointerdown", e => {
      if (e.button !== 0 || e.target.closest("button, input, select, textarea, a, label")) return;
      const x0 = e.clientX, y0 = e.clientY;
      const dx0 = +modal.dataset.uxDx || 0, dy0 = +modal.dataset.uxDy || 0;
      const r = modal.getBoundingClientRect();
      const baseL = r.left - dx0, baseT = r.top - dy0;
      let moved = false;
      const mv = ev => {
        let dx = dx0 + ev.clientX - x0, dy = dy0 + ev.clientY - y0;
        dx = Math.max(-baseL + 4 - r.width + 120, Math.min(dx, vw() - baseL - 120));
        dy = Math.max(-baseT + 4, Math.min(dy, vh() - baseT - 40));
        if (!moved && Math.hypot(ev.clientX - x0, ev.clientY - y0) < 3) return;
        moved = true;
        modal.style.transform = `translate(${dx}px, ${dy}px)`;
        modal.dataset.uxDx = dx; modal.dataset.uxDy = dy;
      };
      const up = () => {
        window.removeEventListener("pointermove", mv);
        window.removeEventListener("pointerup", up);
        head.classList.remove("ux-dragging");
        if (moved) saveGeom(back, modal, { dx: +modal.dataset.uxDx, dy: +modal.dataset.uxDy });
      };
      head.classList.add("ux-dragging");
      window.addEventListener("pointermove", mv);
      window.addEventListener("pointerup", up);
    });
    head.addEventListener("dblclick", e => {
      if (e.target.closest("button, input, select, textarea")) return;
      lsDel(LS_PREFIX + dlgKey(back));
      modal.style.transform = ""; modal.style.width = ""; modal.style.height = "";
      modal.dataset.uxDx = 0; modal.dataset.uxDy = 0;
    });
    head.title = head.title || "Drag to move · double-click to reset size and position";
  }

  function wireGrip(back, modal) {
    const grip = document.createElement("div");
    grip.className = "ux-grip";
    grip.title = "Drag to resize";
    grip.setAttribute("aria-hidden", "true");
    modal.appendChild(grip);
    grip.addEventListener("pointerdown", e => {
      if (e.button !== 0) return;
      e.preventDefault(); e.stopPropagation();
      const r = modal.getBoundingClientRect();
      const x0 = e.clientX, y0 = e.clientY;
      const mv = ev => {
        modal.style.width = Math.max(320, Math.min(r.width + ev.clientX - x0, vw() - 16)) + "px";
        modal.style.height = Math.max(180, Math.min(r.height + ev.clientY - y0, vh() - 16)) + "px";
      };
      const up = () => {
        window.removeEventListener("pointermove", mv);
        window.removeEventListener("pointerup", up);
        const rr = modal.getBoundingClientRect();
        saveGeom(back, modal, { w: Math.round(rr.width), h: Math.round(rr.height) });
      };
      window.addEventListener("pointermove", mv);
      window.addEventListener("pointerup", up);
    });
  }

  /* ---------------- "?" contextual help ---------------- */
  function wireHelp(back, modal, head) {
    const close = head.querySelector(".icon-btn");
    const hb = document.createElement("button");
    hb.type = "button";
    hb.className = "icon-btn ux-help-btn";
    hb.textContent = "?";
    hb.title = "What is this dialog? (ETABS equivalent)";
    hb.setAttribute("aria-label", "Help for this dialog");
    hb.setAttribute("aria-expanded", "false");
    // DOM order keeps the close "×" as the header's FIRST .icon-btn (scripts and
    // tests close dialogs via ".modal-head .icon-btn"); CSS `order` shows "?" before it
    if (close) close.after(hb); else head.appendChild(hb);
    hb.addEventListener("click", e => {
      e.stopPropagation();
      let pop = modal.querySelector(":scope > .ux-help-pop");
      if (pop) { pop.remove(); hb.setAttribute("aria-expanded", "false"); return; }
      const h = uxHelpForDialog(back.id, dlgTitle(back));
      pop = document.createElement("div");
      pop.className = "ux-help-pop";
      pop.setAttribute("role", "note");
      const t = document.createElement("div");
      t.className = "ux-help-text";
      t.textContent = h ? h.text : "Edits made here apply to the working model when you press OK. Cancel or Esc discards them.";
      pop.appendChild(t);
      const meta = document.createElement("div");
      meta.className = "ux-help-meta";
      meta.textContent = (h ? "ETABS: " + h.etabs + " · " : "") + "Enter = OK · Esc = Cancel · drag the title bar to move";
      pop.appendChild(meta);
      head.after(pop);
      hb.setAttribute("aria-expanded", "true");
    });
  }

  /* ---------------- enhance + on-open ---------------- */
  function enhance(back) {
    const modal = back.querySelector(":scope > .modal") || back.querySelector(".modal");
    if (!modal || modal.dataset.uxEnh) return modal;
    modal.dataset.uxEnh = "1";
    modal.classList.add("ux-dlg");
    const head = modal.querySelector(":scope > .modal-head, :scope > header");
    if (head) { wireDrag(back, modal, head); wireHelp(back, modal, head); }
    wireGrip(back, modal);
    // a11y: every dialog is a labelled modal dialog
    if (!modal.getAttribute("role")) modal.setAttribute("role", "dialog");
    if (!modal.hasAttribute("aria-modal")) modal.setAttribute("aria-modal", "true");
    if (!modal.getAttribute("aria-labelledby") && !modal.getAttribute("aria-label")) {
      const h = head && head.querySelector("h2");
      if (h) { if (!h.id) h.id = (back.id || "uxDlg" + Math.random().toString(36).slice(2, 7)) + "Lbl"; modal.setAttribute("aria-labelledby", h.id); }
    }
    // footers rebuilt by their module (re-render) are re-normalised
    const foot = modal.querySelector(":scope > .modal-foot, :scope > footer") || modal.querySelector(".modal-foot");
    if (foot) {
      let q = 0;
      new MutationObserver(() => {
        if (q) return;
        q = requestAnimationFrame(() => { q = 0; if (isShown(back)) normalizeFooter(modal); });
      }).observe(foot, { childList: true, subtree: true });
    }
    // keys typed inside a dialog stay inside it (no tool/run shortcuts behind)
    back.addEventListener("keydown", e => {
      if (e.key === "Escape" || e.key === "Enter" || e.key === "Tab" || e.key === "F1") return;
      if (e.ctrlKey || e.metaKey) return;
      e.stopPropagation();
    });
    return modal;
  }
  function onOpen(back) {
    const modal = enhance(back);
    if (!modal) return;
    const pop = modal.querySelector(":scope > .ux-help-pop");
    if (pop) pop.remove();
    applyGeom(back, modal);
    // remember the opener so focus returns to it when the dialog closes
    const ae = document.activeElement;
    if (ae && ae !== document.body && !back.contains(ae)) openers.set(back, ae);
    try { normalizeFooter(modal); } catch (e) { console.warn("ux footer", e); }
    // keyboard titles on OK / Cancel
    requestAnimationFrame(() => {
      if (!isShown(back)) return;
      try { normalizeFooter(modal); } catch { /* ignore */ }
      const ok = primaryButton(modal), cancel = cancelButton(modal);
      if (ok && !ok.title) ok.title = ok.textContent.trim() + " (Enter)";
      if (cancel && cancel.matches("button") && !cancel.title) cancel.title = cancel.textContent.trim() + " (Esc)";
      // move focus into the dialog so keys stop reaching the workspace
      if (!back.contains(document.activeElement)) {
        const f = modal.querySelector(".modal-body input:not([type=hidden]):not([disabled]):not([readonly]), .modal-body select:not([disabled]), .modal-body textarea:not([disabled])");
        const target = f && f.offsetParent !== null ? f : (ok || modal.querySelector("button"));
        if (target) { try { target.focus({ preventScroll: true }); } catch { /* ignore */ } }
      }
    });
  }

  /* focus returns to the element that opened the dialog (a11y) */
  const openers = new WeakMap();
  function onClosed(back) {
    const op = openers.get(back);
    openers.delete(back);
    setTimeout(() => {
      if (topDialog()) return;   // another dialog took over
      const ae = document.activeElement;
      if (ae && ae !== document.body && ae.isConnected && ae.offsetParent !== null) return;
      if (op && op.isConnected && op.offsetParent !== null) { try { op.focus({ preventScroll: true }); } catch { /* ignore */ } }
    }, 0);
  }

  const watched = new WeakSet();
  const attrObs = new MutationObserver(recs => {
    for (const r of recs) {
      const b = r.target;
      const was = r.oldValue == null ? false : !/\bhidden\b/.test(r.oldValue);
      if (isShown(b) && !was) onOpen(b);
      else if (!isShown(b) && was) onClosed(b);
    }
  });
  function watch(back) {
    if (watched.has(back)) return;
    watched.add(back);
    attrObs.observe(back, { attributes: true, attributeFilter: ["class"], attributeOldValue: true });
    if (isShown(back)) onOpen(back);
  }
  document.querySelectorAll(".modal-backdrop").forEach(watch);
  const addObs = new MutationObserver(recs => {
    for (const r of recs) {
      for (const n of r.addedNodes)
        if (n.nodeType === 1 && n.classList.contains("modal-backdrop")) watch(n);
      for (const n of r.removedNodes)
        if (n.nodeType === 1 && n.classList && n.classList.contains("modal-backdrop") && openers.has(n)) onClosed(n);
    }
  });
  const app = document.getElementById("app");
  if (app) addObs.observe(app, { childList: true });
  addObs.observe(document.body, { childList: true });

  /* ---------------- global Enter / Esc ---------------- */
  let pending = null;   // {back, key} captured at window-capture time
  window.addEventListener("keydown", e => {
    pending = null;
    if (e.key !== "Enter" && e.key !== "Escape") return;
    if (e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return;
    const back = topDialog();
    if (!back) return;
    if (e.key === "Escape") {
      const pop = back.querySelector(".ux-help-pop");
      if (pop) { pop.remove(); e.stopImmediatePropagation(); e.preventDefault(); return; }
    }
    pending = { back, key: e.key };
  }, true);
  window.addEventListener("keydown", e => {
    const p = pending; pending = null;
    if (!p || p.key !== e.key || e.defaultPrevented) return;
    const back = p.back;
    if (!isShown(back) || topDialog() !== back) return;   // the dialog handled it itself
    const modal = back.querySelector(".modal");
    if (!modal) return;
    if (e.key === "Escape") {
      const c = cancelButton(modal);
      if (c) { e.preventDefault(); c.click(); }
      return;
    }
    const t = e.target;
    if (t && t.closest) {
      if (t.closest("textarea, button, a, [contenteditable=''], [contenteditable=true], summary")) return;
      if (t.closest("form") && back.contains(t.closest("form"))) return;   // native submit
      if (t.matches && t.matches("[data-no-enter], input[type=file]")) return;
      if (t !== document.body && !back.contains(t)) return;
    }
    const ok = primaryButton(modal);
    if (!ok || ok.disabled || ok.offsetParent === null) return;
    // editor-style dialogs (Done / Close) never close on Enter — only OK-type actions
    if (/^(done|close)$/i.test(ok.textContent.trim())) return;
    e.preventDefault();
    // commit the field being typed in first: most dialogs read their inputs
    // on "change", which the browser fires only on blur — without this,
    // Enter would press OK with the previous value (UX pass 2)
    const typing = t && t.matches && t.matches("input, select") && back.contains(t) ? t : null;
    if (typing) typing.blur();
    ok.click();
    if (typing && typing.isConnected && isShown(back) && topDialog() === back) { try { typing.focus({ preventScroll: true }); } catch { /* ignore */ } }
  });
  /* focus trap (a11y): Tab / Shift+Tab cycle inside the top-most dialog; a
     Tab pressed while focus is outside it (e.g. on <body>) moves into it */
  window.addEventListener("keydown", e => {
    if (e.key !== "Tab" || e.ctrlKey || e.metaKey || e.altKey) return;
    const back = topDialog();
    if (!back) return;
    if (e.target && e.target.closest && e.target.closest(".ux-cmdk-back, .ux-keys-back")) return;
    const modal = back.querySelector(".modal") || back;
    const f = focusables(modal);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    const a = document.activeElement;
    if (!modal.contains(a)) { e.preventDefault(); (e.shiftKey ? last : first).focus(); return; }
    if (!e.shiftKey && a === last) { e.preventDefault(); first.focus(); }
    else if (e.shiftKey && a === first) { e.preventDefault(); last.focus(); }
  }, true);
  // keys pressed with focus outside an open dialog (e.g. on <body>) must not
  // trigger single-key workspace shortcuts behind it
  window.addEventListener("keydown", e => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (!(e.key.length === 1 || e.key === "Delete" || e.key === "Backspace")) return;
    const back = topDialog();
    if (!back || back.contains(e.target)) return;
    const tag = (e.target && e.target.tagName || "").toLowerCase();
    if (["input", "select", "textarea"].includes(tag)) return;
    if (e.target.closest && e.target.closest(".ux-cmdk-back")) return;
    e.stopPropagation();
  }, true);

  sky.ux = sky.ux || {};
  Object.assign(sky.ux, {
    dialogs: { visible: visibleDialogs, top: topDialog, primaryButton, enhance, onOpen, normalizeFooter, focusables,
      resetGeometry: id => lsDel(LS_PREFIX + id) },
  });
}
