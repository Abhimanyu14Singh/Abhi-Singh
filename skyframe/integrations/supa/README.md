# SkyFrame engine for Supa: test package

This package swaps Supa's linear solver for the SkyFrame engine (OpenSees). Nothing else in Supa
changes: `solveFem(model, progress?) => FemResult` keeps its signature, stays synchronous, and returns
every `FemResult` field with the same meaning. When the engine is not running, Supa's own solver
answers exactly as before.

The test takes about 15 minutes. It runs your own `npm run check` and `npm run solve` from
supa-fem-kit, first against the engine, then without it.

## What is in this package

| Path | What it is |
| --- | --- |
| `src/solve.ts` | The drop-in replacement for the kit's `src/solve.ts` |
| `test/xhr-sync.mjs` | Gives Node a synchronous `XMLHttpRequest`, so the kit's tools call the engine the way a browser worker does. For testing only; the browser needs nothing extra. |
| `scripts/install-into-kit.sh`, `scripts/install-into-kit.bat` | Step 2 below, done for you |
| `expected/demo-solve-output.txt` | What `npm run solve` printed for us on the demo tower |
| `ENGINE_SERVICE.md` | How the engine builds and solves your model, and the full comparison |

## 1. Start the engine

Pick one.

**Windows, no Python needed.** Download `SkyFrame-windows.zip` from
https://github.com/Abhimanyu14Singh/Abhi-Singh/actions/runs/39491613740/artifacts/12296785792
(sign in to GitHub to download). Right-click the ZIP, choose **Extract All**, open the `SkyFrame`
folder, type `cmd` in the address bar and press Enter, then run:

```
SkyFrameCLI.exe --engine-service
```

**Any platform with Python 3.10 to 3.12:**

```
python -m venv skyframe-env
skyframe-env\Scripts\activate          (macOS/Linux: source skyframe-env/bin/activate)
pip install "skyframe @ git+https://github.com/Abhimanyu14Singh/Abhi-Singh@claude/etabs-opensees-ui-rebuild-kt2zwh#subdirectory=skyframe"
python -m skyframe.engine_service serve
```

Either way the window shows `SkyFrame engine listening on http://127.0.0.1:8766`. Leave it open.
To confirm, open http://127.0.0.1:8766/engine/version in a browser; it answers with the engine and
API version.

## 2. Put the drop-in into a copy of supa-fem-kit

Work on a copy of the kit, not your main checkout. Two changes, nothing else:

1. Rename `src/solve.ts` to `src/builtin.ts`. Do not edit inside it: the new `solve.ts` imports it
   as the fallback.
2. Copy this package's `src/solve.ts` into `src/`, and `test/xhr-sync.mjs` into the kit's root.

The scripts do exactly that and refuse to run twice:

```
scripts/install-into-kit.sh  /path/to/supa-fem-kit-copy        (macOS/Linux)
scripts\install-into-kit.bat C:\path\to\supa-fem-kit-copy      (Windows)
```

Then, in the kit copy: `npm install` and `npm run typecheck` (it should print nothing).

## 3. Run your checks against the engine

With the engine running, in the kit copy:

```
npx tsx --import ./xhr-sync.mjs tools/check.ts
npx tsx --import ./xhr-sync.mjs tools/run.ts models/demo.model.json
```

What to expect:

- `check.ts`: every line `ok`, then `all passed`. The demo building's balance lines read `0.000%`
  (the built-in solver gives 0.001% to 0.004%; that is how you can tell the engine answered).
- `run.ts` on the demo tower: progress steps from the engine (`Starting the engine`, `Checking the
  model`, `Assembling`, `Factoring`, `Solving L` ... `Solving Ey`), then about 14 s of solving, then
  the comparison against `demo.result.json`. Ours is in `expected/demo-solve-output.txt`:
  - wind and earthquake cases: 0.00% to 0.01% from your current solver;
  - dead and live: moments within 0.7%, largest movement within 0.9%. The difference comes from
    the small spring your solver puts on every free DOF; the engine puts none.
  - one note on the 1,448 unheld nodes: the engine found no mechanism. They are floor framing on the
    podium levels at 15, 30 and 45 ft (system `ademo101`) whose beam ends have no column or wall
    under them: 72 free member ends, the worst moving 72.7 ft under dead load.

## 4. Run it without the engine

Stop the engine (Ctrl+C in its window) and run `tools/check.ts` again. The first progress line reads
`SkyFrame engine not running: using the built-in solver`, and everything passes as it does today.

## 5. Try it in Supa itself

In the app the solver lives in `app/src/model/fem.ts`, beside `buildFem`, and the worker is
`app/src/model/fem.worker.ts`. Three small files in `app/src/model/`:

1. `solve.ts` from this package.
2. `builtin.ts` containing `export { solveFem } from './fem'` (your current solver, unchanged).
3. `types.ts` containing `export type { FemModel, FemResult } from './fem'` (unless one exists).

Then change the import in `fem.worker.ts` from `./fem` to `./solve`. Start the engine with Supa's
address allowed, or the browser blocks the call:

```
SkyFrameCLI.exe --engine-service --allow-origin https://<supa's address>
python -m skyframe.engine_service serve --allow-origin https://<supa's address>
```

`http://localhost:*` pages are always allowed, so a local dev server needs no flag. Chrome, Edge and
Firefox allow an `https` page to call `http://127.0.0.1`; Safari does not, and Supa then falls back
to its built-in solver.

## Behaviour to know

| Situation | What happens |
| --- | --- |
| Engine running | Progress steps come from the engine; the result has one extra field, `engine` (solver, version, seconds, stability report). `lastSolver` is `'skyframe'`. |
| Engine not running | Supa's own solver, with a progress line saying so. `lastSolver` is `'builtin'`. |
| Engine finds the model malformed (bad node index, wrong array length) | Throws `EngineRefused` with the reason, and does not fall back: that would hide the fault. |
| Part of the model not connected to the ground, or a node no member holds | Solved anyway; those nodes are held, listed in `looseNodes` and explained in `notes`. Load on them has no path to the ground, so the balance shows it as missing reaction. |

## What to send back

- The output of the two commands in step 3.
- Anything that differs from `expected/demo-solve-output.txt`, and any model where the engine and
  your solver disagree by more than 1%.
- For the next step (the engine meshing walls as shells and loading your objects itself): your
  `rules`, `seismic` and `wind` modules, a sample of the building data `buildFem` reads, and who owns
  the object ids (`src`).
