# Supa FEM kit

Supa is a browser app that turns a massing model into a structural model. It has a linear static
solver. This kit is that solver taken out of the app on its own, so it can be replaced with a real one
and dropped back in.

Supa does the rest: building the analysis model from the building, drawing results, colouring
members. The solver only sees plain arrays in and plain arrays out.

```
Supa building ──buildFem()──▶ FemModel ──solveFem()──▶ FemResult ──▶ Supa's UI
                (Supa's side)            (this kit)               (Supa's side)
```

## Getting started

```
npm install
npm run check        # closed-form checks plus the demo building's balance; must all pass
npm run solve        # solves models/demo.model.json and compares against Supa's current result
npm run typecheck
```

`npm run solve -- path/to/x.model.json` solves any model. It writes `x.out.json` next to the model.
If an `x.result.json` exists there, it prints the differences against it.

## What's here

| Path | What it is |
|---|---|
| `src/types.ts` | **The contract.** `FemModel` (input), `FemResult` (output), the load cases. |
| `src/solve.ts` | **The solver.** `solveFem(model, progress?) => FemResult`. Replace this. |
| `src/worker.ts` | Runs the solve in a Web Worker in the browser. Leave as is. |
| `models/demo.model.json` | Supa's demo building as a `FemModel`: 11,665 nodes, 26,795 elements, 76 diaphragms. |
| `models/demo.result.json` | The current solver's result on that model: the baseline. |
| `tools/check.ts` | Known-answer tests: cantilever, simple beam, two columns on a rigid diaphragm. |
| `tools/run.ts` | Solve a model, print size, time, balance and the differences from the baseline. |
| `reference/buildFem.ts` | How Supa builds the `FemModel`. For reading only; it does not compile here. |
| `reference/useFem.ts` | How Supa calls the solver and uses the result. For reading only. |

## The current solver

It is a 3D frame with 6 DOF per node, solved directly:

- **Elements:** 2-node Euler–Bernoulli beams. Bending can be released (pinned) at either end, about
  both axes together, by static condensation. Torsion is never released.
- **Walls:** idealised as wide columns. Each panel is cut into vertical strips no wider than the mesh
  size (10 ft by default). Each strip is a column at its middle with the panel's stiffness: 0.7 Ig
  cracked, strong axis in the wall's plane. Very stiff "arm" elements tie the strip to every node along
  its top and foot. This is the part a real FEM would most likely replace with shells.
- **Diaphragms:** rigid in plane. Each node on a floor slaves ux, uy and rz to the diaphragm centre;
  uz, rx and ry stay free.
- **Supports:** every node at the base level is fully fixed.
- **Solve:** equations ordered floor by floor, a skyline LDLᵀ factored once, then a back-substitution
  for each case. A small spring (1e-3, 1e-2 on rotations) on every free DOF stops singularities.
- **Results:** element end forces from k·u plus the fixed-end forces. Moment and shear are sampled at
  five points along each element.

On the demo building it takes about 6 s in Node. It also reports **1,448 unheld nodes** (more than
1 ft of movement under dead or live load): mechanisms in the model as built today. That may be
Supa's model building rather than the solver, but a better solver should report them too, not hide
them.

## The contract

Units are **kip and foot** everywhere: E in ksf, A in ft², I in ft⁴, distributed loads in k/ft.

**Cases:** `D`, `L`, `Wx`, `Wy`, `Ex`, `Ey`. Every model carries all six.

### FemModel (input)

- `xyz`: node coordinates, flat, `[x0, y0, z0, x1, …]`. z is up.
- `fixed[n]`: node fully fixed (all 6 DOF).
- `els[]`: elements:
  - `i`, `j`: node indices. `E`, `G`, `A`, `Iy`, `Iz`, `J`: section and material.
  - Local x runs i → j. `yh` is a hint for local y: any vector not along x. Local y is `yh` made
    square to x, and local z is x × y.
  - `Iy` resists bending about local y (deflection along local z); `Iz` about local z.
  - `ri` / `rj`: bending released at end i / j.
  - `kind`: `frame` is a member, `wall` is a wall strip, `arm` is a stiff connector with no results.
  - `src`: the Supa member or wall the element belongs to. Many elements can share one `src`:
    members split at joints, walls cut into strips. Arms have `''`.
  - `q[case]`: uniform load along the element, **global** axes, k/ft.
- `dia[]`: rigid diaphragms, each with its centre `x, y, z` and its node indices `nodes`. No node is on
  two diaphragms, and no fixed node is on any.
- `nodeLoads[case]`: forces at nodes, flat, `[fx, fy, fz]` per node. No applied moments.
- `diaLoads[case]`: at each diaphragm centre, flat, `[fx, fy, mz]` per diaphragm.
- `notes`: messages from building the model. Pass them through.

### FemResult (output)

Supa's UI reads these fields. Keep them all, with the same meaning:

| Field | Used for |
|---|---|
| `pieces[src][case]` = `{ N, M, V }` | Colouring members and walls by force, and the top-14 list. N is axial with **tension positive**. M and V are the **largest resultant** moment and shear along the piece. For a wall, sum N and V over its strips side by side and take the largest M; for a member, take the largest of each over its pieces. |
| `dia[k].u[case]` = `[ux, uy]` | Story drift. One entry per input diaphragm, in the same order, carrying its `id` and `z`. |
| `nodeU[case]` | Deflected shape: `[ux, uy, uz]` per node, flat, ft. |
| `maxU[case]` | Scale of the deflected shape, and the summary. |
| `balance[case]` = `{ applied, reactions }` | The equilibrium check shown to the user, `[fx, fy, fz]` each. |
| `loose`, `looseNodes` | The count of unheld nodes, and the nodes themselves (or a sample). Supa traces them back to the systems they belong to. |
| `dofs` | Equation count, shown in the summary. |
| `notes` | Shown as they are. Add your own warnings here. |

`maxAt` is filled in but not used yet.

## Handing it back

Send back `src/`, plus anything you added to `tools/` and `models/`. To drop straight back in:

1. **`solveFem(model, progress?)` keeps its signature.** `progress('Factoring')` and the like are shown
   to the user while it runs.
2. **`src/` runs in a browser Web Worker.** Do not use Node built-ins or the file system there. npm
   packages and WebAssembly are fine if they bundle for the browser; list any you add.
3. **Adding fields is fine; renaming or removing them is not.** New optional output (stresses, modes,
   shell forces) can be added freely and wired into the UI afterwards. If you need **more input**, such
   as walls as shell quads with thickness instead of wide columns, write down what you need in
   `types.ts` as a proposal. Supa's `buildFem` (see `reference/`) changes on our side to fill it.
4. **`npm run check` passes.** Add your own known-answer checks there as you go.
