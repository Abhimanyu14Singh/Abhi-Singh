/* SkyFrame — frame local-axis convention UI (CONTRACT "Frame local axes:
   ETABS convention").

     Options → Convert to ETABS Axis Convention…   legacy → etabs, keeping
                 every member's physical orientation (vertical members get
                 angle − 90); ONE undo step (one markDirty in one task)
     View → Show Local Axes                         1/2/3 arrows (red / green /
                 blue) at member midpoints, line AND extruded 3D view
                 (viewext "axes" display option)
     3D quick-access toolbar (View group): "Extrude" + "Axes" toggles
     Alt+X   Extruded view (registered in the shortcut registry after a
             clash check)

   Everything is VIEW state except the conversion, which edits the model
   through the normal markDirty path (history.js coalesces it into one
   entry). The single local-axis implementation is js/frameaxes.js. */

import { dialog as faDialog, btn as faBtn, footBar as faFootBar, closeDialog as faCloseDialog } from "./analysisdlg.js";
import { axisConvention as faConv, convertToEtabs as faConvertToEtabs, conventionLabel as faLabel,
  frameLocalAxes as faAxes } from "./frameaxes.js";

const FA_KEY = "Alt+X";
const ICO_EXTRUDE = '<svg class="ux-qat-ico" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><path d="M4 8l6-3 10 4-6 3z"/><path d="M4 8v8l10 4v-8"/><path d="M20 9v8l-6 3"/></svg>';
const ICO_AXES = '<svg class="ux-qat-ico" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke-width="1.8" stroke-linecap="round"><path d="M6 18L19 18" stroke="#e5484d"/><path d="M6 18L6 5" stroke="#30a46c"/><path d="M6 18L13 11" stroke="#3e7bfa"/></svg>';

export function initFrameAxesUi(sky) {
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 4500) => sky.toast && sky.toast(t, m, k, ms);
  const conv = () => faConv(S.model);
  const vx = () => sky.vx;
  const axesOn = () => !!(vx() && vx().opts().show.axes);
  const show3d = () => {
    if (S.mode === "analyze" && S.tab === "view3d") return;
    try { if (sky.etabs && sky.etabs.hasItem("view-3d")) sky.etabs.clickItem("view-3d"); } catch (e) { /* view switch is best effort */ }
  };
  const toggleExtrude = () => {
    if (!vx()) return false;
    if (!vx().isExtrude()) show3d();
    const o = vx().toggleExtrude();
    syncButtons();
    return o.extrude;
  };
  const toggleAxes = on => {
    if (!vx()) return false;
    const v = on == null ? !axesOn() : !!on;
    if (v) show3d();
    vx().set({ show: { axes: v } });
    syncButtons();
    return v;
  };

  /* ---------------- conversion ---------------- */
  function convert() {
    const m = S.model;
    if (!m) return null;
    const rep = faConvertToEtabs(m);
    if (!rep.converted) return rep;
    sky.markDirty();                                      // ONE undo step
    try { sky.renderProps && sky.renderProps(); } catch (e) { console.error(e); }
    if (sky.planEditor && sky.planEditor.renderStatic) sky.planEditor.renderStatic();
    if (sky.viewer) sky.viewer._dirty = true;
    document.dispatchEvent(new CustomEvent("sky:frame-axes-converted", { detail: rep }));
    return rep;
  }
  function openConvert() {
    const m = S.model;
    if (!m) { toast("Frame local axes", "Load a model first.", "error"); return null; }
    const body = document.createElement("div");
    body.className = "fa-dlg";
    const verts = (m.members || []).filter(mm => !mm.curve && Math.hypot(mm.pj[0] - mm.pi[0], mm.pj[1] - mm.pi[1]) < 1e-6);
    const cur = conv();
    body.innerHTML = `
      <p><b>Current convention:</b> <span id="faCurConv">${faLabel(cur)}</span></p>
      <table class="fa-tbl"><thead><tr><th>Member</th><th>Local 1</th><th>Local 2 (angle 0)</th><th>Local 3 (angle 0)</th></tr></thead><tbody>
        <tr><td>Beam / brace</td><td>i → j</td><td>vertical plane, up</td><td>1 × 2</td></tr>
        <tr><td>Column, legacy</td><td>i → j</td><td>−Y (drawn up) · +Y (drawn down)</td><td>+X</td></tr>
        <tr><td>Column, ETABS</td><td>i → j</td><td>+X</td><td>+Y (drawn up) · −Y (drawn down)</td></tr>
      </tbody></table>
      <p class="muted">${cur === "etabs"
        ? "This model already uses the ETABS convention — nothing to convert."
        : `Converting switches the model to the ETABS convention and sets the local-axis angle of the ${verts.length} vertical member${verts.length === 1 ? "" : "s"} to angle − 90°, so every member keeps its physical orientation (same b / h directions, identical analysis results). One undo step.`}</p>`;
    const err = document.createElement("p"); err.className = "field-error hidden dlg-error"; body.appendChild(err);
    const bOk = faBtn(cur === "etabs" ? "Close" : "Convert", "btn-primary", () => {
      if (cur === "etabs") { dlg.close(); return; }
      const rep = convert();
      dlg.close();
      toast("Frame local axes", `Converted to the ETABS convention · ${rep ? rep.members.length : 0} vertical member angle${rep && rep.members.length === 1 ? "" : "s"} adjusted (Ctrl+Z undoes).`);
    });
    bOk.id = "faConvertOk";
    const bCancel = faBtn("Cancel", "", () => dlg.close());
    bCancel.id = "faConvertCancel";
    const fb = faFootBar("Options → Convert to ETABS Axis Convention", cur === "etabs" ? [bOk] : [bCancel, bOk]);
    const dlg = faDialog("faConvertDlg", { title: "Frame Local Axis Convention", iconId: "menu-options", body, foot: fb.wrap });
    return dlg;
  }

  /* ---------------- menus ---------------- */
  if (sky.menu && sky.menu.add) {
    sky.menu.add("Options", { label: "Convert to ETABS Axis Convention…", act: "opt-fa-convert",
      hint: () => "current: " + conv(), fn: () => openConvert() });
    sky.menu.add("View", { label: "Show Local Axes", act: "vx-fa-axes", hint: "frame 1-2-3 arrows",
      check: () => axesOn(), fn: () => toggleAxes() });
  }

  /* ---------------- toolbar + shortcut (after the UX pass builds them) ---------------- */
  const btns = {};
  function syncButtons() {
    const on = { extrude: !!(vx() && vx().isExtrude()), axes: axesOn() };
    for (const [k, b] of Object.entries(btns)) {
      b.classList.toggle("is-active", on[k]);
      b.setAttribute("aria-pressed", on[k] ? "true" : "false");
    }
  }
  function installLate() {
    const tb = sky.ux && sky.ux.toolbar;
    const grp = tb && tb.group && tb.group("view");
    const inner = grp && grp.querySelector(".sf-tb-inner");
    if (inner && !inner.querySelector('[data-qat="fa-extrude"]')) {
      const mk = (id, ico, label, title, fn) => {
        const b = document.createElement("button");
        b.type = "button"; b.className = "ux-qat-btn"; b.dataset.qat = id;
        b.title = title; b.setAttribute("aria-label", label); b.setAttribute("aria-pressed", "false");
        b.innerHTML = ico + `<span class="ux-qat-txt">${label}</span>`;
        b.addEventListener("click", fn);
        inner.appendChild(b);
        return b;
      };
      btns.extrude = mk("fa-extrude", ICO_EXTRUDE, "Extrude", `Extruded view — frames with their section shape (${FA_KEY})`, () => toggleExtrude());
      btns.axes = mk("fa-axes", ICO_AXES, "Axes", "Show frame local axes 1 / 2 / 3 (red / green / blue)", () => toggleAxes());
    }
    const sc = sky.ux && sky.ux.shortcuts;
    if (sc && sc.register) {
      const clash = sc.list().find(s => (s.keys || []).includes(FA_KEY));
      if (clash) console.warn(`frame axes: shortcut ${FA_KEY} already used by "${clash.desc}" — not registered`);
      else sc.register({ id: "fa-extrude", keys: [FA_KEY], desc: "Extruded view on / off", group: "View", run: () => toggleExtrude() });
    }
    syncButtons();
  }
  setTimeout(installLate, 0);
  document.addEventListener("sky:model-changed", () => setTimeout(syncButtons, 0));
  document.addEventListener("click", () => setTimeout(syncButtons, 0), true);

  if (!document.getElementById("faStyles")) {
    const st = document.createElement("style");
    st.id = "faStyles";
    st.textContent = `
.fa-dlg { display: flex; flex-direction: column; gap: 8px; font-size: 12.5px; max-width: 620px; }
.fa-tbl { border-collapse: collapse; width: 100%; font-size: 12px; }
.fa-tbl th, .fa-tbl td { border: 1px solid var(--border); padding: 4px 6px; text-align: left; }
.fa-tbl th { color: var(--text-2, #8b98a8); font-weight: 600; }`;
    document.head.appendChild(st);
  }

  sky.frameAxes = {
    convention: conv, convert, openConvert, close: () => faCloseDialog("faConvertDlg"),
    toggleExtrude, toggleAxes, isAxesOn: axesOn, shortcut: FA_KEY,
    axesOf: uid => { const mm = ((S.model || {}).members || []).find(x => x.uid === uid); return mm ? faAxes(mm, S.model) : null; },
  };
}
