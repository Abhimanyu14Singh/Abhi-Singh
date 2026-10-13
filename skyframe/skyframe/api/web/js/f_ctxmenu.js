/* SkyFrame Wave F — right-click menus by object type in EVERY view: main 3D
   view, View › Windows 3D panes (js/f_select3d.js binds them), plan and
   elevation (js/workflow.js binds them). js/workflow.js asks
   sky.f.ctxItems(ref) for the items (one hook line) and sky.f.ctxPick(v, e)
   for the object under the cursor in the main 3D view (all object types).

   frame · shell · joint · link · point / line spring · tendon · mixed
   selection · background. Every item runs a menu command
   (__sky.etabs.clickItem → the same dialogs as the menu bar, acting on the
   selection, with the story scope) or a local function. With analysis
   results, "Show results…" calls sky.f.onShowResults(obj): Wave G's member
   detail window (sky.results.openDetail) when present, else the Analyze-mode
   Member panel / reactions / shell contour fallback. */

import * as PG from "./f_pickgeom.js";

const A = (label, act, extra = {}) => ({ label, act, ...extra });
const SEP = { sep: true };
const BY_TYPE = {
  member: [A("Frame Section…", "asn-fsec"), A("Releases / Partial Fixity…", "asn-frel"), A("Local Axes…", "asn-forient"), A("Insertion Point…", "asn-finsert"),
    A("Hinges…", "asn-uh-hinges"), SEP, A("Distributed Load…", "asn-l116-fdist"), A("Point Load…", "asn-fconc"), A("Additional Mass…", "asn-fmass")],
  shell: [A("Slab / Wall Section…", "asn-ssec"), A("Area Springs…", "asn-sspring"), A("Diaphragm…", "asn-sdiaph"), SEP,
    A("Uniform Load…", "asn-suniform"), A("Temperature Load…", "asn-l116-stemp"), A("Additional Mass…", "asn-smass")],
  joint: [A("Restraints…", "asn-support"), A("Springs…", "asn-b9-jsprings"), A("Diaphragm…", "asn-jdiaph"), A("Panel Zone…", "asn-np-jpanelzone"), SEP,
    A("Joint Loads…", "asn-jforce"), A("Ground Displacement…", "asn-jground"), A("Additional Mass…", "asn-jmass")],
  link: [A("Link Properties…", "asn-b9-linkprops")],
  spring: [A("Joint Springs…", "asn-b9-jsprings"), A("Restraints…", "asn-support")],
  linespring: [],
  tendon: [],
};
const EDIT = [A("Copy", "edit-copy", { key: "Ctrl+C" }), A("Replicate…", "edit-replicate"), A("Move…", "edit-move"), A("Delete", "edit-delete", { key: "Del", danger: true })];

export function initFCtxMenu(sky, F) {
  const S = sky.store;
  const has = act => !!(sky.etabs && sky.etabs.hasItem && sky.etabs.hasItem(act));
  const head = ref => {
    const n = (S.selection || []).length;
    if (n > 1) return `${n} objects selected`;
    if (ref.type === "joint") return `Joint ${PG.jointPoint(ref.uid).map(v => +v.toFixed(3)).join(", ")}`;
    return `${PG.TYPE_LABEL[ref.type] || ref.type} ${ref.uid}`;
  };
  const selTypes = () => new Set((S.selection || []).map(r => r.type));

  function onShowResults(obj) {
    obj = obj || (S.selection || [])[0];
    if (!obj) return false;
    // Wave G member / object detail window
    if (sky.results && typeof sky.results.openDetail === "function") { try { sky.results.openDetail(obj); return true; } catch (e) { console.error(e); } }
    if (!S.results) { sky.toast && sky.toast("Show results", "Run the analysis first (F5).", "info", 3000); return false; }
    if (obj.type === "member" && sky.selectMemberFrom3D) {
      if (S.mode !== "analyze") sky.setMode("analyze");
      sky.selectMemberFrom3D(obj.uid);
      return true;
    }
    if ((obj.type === "joint" || obj.type === "spring") && sky.switchTab) { if (S.mode !== "analyze") sky.setMode("analyze"); sky.switchTab("reactions"); return true; }
    if (obj.type === "shell" && sky.etabs) { sky.etabs.clickItem(has("dis-shr-contours") ? "dis-shr-contours" : "dis-contours"); return true; }
    if (sky.etabs) sky.etabs.clickItem(has("dis-forces") ? "dis-forces" : "dis-deformed");
    return true;
  }
  F.onShowResults = onShowResults;

  function propsItem() {
    return { label: "Properties", fn: () => { if (F.props) F.props.setOn(true); const p = document.getElementById("propsPanel"); if (p) p.scrollIntoView({ block: "nearest" }); } };
  }

  /** Context-menu items for ref (null = background). */
  F.ctxItems = ref => {
    const out = [];
    if (!ref) {
      out.push({ head: "View" });
      out.push(A("Select All", "sel-all", { key: "Ctrl+A" }));
      if ((S.selection || []).length) out.push(A("Clear Selection", "sel-clear"), { label: "Properties", fn: propsItem().fn });
      out.push(A("Intersecting Line", "sel-w8-line"), { label: "Selection Filter…", fn: () => F.openFilter && F.openFilter() });
      out.push(SEP, A("Paste…", "edit-paste", { key: "Ctrl+V" }), A("Set Display Options…", "vx-display-options"), SEP, A("Run Analysis", "an-run", { key: "F5" }));
      return out.filter(i => i.sep || i.head || i.fn || has(i.act));
    }
    out.push({ head: head(ref) });
    const types = selTypes();
    const mixed = types.size > 1;
    if (mixed) {
      out.push({ label: "Properties", fn: propsItem().fn });
      if (types.has("joint")) out.push(A("Joint Restraints…", "asn-support"));
      if (types.has("member")) out.push(A("Frame Section…", "asn-fsec"));
      if (types.has("shell")) out.push(A("Slab / Wall Section…", "asn-ssec"));
    } else {
      for (const it of BY_TYPE[ref.type] || []) out.push(it);
      if (ref.type === "tendon" && sky.openTendonProfile) out.push({ label: "Edit Tendon…", fn: () => sky.openTendonProfile(ref.uid) });
      out.push(SEP, propsItem());
    }
    if (["member", "shell", "link"].includes(ref.type) || mixed) out.push(A("Assign to Group…", "asn-group"));
    out.push(SEP, ...EDIT, SEP);
    if (S.results) out.push({ label: "Show results…", fn: () => onShowResults(ref), key: "" });
    if (ref.type === "member" && sky.bucklingFem) out.push({ label: "Member Buckling…", fn: () => sky.bucklingFem.open(ref.uid) });
    out.push({ label: "Object Information…", fn: () => objectInfo(ref) });
    return out.filter(i => i.sep || i.head || i.fn || has(i.act));
  };

  function objectInfo(ref) {
    if (ref.type !== "joint" && sky.std && sky.std.info) return sky.std.info(ref);
    // joints: a compact read-only info dialog
    const m = S.model, p = PG.jointPoint(ref.uid);
    const sup = (m.supports || []).find(s => s.point.every((c, i) => Math.abs(c - p[i]) < 1e-6));
    const spr = (m.spring_supports || []).find(s => s.point.every((c, i) => Math.abs(c - p[i]) < 1e-6));
    const loads = Object.entries(m.patterns || {}).map(([pn, pt]) => [pn, (pt.nodal_loads || []).filter(l => Array.isArray(l.point) && l.point.every((c, i) => Math.abs(c - p[i]) < 1e-6)).length]).filter(([, n]) => n);
    const conn = [...(m.members || []).filter(x => [x.pi, x.pj].some(q => q.every((c, i) => Math.abs(c - p[i]) < 1e-6))).map(x => x.uid),
      ...(m.shells || []).filter(x => x.corners.some(q => q.every((c, i) => Math.abs(c - p[i]) < 1e-6))).map(x => x.uid)];
    const U = sky.U;
    const L = v => (U ? U.fmt("length", v, 3) : v.toFixed(3));
    const rows = [["Coordinates", p.map(L).join(", ") + (U ? " " + U.label("length") : "")], ["Story", PG.storyAtZ(m, p[2]) || "—"],
      ["Restraints", sup ? sup.restraints.map((r, i) => (r ? ["UX", "UY", "UZ", "RX", "RY", "RZ"][i] : "")).filter(Boolean).join(" ") || "none" : "none"],
      ["Point spring", spr ? (spr.property || "inline") : "none"], ["Joint loads", loads.map(([pn, n]) => `${pn} (${n})`).join(", ") || "none"],
      ["Connected objects", conn.join(", ") || "none"]];
    if (F.infoDialog) return F.infoDialog(`Joint Information`, rows);
    return null;
  }
  F.objectInfo = objectInfo;

  /** main 3D view: object under the right-click (all types). */
  F.ctxPick = (v, e) => {
    const r = v.canvas.getBoundingClientRect();
    return F.pick.pickAt(v, e.clientX - r.left, e.clientY - r.top, 8);
  };
  return { items: F.ctxItems, onShowResults, objectInfo };
}
