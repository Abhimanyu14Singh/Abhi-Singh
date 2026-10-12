/* SkyFrame Wave C — Define › Groups, Cuts & Labels › Generalized Displacements…
   (ETABS Define > Generalized Displacements). dlgkit standard list dialog +
   "Generalized Displacement Data" sub-dialog editing
   model.generalized_displacements = {name: {type, terms: [{point, dof, sf}]}}
   (CONTRACT "Wave C: Show Tables and Interactive Database Editing coverage").
   Points go through units.js (length); scale factors act on SI joint
   displacements (m, rad). Results: Display › Show Tables › Generalized
   Displacements. Exported: initGenDisp(sky), openGenDisp(sky). */

import { dkForm as gdForm, dkGroup as gdGroup, dkList as gdList, dkDialog as gdDialog, dkDraft as gdDraft,
  dkCommit as gdCommit, dkNote as gdNote, el as gdEl, uniqueName as gdUnique, clone as gdClone } from "./dlgkit.js";

const GD_DOFS = ["UX", "UY", "UZ", "RX", "RY", "RZ"];
const TYPES = [["translation", "Translation", "length result"], ["rotation", "Rotation", "angle result"]];

export function openGenDisp(sky, { select } = {}) {
  const D = gdDraft(sky);
  const had = "generalized_displacements" in D;
  const gds = () => D.generalized_displacements || {};
  const F = gdForm();
  const list = gdList({
    id: "wcGdList",
    items: () => Object.entries(gds()).map(([k, v]) => ({ key: k, v })),
    columns: [{ label: "Name", get: it => it.key }, { label: "Type", get: it => (it.v.type || "translation") === "rotation" ? "Rotation" : "Translation" },
      { label: "Terms", get: it => (it.v.terms || []).length }],
    selected: select || null,
    onOpen: k => edit(k),
    empty: "No generalized displacements yet — add one, e.g. an inter-story drift (u_top − u_bottom) / h.",
    actions: [
      { label: "Add New…", id: "wcGdAdd", fn: () => edit(null) },
      { label: "Add Copy…", id: "wcGdCopy", needsSel: true, fn: k => edit(k, true) },
      { label: "Modify/Show…", id: "wcGdModify", needsSel: true, fn: k => edit(k) },
      { label: "Delete", id: "wcGdDelete", needsSel: true, fn: k => {
        const next = { ...gds() }; delete next[k];
        D.generalized_displacements = next; list.render(); } },
    ],
  });
  const edit = (name, copy = false) => {
    const F2 = gdForm();
    const src = name ? gds()[name] : null;
    const cur = { name: name && !copy ? name : gdUnique(gds(), name ? name + "_copy" : "GD1"), type: src ? (src.type || "translation") : "translation" };
    const rows = src ? (src.terms || []).map(t => ({ x: t.point[0], y: t.point[1], z: t.point[2], dof: t.dof, sf: t.sf ?? 1 })) : [];
    const body = [
      gdGroup("General", [
        F2.text(cur, "name", { label: "Name", id: "wcGdName", validate: v => !v ? "Enter a name." : (v !== (copy ? null : name) && gds()[v] ? `“${v}” is already used — pick another name.` : "") }),
        F2.radio(cur, "type", TYPES, { label: "Type", idPrefix: "wcGdType_" }),
      ]),
      gdGroup("Joint Degrees of Freedom", [
        F2.table(rows, [
          { key: "x", label: "X", type: "num", kind: "length" },
          { key: "y", label: "Y", type: "num", kind: "length" },
          { key: "z", label: "Z", type: "num", kind: "length" },
          { key: "dof", label: "DOF", type: "select", options: GD_DOFS.map(d => [d, d]) },
          { key: "sf", label: "Scale Factor", type: "num" },
        ], { id: "wcGdTerms", minRows: 1, minMsg: "Add at least one joint DOF.", addLabel: "+ Add joint DOF",
          newRow: r => ({ ...(r.length ? { x: r[r.length - 1].x, y: r[r.length - 1].y, z: r[r.length - 1].z } : { x: 0, y: 0, z: 0 }), dof: "UX", sf: 1 }),
          empty: "Each row adds scale factor × the joint's displacement in that DOF." }),
        gdNote("g = Σ SF·u(joint, DOF), global axes. Scale factors act on SI joint displacements (m, rad): a drift ratio uses SF = ±1/h with h in m."),
      ]),
    ];
    return gdDialog({
      id: "wcGdData", title: "Generalized Displacement Data", iconId: "exleaf-stories", size: "md", form: F2, body,
      onOk: () => {
        if (!F2.touched && name && !copy) return;
        if (!rows.length) return "Add at least one joint DOF.";
        const def = { type: cur.type, terms: rows.map(r => ({ point: [+r.x, +r.y, +r.z], dof: r.dof, sf: +r.sf })) };
        const next = {};
        for (const [k, v] of Object.entries(gds())) next[k === name && !copy ? cur.name : k] = k === name && !copy ? def : v;
        if (!(cur.name in next)) next[cur.name] = def;
        D.generalized_displacements = next;
        list.select(cur.name);
      },
    });
  };
  return gdDialog({
    id: "wcGdDefine", title: "Define Generalized Displacements", iconId: "exleaf-stories", size: "lg", form: F,
    body: [list, gdEl("p", { class: "muted dk-note", html: "Results: <b>Display › Show Tables › Analysis Results › Generalized Displacements</b> (static cases, combinations, recorded time histories)." })],
    intro: "A generalized displacement is a named linear combination of joint displacements and rotations.",
    onOk: () => {
      if (!had && D.generalized_displacements && !Object.keys(D.generalized_displacements).length) delete D.generalized_displacements;
      if (had && D.generalized_displacements && !Object.keys(D.generalized_displacements).length) delete D.generalized_displacements;
      gdCommit(sky, D);
    },
  });
}

export function initGenDisp(sky) {
  if (!sky || !sky.menu || !sky.menu.add) return;
  sky.menu.add("Define/Groups, Cuts & Labels", { label: "Generalized Displacements…", act: "def-wc-gendisp", hint: "joint DOF combinations", fn: () => openGenDisp(sky) });
  sky.openGenDisp = o => openGenDisp(sky, o);
  void gdClone;
}
