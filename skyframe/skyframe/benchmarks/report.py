"""Scorecard JSON and the self-contained HTML leadership report."""

from __future__ import annotations

import datetime as _dt
import json
import platform
from html import escape
from pathlib import Path
from typing import Dict, List

from ._core import CSI_DOC, LIMITS, CaseResult, fmt, num

TIER_LABEL = {"1": "Tier 1", "2": "Tier 2", "gap": "Gap case",
              "blocked": "Blocked"}


def totals(results: List[CaseResult]) -> Dict[str, int]:
    t = {"exact": 0, "acceptable": 0, "unacceptable": 0, "n/a": 0,
         "values": 0, "cases_run": 0, "cases_blocked": 0, "cases_error": 0}
    for r in results:
        if r.status == "blocked":
            t["cases_blocked"] += 1
            continue
        if r.status == "error":
            t["cases_error"] += 1
            continue
        t["cases_run"] += 1
        for k, v in r.counts().items():
            t[k] = t.get(k, 0) + v
        t["values"] += len(r.rows)
    return t


def scorecard(results: List[CaseResult]) -> dict:
    from skyframe import __version__ as ver
    return {"generated": _dt.datetime.now(_dt.timezone.utc).isoformat(
                timespec="seconds"),
            "skyframe_version": ver,
            "platform": platform.platform(),
            "reference_document": CSI_DOC,
            "acceptance": {"exact": "no difference within output precision",
                           "acceptable_pct": LIMITS},
            "totals": totals(results),
            "cases": [r.to_dict() for r in results]}


def write_json(results: List[CaseResult], path: Path) -> dict:
    sc = scorecard(results)
    Path(path).write_text(json.dumps(sc, indent=1))
    return sc


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #
CSS = """
/* Layout: one reading column; scorecard first, then one section per case. */
:root{
 --bg:#f6f7f9;--paper:#ffffff;--ink:#1b2430;--muted:#5b6675;--rule:#d9dee5;
 --head:#eef1f5;--accent:#1f5f8b;
 --exact:#1d7a46;--exact-bg:#e3f3ea;--ok:#8a6400;--ok-bg:#fbf1d6;
 --bad:#b3261e;--bad-bg:#fbe4e2;--na:#6b7280;--na-bg:#eceff3;
 --font-body:"Source Sans 3","Segoe UI",system-ui,-apple-system,sans-serif;
 --font-head:"Source Serif 4",Georgia,"Times New Roman",serif;
 --font-num:"JetBrains Mono",ui-monospace,"Cascadia Mono",Consolas,monospace;
 color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
 --bg:#11161d;--paper:#171e27;--ink:#e3e8ef;--muted:#9aa6b5;--rule:#2b3542;
 --head:#1d2631;--accent:#7cb6e0;--exact:#6fd39a;--exact-bg:#16301f;
 --ok:#e6c15a;--ok-bg:#33290f;--bad:#ff8a80;--bad-bg:#3a1714;
 --na:#9aa6b5;--na-bg:#222b36;color-scheme:dark}}
:root[data-theme="dark"]{
 --bg:#11161d;--paper:#171e27;--ink:#e3e8ef;--muted:#9aa6b5;--rule:#2b3542;
 --head:#1d2631;--accent:#7cb6e0;--exact:#6fd39a;--exact-bg:#16301f;
 --ok:#e6c15a;--ok-bg:#33290f;--bad:#ff8a80;--bad-bg:#3a1714;
 --na:#9aa6b5;--na-bg:#222b36;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5
 var(--font-body)}
.wrap{max-width:1180px;margin:0 auto;padding-inline:16px;
 padding-block:28px 60px;display:flex;flex-direction:column;gap:26px}
h1,h2,h3{font-family:var(--font-head);text-wrap:balance;margin:0}
h1{font-size:1.9rem;line-height:1.2}
h2{font-size:1.35rem;border-bottom:1px solid var(--rule);padding-bottom:6px}
h3{font-size:1.05rem}
p{margin:0;max-width:72ch}
.eyebrow{text-transform:uppercase;letter-spacing:.08em;font-size:.75rem;
 color:var(--muted);font-weight:600}
.lede{color:var(--muted)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
 gap:12px}
.stat{background:var(--paper);border:1px solid var(--rule);border-radius:6px;
 padding:12px 14px;display:flex;flex-direction:column;gap:2px}
.stat b{font:600 1.6rem/1.1 var(--font-num)}
.stat.exact b{color:var(--exact)}.stat.ok b{color:var(--ok)}
.stat.bad b{color:var(--bad)}
.tbl{overflow-x:auto;background:var(--paper);border:1px solid var(--rule);
 border-radius:6px}
table{border-collapse:collapse;width:100%;font-size:.86rem}
th,td{padding:5px 8px;border-bottom:1px solid var(--rule);text-align:left;
 vertical-align:top}
th{background:var(--head);font-weight:600;white-space:nowrap}
td.n{font-family:var(--font-num);font-variant-numeric:tabular-nums;
 text-align:right;white-space:nowrap}
tr.grp td{background:var(--head);font-weight:600;color:var(--muted)}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;
 font-size:.75rem;font-weight:600;white-space:nowrap}
.r-exact{background:var(--exact-bg);color:var(--exact)}
.r-acceptable{background:var(--ok-bg);color:var(--ok)}
.r-unacceptable{background:var(--bad-bg);color:var(--bad)}
.r-na,.r-blocked{background:var(--na-bg);color:var(--na)}
.note{color:var(--muted);font-size:.78rem}
section.case{display:flex;flex-direction:column;gap:12px;
 background:var(--paper);border:1px solid var(--rule);border-radius:6px;
 padding:18px}
.cols{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.3fr);
 gap:18px}
@media (max-width:760px){.cols{grid-template-columns:1fr}}
.sk{width:100%;max-width:420px;height:auto;color:var(--ink)}
.sk line,.sk path{stroke:currentColor;stroke-width:1.6;fill:none}
.sk line.b{stroke:var(--accent);stroke-width:2.4}
.sk line.w{stroke:var(--accent);stroke-width:4}
.sk line.mesh{stroke:var(--rule);stroke-width:1}
.sk line.ol{stroke:var(--muted);stroke-dasharray:4 3}
.sk line.ld{stroke:var(--bad)}.sk path.ldh{fill:var(--bad);stroke:none}
.sk path.sup{stroke:var(--muted)}
.sk polygon.wall{fill:var(--head);stroke:var(--accent);stroke-width:1.2}
.sk circle{fill:var(--ink)}.sk circle.pin{fill:var(--paper);
 stroke:var(--ink);stroke-width:1.5}
.sk circle.cm{fill:var(--bad)}.sk circle.cr{fill:var(--exact)}
.sk circle.col{fill:var(--accent)}
.sk text{fill:var(--muted);font:11px var(--font-body)}
dl{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:3px 12px;
 margin:0;font-size:.88rem}
dt{color:var(--muted)}dd{margin:0}
ul{margin:0;padding-left:20px;display:flex;flex-direction:column;gap:3px}
.box{border:1px solid var(--rule);border-left:4px solid var(--ok);
 background:var(--paper);border-radius:4px;padding:14px 16px;display:flex;
 flex-direction:column;gap:8px}
.box.legal{border-left-color:var(--accent)}
a{color:var(--accent)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
@media print{body{background:#fff;color:#000;font-size:11px}
 .wrap{max-width:none;padding:0}section.case{break-inside:avoid-page;
 border:none;padding:0}.scorecard{break-after:page}}
"""


def _pill(rating: str) -> str:
    cls = {"n/a": "na"}.get(rating, rating)
    return f'<span class="pill r-{cls}">{escape(rating)}</span>'


def _pct(v) -> str:
    if v is None:
        return "-"
    return "0.000" if abs(v) < 5e-4 else f"{v:+.3f}"


def _val(s) -> str:
    if s is None:
        return "-"
    v, _ = num(s)
    return escape(str(s)) if isinstance(s, str) and "e" not in s.lower() \
        else fmt(v)


def _rows(rows, case_col: bool, case_id: str = "") -> str:
    out = []
    grp = None
    for r in rows:
        if not case_col and r.group and r.group != grp:
            grp = r.group
            out.append(f'<tr class="grp"><td colspan="9">{escape(grp)}'
                       "</td></tr>")
        lead = (f"<td>{escape(case_id)}</td>" if case_col else "")
        q = escape(r.quantity) + (f" <span class='note'>"
                                  f"{escape(r.location)}</span>"
                                  if r.location else "")
        note = (f"<br><span class='note'>{escape(r.note)}</span>"
                if (r.note and not case_col) else "")
        out.append(
            f"<tr>{lead}<td>{q}{note}</td><td>{escape(r.units)}</td>"
            f"<td class='n'>{_val(r.independent)}</td>"
            f"<td class='n'>{_val(r.etabs)}</td>"
            f"<td class='n'>{fmt(r.skyframe)}</td>"
            f"<td class='n'>{_pct(r.pct_ind)}</td>"
            f"<td class='n'>{_pct(r.pct_etabs)}</td>"
            f"<td>{_pill(r.rating)}</td></tr>")
    return "".join(out)


def _head(case_col: bool) -> str:
    return ("<tr>" + ("<th>Case</th>" if case_col else "")
            + "<th>Quantity</th><th>Units</th><th>Independent</th>"
              "<th>CSI ETABS</th><th>SkyFrame</th><th>% vs indep.</th>"
              "<th>% vs ETABS</th><th>CSI rating</th></tr>")


def _case_section(r: CaseResult) -> str:
    c = r.counts()
    badge = (_pill("blocked") if r.status == "blocked" else
             _pill("error") if r.status == "error" else
             " ".join(f"{_pill(k)} {v}" for k, v in c.items()
                      if v and k != "n/a"))
    parts = [f'<section class="case" id="{escape(r.id)}">',
             f'<div class="eyebrow">{escape(TIER_LABEL.get(r.tier, r.tier))}'
             f" &middot; {escape(r.id)}"
             + (f" &middot; {r.seconds:.1f} s" if r.seconds else "")
             + "</div>",
             f"<h2>{escape(r.title)}</h2>",
             f'<p class="lede">{escape(r.tests)}</p>',
             f"<p><b>Independent reference:</b> {escape(r.source)}</p>",
             f"<div>{badge}</div>"]
    left = []
    if r.svg:
        left.append(r.svg)
    if r.inputs:
        left.append("<h3>Inputs</h3><dl>" + "".join(
            f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>"
            for k, v in r.inputs) + "</dl>")
    if r.mesh:
        left.append(f"<p class='note'>Mesh: {escape(r.mesh)}</p>")
    right = []
    if r.assumptions:
        right.append("<h3>Assumptions and derived inputs</h3><ul>" + "".join(
            f"<li>{escape(a)}</li>" for a in r.assumptions) + "</ul>")
    if r.blocked:
        right.append("<h3>Blocked: missing from the PDF</h3><ul>" + "".join(
            f"<li>{escape(a)}</li>" for a in r.blocked) + "</ul>")
    if r.error:
        right.append(f"<pre>{escape(r.error)}</pre>")
    if left or right:
        parts.append('<div class="cols"><div style="display:flex;'
                     'flex-direction:column;gap:12px;min-width:0">'
                     + "".join(left) + '</div><div style="display:flex;'
                     'flex-direction:column;gap:12px;min-width:0">'
                     + "".join(right) + "</div></div>")
    if r.rows:
        parts.append('<div class="tbl"><table>' + _head(False)
                     + _rows(r.rows, False) + "</table></div>")
    parts.append("</section>")
    return "".join(parts)


def write_html(results: List[CaseResult], path: Path) -> None:
    sc = scorecard(results)
    t = sc["totals"]
    run = [r for r in results if r.status not in ("blocked", "error")]
    worst = sorted((row for r in run for row in r.rows
                    if row.pct_ind is not None),
                   key=lambda x: -abs(x.pct_ind))[:5]
    diffs = [d for r in results for d in r.differences]
    score_rows = "".join(
        _rows(r.rows, True, r.id) for r in run)
    blocked = [r for r in results if r.status == "blocked"]
    partial = [r for r in results if r.blocked and r.status != "blocked"]
    html = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,"
        "initial-scale=1,viewport-fit=cover'>",
        "<title>SkyFrame Benchmark Scorecard</title>",
        f"<style>{CSS}</style></head><body><div class='wrap'>",
        "<header style='display:flex;flex-direction:column;gap:8px'>",
        "<div class='eyebrow'>Leadership review &middot; analysis "
        "verification</div>",
        "<h1>SkyFrame against the CSI ETABS verification examples</h1>",
        f"<p class='lede'>SkyFrame {escape(sc['skyframe_version'])} rebuilt "
        "from the published example data and run on its own engine. Every "
        "value is compared with the independent reference CSI uses and "
        "with CSI's own ETABS result, and rated with CSI's acceptance "
        f"criteria. Generated {escape(sc['generated'])}.</p></header>",
        "<div class='scorecard' style='display:flex;flex-direction:column;"
        "gap:16px'>",
        "<div class='stats'>",
        f"<div class='stat'><span class='eyebrow'>Values compared</span>"
        f"<b>{t['values']}</b><span class='note'>{t['cases_run']} cases run"
        "</span></div>",
        f"<div class='stat exact'><span class='eyebrow'>Exact</span>"
        f"<b>{t['exact']}</b><span class='note'>within output precision"
        "</span></div>",
        f"<div class='stat ok'><span class='eyebrow'>Acceptable</span>"
        f"<b>{t['acceptable']}</b><span class='note'>&le; 5 % / &le; 10 %"
        "</span></div>",
        f"<div class='stat bad'><span class='eyebrow'>Unacceptable</span>"
        f"<b>{t['unacceptable']}</b><span class='note'>beyond the limits"
        "</span></div>",
        f"<div class='stat'><span class='eyebrow'>Blocked cases</span>"
        f"<b>{t['cases_blocked']}</b><span class='note'>inputs not "
        "published</span></div>",
        "</div>",
        "<h2>Scorecard</h2>",
        "<div class='tbl'><table>" + _head(True) + score_rows
        + "</table></div>",
    ]
    if worst:
        html.append("<p class='note'>Largest differences from the "
                    "independent reference: " + "; ".join(
                        f"{escape(w.quantity)} {escape(w.location)} "
                        f"{w.pct_ind:+.2f} %" for w in worst) + ".</p>")
    html.append("</div>")
    html.append("<div class='box'><h2 style='border:0;padding:0'>Known "
                "differences and assumptions</h2><ul>" + "".join(
                    f"<li>{escape(d)}</li>" for d in diffs)
                + "<li>Live-load reduction is off in every case; SkyFrame, "
                  "like ETABS, never reduces live load in analysis output "
                  "(reduced forces are separate tables).</li>"
                + "".join(f"<li>{escape(r.id)}: partly blocked -- "
                          f"{escape(b)}</li>" for r in partial
                          for b in r.blocked)
                + "</ul></div>")
    html.append(
        "<section class='case'><h2>Methodology</h2>"
        "<p>Each case module builds a SkyFrame model from the inputs "
        "printed in the CSI example (geometry, sections, materials, masses, "
        "loads); values read from figures or derived by hand are marked "
        "<i>[derived]</i>, inputs that are not printed and had to be chosen "
        "are marked <i>[assumed]</i>. The real SkyFrame engine (OpenSees "
        "back end) runs every case; nothing is fitted to the answer.</p>"
        "<div class='tbl'><table><tr><th>CSI rating</th><th>Forces, "
        "moments, displacements</th><th>Internal forces, stresses</th>"
        "</tr><tr><td>" + _pill("exact") + "</td><td colspan='2'>No "
        "difference within the output precision of the published value "
        "(SkyFrame rounded to the printed digits equals it)</td></tr><tr>"
        "<td>" + _pill("acceptable") + "</td><td>&le; 5 %</td><td>&le; "
        "10 %</td></tr><tr><td>" + _pill("unacceptable") + "</td><td>"
        "&gt; 5 %</td><td>&gt; 10 %</td></tr></table></div>"
        "<p>Percent difference = 100 &times; (value / independent &minus; "
        "1). Where an example has several meshes only the finest must meet "
        "the limits (CSI). Periods, eigenvalues, mode shapes, story shears "
        "and displacements are rated as global quantities; member and "
        "station forces as internal forces. The ETABS column is CSI's own "
        "published result, shown for comparison; the rating is always "
        "against the independent reference.</p>"
        f"<p class='note'>Reference document: {escape(CSI_DOC)}. Run: "
        "<code>python -m skyframe.benchmarks --report &lt;dir&gt;</code>. "
        "Platform: " + escape(sc["platform"]) + ".</p></section>")
    html.append(
        "<div class='box legal'><h2 style='border:0;padding:0'>Licence "
        "note</h2><p>The models are re-built in SkyFrame from data "
        "published in the CSI document cited above; this report quotes "
        "only the numbers compared against. No CSI PDF, figure, .EDB model "
        "or record file is copied or redistributed; the sketches are "
        "redrawn from the published dimensions. Obtain sign-off before "
        "showing these comparisons outside the company.</p></div>")
    for r in results:
        html.append(_case_section(r))
    if blocked:
        html.append("<p class='note'>Blocked examples need the ETABS "
                    "installation files (Example .EDB models and the El "
                    "Centro / Loma Prieta records).</p>")
    html.append("</div></body></html>")
    Path(path).write_text("".join(html))
