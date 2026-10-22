# Structural drawings with CADCraft

Exploration of [storytold/cadcraft](https://github.com/storytold/cadcraft) (tested at commit
`acd109d`, pre-alpha) for producing structural drawings from code.

`beam_b1_detail.py` builds a reinforced-concrete beam detail (elevation + section, dimensions,
rebar callouts, notes, title block) entirely through CADCraft's JSON commands, then plots an A3
sheet from a paper-space layout at 1:25 with `monochrome.ctb` and lineweights.

Output: [`output/S-201_beam_B1.pdf`](output/S-201_beam_B1.pdf) (also `.dxf` and a `.png` preview).

## Run it

```sh
git clone https://github.com/storytold/cadcraft && cd cadcraft
cargo build --release -p cadcraft-cli          # Rust 1.90+, ~3 min
python3 beam_b1_detail.py path/to/cadcraft/target/release/cadcraft-cli out/S-201
# writes out/S-201.pdf and out/S-201.dxf
```

## How the drawing is set up

| Concern | How |
|---|---|
| Layers / pens | `S-CONC` 0.50, `S-REBAR` 0.50, `S-STIR` 0.25, `S-DIMS` 0.18, `S-HATCH` 0.09, `S-CENT` CENTER linetype; viewport on a no-plot layer |
| Annotation scale | Dimstyle `DIMSCALE=25`, multileader style text/arrow sizes x 25 (model-space annotation) |
| Sheet | Layout `S-201`: A3 landscape, border and title block drawn in paper mm, one viewport zoomed to 1/25 XP |
| Plot | `exportpdf` of the layout; `monochrome.ctb` so coloured layers print black |

## What we found

Works well enough for real details: DIMSTYLE variables, ARCHTICK, multileaders, hatch
(`AR-CONC`), TrueType text, layouts/viewports, CTB plot styles, lineweights, true-scale vector
PDF, DXF out. Commands fail loudly with clear messages (unknown linetype, unknown paper).

Gaps hit:

- **Plot styles need a layout.** Model-space page setups are not stored yet, so a model-space
  PDF plots in layer colours (yellow text on white). Use a layout.
- **Multileader style is not scaled by DIMSCALE.** Set `mleaderstyle` text/arrow sizes yourself
  or callouts come out 2.5 mm tall in model units (invisible).
- **Paper names are portrait-first**: `"ISO A3 (297.00 x 420.00 MM)"` + `landscape`, or just `"A3"`
  in `pagesetup`.
- **No structural primitives**: grid bubbles, section markers, bar marks, bar schedules are
  all hand-built. Blocks with attributes are the way to make these reusable.
- DWG output is unverified in AutoCAD per the project's own roadmap. Treat DXF/PDF as the
  deliverables.
