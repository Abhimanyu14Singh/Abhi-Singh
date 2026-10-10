/* SkyFrame UX — shared helpers for the ux_* modules (DOM builder, storage,
   the dialog shell, extra icons registered into icons.js at runtime). */

import { ICONS as uxICONS, icon as uxIcon } from "./icons.js";

export const icon = uxIcon;

/* Extra glyphs in the icons.js house style (20×20, stroke=currentColor).
   Registered additively into the shared ICONS map so icon("ux-…") works. */
Object.assign(uxICONS, {
  "ux-new": '<path d="M6 3 H12 L16 7 V17 H6 Z"/><path d="M12 3 V7 H16"/><path d="M11 10 V15 M8.5 12.5 H13.5"/>',
  "ux-open": '<path d="M3 6 V16 H15 L17.5 9 H6 L3.5 16"/><path d="M3 6 H8 L9.5 7.5 H14 V9"/>',
  "ux-save": '<path d="M4 4 H14 L16 6 V16 H4 Z"/><path d="M7 4 V8 H13 V4"/><rect x="7" y="11" width="6" height="5"/>',
  "ux-undo": '<path d="M7 5 L4 8 L7 11"/><path d="M4 8 H12 A4 4 0 0 1 12 16 H8"/>',
  "ux-redo": '<path d="M13 5 L16 8 L13 11"/><path d="M16 8 H8 A4 4 0 0 0 8 16 H12"/>',
  "ux-check": '<path d="M10 3 L16 5 V10 C16 14 13 16 10 17 C7 16 4 14 4 10 V5 Z"/><path d="M7.4 10 L9.3 12 L12.8 8"/>',
  "ux-tables": '<rect x="3.5" y="4" width="13" height="12" rx="1"/><path d="M3.5 8 H16.5 M3.5 12 H16.5 M8 8 V16"/>',
  "ux-deformed": '<path d="M5 17 C5 12 7 8 9 4" stroke-dasharray="2 2"/><path d="M5 17 C6 12 10 9 14 4"/><path d="M3 17 H17"/>',
  "ux-search": '<circle cx="8.5" cy="8.5" r="5"/><path d="M12.3 12.3 L16.5 16.5"/>',
  "ux-help": '<circle cx="10" cy="10" r="7"/><path d="M8 8 A2 2 0 1 1 10.8 9.8 C10.2 10.1 10 10.6 10 11.3"/><circle cx="10" cy="14" r="0.8" fill="currentColor" stroke="none"/>',
  "ux-keyboard": '<rect x="2.5" y="5.5" width="15" height="9" rx="1.5"/><path d="M5 8.5 H6 M8 8.5 H9 M11 8.5 H12 M14 8.5 H15 M6.5 11.5 H13.5"/>',
  "ux-start": '<path d="M4 17 V6 L10 3 L16 6 V17 Z"/><path d="M8 17 V12 H12 V17"/>',
});

export const lsGet = (k, d = null) => { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } };
export const lsSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage blocked */ } };

export const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

export function el(tag, attrs = {}, kids = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
    else if (v != null && v !== false) n.setAttribute(k, v === true ? "" : v);
  }
  (Array.isArray(kids) ? kids : [kids]).forEach(c =>
    c != null && n.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
  return n;
}

let cssDone = false;
export function ensureCss() {
  if (cssDone || document.querySelector("link[data-ux-css]")) { cssDone = true; return; }
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = "/static/ux.css"; l.setAttribute("data-ux-css", "1");
  document.head.appendChild(l);
  cssDone = true;
}

/** Dialog shell in the shared .modal-backdrop/.modal markup (so the ux
    enhancer adds drag / help / Enter-Esc). Returns {el, modal, close}. */
const openDlgs = new Map();
export function uxDialog(id, { title, iconId, cls = "", body, foot, onClose }) {
  closeUxDialog(id);
  const back = el("div", { class: "modal-backdrop sky-dlg ux-own " + cls, id });
  const modal = el("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-labelledby": id + "Title" });
  const head = el("header", { class: "modal-head" }, [
    el("h2", { id: id + "Title", class: "dlg-title", html: (iconId ? icon(iconId, "dlg-ico") : "") + `<span>${esc(title)}</span>` }),
  ]);
  const x = el("button", { class: "icon-btn", type: "button", title: "Close (Esc)", "aria-label": "Close " + title, text: "×" });
  head.appendChild(x);
  const bd = el("div", { class: "modal-body" }, body);
  modal.append(head, bd);
  if (foot) modal.appendChild(el("footer", { class: "modal-foot" }, foot));
  back.appendChild(modal);
  (document.getElementById("app") || document.body).appendChild(back);
  const close = () => {
    if (!openDlgs.has(id)) return;
    openDlgs.delete(id);
    back.remove();
    onClose && onClose();
  };
  x.addEventListener("click", close);
  back.addEventListener("mousedown", e => { if (e.target === back) close(); });
  openDlgs.set(id, close);
  return { el: back, modal, body: bd, close };
}
export function closeUxDialog(id) { const c = openDlgs.get(id); if (c) c(); }
export const isUxDialogOpen = id => openDlgs.has(id);
