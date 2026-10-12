/* SkyFrame — ETABS spandrel labels and spandrel forces (CONTRACT "Results
   tables export, spandrels and Word report").

     Define › Spandrel Labels…            def-a8-spandrels   (list dialog)
     Assign › Shell › Spandrel Label…     asn-a8-sspandrel   (selected walls)
     Assign › Frame › Spandrel Label…     asn-a8-fspandrel   (selected frames)
     Display › Force Diagrams › Spandrel Forces…   dis-a8-spandrels

   Model (SI, round-tripped by the backend): `members[i].spandrel`,
   `shells[i].spandrel` (walls only) — written ONLY when set, so OK with
   defaults leaves the model byte-identical — and `spandrel_labels: [names]`
   (defined labels, emitted only when non-empty).
   Results: `results.spandrels[case][label][story] = {Left: {P, V2, V3, T,
   M2, M3}, Right: {…}, length, n_shells, n_frames}`; every number goes
   through js/units.js. */

import spU from "./units.js";
import { el as spEl, esc as spEsc, dkForm as spForm, dkGroup as spGroup, dkList as spList, dkNote as spNote,
  dkDialog as spDialog, dkDraft as spDraft, dkCommit as spCommit, uniqueName as spUnique } from "./dlgkit.js";

const SP_LABEL_RE = /^[^\s,;][^,;]{0,63}$/;
const spSel = sky => (sky.store.selection || []);

/** Labels defined + used, in order (mirrors core.spandrels.all_labels). */
export function spAllLabels(m) {
  const out = [...((m && m.spandrel_labels) || [])];
  for (const o of [...((m && m.members) || []), ...((m && m.shells) || [])])
    if (o.spandrel && !out.includes(o.spandrel)) out.push(o.spandrel);
  return out;
}
function spUsage(m, lab) {
  const fr = (m.members || []).filter(x => x.spandrel === lab).map(x => x.uid);
  const sh = (m.shells || []).filter(x => x.spandrel === lab).map(x => x.uid);
  return { frames: fr, shells: sh, n: fr.length + sh.length };
}
function spSetLabels(D, labels) {
  if (labels.length) D.spandrel_labels = labels; else delete D.spandrel_labels;
}

/* ================================================================
   Define › Spandrel Labels
   ================================================================ */
export function spOpenDefine(sky) {
  const D = spDraft(sky);
  let labels = spAllLabels(D);
  const items = () => labels.map(l => ({ key: l, ...spUsage(D, l) }));
  const ask = (title, init, onOk) => {
    const F = spForm();
    const V = { name: init };
    spDialog({ id: "spLabelData", title, size: "sm", form: F,
      body: [spGroup("Spandrel Label", [F.text(V, "name", { label: "Label", id: "spLabelName",
        validate: v => !SP_LABEL_RE.test(String(v || "").trim()) ? "1–64 characters, no comma or semicolon." :
          (String(v).trim() !== init && labels.includes(String(v).trim()) ? `“${String(v).trim()}” is already used — pick another name.` : "") })])],
      onOk: () => { const n = String(V.name || "").trim(); if (!SP_LABEL_RE.test(n)) return "Enter a valid label."; onOk(n); } });
  };
  const list = spList({
    id: "spLabelList", items,
    columns: [{ label: "Spandrel", get: it => it.key }, { label: "Frames", get: it => it.frames.length },
      { label: "Walls", get: it => it.shells.length }],
    empty: "No spandrel labels yet — add one, then assign it to coupling beams or wall panels (Assign › Frame / Shell › Spandrel Label).",
    actions: [
      { label: "Add New Spandrel…", id: "spAddBtn", fn: () => ask("Spandrel Label", spUnique(new Set(labels), "S1"), n => {
        labels.push(n); spSetLabels(D, labels); list.select(n); }) },
      { label: "Modify / Rename…", id: "spRenameBtn", needsSel: true, fn: s => ask("Spandrel Label", s, n => {
        if (n === s) return;
        labels = labels.map(l => l === s ? n : l);
        for (const o of [...(D.members || []), ...(D.shells || [])]) if (o.spandrel === s) o.spandrel = n;
        spSetLabels(D, labels); list.select(n); }) },
      { label: "Delete Spandrel", id: "spDelBtn", needsSel: true, fn: s => {
        const u = spUsage(D, s);
        if (u.n) { dlg.setError(`“${s}” is assigned to ${u.n} object${u.n > 1 ? "s" : ""} (${[...u.frames, ...u.shells].slice(0, 6).join(", ")}${u.n > 6 ? " …" : ""}) — unassign first.`); return; }
        labels = labels.filter(l => l !== s); spSetLabels(D, labels); dlg.setError(""); list.render(); } },
    ],
  });
  const dlg = spDialog({ id: "spDefine", title: "Spandrel Labels", iconId: "exleaf-shells", size: "md",
    intro: "ETABS spandrels: coupling beams (frames) and wall panels (walls) that share a label form one spandrel per story. " +
      "Spandrel forces (P, V2, M3 at the Left / Right ends) are reported after a run.",
    body: [list], onOk: () => { spCommit(sky, D); } });
  return dlg;
}

/* ================================================================
   Assign › Frame / Shell › Spandrel Label
   ================================================================ */
export function spOpenAssign(sky, kind) {
  const S = sky.store;
  const isFrame = kind === "frame";
  const refs = spSel(sky).filter(r => r.type === (isFrame ? "member" : "shell"));
  const objs = refs.map(r => (isFrame ? S.model.members : S.model.shells).find(o => o.uid === r.uid))
    .filter(o => o && (isFrame || o.kind === "wall"));
  const title = isFrame ? "Assign Frame Spandrel Label" : "Assign Shell Spandrel Label";
  const id = isFrame ? "spAssignFrame" : "spAssignShell";
  if (!objs.length) {
    return spDialog({ id, title, size: "sm", noCancel: true, iconId: isFrame ? "exleaf-frames" : "exleaf-shells",
      body: [spNote(`Nothing to assign — select one or more <b>${isFrame ? "frame objects (coupling beams)" : "walls"}</b> first, then choose this command again.` +
        (isFrame ? "" : " Spandrel labels apply to walls only."))] });
  }
  const F = spForm();
  const cur = new Set(objs.map(o => o.spandrel || ""));
  const V = { label: cur.size === 1 ? [...cur][0] : "", newName: "" };
  let touched = false;
  const opts = () => [["", "None"], ...spAllLabels(S.model).map(l => [l, l]), ["\u0000new", "New label…"]];
  const body = [spGroup("Spandrel", [
    F.select(V, "label", opts, { label: "Spandrel label", id: id + "Label", mixed: cur.size > 1, write: v => { V.label = v; touched = true; } }),
    F.text(V, "newName", { label: "New label", id: id + "New", placeholder: "e.g. S1", required: false,
      hint: "used when the label is “New label…”", write: v => { V.newName = v; } }),
  ], { note: isFrame ? "Coupling beams sharing a label on one story form one spandrel; forces are cut at its two ends."
    : "Wall panels (meshed shells) between piers; the spandrel is cut vertically at its two ends." })];
  const apply = () => {
    if (!touched) return "";
    let lab = V.label;
    if (lab === "\u0000new") {
      lab = String(V.newName || "").trim();
      if (!SP_LABEL_RE.test(lab)) return "Enter a new label (1–64 characters, no comma or semicolon).";
    }
    const D = spDraft(sky);
    const uids = new Set(objs.map(o => o.uid));
    for (const o of (isFrame ? D.members : D.shells) || []) {
      if (!uids.has(o.uid)) continue;
      if (lab) o.spandrel = lab; else delete o.spandrel;
    }
    if (V.label === "\u0000new") {                 // a new label is also defined
      const defs = [...(D.spandrel_labels || [])];
      if (!defs.includes(lab)) { defs.push(lab); D.spandrel_labels = defs; }
    }
    spCommit(sky, D);
    return "";
  };
  return spDialog({ id, title, size: "sm", form: F, body, iconId: isFrame ? "exleaf-frames" : "exleaf-shells",
    intro: `${objs.length} ${isFrame ? "frame object" : "wall"}${objs.length > 1 ? "s" : ""} selected. Choose None to remove the label.`,
    onOk: () => { const m = apply(); if (m) return m; }, onApply: () => apply() });
}

/* ================================================================
   Display › Spandrel Forces
   ================================================================ */
const SP_ST = { case: null };
export function spOpenForces(sky) {
  const S = sky.store, r = S.results;
  const blk = (r && r.spandrels) || {};
  const names = Object.keys(blk);
  const body = spEl("div", { class: "sp-forces" });
  const draw = () => {
    body.textContent = "";
    if (!names.length) {
      body.appendChild(spNote(r ? "No spandrel forces in the results — label coupling beams or wall panels (Assign › Frame / Shell › Spandrel Label) and run the analysis."
        : "Run the analysis first (F5)."));
      return;
    }
    if (!SP_ST.case || !names.includes(SP_ST.case)) SP_ST.case = names[0];
    const sel = spEl("select", { id: "spForcesCase", class: "dk-select" });
    sel.innerHTML = names.map(n => `<option${n === SP_ST.case ? " selected" : ""}>${spEsc(n)}</option>`).join("");
    sel.addEventListener("change", () => { SP_ST.case = sel.value; draw(); });
    body.appendChild(spEl("div", { class: "dk-row" }, [spEl("label", { class: "dk-label", text: "Output case", for: "spForcesCase" }), spEl("div", { class: "dk-ctl" }, [sel]), spEl("span")]));
    const F = spU.label("force"), M = spU.label("moment"), Lu = spU.label("length");
    const rank = Object.fromEntries((S.model.stories || []).map((s, i) => [s.name, i]));
    const rows = [];
    for (const [lab, byStory] of Object.entries(blk[SP_ST.case] || {}).sort())
      for (const [st, e] of Object.entries(byStory).sort((a, b) => (rank[b[0]] ?? -1) - (rank[a[0]] ?? -1)))
        for (const loc of ["Left", "Right"]) if (e[loc]) rows.push({ lab, st, loc, len: e.length, ...e[loc] });
    const vmax = Math.max(1e-9, ...rows.map(x => Math.abs(x.V2)));
    const t = spEl("table", { class: "data-table", id: "spForcesTable" });
    t.innerHTML = `<thead><tr><th class="txt">Story</th><th class="txt">Spandrel</th><th class="txt">Location</th>` +
      `<th>P (${spEsc(F)})</th><th>V2 (${spEsc(F)})</th><th>M3 (${spEsc(M)})</th><th class="txt">V2</th><th>Length (${spEsc(Lu)})</th></tr></thead><tbody>` +
      rows.map(x => `<tr><td class="txt">${spEsc(x.st)}</td><td class="txt">${spEsc(x.lab)}</td><td class="txt">${x.loc}</td>` +
        `<td>${spU.fmt("force", x.P, 2)}</td><td>${spU.fmt("force", x.V2, 2)}</td><td>${spU.fmt("moment", x.M3, 2)}</td>` +
        `<td class="txt"><span style="display:inline-block;height:8px;border-radius:2px;background:${x.V2 >= 0 ? "#1274ab" : "#c0392b"};width:${(60 * Math.abs(x.V2) / vmax).toFixed(1)}px"></span></td>` +
        `<td>${spU.fmt("length", x.len, 2)}</td></tr>`).join("") + `</tbody>`;
    body.appendChild(spEl("div", { class: "table-scroll" }, [t]));
    body.appendChild(spNote("Internal forces at the two end cuts acting on the material on the left (ETABS convention): P tension-positive, " +
      "V2 vertical, M3 in-plane about the cut centroid. Free-body check: V2 Left = V2 Right and M3 Right − M3 Left = −V2 × length when the spandrel is unloaded. " +
      "Also in Display › Show Tables › Wall Output › Spandrel Forces."));
  };
  draw();
  const dlg = spDialog({ id: "spForces", title: "Spandrel Forces", iconId: "show-tables", size: "lg", body: [body], noCancel: true,
    onUnits: draw });
  return dlg;
}

/* ================================================================
   install
   ================================================================ */
export function initSpandrels(sky) {
  if (!sky) return;
  sky.spandrels = { openDefine: () => spOpenDefine(sky), openAssign: k => spOpenAssign(sky, k), openForces: () => spOpenForces(sky),
    labels: () => spAllLabels(sky.store.model) };
  if (!(sky.menu && sky.menu.add)) return;
  sky.menu.add("Define", { label: "Spandrel Labels…", act: "def-a8-spandrels", hint: "coupling beams · wall panels", fn: () => spOpenDefine(sky) });
  sky.menu.add("Assign/Frame", { label: "Spandrel Label…", act: "asn-a8-fspandrel", hint: "selected frames", fn: () => spOpenAssign(sky, "frame") });
  sky.menu.add("Assign/Shell", { label: "Spandrel Label…", act: "asn-a8-sspandrel", hint: "selected walls", fn: () => spOpenAssign(sky, "shell") });
  sky.menu.add("Display/Force Diagrams", { label: "Spandrel Forces…", act: "dis-a8-spandrels", hint: "P · V2 · M3 at both ends", fn: () => spOpenForces(sky) });
}
