/* SkyFrame — Analyze › Analysis Options › Solver tab (Wave E2).

   Linear solver policy (model.analysis_options.solver, incl. the MKL
   PARDISO fast path) and the PARDISO thread count
   (analysis_options.pardiso_threads: "auto" = every physical core).
   Defaults are never stored: choosing "auto" removes the key, and an empty
   analysis_options object is deleted, so OK with defaults leaves the saved
   model byte-identical.  CONTRACT "PARDISO linear solver (MKL)". */
import { el as soEl, dkGroup as soGroup, dkNote as soNote } from "./dlgkit.js";

export const SO_SOLVERS = [
  ["auto", "Auto (by model size)"],
  ["pardiso", "PARDISO (Intel MKL, multithreaded)"],
  ["mumps", "MUMPS"],
  ["umfpack", "UMFPACK"],
  ["sparse_general", "Sparse general"],
  ["band", "Band (legacy)"],
];
export const SO_EIGEN = [["auto", "Auto"], ["lanczos", "Shift-invert (sparse)"], ["legacy", "OpenSees eigen (legacy)"]];

function soSet(D, key, v, def) {
  const ao = (D.analysis_options && typeof D.analysis_options === "object") ? D.analysis_options : {};
  if (v === def || v == null) delete ao[key]; else ao[key] = v;
  if (Object.keys(ao).length) D.analysis_options = ao; else delete D.analysis_options;
}
const soGet = (D, key, def) => (D.analysis_options && D.analysis_options[key] != null) ? D.analysis_options[key] : def;

/** The Solver tab body for the Analysis Options dialog (dlgkit form F,
    draft model D). */
export function soSolverPanel(F, D) {
  const st = { solver: soGet(D, "solver", "auto"), eigen: soGet(D, "eigen_solver", "auto") };
  return soEl("div", {}, [
    soGroup("Linear Equation Solver", [
      F.select(st, "solver", SO_SOLVERS, { label: "Solver", id: "soSolver",
        hint: "Auto uses PARDISO on large models when Intel MKL is installed (pip install skyframe[fast]).",
        write: v => { st.solver = v; soSet(D, "solver", v, "auto"); } }),
      F.num(null, "pardiso_threads", { label: "PARDISO threads", int: true, min: 1, max: 256, allowNull: true,
        nullText: "auto — all physical cores", def: null, id: "soThreads",
        read: () => { const v = soGet(D, "pardiso_threads", "auto"); return v === "auto" ? null : v; },
        write: v => soSet(D, "pardiso_threads", v == null ? "auto" : Math.round(v), "auto") }),
      F.select(st, "eigen", SO_EIGEN, { label: "Eigen solver", id: "soEigen",
        write: v => { st.eigen = v; soSet(D, "eigen_solver", v, "auto"); } }),
    ], { note: "PARDISO factors the stiffness once and solves every linear case, modal and response-spectrum load as forward/back substitutions. Anything it cannot represent exactly runs on OpenSees; the run log reports which engine solved and why." }),
    soNote("Results are equal to the OpenSees solvers within 1e-9 (relative); see Help › Performance."),
  ]);
}
