"""Real projects: the user's own ETABS models, run in both programs and compared (no hand calculation).

R1 is not built from a definition.  On the ETABS PC the runner hands it to ``etabs_ext_real.run_case``,
which opens every ``*.EDB`` in the folder ``RealProjects`` next to the results folders (``--out``),
runs the analysis cases the model has set to run (all of them when none is set), and exports per project:
base reactions per load case and combination, modal periods and participating mass ratios, story drifts,
the maximum joint displacements per case, and the model's text export (``<model>.$et``, written by ETABS
on save), which is copied into the R1 folder and also embedded (compressed) in ``R1.etabs.json``.

Here, ``sf_ext_real.run`` imports each text export with SkyFrame's e2k reader, runs it and exports the
same quantities; ``compare_real.py`` prints the two side by side with relative differences.
"""

from __future__ import annotations


def R1() -> dict:
    return {
        "id": "R1", "title": "Real projects: the user's ETABS models",
        "purpose": ("Every ETABS model dropped into the RealProjects folder is run in ETABS as its author set it "
                    "up, exported to text (.$et), imported into SkyFrame and run there. Base reactions, periods "
                    "and mass ratios, story drifts and the largest joint displacements are compared program "
                    "against program (python compare_real.py); there is no hand calculation."),
        "units": "kip-ft",
        "ext": "real",
        "slow": True,
        "folder": "RealProjects",
        "settings": {
            "model": "as the project's author built it (the EDB is opened unchanged; a copy is analysed)",
            "analysis": "the cases the model has set to run; all of its cases when none is set",
            "solver": "ETABS advanced solver, all cores; SkyFrame default",
            "transfer": "ETABS text export (.$et) read by SkyFrame's e2k importer",
        },
        "materials": [], "sections": [], "stories": [], "frames": [], "supports": [], "patterns": [],
        "checks": [],
    }


CASES = {"R1": R1}
