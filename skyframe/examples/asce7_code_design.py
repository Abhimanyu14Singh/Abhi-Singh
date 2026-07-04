"""Example: ASCE 7-16 code-based workflow on a SkyFrame building.

Demonstrates the v0.7 "code tools":
  * real self-weight loads (from material density),
  * an ASCE 7-16 design response spectrum + response-spectrum case,
  * automatic ASCE 7 LRFD load-combination generation,
  * the equivalent-lateral-force (ELF) seismic pattern.

Run:  python3 examples/asce7_code_design.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skyframe import quick_building, OpenSeesEngine
from skyframe.core.builder import add_self_weight
from skyframe.core import codes


def main() -> None:
    model = quick_building(name="ASCE 7 Office", bays_x=3, bays_y=2,
                           stories=5, story_height=3.5, quake_coeff=0.10)

    # 1) real self-weight as its own pattern + case, and add it to the mass
    add_self_weight(model, pattern="SW", factor=1.0)
    model.mass_source = {"DEAD": 1.0, "SW": 1.0}

    # 2) ASCE 7-16 design spectrum (mapped accelerations + site class D)
    Ss, S1, site = 1.5, 0.6, "D"
    SDS, SD1, SMS, SM1, T0, Ts = codes.spectrum_parameters(Ss, S1, site)
    print("Design spectrum parameters (Ss=1.5, S1=0.6, Site D):")
    for label, val in (("SDS", SDS), ("SD1", SD1), ("SMS", SMS),
                       ("SM1", SM1), ("T0", T0), ("Ts", Ts)):
        print(f"  {label:4s} = {val:.4f}")
    codes.make_rs_case_from_code(model, "RS-X", direction="X",
                                 Ss=Ss, S1=S1, site_class=site, R=8.0, Ie=1.0)

    # 3) equivalent-lateral-force seismic pattern (§12.8)
    codes.asce7_elf(model, SDS=SDS, SD1=SD1, R=8.0, Ie=1.0, direction="X")

    # 4) auto-generate the ASCE 7 LRFD strength combinations
    added = codes.apply_asce7_combinations(model, standard="LRFD")
    print(f"\nGenerated {len(added)} ASCE 7 LRFD combinations, e.g.:")
    for name in list(added)[:4]:
        print(f"  {name}")

    # solve and report
    results = OpenSeesEngine(model).run().to_dict()
    dead_fz = results["cases"]["DEAD"]["base"]["FZ"]
    sw_fz = results["cases"]["SW"]["base"]["FZ"]
    print(f"\nDEAD base FZ      = {dead_fz:10.1f} kN")
    print(f"Self-weight FZ    = {sw_fz:10.1f} kN")
    print(f"Fundamental T1    = {results['modal']['periods'][0]:.3f} s")
    rsx = results.get("rs_cases", {}).get("RS-X", {})
    if rsx:
        top = model.stories[-1].name
        print(f"RS-X roof drift   = {rsx['story'][top]['drift_x']:.5f}")


if __name__ == "__main__":
    main()
