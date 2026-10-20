"""``python -m skyframe.engine_service`` -- serve | solve | version."""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="skyframe-engine", description="SkyFrame analysis engine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the local engine service on 127.0.0.1")
    s.add_argument("--port", type=int, default=None)
    s.add_argument("--allow-origin", action="append", default=[],
                   help="a web page origin allowed to call the engine (repeatable), e.g. https://supa.example.com")
    v = sub.add_parser("solve", help="solve a Supa FemModel JSON file")
    v.add_argument("model")
    v.add_argument("-o", "--out", help="result file (default: <model>.out.json beside it)")
    sub.add_parser("version", help="print the engine and API versions")
    a = ap.parse_args(argv)

    if a.cmd == "version":
        from .service import _versions
        print(json.dumps(_versions(), indent=1))
        return 0
    if a.cmd == "solve":
        from .supa import solve_fem
        with open(a.model, encoding="utf-8") as fh:
            m = json.load(fh)
        t = time.perf_counter()
        r = solve_fem(m, lambda st: print(f"  {st}", flush=True))
        out = a.out or (a.model[:-11] if a.model.endswith(".model.json") else a.model.rsplit(".json", 1)[0]) + ".out.json"
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(r, fh)
        print(f"{len(m['xyz']) // 3} nodes, {len(m['els'])} elements, {r['dofs']} equations, "
              f"{time.perf_counter() - t:.1f} s -> {out}")
        for n in r["notes"]:
            print(f"note: {n}")
        return 0
    from .service import DEFAULT_PORT, serve
    port = a.port or DEFAULT_PORT
    httpd = serve(port, a.allow_origin)
    print(f"SkyFrame engine listening on http://127.0.0.1:{port} (Ctrl+C to stop)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    mp.freeze_support()
    sys.exit(main())
