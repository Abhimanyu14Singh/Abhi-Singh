"""Write js/mock_steeldb_data.js: a small verbatim subset of aisc.json.

usage: python3 tools/gen_mock_steeldb.py skyframe/data/steel/aisc.json \
           skyframe/api/web/js/mock_steeldb_data.js
"""
import json, sys
src, out = sys.argv[1], sys.argv[2]
d = json.load(open(src, encoding="utf-8"))
by = {s["name"]: s for s in d["shapes"]}
want = ["W8X31", "W10X49", "W12X26", "W14X90", "W16X36", "W18X50", "W21X62", "W24X76", "W27X94", "W30X116", "W33X130",
        "W36X150", "W44X335", "M12.5X12.4", "S24X121", "HP14X73", "HP18X204", "C10X20", "C15X50", "MC18X58",
        "L4X3X1/4", "L4X4X1/2", "L6X4X1/2", "L8X6X1", "2L4X4X1/2", "2L6X4X1/2LLBB", "2L8X6X1LLBB", "2L8X6X1SLBB",
        "WT7X45", "WT9X35.5", "MT6.25X6.2", "ST12X60.5", "HSS8X4X1/4", "HSS12X8X1/2", "HSS20X12X5/8", "HSS6X6X3/8",
        "HSS10X10X1/2", "HSS6.625X0.280", "HSS16.000X0.625", "Pipe4STD", "Pipe8STD", "Pipe12XS"]
missing = [n for n in want if n not in by]
if missing:
    raise SystemExit(f"missing {missing}")
order = {s["name"]: i for i, s in enumerate(d["shapes"])}
recs = []
for n in sorted(want, key=order.get):
    r = dict(by[n]); r.pop("aisc", None)
    recs.append(r)
head = {k: d[k] for k in ("standard", "edition", "source", "units")}
with open(out, "w", encoding="utf-8") as fh:
    fh.write("/* GENERATED from skyframe/data/steel/aisc.json (tools: verbatim records, the\n"
             "   'aisc' source block dropped) - the ?mock=1 stand-in of the AISC Shapes\n"
             f"   Database v16.0: {len(recs)} of {len(d['shapes'])} shapes across all families. */\n")
    fh.write("export const MOCK_AISC = " + json.dumps({**head, "shapes": recs}, ensure_ascii=False, separators=(",", ":"))
             .replace('{"name"', '\n  {"name"') + ";\n")
print(len(recs), "records")
