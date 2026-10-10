/* SkyFrame UX pass 2 — empty states with a call to action.
     · Results tabs: every ".empty-state" (story, modal, reactions, forces,
       design, drift, TH, pushover, buckling, takedown, cuts, piers, svc,
       frequency …) gets a "Run Analysis (F5)" button, plus a "Define …"
       button when the tab needs a particular case type.
     · Result dialogs / toasts that say "run the analysis" get a Run button.
     · List dialogs: an empty list ("No groups yet", "No tendons yet", an
       empty listbox …) gets an "Add…" button that clicks the dialog's own
       Add / New button — no new model logic.
   Detection: one rAF-throttled MutationObserver per open dialog plus the
   result panes; nothing polls. */

import { el as ueEl } from "./ux_common.js";

const PANE_DEFINE = {
  th: ["def-th", "Define a Time-History Case…"],
  pushover: ["def-pushover", "Define a Pushover Case…"],
  buckling: ["def-buckling", "Define a Buckling Case…"],
  cuts: ["def-cuts", "Define Section Cuts…"],
  freq: ["def-ss", "Define a Steady-State Case…"],
  takedown: ["def-cases", "Define Load Cases…"],
};
const RUN_RE = /\b(run (the |an )?analysis|run analysis|after a solve|run the model|then run|and run)\b/i;
const EMPTY_RE = /^\s*(no\s+[\w\s/()-]{1,60}?(yet|defined)?\b|nothing (to|selected))/i;
const ADD_RE = /^\s*(\+\s*)?(add|new)\b/i;

export function installEmpty(sky) {
  const S = sky.store;
  const run = () => {
    if (sky.etabs && sky.etabs.runNow) sky.etabs.runNow();
    else if (sky.doRun) sky.doRun();
  };
  const runBtn = (extraCls = "") => {
    const b = ueEl("button", { type: "button", class: "btn ux-btn-primary ux-empty-run " + extraCls, title: "Run the analysis (F5)" }, ["Run Analysis (F5)"]);
    b.addEventListener("click", e => {
      e.stopPropagation();
      const back = b.closest(".modal-backdrop");
      if (back) { const x = back.querySelector(".modal-head .icon-btn"); if (x) x.click(); }
      run();
    });
    return b;
  };

  /* ---------------- results panes ---------------- */
  function decoratePane(es) {
    if (es.querySelector(".ux-empty-actions")) return;
    const pane = es.closest(".tabpane");
    const key = (es.id || "").replace(/^empty-/, "") || (pane && pane.id.replace(/^pane-/, ""));
    const acts = ueEl("div", { class: "ux-empty-actions" }, [runBtn()]);
    const def = PANE_DEFINE[key];
    if (def && sky.etabs && sky.etabs.hasItem(def[0])) {
      const d = ueEl("button", { type: "button", class: "btn ux-btn-secondary", text: def[1] });
      d.addEventListener("click", () => sky.etabs.clickItem(def[0]));
      acts.appendChild(d);
    }
    es.appendChild(acts);
    es.setAttribute("role", "status");
  }
  const scanPanes = () => document.querySelectorAll("#analyzeMain .empty-state, .tabpane .empty-state").forEach(decoratePane);
  scanPanes();
  // panes built later (freqplots.js) or rebuilt
  const am = document.getElementById("analyzeMain") || document.querySelector(".tabpanes") || document.body;
  let qp = 0;
  new MutationObserver(() => { if (!qp) qp = requestAnimationFrame(() => { qp = 0; scanPanes(); }); })
    .observe(am, { childList: true, subtree: true });

  /* ---------------- toasts: "Run an analysis first" → Run button ---------------- */
  const toasts = document.getElementById("toasts");
  if (toasts) new MutationObserver(recs => {
    for (const r of recs) for (const n of r.addedNodes) {
      if (n.nodeType !== 1 || n.querySelector(".ux-empty-run, .ux-toast-actions")) continue;
      const msg = n.querySelector(".toast-msg");
      const t = (msg || n).textContent || "";
      // "No groups defined — Define > Groups… first" → a button that opens it
      const dm = /\b(define|draw)\s*[>›→]\s*([A-Za-z][\w /-]*?)(?:…|\.\.\.|\s+first|$)/i.exec(t);
      if (dm && /\bno\b/i.test(t) && sky.etabs) {
        const want = dm[2].trim().toLowerCase();
        const item = [...document.querySelectorAll(`#etabsMenubar .etabs-menu-wrap .etabs-menu-item[data-act^="${dm[1].toLowerCase() === "draw" ? "draw-" : "def-"}"]`)]
          .find(i => ((i.dataset.uxLbl || "") + " " + i.textContent).toLowerCase().includes(want));
        if (item) {
          const b = ueEl("button", { type: "button", class: "btn btn-small ux-btn-primary", text: (item.dataset.uxLbl || item.querySelector(".mi-label").textContent).trim().replace(/…?$/, "…") });
          b.addEventListener("click", () => { n.remove(); sky.etabs.clickItem(item.dataset.act); });
          n.appendChild(ueEl("div", { class: "ux-toast-actions" }, [b]));
          continue;
        }
      }
      if (!RUN_RE.test(t) || /analysis (failed|complete)/i.test(n.textContent || "")) continue;
      if (S.results && !/first|no .*result/i.test(t)) continue;
      const b = runBtn("btn-small ux-toast-run");
      b.addEventListener("click", () => n.remove());
      n.appendChild(ueEl("div", { class: "ux-toast-actions" }, [b]));
    }
  }).observe(toasts, { childList: true });

  /* ---------------- dialogs ---------------- */
  function findAdd(fromNode, modal) {
    // nearest Add / New button: walk up from the empty message to the modal
    let n = fromNode.parentElement;
    while (n && n !== modal.parentElement) {
      const b = [...n.querySelectorAll("button")].find(x => ADD_RE.test(x.textContent) && !x.disabled && x.offsetParent !== null && !x.closest(".ux-empty-cta") && !x.closest(".modal-head"));
      if (b) return b;
      n = n.parentElement;
    }
    return null;
  }
  function scanDialog(back) {
    const modal = back.querySelector(".modal");
    if (!modal) return;
    const body = modal.querySelector(".modal-body") || modal;
    // 1) explicit empty messages
    const cands = [...body.querySelectorAll(".cd-empty, .asn-empty, .files-empty, .lib-none, .pf-empty, .b9-small, p.muted, td[colspan].muted, .muted.cd-note")]
      .filter(p => p.offsetParent !== null && !p.querySelector(".ux-empty-cta") && !p.closest(".ux-help-pop"));
    for (const p of cands) {
      const t = (p.textContent || "").trim();
      if (t.length > 220) continue;
      if (RUN_RE.test(t) && /first|no .*results?\b|then run|and run/i.test(t) && (!S.results || /no .*results?\b/i.test(t))) {
        p.appendChild(ueEl("span", { class: "ux-empty-cta" }, [runBtn("btn-small")]));
        continue;
      }
      if (!EMPTY_RE.test(t)) continue;
      if (RUN_RE.test(t) || /no .*results?\b/i.test(t)) {
        if (S.results && !/no .*results?\b/i.test(t)) continue;
        p.appendChild(ueEl("span", { class: "ux-empty-cta" }, [runBtn("btn-small")]));
        continue;
      }
      if (/selected|select (them|.* first)/i.test(t)) continue;   // a selection hint, not a list
      const add = findAdd(p, modal);
      if (!add) continue;
      const label = ADD_RE.test(add.textContent) ? add.textContent.trim().replace(/^\+\s*/, "Add ").replace(/^Add Add/, "Add") : "Add…";
      const cta = ueEl("button", { type: "button", class: "btn btn-small ux-btn-primary", title: "Same as the dialog's " + add.textContent.trim() + " button", text: /…$/.test(label) ? label : label + "…" });
      cta.addEventListener("click", e => { e.stopPropagation(); add.click(); });
      p.appendChild(ueEl("span", { class: "ux-empty-cta" }, [cta]));
    }
    // 2) empty list boxes with no message at all
    body.querySelectorAll("select[size], [role=listbox]").forEach(lb => {
      if (lb.offsetParent === null) return;
      const n = lb.tagName === "SELECT" ? lb.options.length : lb.querySelectorAll("[role=option]").length;
      const next = lb.nextElementSibling;
      const has = next && next.classList.contains("ux-empty-list");
      if (n > 0) { if (has) next.remove(); return; }
      if (has) return;
      // the module already explains the empty list (or step 1 added a CTA)
      if ((lb.textContent || "").trim() || (lb.parentElement && lb.parentElement.querySelector(".ux-empty-cta"))) return;
      const add = findAdd(lb, modal);
      const box = ueEl("div", { class: "ux-empty-list muted", role: "status" }, ["Nothing here yet."]);
      if (add) {
        const cta = ueEl("button", { type: "button", class: "btn btn-small ux-btn-primary", text: "Add…" });
        cta.addEventListener("click", e => { e.stopPropagation(); add.click(); });
        box.append(" ", ueEl("span", { class: "ux-empty-cta" }, [cta]));
      }
      lb.after(box);
    });
  }
  const watched = new WeakSet();
  function watchDialog(back) {
    if (watched.has(back)) return;
    watched.add(back);
    let q = 0;
    const later = () => { if (!q) q = requestAnimationFrame(() => { q = 0; if (!back.classList.contains("hidden") && back.isConnected) scanDialog(back); }); };
    new MutationObserver(later).observe(back, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });
    later();
  }
  document.querySelectorAll(".modal-backdrop").forEach(watchDialog);
  const addObs = new MutationObserver(recs => {
    for (const r of recs) for (const n of r.addedNodes)
      if (n.nodeType === 1 && n.classList.contains("modal-backdrop")) watchDialog(n);
  });
  addObs.observe(document.getElementById("app") || document.body, { childList: true });
  addObs.observe(document.body, { childList: true });

  sky.ux = sky.ux || {};
  sky.ux.empty = { scanPanes, scanDialog };
}
