/* E1 — Analysis Options › Solver tab: parallel solve lanes and how the
   worker processes start (CONTRACT "Windows-capable parallel solves").

   Edits model.analysis_options.parallel ("auto" | "off" | 2..8) and
   .parallel_start ("auto" | "fork" | "spawn").  A value equal to the
   default is removed, and an analysis_options object left empty is
   deleted, so OK with defaults leaves the model byte-identical. */
import { el as e1El, dkGroup as e1Group, dkNote as e1Note } from "./dlgkit.js";

const E1_PAR = [["auto", "Automatic (performance cores, up to 8)"], ["off", "Off (serial)"],
  ...[2, 3, 4, 5, 6, 7, 8].map(n => [n, n + " processes"])];
const E1_START = [["auto", "Automatic (fork on Linux, worker pool on Windows)"],
  ["spawn", "Persistent worker pool (spawn)"], ["fork", "Fork (Linux only)"]];

function e1Set(D, key, v) {
  const o = { ...(D.analysis_options || {}) };
  if (v === "auto") delete o[key]; else o[key] = v;
  if (Object.keys(o).length) D.analysis_options = o; else delete D.analysis_options;
}

/** The Solver tab body; F = the dialog's dkForm, D = the draft model. */
export function e1SolverPanel(F, D) {
  const ao = () => D.analysis_options || {};
  return e1El("div", {}, [e1Group("Parallel Solves", [
    F.select({}, "parallel", E1_PAR, { label: "Parallel processes", id: "dcOptParallel",
      read: () => ao().parallel ?? "auto", write: v => e1Set(D, "parallel", v) }),
    F.select({}, "parallel_start", E1_START, { label: "Worker processes", id: "dcOptParStart",
      read: () => ao().parallel_start ?? "auto", write: v => e1Set(D, "parallel_start", v) }),
    e1Note("Large models (sparse solver) solve their independent static cases, modal condensation columns and mode shapes " +
      "on several processes. Results are identical to a serial run. The worker pool rebuilds the model once and stays warm " +
      "between runs until the model changes; small models always run serially."),
  ])]);
}
