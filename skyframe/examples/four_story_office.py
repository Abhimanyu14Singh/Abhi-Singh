"""Example: 4-story concrete moment-frame office building.

Run:  python3 examples/four_story_office.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skyframe import quick_building, OpenSeesEngine


def main() -> None:
    model = quick_building(
        name="4-Story Office",
        bays_x=4, bay_width_x=7.0,
        bays_y=3, bay_width_y=6.0,
        stories=4, story_height=3.4, first_story_height=4.2,
        E=27_800_000.0,          # C30 concrete, kPa
        column_size=0.55,
        beam_b=0.30, beam_h=0.65,
        dead_udl=28.0, live_udl=12.0,
        quake_coeff=0.10,
    )
    results = OpenSeesEngine(model).run().to_dict()

    print(f"Model: {model.name}")
    print(f"  members: {len(model.members)}, stories: {len(model.stories)}")
    print("\nModal:")
    for p in results["modal"]["participation"][:4]:
        print(f"  Mode {p['mode']}: T = {p['T']:.3f} s   "
              f"UX {p['ux']*100:5.1f}%  UY {p['uy']*100:5.1f}%  RZ {p['rz']*100:5.1f}%")

    print("\nStory drifts under EQX (ratio):")
    eqx = results["cases"]["EQX"]["story"]
    for s in results["story_order"]:
        print(f"  {s:8s} drift_x = {eqx[s]['drift_x']:.5f}   "
              f"shear_x = {eqx[s]['shear_x']:8.1f} kN")

    base = results["cases"]["DEAD"]["base"]
    print(f"\nDEAD base reaction FZ = {base['FZ']:.1f} kN")

    out = Path(__file__).with_suffix(".results.json")
    out.write_text(json.dumps(results, indent=1))
    print(f"\nFull results written to {out.name}")


if __name__ == "__main__":
    main()
