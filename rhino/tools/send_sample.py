#!/usr/bin/env python3
"""Post the sample skyframe.exchange/1 payloads to a running SkyFrame server.

These are the SAME payloads the C# unit tests serialize and compare against
(rhino/samples/*.json), so a green run here means the Grasshopper plugin and the
app agree on the wire format (spec §1/§2/§6).

Usage (stdlib only):
    python rhino/tools/send_sample.py                         # status + preview of every sample
    python rhino/tools/send_sample.py --apply --run           # apply (merge) + run analysis
    python rhino/tools/send_sample.py --apply --mode replace --results DEAD
    python rhino/tools/send_sample.py --url 127.0.0.1:53817 --sample frames

Exit code 0 when every request succeeded and every response had the spec's keys.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
QUANTITIES = ("displacements", "member_forces", "reactions", "periods", "drifts", "story")
problems: list[str] = []


def base_url(text: str) -> str:
    t = text.strip()
    if t.isdigit():
        return f"http://127.0.0.1:{t}"
    if not t.startswith(("http://", "https://")):
        t = "http://" + t
    return t.rstrip("/")


def call(url: str, method: str, path: str, body: dict | None = None, timeout: float = 300.0):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(text)
        except ValueError:
            return e.code, {"error": text[:300]}
    except urllib.error.URLError as e:
        sys.exit(f"Cannot reach SkyFrame at {url}: {e.reason}. Is the app running? (check the port it prints)")


def expect(what: str, obj, keys):
    if not isinstance(obj, dict):
        problems.append(f"{what}: expected an object, got {type(obj).__name__}")
        return
    missing = [k for k in keys if k not in obj]
    if missing:
        problems.append(f"{what}: missing key(s) {missing}")


def show(title: str, status: int, payload) -> None:
    text = json.dumps(payload, indent=2)
    if len(text) > 2500:
        text = text[:2500] + "\n  …"
    print(f"\n=== {title} -> HTTP {status}\n{text}")
    if status >= 400:
        problems.append(f"{title}: HTTP {status} {payload.get('error', '') if isinstance(payload, dict) else ''}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8600", help="SkyFrame address or port (default %(default)s)")
    ap.add_argument("--sample", choices=["frames", "shells", "all"], default="all")
    ap.add_argument("--mapping", help="mapping profile name to pass along")
    ap.add_argument("--apply", action="store_true", help="also POST /api/exchange/apply (modifies the open model)")
    ap.add_argument("--mode", choices=["merge", "replace"], default="merge")
    ap.add_argument("--run", action="store_true", help="apply with run:true (runs the analysis)")
    ap.add_argument("--results", metavar="CASE", help="after apply, GET results of CASE for every quantity")
    args = ap.parse_args()
    url = base_url(args.url)

    status, st = call(url, "GET", "/api/exchange/status")
    show("GET /api/exchange/status", status, st)
    expect("status", st, ["app", "version", "model", "sessions", "analysis"])

    names = ["frames", "shells"] if args.sample == "all" else [args.sample]
    for name in names:
        exchange = json.loads((SAMPLES / f"{name}.json").read_text(encoding="utf-8"))
        assert exchange["schema"] == "skyframe.exchange/1"
        body = {"exchange": exchange}
        if args.mapping:
            body["mapping"] = args.mapping

        status, pv = call(url, "POST", "/api/exchange/preview", body)
        show(f"POST /api/exchange/preview [{name}]", status, pv)
        if status < 400:
            expect(f"preview[{name}]", pv, ["summary", "warnings", "objects"])
            expect(f"preview[{name}].summary", pv.get("summary"), ["create", "update", "delete"])
            sent = {o["id"] for o in exchange["objects"]}
            got = {o.get("id") for o in pv.get("objects", [])}
            if got and not got <= sent:
                problems.append(f"preview[{name}]: unknown ids in response {sorted(got - sent)[:5]}")

        if not args.apply:
            continue
        status, ap_ = call(url, "POST", "/api/exchange/apply", dict(body, mode=args.mode, run=bool(args.run)))
        show(f"POST /api/exchange/apply [{name}] mode={args.mode} run={args.run}", status, ap_)
        if status < 400:
            expect(f"apply[{name}]", ap_, ["report", "model_summary"])

        if args.results:
            for what in QUANTITIES:
                q = urllib.parse.urlencode({"session": exchange["session"], "case": args.results, "what": what})
                status, rs = call(url, "GET", f"/api/exchange/results?{q}")
                show(f"GET /api/exchange/results [{name}] {what}", status, rs)
                if status < 400:
                    expect(f"results[{name}/{what}]", rs, ["case", "units", "items"])

    status, st = call(url, "GET", "/api/exchange/status")
    show("GET /api/exchange/status (after)", status, st)

    print("\n" + ("OK — all responses match the spec envelope." if not problems else "PROBLEMS:"))
    for p in problems:
        print("  -", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
