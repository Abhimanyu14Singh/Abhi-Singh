/* SkyFrame Wave F — every Assign command is a real dialog on the selection
   (no mode switch, the 3D view stays), with a story scope.

   1. The Assign items that still only pointed at the Properties panel
      (assignHint in js/etabs.js) now open dialogs acting on the selection:
        Assign › Frame › Panel Zone            → per-joint Panel Zone dialog
        Assign › Shell › Area Springs          → "Assign Area Springs" (new)
        Assign › Frame Loads › Member Loads    → Frame Distributed Loads
        Assign › Shell Loads › Area Loads      → Shell Uniform Loads
        Model Explorer › Assignments › Loads   → the loads dialog of the selection
      (routed at the menu item, __sky.etabs.clickItem and the explorer, like
      js/dlgstd.js does; act ids and labels unchanged).
   2. Story scope — This Story / Similar Stories / All Stories — in the footer
      of every Assign dialog opened with a selection (ETABS Story Data
      "Master Story / Similar To", Welcome to ETABS p. 3-4). It shares the plan
      "Apply to" scope (sky.w8stories). When the dialog writes the model
      (OK / Apply) and the scope is not This Story, whatever the dialog changed
      on the selected objects — and on their end joints — is copied onto the
      corresponding objects of the other stories of the scope (same kind, same
      plan position: f_pickgeom.correspondingRefs / replicateChanges), inside
      the same undo step. */

import * as PG from "./f_pickgeom.js";
import { el as faEl, esc as faEsc, dkForm as faForm, dkGroup as faGroup, dkNote as faNote, dkDialog as faDialog,
  dkDraft as faDraft, dkCommit as faCommit } from "./dlgkit.js";

const SCOPES = [["one", "This Story"], ["similar", "Similar Stories"], ["all", "All Stories"]];
const clone = o => JSON.parse(JSON.stringify(o));

export function initFAssign(sky, F) {
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 3500) => sky.toast && sky.toast(t, m, k, ms);

  /* ================================================================ Area Springs */
  function openAreaSprings() {
    const shells = (S.selection || []).filter(r => r.type === "shell").map(r => (S.model.shells || []).find(s => s.uid === r.uid)).filter(Boolean);
    if (!shells.length) {
      return faDialog({ id: "fAreaSpring", title: "Assign Area Springs", iconId: "exleaf-shells", size: "sm", noCancel: true,
        body: [faNote("Nothing to assign — select one or more <b>slabs or walls</b> first (click them in any view), then choose this command again.")] });
    }
    const on0 = shells.map(s => !!s.area_spring);
    const kz0 = shells.map(s => (s.area_spring ? s.area_spring.kz : null));
    const co0 = shells.map(s => !!(s.area_spring && s.area_spring.compression_only));
    const mixed = a => a.some(x => JSON.stringify(x) !== JSON.stringify(a[0]));
    const V = { on: on0[0], kz: kz0.find(x => x != null) ?? 10000, co: co0[0] };
    const P = {};
    const F2 = faForm();
    const body = [
      faGroup("Area spring (subgrade bed under the region)", [
        F2.check(V, "on", { label: "Area spring on the selected objects", id: "fAspOn", mixed: mixed(on0), write: v => { V.on = v; P.on = true; } }),
        F2.num(V, "kz", { label: "Subgrade modulus kz", kind: "subgrade", gt: 0, id: "fAspKz", mixed: mixed(kz0), write: v => { V.kz = v; P.kz = true; } }),
        F2.check(V, "co", { label: "Compression-only (makes the analysis nonlinear)", id: "fAspCo", mixed: mixed(co0), write: v => { V.co = v; P.co = true; } }),
      ]),
    ];
    return faDialog({
      id: "fAreaSpring", title: "Assign Area Springs", iconId: "exleaf-shells", size: "sm", form: F2, body,
      intro: `${shells.length} slab / wall object${shells.length > 1 ? "s" : ""} selected. Only the fields you change are written.`,
      onOk: () => apply(), onApply: () => apply(),
    });
    function apply() {
      if (!P.on && !P.kz && !P.co) return "";
      const D = faDraft(sky);
      for (const s0 of shells) {
        const s = D.shells.find(x => x.uid === s0.uid);
        if (!s) continue;
        const on = P.on ? V.on : !!s.area_spring;
        if (!on) { delete s.area_spring; continue; }
        const cur = s.area_spring || {};
        const kz = P.kz ? V.kz : (cur.kz ?? V.kz);
        if (!(kz > 0)) return "Enter a subgrade modulus kz greater than zero.";
        s.area_spring = { kz, compression_only: P.co ? !!V.co : !!cur.compression_only };
      }
      faCommit(sky, D);
      return "";
    }
  }

  /* ================================================================ routes */
  const selHas = t => (S.selection || []).some(r => r.type === t);
  const ROUTES = {
    "asn-fpz": () => (sky.openPanelZone ? sky.openPanelZone() : null),
    "asn-sspring": () => openAreaSprings(),
    "asn-fload": () => (sky.openFrameDistributed ? sky.openFrameDistributed() : null),
    "asn-aload": () => (sky.openShellUniform ? sky.openShellUniform() : null),
  };
  const loadsOfSelection = () => (selHas("shell") && !selHas("member") ? ROUTES["asn-aload"]() : selHas("joint") && !selHas("member") && sky.openJointLoads ? sky.openJointLoads("force") : ROUTES["asn-fload"]());

  /* ================================================================ story scope */
  let armed = null;
  function scopeDefault() { try { return sky.w8stories ? sky.w8stories.scope() : "one"; } catch { return "one"; } }
  /** selection + the end joints of its frames / shells / links (joint-keyed assignments replicate too). */
  function scopeRefs(refs) {
    const m = S.model, out = refs.slice(), seen = new Set(refs.map(PG.refKey));
    const addJ = p => { const r = { type: "joint", uid: PG.jointKey(p) }; const k = PG.refKey(r); if (!seen.has(k)) { seen.add(k); out.push(r); } };
    for (const r of refs) {
      if (r.type === "member") { const x = (m.members || []).find(o => o.uid === r.uid); if (x) { addJ(x.pi); addJ(x.pj); } }
      else if (r.type === "shell") { const x = (m.shells || []).find(o => o.uid === r.uid); if (x) x.corners.forEach(addJ); }
      else if (r.type === "link") { const x = (m.links || []).find(o => o.uid === r.uid); if (x) { addJ(x.pi); addJ(x.pj); } }
    }
    return out;
  }
  function arm(dlg, refs, act) {
    disarm();
    const m = S.model;
    if (!m || !refs.length || (m.stories || []).length < 2) return;
    const st = { dlg, refs, act, scope: scopeDefault(), before: PG.assignSnapshot(m), busy: false, replicated: 0 };
    const sel = faEl("select", { class: "mini-select f-scope-sel", id: "fScopeSel", "aria-label": "Assignment scope" });
    for (const [v, l] of SCOPES) sel.appendChild(faEl("option", { value: v, selected: v === st.scope || null }, [l]));
    const cnt = faEl("span", { class: "muted f-scope-n", id: "fScopeCount" });
    const count = () => {
      const n = st.scope === "one" ? 0 : PG.correspondingRefs(S.model, refs, st.scope).refs.length;
      cnt.textContent = st.scope === "one" ? "" : `+${n} object${n === 1 ? "" : "s"} on other stories`;
    };
    sel.addEventListener("change", () => {
      st.scope = sel.value;
      try { if (sky.w8stories && sky.w8stories.setScope) sky.w8stories.setScope(st.scope); } catch { /* plan scope optional */ }
      count();
    });
    count();
    const bar = faEl("label", { class: "f-scope", title: "Apply the assignment to the corresponding objects (same kind, same plan position) on the similar stories (Story Data › Master Story / Similar To) or on all stories" },
      [faEl("span", { text: "Apply to" }), sel, cnt]);
    const foot = dlg.querySelector(".dk-foot-left, .dlg-foot, .modal-foot");
    if (foot) foot.insertBefore(bar, foot.firstChild); else return;
    st.bar = bar;
    st.onChange = () => {
      if (st.busy || st.scope === "one" || !S.model) return;
      st.busy = true;
      try {
        const pairs = PG.correspondingRefs(S.model, scopeRefs(st.refs), st.scope).pairs;
        const n = PG.replicateChanges(S.model, st.before, pairs);
        st.before = PG.assignSnapshot(S.model);
        if (n) {
          st.replicated += n;
          F.lastScope = { act, scope: st.scope, replicated: n };
          queueMicrotask(() => toast("Assign", `Also applied to ${n} object${n === 1 ? "" : "s"} on the ${st.scope === "all" ? "other stories" : "similar stories"}.`));
        }
      } catch (e) { console.error("scope replication", e); }
      finally { st.busy = false; }
    };
    document.addEventListener("sky:model-changed", st.onChange);
    st.mo = new MutationObserver(() => { if (!dlg.isConnected) setTimeout(() => { if (armed === st) disarm(); }, 0); });
    st.mo.observe(dlg.parentNode || document.body, { childList: true });
    armed = st;
  }
  function disarm() {
    if (!armed) return;
    document.removeEventListener("sky:model-changed", armed.onChange);
    try { armed.mo.disconnect(); } catch { /* gone */ }
    armed = null;
  }
  let depth = 0;
  /** Run an Assign act; arm the scope bar on the dialog it opens. */
  function withScope(act, fn) {
    if (depth) return fn();
    const refs = clone(S.selection || []);
    const known = new Set(document.querySelectorAll(".modal-backdrop"));
    const host = document.getElementById("app") || document.body;
    let done = false;
    const look = () => {
      if (done) return;
      const fresh = [...document.querySelectorAll(".modal-backdrop")].filter(b => !known.has(b) && b.isConnected && !b.classList.contains("hidden") && b.id !== "wcLockDlg");
      if (!fresh.length) return;
      done = true;
      try { mo.disconnect(); } catch { /* gone */ }
      arm(fresh[fresh.length - 1], refs, act);
    };
    // dialogs that open after a prompt (model lock) or a fetch: watch briefly
    const mo = new MutationObserver(look);
    mo.observe(host, { childList: true });
    setTimeout(() => { done = true; try { mo.disconnect(); } catch { /* gone */ } }, 15000);
    depth++;
    let r;
    try { r = fn(); } finally { depth--; }
    look();
    return r;
  }

  /* ================================================================ install */
  const acts = () => (sky.menu && sky.menu.acts ? sky.menu.acts() : []).filter(a => /^asn-/.test(a));
  for (const act of acts()) {
    const it = sky.menu.item(act);
    if (!it || typeof it.fn !== "function") continue;
    const base = ROUTES[act] || it.fn;
    if (ROUTES[act]) it.legacyFn = it.fn;
    it.fn = () => withScope(act, base);
  }
  if (sky.etabs && sky.etabs.clickItem) {
    const oc = sky.etabs.clickItem;
    sky.etabs.clickItem = act => {
      if (/^asn-/.test(act) && sky.etabs.hasItem(act)) {
        sky.etabs.closeMenus && sky.etabs.closeMenus();
        if (ROUTES[act]) { withScope(act, ROUTES[act]); return true; }
        return withScope(act, () => oc(act));
      }
      return oc(act);
    };
  }
  if (sky.etabs && sky.etabs.explorerClick) {
    const oe = sky.etabs.explorerClick;
    sky.etabs.explorerClick = label => (label === "Loads" ? (withScope("asn-loads", loadsOfSelection), true) : oe(label));
  }
  window.addEventListener("click", e => {
    const leaf = e.target && e.target.closest ? e.target.closest("#etabsExplorer .ex-leaf") : null;
    if (!leaf || leaf.classList.contains("ex-case")) return;
    const lbl = (leaf.querySelector(".ex-leaf-lbl") || leaf).textContent.trim();
    if (lbl === "Loads") {   // Model Explorer › Assignments › Loads (the label is unique in the tree)
      e.stopPropagation(); e.preventDefault(); withScope("asn-loads", loadsOfSelection);
    }
  }, true);

  F.openAreaSprings = openAreaSprings;
  return { openAreaSprings, withScope, ROUTES, armed: () => (armed ? { act: armed.act, scope: armed.scope, refs: armed.refs.length } : null), scopeRefs };
}
