/* SkyFrame — Wave C: Output group of Load Case Data — Time History
   (core/th_output.py; CONTRACT "TH shell results (TimeHistoryCase.th_output)").

     Record shell forces / stresses   th_output.shells   (step envelope +
                                                         snapshots)
     Snapshot steps                   th_output.steps    ("50, 120")
     Snapshot every N steps           th_output.every
     Animation frames                 th_output.frames   (deformed-shape
                                                         frames for the
                                                         video export)

   Hooked into js/dlgstd_cases.js editThCase with one body line
   (window.__sky.wcThOut.thFields). Keys are written only when the user
   changes a field; a key at its default is removed and an empty request
   removes `th_output`, so OK with defaults leaves the model byte-identical.
   Section-cut / pier / spandrel histories need no request (always recorded
   when the model defines them). */

import { dkGroup as toGroup } from "./dlgkit.js";

export const TH_OUT_DEFAULTS = { shells: false, steps: [], every: 0, frames: 0 };

function setKey(c, key, v) {
  const q = { ...(c.th_output || {}) };
  const def = TH_OUT_DEFAULTS[key];
  if (v == null || JSON.stringify(v) === JSON.stringify(def)) delete q[key]; else q[key] = v;
  if (Object.keys(q).length) c.th_output = q; else delete c.th_output;
}
const get = (c, key) => (c.th_output && c.th_output[key] != null) ? c.th_output[key] : TH_OUT_DEFAULTS[key];

export function parseSteps(txt) {
  const parts = String(txt || "").split(/[\s,;]+/).filter(Boolean);
  const out = [];
  for (const p of parts) {
    const v = Number(p);
    if (!Number.isInteger(v) || v < 1) return null;
    out.push(v);
  }
  return [...new Set(out)].sort((a, b) => a - b);
}

/** dkForm fields for the TH case dialog (F = dkForm, c = draft case). */
export function thOutFields(F, c) {
  return [toGroup("Output", [
    F.check(null, "shells", { label: "Record shell forces / stresses (step envelope + snapshots)", id: "wcThShells",
      read: () => !!get(c, "shells"), write: v => setKey(c, "shells", !!v) }),
    F.text(null, "steps", { label: "Snapshot steps", id: "wcThSteps", required: false, placeholder: "e.g. 50, 120",
      hint: "1-based output steps with full shell results (step k = time k·Δt)",
      read: () => get(c, "steps").join(", "),
      validate: v => parseSteps(v) == null ? "Enter whole step numbers ≥ 1, separated by commas." : (parseSteps(v).length > 200 ? "At most 200 snapshot steps." : ""),
      write: v => setKey(c, "steps", parseSteps(v) || []) }),
    F.num(null, "every", { label: "Snapshot every N steps", int: true, min: 0, def: 0, id: "wcThEvery",
      read: () => get(c, "every"), write: v => setKey(c, "every", v == null ? 0 : Math.round(v)) }),
    F.num(null, "frames", { label: "Animation frames", int: true, min: 0, max: 1000, def: 0, id: "wcThFrames",
      hint: "deformed-shape frames for Display › Export Animation Video (0 = none)",
      read: () => get(c, "frames"), write: v => setKey(c, "frames", v == null ? 0 : Math.round(v)) }),
  ])];
}

export function initWcThOut(sky) {
  sky.wcThOut = { thFields: (F, c) => thOutFields(F, c), parseSteps, defaults: TH_OUT_DEFAULTS };
}
