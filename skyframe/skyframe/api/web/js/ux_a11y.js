/* SkyFrame UX pass 2 — accessibility sweep.
     · icon-only buttons (×, ?, ◀, ▶, ⬇ CSV, svg-only tools …) get an
       aria-label from their title, or from a glyph map when untitled;
     · the results tab bar is a proper tablist (aria-selected follows the
       active tab, ←/→ move between tabs);
     · visible focus rings and contrast-fixed theme tokens live in ux.css
       (`.ux-on` scoped, so ?ux=off shows the original look).
   New buttons are picked up by a rAF-batched MutationObserver that only
   looks at added HTML elements (SVG redraws are skipped). */

const GLYPH = {
  "×": "Close", "✕": "Close", "?": "Help", "◀": "Collapse", "▶": "Expand", "◂": "Previous", "▸": "Next",
  "▲": "Move up", "▼": "Move down", "↑": "Move up", "↓": "Move down", "+": "Add", "−": "Remove", "-": "Remove",
  "⬇": "Download", "⚙": "Settings", "☰": "Menu", "⟲": "Reset", "↺": "Undo", "↻": "Redo", "✎": "Edit", "🗑": "Delete",
  "⧉": "Copy", "⤢": "Expand", "…": "More",
};
const SVG_NS = "http://www.w3.org/2000/svg";

export function labelButton(b) {
  if (b.hasAttribute("aria-label") || b.hasAttribute("aria-labelledby")) return false;
  const txt = (b.textContent || "").replace(/\s+/g, " ").trim();
  // "real" text (two+ letters) is an accessible name already
  if (/[A-Za-z]{2,}/.test(txt)) return false;
  let name = (b.getAttribute("title") || "").split("\n")[0].replace(/\s*\((?:[^)]*)\)\s*$/, "").trim();
  if (!name) name = GLYPH[txt] || (b.dataset.tool ? b.dataset.tool : "") || (b.dataset.act || "") || "";
  if (!name && txt) name = txt;
  if (!name) return false;
  b.setAttribute("aria-label", name);
  return true;
}

export function installA11y(sky) {
  const sweep = (root = document) => {
    if (!root.querySelectorAll) return 0;
    let n = 0;
    if (root.tagName === "BUTTON") n += labelButton(root) ? 1 : 0;
    root.querySelectorAll("button, [role=button]").forEach(b => { if (labelButton(b)) n++; });
    return n;
  };
  sweep();

  /* results tab bar → tablist */
  const bar = document.querySelector(".tabbar");
  if (bar) {
    if (!bar.getAttribute("role")) bar.setAttribute("role", "tablist");
    bar.setAttribute("aria-label", "Results views");
    const syncTabs = () => bar.querySelectorAll(".tab").forEach(t => {
      const on = t.classList.contains("is-active");
      t.setAttribute("aria-selected", on ? "true" : "false");
      const pane = document.getElementById("pane-" + t.dataset.tab);
      if (pane) { if (!t.id) t.id = "tab-" + t.dataset.tab; t.setAttribute("aria-controls", pane.id); pane.setAttribute("aria-labelledby", t.id); }
    });
    syncTabs();
    new MutationObserver(syncTabs).observe(bar, { subtree: true, attributes: true, attributeFilter: ["class"] });
    bar.addEventListener("keydown", e => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      const tabs = [...bar.querySelectorAll(".tab")].filter(t => t.offsetParent !== null && !t.classList.contains("hidden"));
      const i = tabs.indexOf(document.activeElement);
      if (i < 0) return;
      const nx = tabs[(i + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
      e.preventDefault(); e.stopPropagation();
      nx.focus(); nx.click();
    });
  }

  /* toasts are announced */
  const toasts = document.getElementById("toasts");
  if (toasts) { toasts.setAttribute("aria-live", "polite"); toasts.setAttribute("role", "status"); }

  /* new buttons from any module */
  const pending = new Set();
  let q = 0;
  const flush = () => { q = 0; for (const n of pending) if (n.isConnected) sweep(n); pending.clear(); };
  new MutationObserver(recs => {
    for (const r of recs) for (const n of r.addedNodes) {
      if (n.nodeType !== 1 || n.namespaceURI === SVG_NS) continue;
      pending.add(n);
    }
    if (pending.size && !q) q = requestAnimationFrame(flush);
  }).observe(document.body, { childList: true, subtree: true });

  sky.ux = sky.ux || {};
  sky.ux.a11y = { sweep, labelButton };
}
