/* SkyFrame Wave C (C9) — Layered Shell Section Data: layers of ANY material.

   CONTRACT "Anisotropic shells, material angle, Section Designer
   moment-curvature, layered shells with any material, results CSV folder"
   (skyframe/core/aniso.py, CONTRACT v0.22 for concrete / steel).

     ShellSection.layered = {layers: [{t, material, kind, angle?}, …]}
       kind  concrete | steel           (v0.22 nonlinear laws)
             elastic                    any material, linear elastic
             orthotropic                orthotropic / anisotropic material
             timber                     orthotropic material (C24, GL24h…)
             user                       the material's stress–strain law,
                                        smeared uniaxial along `angle`
       angle deg from element local 1 (steel 0 / 90; new kinds any angle;
             never on concrete)

   A section with a new kind is solved as the REAL elastic stack in linear
   runs (exact layer stresses) and as the real layered section in
   nonlinear runs.  Opened from Assign › Shell › Layered Section…
   (asn-slayer, which used to show a hint only): edits the section of the
   selected shells, or any section picked in the dialog.  Draft + changed-
   keys commit (dlgkit) — OK without edits writes nothing. */

import lyU from "./units.js";
import { el as lyEl, esc as lyEsc, clone as lyClone, isNum as lyIsNum, dkForm as lyForm, dkGroup as lyGroup, dkNote as lyNote,
  dkDialog as lyDialog, dkDraft as lyDraft, dkCommit as lyCommit } from "./dlgkit.js";
import { wc9OrthoQ as lyOrthoQ, wc9AnisoQ as lyAnisoQ, wc9Rotate as lyRotate } from "./wc9_aniso.js";

export const WC9_LAYER_KINDS = [
  ["concrete", "Concrete (nonlinear)"], ["steel", "Steel / rebar (PlateRebar)"],
  ["elastic", "Elastic (any material)"], ["orthotropic", "Orthotropic / anisotropic"],
  ["timber", "Timber (orthotropic)"], ["user", "User stress–strain"],
];
const NEW_KINDS = new Set(["elastic", "orthotropic", "timber", "user"]);

/** "" or the first problem of one layer (mirror of core/aniso.validate_layer). */
export function layerProblem(la, mats, i = 0) {
  const pre = `Layer ${i + 1}`;
  if (!(lyIsNum(la.t) && la.t > 0)) return `${pre}: thickness must be greater than zero.`;
  const m = (mats || {})[la.material];
  if (!m) return `${pre}: pick a material.`;
  if (!WC9_LAYER_KINDS.some(([k]) => k === la.kind)) return `${pre}: unknown kind ${la.kind}.`;
  const hasAng = la.angle != null && la.angle !== "";
  if (hasAng && !lyIsNum(+la.angle)) return `${pre}: angle must be a number of degrees.`;
  if (la.kind === "concrete" && hasAng && +la.angle !== 0) return `${pre}: concrete layers take no angle.`;
  if (la.kind === "steel" && hasAng && ![0, 90].includes(+la.angle)) return `${pre}: steel layers run at 0° or 90°.`;
  const sym = m.symmetry || "isotropic";
  if (la.kind === "orthotropic" && !["orthotropic", "anisotropic"].includes(sym)) return `${pre}: kind “orthotropic” needs an orthotropic or anisotropic material (${la.material} is ${sym}).`;
  if (la.kind === "timber" && sym !== "orthotropic") return `${pre}: kind “timber” needs an orthotropic material such as C24 / GL24h (${la.material} is ${sym}).`;
  if (la.kind === "elastic" && sym === "uniaxial") return `${pre}: kind “elastic” needs an isotropic, orthotropic or anisotropic material — use “steel” or “user” for ${la.material}.`;
  if (la.kind === "user" && !(m.stress_strain && m.stress_strain.model && m.stress_strain.model !== "default")) return `${pre}: kind “user” needs a material with a stress–strain law (${la.material} has none).`;
  return "";
}

/** Elastic membrane stiffness A = Σ t Q̄ (kPa·m) of the stack — preview only
    (same rules as core/aniso.layer_elastic; user = material E as a fibre). */
export function layeredA(layers, mats) {
  const A = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (const la of layers || []) {
    const m = (mats || {})[la.material];
    if (!m || !(la.t > 0)) continue;
    const ang = +la.angle || 0;
    let Q;
    if (la.kind === "steel" || la.kind === "user") {
      const t = ang * Math.PI / 180, v = [Math.cos(t) ** 2, Math.sin(t) ** 2, Math.cos(t) * Math.sin(t)];
      Q = v.map(a => v.map(b => m.E * a * b));
    } else if (la.kind === "concrete") Q = lyOrthoQ({ ...m, symmetry: "isotropic" });
    else Q = lyRotate(m.symmetry === "anisotropic" && m.aniso ? lyAnisoQ(m.aniso) : lyOrthoQ(m), ang);
    for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) A[i][j] += la.t * Q[i][j];
  }
  return A;
}

function pickSection(sky, D) {
  const S = sky.store;
  const ref = (S.selection || []).find(r => r.type === "shell");
  const sh = ref && (S.model.shells || []).find(s => s.uid === ref.uid);
  if (sh && D.shell_sections[sh.section]) return sh.section;
  return Object.keys(D.shell_sections || {})[0] || null;
}

/** Open the dialog (section name optional). */
export function openLayeredSections(sky, name = null) {
  const D = lyDraft(sky);
  D.shell_sections = D.shell_sections || {};
  const st = { name: name || pickSection(sky, D) };
  if (!st.name) { sky.toast && sky.toast("Layered Section", "Define a slab / wall section first (Define › Section Properties › Slab / Wall Sections).", "info"); return null; }
  const F = lyForm();
  const body = lyEl("div", { class: "wc9-layers", id: "wc9LayersBody" });
  const touchedSec = new Set();
  const mats = () => D.materials || {};
  let work = null;                  // {layers} being edited for st.name
  const err = lyEl("p", { class: "dk-err", role: "alert", id: "wc9LayErr" });
  const prev = lyEl("p", { class: "muted dk-note", id: "wc9LayPrev" });
  const check = () => {
    if (!work) { err.textContent = ""; return ""; }
    if (!work.layers.length) { err.textContent = "Add at least one layer."; return err.textContent; }
    for (const [i, la] of work.layers.entries()) { const e = layerProblem(la, mats(), i); if (e) { err.textContent = e; return e; } }
    err.textContent = "";
    return "";
  };
  const paint = () => {
    if (!work) { prev.textContent = "Not layered — the section is analysed as one elastic plate of its thickness."; return; }
    const tot = work.layers.reduce((a, l) => a + (+l.t || 0), 0);
    const nw = work.layers.some(l => NEW_KINDS.has(l.kind));
    const A = layeredA(work.layers, mats());
    const f = x => lyEsc(lyU.fmt("line_force", x * 1e-3, 1));
    prev.innerHTML = `Σt <b>${lyEsc(lyU.fmt("small", tot, 1))} ${lyEsc(lyU.label("small"))}</b> · membrane A11 ${f(A[0][0])}, A22 ${f(A[1][1])}, A16 ${f(A[0][2])} ${lyEsc(lyU.label("line_force"))} per 10⁻³ strain. ` +
      (nw ? "Contains elastic / orthotropic / timber / user layers: linear and nonlinear runs use the real layer stack (exact layer stresses)."
        : "Concrete / steel only: linear runs use the homogeneous section at Σt (legacy); nonlinear runs the layers.");
  };
  const render = () => {
    F.forget(body);
    body.textContent = "";
    const s = D.shell_sections[st.name];
    work = s.layered ? s.layered : null;
    const secRow = F.select(st, "name", () => Object.keys(D.shell_sections).map(n => [n, n]), { label: "Slab / Wall Section", id: "wc9LaySec", onChange: () => render() });
    const onRow = F.check(null, "on", {
      label: "Layered section", id: "wc9LayOn", read: () => !!s.layered,
      write: v => {
        touchedSec.add(st.name);
        if (v) {
          const t = s.thickness > 0 ? s.thickness : 0.2, half = +(t / 2).toFixed(4);
          s.layered = { layers: [{ t: half, material: s.material, kind: "concrete" }, { t: +(t - half).toFixed(4), material: s.material, kind: "concrete" }] };
        } else s.layered = null;
      },
      onChange: () => render(),
    });
    const kids = [secRow, onRow];
    if (work) {
      kids.push(F.table(work.layers, [
        { key: "t", label: "Thickness", type: "num", kind: "small" },
        { key: "material", label: "Material", type: "select", options: () => Object.keys(mats()).map(n => [n, n]) },
        { key: "kind", label: "Kind", type: "select", options: WC9_LAYER_KINDS },
        { key: "angle", label: "Angle (deg)", type: "num" },
      ], { id: "wc9LayTable", addLabel: "+ Layer", minRows: 1,
        newRow: rows => { const l = rows[rows.length - 1]; return { t: l ? l.t : 0.05, material: l ? l.material : s.material, kind: l ? l.kind : "concrete" }; },
        onChange: () => { touchedSec.add(st.name); paint(); check(); },
        validate: () => check() }));
    }
    body.appendChild(lyGroup("Layer Definition (bottom → top)", kids, { id: "wc9LayGrp" }));
    paint();
    check();
  };
  render();
  const normalise = layers => layers.map(l => {
    const o = { t: +l.t, material: l.material, kind: l.kind };
    const a = l.angle == null || l.angle === "" ? null : +l.angle;
    if (l.kind !== "concrete" && a != null && (a !== 0 || NEW_KINDS.has(l.kind) || "angle" in l)) o.angle = a;
    return o;
  });
  return lyDialog({
    id: "wc9LayeredDlg", title: "Layered Shell Section Data", iconId: "exleaf-shells", size: "lg", form: F,
    intro: "Layers of any material: concrete / steel (nonlinear laws), elastic, orthotropic / anisotropic, timber or a user stress–strain law, each at its own angle from the element local axis 1.",
    body: [body, err, prev, lyNote("Angles in degrees from local 1 toward local 2. Self-weight and mass use the section material × Σt (set the section material to the dominant layer, or use the weight / mass modifiers).")],
    onOk: () => {
      const e = check();
      if (e) return e;
      if (!touchedSec.size) return;            // OK with defaults: nothing written
      for (const n of touchedSec) {
        const s = D.shell_sections[n];
        if (s && s.layered) s.layered = { layers: normalise(s.layered.layers) };
      }
      lyCommit(sky, D);
    },
  });
}

/** Register Assign › Shell › Layered Section… (keeps the act id). */
export function initWc9Layers(sky) {
  if (!sky) return;
  sky.wc9 = sky.wc9 || {};
  sky.wc9.openLayered = n => openLayeredSections(sky, n);
  if (!(sky.menu && sky.menu.add)) return;
  try { sky.menu.remove && sky.menu.remove("asn-slayer"); } catch (e) { /* not registered */ }
  sky.menu.add("Assign/Shell", { label: "Layered Section…", act: "asn-slayer", before: "asn-sspring", hint: "layers of any material", fn: () => openLayeredSections(sky) });
}

export default { openLayeredSections, initWc9Layers, layerProblem, layeredA, WC9_LAYER_KINDS };
