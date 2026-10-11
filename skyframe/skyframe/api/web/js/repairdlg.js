/* SkyFrame — Analyze › Check Model › Fix… (model repair, CONTRACT "Model repair").

   One dialog: a checkbox per fix (merge joints, zero-length / duplicate /
   overlapping frames, shell-corner snap, split at joints / crossings,
   unconnected joints, mechanism report), a tolerance in the display length
   unit (js/units.js, "small" kind; the store stays SI), "Preview" = dry run
   (POST /api/repair {dry_run: true}) listing every action, "Apply" = adopt the
   repaired model as ONE labelled undo step ("Repair model") through the Edit
   menu's history (js/history.js: history.label + markDirty), then re-run Check
   Model when it has results. Opened from Analyze › Fix Model… and from the
   "Fix…" button of the Check Model panel. ?mock=1 runs js/mock_repair.js.
   Nothing changes the model unless Apply finds something to fix.
   Installs window.__sky.openRepair / .repair. */

import rpU from "./units.js";
import { normalizeModel as rpNormalizeModel } from "./modeledit.js";
import { dialog as rpDialog, btn as rpBtn, footBar as rpFootBar, errorLine as rpErrorLine,
  showError as rpShowError } from "./analysisdlg.js";
import { mockRepair as rpMockRepair, REPAIR_FIXES as RP_FIXES, DEFAULT_FIXES as RP_DEFAULTS,
  FIX_TITLES as RP_TITLES, FIX_HINTS as RP_HINTS } from "./mock_repair.js";

const LS_KEY = "skyframe.repair.options";
const lsGet = k => { try { return localStorage.getItem(k); } catch (e) { return null; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } };

function el(tag, attrs = {}, kids = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
    else if (v != null && v !== false) n.setAttribute(k, v === true ? "" : v);
  }
  (Array.isArray(kids) ? kids : [kids]).forEach(c =>
    c != null && n.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
  return n;
}

export function initRepair(sky) {
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 4500) => sky.toast && sky.toast(t, m, k, ms);
  if (!document.querySelector("link[data-repair-css]")) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/static/repair.css"; l.setAttribute("data-repair-css", "1");
    document.head.appendChild(l);
  }
  const st = { opts: loadOpts(), last: null, busy: false };

  function loadOpts() {
    let o = null;
    try { o = JSON.parse(lsGet(LS_KEY) || "null"); } catch (e) { o = null; }
    const fixes = Array.isArray(o && o.fixes) ? o.fixes.filter(f => RP_FIXES.includes(f)) : [...RP_DEFAULTS];
    const tol = o && +o.tol > 0 && +o.tol <= 1 ? +o.tol : null;
    return { fixes, tol };
  }
  const defaultTol = () => {
    const ck = sky.checkModel && sky.checkModel.options && sky.checkModel.options();
    return st.opts.tol || (ck && +ck.tol > 0 ? +ck.tol : 0.001);
  };

  /* ---------------------------------------------------------------- backend */
  async function post(body) {
    const res = await fetch("/api/repair", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
    return data;
  }
  /** Run the repair on the CURRENT working model → { model, report } (never adopts). */
  async function run({ fixes, tol, dryRun = true } = {}) {
    if (!S.model) throw new Error("No model loaded");
    const body = { fixes: fixes || st.opts.fixes, tolerance: tol || defaultTol(), dry_run: !!dryRun };
    if (S.mock) return rpMockRepair(S.model, body);
    const payload = JSON.parse(JSON.stringify(S.model));
    delete payload._mock_params;
    return post(Object.assign({ model: payload }, body));
  }

  function refreshViews() {
    S.modelEdited = true;
    try { sky.rebuildStorySelect && sky.rebuildStorySelect(); } catch (e) { console.error(e); }
    try { sky.planEditor && sky.planEditor.refresh(); } catch (e) { console.error(e); }
    try { sky.elevEditor && sky.elevEditor.refresh(); } catch (e) { console.error(e); }
    try { sky.renderProps && sky.renderProps(); } catch (e) { console.error(e); }
    try { if (S.mode === "analyze" && sky.viewer) { sky.viewer.setModel(S.model); S.modelEdited = false; } } catch (e) { console.error(e); }
    try { if (S.mode === "loads" && sky.loadsEditor) sky.loadsEditor.render(); } catch (e) { console.error(e); }
  }

  /** Adopt a repair result as ONE undo step. Returns true when the model changed. */
  function adopt(res) {
    if (!res || !res.report || !res.report.changed) return false;
    const md = S.mock ? res.model : (sky.keepSetup ? sky.keepSetup(res.model) : res.model);
    if (S.model && S.model._mock_params !== undefined && md._mock_params === undefined) md._mock_params = S.model._mock_params;
    if (sky.history && sky.history.label) sky.history.label("Repair model");
    S.model = rpNormalizeModel(md);
    const live = {};
    for (const k of ["member", "shell", "link"]) live[k] = new Set((S.model[k + "s"] || []).map(o => o.uid));
    S.selection = (S.selection || []).filter(r => !live[r.type] || live[r.type].has(r.uid));
    sky.markDirty();                     // → history checkpoint (labelled) + chrome refresh
    refreshViews();
    return true;
  }

  async function apply(opts = {}) {
    const res = await run(Object.assign({}, opts, { dryRun: false }));
    const changed = adopt(res);
    st.last = res;
    const n = res.report.actions.filter(a => a.fix !== "report_mechanisms").length;
    if (changed) toast("Repair model", `${n} fix${n === 1 ? "" : "es"} applied — Edit › Undo reverts them.`);
    else toast("Repair model", "Nothing to fix — the model is unchanged.");
    try {
      if (changed && sky.checkModel && sky.checkModel.last && sky.checkModel.last()) {
        const p = sky.checkModel.panel && sky.checkModel.panel();
        await sky.checkModel.run({ open: !!(p && !p.classList.contains("hidden")) });
      }
    } catch (e) { console.error(e); }
    return res;
  }

  /* ---------------------------------------------------------------- dialog */
  /** Backend messages carry SI numbers: show coordinate triples and "<v> m" lengths in display units. */
  const NUM = "(-?\\d+(?:\\.\\d+)?(?:e[-+]?\\d+)?)";
  const unitText = s => String(s)
    .replace(new RegExp(`\\(${NUM}, ${NUM}, ${NUM}\\)`, "g"), (_m, a, b, c) =>
      `(${[a, b, c].map(v => rpU.fmt("length", +v, 3)).join(", ")}) ${rpU.label("length")}`)
    .replace(new RegExp(`${NUM} m\\b`, "g"), (_m, v) => rpU.fmtU("small", +v, 2));
  function renderPreview(box, res) {
    box.textContent = "";
    const r = res.report;
    const geo = r.actions.filter(a => a.fix !== "report_mechanisms");
    const head = el("div", { class: "rp-sum" });
    head.appendChild(el("span", { class: "rp-pill" + (geo.length ? " is-on" : ""), text: geo.length ? `${geo.length} change${geo.length === 1 ? "" : "s"}` : "No changes" }));
    const cb = r.check_before, ca = r.check_after;
    if (cb && ca) head.appendChild(el("span", { class: "muted", text: `Check Model: ${cb.errors} error(s) / ${cb.warnings} warning(s) → ${ca.errors} / ${ca.warnings}` }));
    box.appendChild(head);
    if (!r.actions.length) {
      box.appendChild(el("div", { class: "rp-empty muted", text: "Nothing to fix with the selected options." }));
    }
    for (const f of r.fixes) {
      const acts = r.actions.filter(a => a.fix === f);
      if (!acts.length) continue;
      const grp = el("div", { class: "rp-grp", "data-fix": f });
      grp.appendChild(el("div", { class: "rp-grp-head" }, [el("span", { text: RP_TITLES[f] || f }), el("span", { class: "rp-count", text: String(acts.length) })]));
      for (const a of acts.slice(0, 200)) {
        const meta = [];
        if (a.objects && a.objects.length) meta.push(a.objects.slice(0, 6).join(", ") + (a.objects.length > 6 ? ` +${a.objects.length - 6}` : ""));
        if (a.location) meta.push("(" + a.location.map(v => rpU.fmt("length", v, 3)).join(", ") + ") " + rpU.label("length"));
        grp.appendChild(el("div", { class: "rp-row" + (a.severity ? " rp-" + a.severity : "") }, [
          el("div", { class: "rp-msg", text: unitText(a.message) }),
          meta.length ? el("div", { class: "rp-meta muted", text: meta.join(" · ") }) : null,
        ]));
      }
      if (acts.length > 200) grp.appendChild(el("div", { class: "muted rp-meta", text: `+${acts.length - 200} more` }));
      box.appendChild(grp);
    }
    if (r.stability && r.stability.message) box.appendChild(el("div", { class: "rp-note" + (r.stability.stable === false ? " rp-bad" : ""), text: `Stability: ${unitText(r.stability.message)}` }));
    if (r.warnings.length) {
      const w = el("div", { class: "rp-warn" });
      w.appendChild(el("div", { class: "rp-grp-head", text: "Warnings" }));
      for (const t of r.warnings.slice(0, 50)) w.appendChild(el("div", { class: "rp-row muted", text: unitText(t) }));
      box.appendChild(w);
    }
  }

  function openRepair() {
    if (!S.model) { toast("Fix Model", "Load a model first."); return null; }
    const o = { fixes: [...st.opts.fixes], tol: defaultTol() };
    const bodyEl = el("div", { class: "dlg-check rp-dlg" });
    bodyEl.appendChild(el("p", { class: "dlg-intro muted", text:
      "Repairs what Check Model reports. Preview lists every change without touching the model; Apply makes them as one step (Edit › Undo reverts it)." }));
    const g1 = el("fieldset", { class: "dlg-group" }, [el("legend", { text: "Fixes" })]);
    const boxes = [];
    for (const f of RP_FIXES) {
      const cb = el("input", { type: "checkbox", "data-fix": f, id: "rpFix_" + f });
      cb.checked = o.fixes.includes(f);
      cb.addEventListener("change", () => { o.fixes = RP_FIXES.filter(x => boxes.find(b => b.dataset.fix === x).checked); });
      boxes.push(cb);
      g1.appendChild(el("label", { class: "dlg-chk rp-fix", title: RP_HINTS[f] || "" }, [cb,
        el("span", {}, [RP_TITLES[f] || f, el("span", { class: "muted rp-hint", text: ` — ${RP_HINTS[f] || ""}` })])]));
    }
    const setAll = on => { boxes.forEach(b => { b.checked = on === "def" ? RP_DEFAULTS.includes(b.dataset.fix) : on; }); o.fixes = RP_FIXES.filter(x => boxes.find(b => b.dataset.fix === x).checked); };
    g1.appendChild(el("div", { class: "ck-allnone rp-allnone" }, [
      rpBtn("Select All", "btn-small", () => setAll(true)),
      rpBtn("Defaults", "btn-small", () => setAll("def")),
      rpBtn("Clear All", "btn-small", () => setAll(false)),
    ]));
    bodyEl.appendChild(g1);
    const g2 = el("fieldset", { class: "dlg-group" }, [el("legend", { text: "Tolerance" })]);
    const tolIn = el("input", { type: "number", id: "rpTolInput", min: "0", step: rpU.step("small", 0.001), value: rpU.inputValue("small", o.tol) });
    g2.appendChild(el("label", { class: "dlg-sub-row rp-tol-row" }, [el("span", { text: "Joint / length tolerance" }), tolIn, el("span", { class: "muted", text: rpU.label("small") })]));
    g2.appendChild(el("div", { class: "muted ck-dlg-hint", text: "Joints closer than this merge; frames shorter are zero-length; joints this close to a frame axis split it." }));
    bodyEl.appendChild(g2);
    const prev = el("div", { class: "rp-preview", id: "rpPreview" });
    prev.appendChild(el("div", { class: "muted rp-empty", text: "Click Preview to list the changes (dry run)." }));
    bodyEl.appendChild(prev);
    const err = rpErrorLine();
    bodyEl.appendChild(err);

    const readInputs = () => {
      const tol = rpU.parse("small", tolIn.value);
      if (!(tol > 0 && tol <= 1)) { rpShowError(err, `Tolerance must be > 0 and ≤ ${rpU.fmtU("small", 1, 0)}.`); return null; }
      if (!o.fixes.length) { rpShowError(err, "Select at least one fix."); return null; }
      rpShowError(err, "");
      o.tol = tol;
      st.opts = { fixes: [...o.fixes], tol };
      lsSet(LS_KEY, JSON.stringify(st.opts));
      return { fixes: [...o.fixes], tol };
    };
    let busy = false;
    const doPreview = async () => {
      const inp = readInputs();
      if (!inp || busy) return null;
      busy = true; prevBtn.disabled = true;
      prev.classList.add("is-busy");
      try {
        const res = await run({ fixes: inp.fixes, tol: inp.tol, dryRun: true });
        st.last = res;
        renderPreview(prev, res);
        return res;
      } catch (e) { rpShowError(err, e.message); return null; }
      finally { busy = false; prevBtn.disabled = false; prev.classList.remove("is-busy"); }
    };
    const doApply = async () => {
      const inp = readInputs();
      if (!inp || busy) return null;
      busy = true; applyBtn.disabled = true;
      try {
        const res = await apply({ fixes: inp.fixes, tol: inp.tol });
        dlg.close();
        return res;
      } catch (e) { rpShowError(err, e.message); return null; }
      finally { busy = false; applyBtn.disabled = false; }
    };
    const prevBtn = rpBtn("Preview", "", doPreview, "Dry run: list the changes, the model is not modified");
    const applyBtn = rpBtn("Apply", "btn-run", doApply, "Apply the selected fixes (one undo step)");
    prevBtn.id = "rpPreviewBtn"; applyBtn.id = "rpApplyBtn";
    const fb = rpFootBar("Analyze › Check Model › Fix", [rpBtn("Cancel", "", () => dlg.close()), prevBtn, applyBtn]);
    const dlg = rpDialog("rpRepairModal", { title: "Fix Model", iconId: undefined, body: bodyEl, foot: fb.wrap });
    tolIn.focus();
    return dlg;
  }

  sky.openRepair = openRepair;
  sky.repair = {
    open: openRepair, run, apply, adopt,
    preview: opts => run(Object.assign({}, opts, { dryRun: true })),
    options: () => st.opts, last: () => st.last,
    FIXES: RP_FIXES, DEFAULT_FIXES: RP_DEFAULTS,
  };
}

export default initRepair;
