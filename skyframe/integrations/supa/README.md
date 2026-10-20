# Supa drop-in: solveFem on the SkyFrame engine

`src/solve.ts` replaces `supa-fem-kit/src/solve.ts`. Same function, same signature, still
synchronous: `solveFem(model, progress?) => FemResult`. When the SkyFrame engine runs on the user's
machine it does the solve (OpenSees); when it does not, Supa's own solver answers as before.

## Install in Supa

1. Rename the current `src/solve.ts` to `src/builtin.ts`. Change nothing inside it.
2. Copy `src/solve.ts` from here into `src/`.
3. `src/worker.ts`, `src/types.ts` and `buildFem` stay as they are.

The engine is started on the user's machine with Supa's address allowed:

```
python -m skyframe.engine_service serve --allow-origin https://<supa's address>
```

Optional: set `self.SKYFRAME_ENGINE_URL` in the worker before the first solve to use another port.

## Behaviour

| Situation | What happens |
| --- | --- |
| Engine running | Steps arrive through `progress` ("Checking the model", "Assembling", "Factoring", "Solving L" ...); the result carries an extra `engine` field. `lastSolver` is `'skyframe'`. |
| Engine not running, or another API major version | `progress('SkyFrame engine not running: using the built-in solver')`, then Supa's solver. `lastSolver` is `'builtin'`. |
| Engine refuses the model (malformed) | Throws `EngineRefused` with the reason. Not retried with the built-in solver, which would hide the fault. |
| Engine fails during the solve | Throws with the engine's message. |
| Safari (blocks `http://127.0.0.1` from an `https` page) | Falls back to the built-in solver. |

## Tested with the kit's own tools

`test/xhr-sync.mjs` gives Node a synchronous `XMLHttpRequest`, so the kit's unmodified tools run the
drop-in the way a browser worker would:

```
npx tsx --import ./test/xhr-sync.mjs tools/check.ts          # all checks pass, engine on or off
npx tsx --import ./test/xhr-sync.mjs tools/run.ts models/demo.model.json
```

Results on the demo tower are in `skyframe/docs/ENGINE_SERVICE.md`.
