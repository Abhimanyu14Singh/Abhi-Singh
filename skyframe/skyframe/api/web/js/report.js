/* SkyFrame report generator (v0.4) — a self-contained, print-friendly
   LIGHT-theme report opened in a new tab (window.open + document.write).
   Inlines the story-drift charts and RS spectrum charts as SVG markup by
   temporarily switching charts.js to its light theme. No frameworks. */

import { renderStoryCharts, spectrumChart, pushoverChart, setChartTheme } from "./charts.js";

const esc = s => String(s).replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (v, d = 1) => (v == null || !isFinite(v)) ? "—" :
  v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const sci = v => (v == null || !isFinite(v)) ? "—" : Number(v).toExponential(2);

/* ------------------------------------------------ small builders */
function table(headers, rows, cls = "") {
  const th = headers.map(h =>
    `<th class="${h.txt ? "txt" : ""}">${esc(h.label !== undefined ? h.label : h)}</th>`).join("");
  const body = rows.map(r =>
    `<tr>${r.map(c => typeof c === "object" && c !== null
      ? `<td class="${c.txt ? "txt" : ""}${c.dim ? " dim" : ""}">${c.html !== undefined ? c.html : esc(c.v)}</td>`
      : `<td>${esc(c)}</td>`).join("")}</tr>`).join("");
  return `<table class="rt ${cls}"><thead><tr>${th}</tr></thead><tbody>${body}</tbody></table>`;
}
const T = v => ({ v, txt: true });          // left-aligned text cell
const D = v => ({ v, txt: true, dim: true });

function section(title, inner, note = "") {
  return `<section class="rsec">
    <h2>${esc(title)}</h2>
    ${note ? `<p class="note">${note}</p>` : ""}
    ${inner}
  </section>`;
}

/* ------------------------------------------------ loads helpers */
function memberLenOf(model) {
  const len = {};
  for (const m of model.members)
    len[m.uid] = m.length || Math.hypot(
      m.pj[0] - m.pi[0], m.pj[1] - m.pi[1], m.pj[2] - m.pi[2]);
  return len;
}
function polyArea(corners) {
  // planar quad area via cross products (3D-safe)
  const [a, b, c, d] = corners;
  const v = (p, q) => [q[0] - p[0], q[1] - p[1], q[2] - p[2]];
  const cr = (u, w) => [u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0]];
  const n1 = cr(v(a, b), v(a, c)), n2 = cr(v(a, c), v(a, d));
  return (Math.hypot(...n1) + Math.hypot(...n2)) / 2;
}
function patternTotals(model, p) {
  const len = memberLenOf(model);
  let mem = 0;
  for (const l of (p.member_loads || [])) {
    const L = len[l.member_uid] || 0;
    const kind = l.kind || "udl";
    if (kind === "point") mem += Math.abs(l.w);
    else if (kind === "trapezoid")
      mem += Math.abs((l.w + (l.w2 || 0)) / 2) * ((l.b ?? 1) - (l.a ?? 0)) * L;
    else mem += Math.abs(l.w) * L;
  }
  let area = 0;
  const byUid = {};
  for (const sh of (model.shells || [])) byUid[sh.uid] = sh;
  for (const l of (p.area_loads || [])) {
    const sh = byUid[l.region_uid];
    if (sh) area += Math.abs(l.q) * polyArea(sh.corners);
  }
  let fx = 0, fy = 0;
  for (const f of (p.story_forces || [])) { fx += f.fx || 0; fy += f.fy || 0; }
  return { mem, area, fx, fy };
}

const factorStr = dict => Object.entries(dict || {})
  .map(([k, f]) => `${f} × ${k}`).join("  +  ") || "—";

/* ------------------------------------------------ report body */
export function buildReportHtml(model, results, opts = {}) {
  const r = results;
  const caseName = opts.caseName || null;
  const now = new Date();

  /* ---- charts (light theme) rendered off-DOM, serialized to markup */
  setChartTheme("light");
  let chartsHtml = "", spectraHtml = "", pushoverHtml = "";
  try {
    const allCd = name =>
      (r.cases && r.cases[name]) || (r.combos && r.combos[name]) || null;
    let chartCase = caseName && allCd(caseName) ? caseName : null;
    if (!chartCase) {
      const names = [...Object.keys(r.cases || {}), ...Object.keys(r.combos || {})];
      chartCase = names.find(n => {
        const cd = allCd(n);
        return cd && cd.story && Object.values(cd.story).some(
          s => Math.abs(s.drift_x || 0) + Math.abs(s.drift_y || 0) > 1e-9);
      }) || names[0];
    }
    const cd = allCd(chartCase);
    if (cd && cd.story) {
      const holder = document.createElement("div");
      renderStoryCharts(holder, r, cd, opts.driftLimitPct || 0.5);
      chartsHtml = `<p class="note">Case <b>${esc(chartCase)}</b> · drift limit ${fmt(opts.driftLimitPct || 0.5, 2)} %</p>
        <div class="chart-grid">${holder.innerHTML}</div>`;
    }
    const specs = Object.entries(model.rs_cases || {});
    if (specs.length) {
      spectraHtml = `<div class="chart-grid">` + specs.map(([n, rc]) => {
        const svg = spectrumChart(rc.spectrum, { width: 340, height: 180 });
        return `<div class="chart-card"><div class="chart-title">${esc(n)}
          <span class="unit">Sa g vs T s · ${esc(rc.direction)} · ${esc(rc.combo_method)}</span></div>${svg.outerHTML}</div>`;
      }).join("") + `</div>`;
    }
    /* ---- v0.5: pushover — capacity curve + hinge rotation table per case */
    const Hbld = model.stories.length ? model.stories[model.stories.length - 1].elevation : 1;
    for (const [pn, po] of Object.entries(r.pushover || {})) {
      const pc = (model.pushover_cases || {})[pn] || {};
      const card = pushoverChart(po.roof_disp || [], po.base_shear || [],
        { title: esc(pn), H: Hbld });
      const memBy = {};
      for (const mm of model.members || []) memBy[mm.uid] = mm;
      const hinges = Object.entries(po.hinge_rotations || {})
        .sort((a, b) => b[1] - a[1])
        .slice(0, 15)
        .map(([uid, rot]) => {
          const mm = memBy[uid] || {};
          return [T(uid), D(mm.kind || "—"), D(mm.story || "—"),
            fmt(rot * 1000, 2),
            (pc.My && pc.My[uid] != null) ? fmt(pc.My[uid], 0) : D("default").v];
        });
      const hingeTable = hinges.length
        ? table([{ label: "Member", txt: true }, { label: "Kind", txt: true },
                 { label: "Story", txt: true }, "θ (mrad)", "My (kN·m)"], hinges)
        : `<p class="note">No hinge rotations reported.</p>`;
      const warn = (po.warnings || []).length
        ? `<p class="note po-warn">⚠ ${(po.warnings || []).map(esc).join(" · ")}</p>` : "";
      pushoverHtml += `<div class="case-block">
        <h3>${esc(pn)} <span class="tag">pushover · ${esc(pc.direction || "X")} ·
          target ${fmt((pc.target_drift || 0.02) * 100, 1)} % drift</span></h3>
        ${warn}
        <div class="chart-grid"><div style="flex:1 1 480px;max-width:640px" class="chart-card">
          ${card.innerHTML}</div></div>
        ${hingeTable}</div>`;
    }
  } finally {
    setChartTheme("dark");
  }

  /* ---- model summary */
  const kinds = {};
  for (const m of model.members) kinds[m.kind] = (kinds[m.kind] || 0) + 1;
  const kindStr = Object.entries(kinds).map(([k, n]) => `${n} ${k}${n > 1 ? "s" : ""}`).join(" · ") || "—";
  // v0.5: opening + link counts surface in the summary when present
  const nOpenings = (model.shells || [])
    .reduce((a, s) => a + ((s.openings || []).length), 0);
  const nLinks = (model.links || []).length;
  const shellStr = String((model.shells || []).length) +
    (nOpenings ? `  (${nOpenings} opening${nOpenings > 1 ? "s" : ""})` : "") +
    (nLinks ? ` · ${nLinks} link${nLinks > 1 ? "s" : ""}` : "");
  const summaryTable = table(
    [{ label: "Stories" }, { label: "Members" }, { label: "Shell regions" },
     { label: "Footprint" }, { label: "Height" }, { label: "Base fixity" }],
    [[
      String(model.stories.length),
      `${model.members.length}  (${kindStr})`,
      shellStr,
      model.grid ? `${fmt(model.grid.x_lines[model.grid.x_lines.length - 1] - model.grid.x_lines[0], 1)} × ${fmt(model.grid.y_lines[model.grid.y_lines.length - 1] - model.grid.y_lines[0], 1)} m` : "—",
      `${fmt(model.stories.length ? model.stories[model.stories.length - 1].elevation : 0, 1)} m`,
      model.base_fixity || "fixed",
    ]]);

  const matTable = table(
    [{ label: "Material", txt: true }, "E (kPa)", "ν", "γ (kN/m³)"],
    Object.values(model.materials || {}).map(m =>
      [T(m.name), fmt(m.E, 0), fmt(m.nu, 2), fmt(m.unit_weight, 0)]));

  const secTable = table(
    [{ label: "Frame section", txt: true }, { label: "Geometry", txt: true },
     { label: "Material", txt: true }, "mod A", "mod I33", "mod I22", "mod J"],
    Object.values(model.sections || {}).map(s => [
      T(s.name),
      D(s.b > 0 && s.h > 0 && s.shape !== "W"
        ? `${fmt(s.b, 2)} × ${fmt(s.h, 2)} m`
        : `A ${sci(s.A)} m² · I33 ${sci(s.I33)} m⁴`),
      D(s.material),
      fmt(s.mod_A ?? 1, 2), fmt(s.mod_I33 ?? 1, 2),
      fmt(s.mod_I22 ?? 1, 2), fmt(s.mod_J ?? 1, 2),
    ]));

  const shellSecTable = table(
    [{ label: "Shell section", txt: true }, "t (m)", { label: "Material", txt: true }, "mod"],
    Object.values(model.shell_sections || {}).map(s =>
      [T(s.name), fmt(s.thickness, 3), D(s.material), fmt(s.mod ?? 1, 2)]));

  /* ---- loads summary */
  const patTable = table(
    [{ label: "Pattern", txt: true }, { label: "Kind", txt: true },
     "Member loads (kN)", "Area loads (kN)", "Story ΣFx (kN)", "Story ΣFy (kN)"],
    Object.values(model.patterns || {}).map(p => {
      const t = patternTotals(model, p);
      return [T(p.name), D(p.kind || "—"), fmt(t.mem, 1), fmt(t.area, 1), fmt(t.fx, 1), fmt(t.fy, 1)];
    }));

  const massStr = Object.entries(model.mass_source || {})
    .map(([k, f]) => `${f} × ${k}`).join("  +  ") || "1.0 × DEAD";

  const caseTable = table(
    [{ label: "Static case", txt: true }, { label: "Patterns", txt: true }, { label: "P-Δ", txt: true }],
    Object.values(model.cases || {}).map(c =>
      [T(c.name), D(factorStr(c.patterns)), T(c.pdelta ? "yes" : "—")]));

  const comboTable = table(
    [{ label: "Combination", txt: true }, { label: "Type", txt: true }, { label: "Cases", txt: true }],
    Object.values(model.combos || {}).map(cb =>
      [T(cb.name), T(cb.combo_type === "envelope" ? "envelope (max/min)" : "add"),
       D(factorStr(cb.cases))]));

  const rsTable = table(
    [{ label: "RS case", txt: true }, { label: "Dir", txt: true }, { label: "Combo", txt: true },
     "Damping", "Scale", "Points"],
    Object.values(model.rs_cases || {}).map(rc =>
      [T(rc.name), T(rc.direction), T(rc.combo_method), fmt(rc.damping, 3),
       fmt(rc.scale, 2), String((rc.spectrum || []).length)]));

  const thTable = table(
    [{ label: "TH case", txt: true }, { label: "Dir", txt: true }, "dt (s)",
     "Points", "Duration (s)", "Damping", "Scale"],
    Object.values(model.th_cases || {}).map(tc =>
      [T(tc.name), T(tc.direction), fmt(tc.dt, 3), String((tc.accel || []).length),
       fmt(((tc.accel || []).length - 1) * (tc.dt || 0), 1), fmt(tc.damping, 3), fmt(tc.scale, 2)]));

  /* ---- modal */
  let modalHtml = `<p class="note">No modal results.</p>`;
  if (r.modal && r.modal.periods && r.modal.periods.length) {
    let cx = 0, cy = 0;
    modalHtml = table(
      ["Mode", "T (s)", "f (Hz)", "UX %", "UY %", "RZ %", "Γx", "Γy", "Σ UX %", "Σ UY %"],
      r.modal.participation.map((p, i) => {
        cx += p.ux || 0; cy += p.uy || 0;
        return [String(p.mode), fmt(r.modal.periods[i], 3), fmt(r.modal.frequencies[i], 2),
          fmt((p.ux || 0) * 100, 1), fmt((p.uy || 0) * 100, 1), fmt((p.rz || 0) * 100, 1),
          fmt(p.gamma_x ?? 0, 2), fmt(p.gamma_y ?? 0, 2),
          fmt(cx * 100, 1), fmt(cy * 100, 1)];
      }));
  }

  /* ---- per-case story tables + base reactions */
  const caseEntries = [
    ...Object.entries(r.cases || {}).map(([n, cd]) => [n, cd, "case"]),
    ...Object.entries(r.combos || {}).map(([n, cd]) => {
      const env = (model.combos && model.combos[n] && model.combos[n].combo_type === "envelope");
      return [n, cd, env ? "envelope combo" : "combo"];
    }),
    ...Object.entries(r.rs_cases || {}).map(([n, cd]) => [`RS: ${n}`, cd, "response spectrum ±"]),
  ];

  // v0.8 — center of mass / rigidity per story (case-independent story_props,
  // else on the case's story dict). Present only when diaphragms exist.
  const cmcrOf = (cd, s) => {
    const sp = (r.story_props && r.story_props[s]) || (cd.story && cd.story[s]) || {};
    return ["cm_x", "cm_y", "cr_x", "cr_y"].some(k => isFinite(sp[k])) ? sp : null;
  };
  const hasCmCr = r.story_order.some(s => cmcrOf({}, s));
  const storyTableFor = cd => table(
    [{ label: "Story", txt: true }, "Elev (m)", "ux (mm)", "uy (mm)",
     "drift ‰ x", "drift ‰ y", "Vx (kN)", "Vy (kN)",
     ...(hasCmCr ? ["CM x (m)", "CM y (m)", "CR x (m)", "CR y (m)", "e (m)"] : [])],
    [...r.story_order].reverse().map(s => {
      const st = (cd.story && cd.story[s]) || {};
      const row = [T(s), fmt(r.story_elev[s], 1),
        fmt((st.ux || 0) * 1000, 2), fmt((st.uy || 0) * 1000, 2),
        fmt((st.drift_x || 0) * 1000, 3), fmt((st.drift_y || 0) * 1000, 3),
        fmt(st.shear_x || 0, 1), fmt(st.shear_y || 0, 1)];
      if (hasCmCr) {
        const cc = cmcrOf(cd, s);
        const ecc = cc ? Math.hypot((cc.cm_x ?? 0) - (cc.cr_x ?? 0), (cc.cm_y ?? 0) - (cc.cr_y ?? 0)) : null;
        row.push(fmt(cc && cc.cm_x, 2), fmt(cc && cc.cm_y, 2),
          fmt(cc && cc.cr_x, 2), fmt(cc && cc.cr_y, 2), fmt(ecc, 3));
      }
      return row;
    }));

  const storyBlocks = caseEntries.map(([name, cd, kind]) => {
    let inner = storyTableFor(cd);
    if (cd.min) inner += `<p class="minihead">min envelope</p>` + storyTableFor(cd.min);
    return `<div class="case-block"><h3>${esc(name)} <span class="tag">${esc(kind)}</span>${cd.min ? ` <span class="tag">max above</span>` : ""}</h3>${inner}</div>`;
  }).join("");

  /* ---- v0.9: story stiffness + irregularity diagnostics (ASCE 7 §12.3) */
  let diagBlocks = "";
  const ssAll = r.story_stiffness || {}, irAll = r.irregularity || {};
  const diagCases = Object.keys(ssAll).filter(cn => irAll[cn]);
  if (diagCases.length) {
    const CH = {
      none: "#1a7f4b", torsional: "#b26a00", extreme: "#c0392b",
      soft: "#b26a00", extreme_soft: "#c0392b",
    };
    const LBL = { extreme_soft: "extreme soft" };
    const chip = flag => `<span style="font-weight:650;color:${CH[flag] || "#5c6672"}">${esc(LBL[flag] || flag || "none")}</span>`;
    diagBlocks = `<p class="minihead">Story stiffness &amp; irregularity (ASCE 7 §12.3)</p>` +
      diagCases.map(cn => {
        const ss = ssAll[cn] || {}, ir = irAll[cn] || {};
        const rows = [...r.story_order].reverse().map(s => {
          const k = ss[s] || {}, x = ir[s] || {};
          return [T(s), fmt(k.kx, 0), fmt(k.ky, 0),
            fmt(x.tors_ratio_x, 2), fmt(x.tors_ratio_y, 2),
            { html: chip(x.flag || "none"), txt: true },
            x.stiff_ratio == null ? D("—") : fmt(x.stiff_ratio, 2),
            { html: x.stiff_ratio == null ? "—" : chip(x.soft_flag || "none"), txt: true }];
        });
        const t = table([{ label: "Story", txt: true }, "kx (kN/m)", "ky (kN/m)",
          "τ ratio x", "τ ratio y", { label: "Torsion", txt: true },
          "stiff ratio", { label: "Soft story", txt: true }], rows);
        return `<div class="case-block"><h3>${esc(cn)} <span class="tag">diagnostics</span></h3>${t}</div>`;
      }).join("") +
      `<p class="note">Torsional irregularity: max/avg story drift — ≥ 1.2 torsional (Type 1a), ≥ 1.4 extreme (Type 1b).
        Soft story: story stiffness vs the story above — &lt; 70 % soft (Type 1a), &lt; 60 % extreme soft (Type 1b).</p>`;
  }

  const baseTable = table(
    [{ label: "Case", txt: true }, "FX (kN)", "FY (kN)", "FZ (kN)",
     "MX (kN·m)", "MY (kN·m)", "MZ (kN·m)"],
    caseEntries.map(([name, cd]) => {
      const b = cd.base || {};
      return [T(name), fmt(b.FX, 1), fmt(b.FY, 1), fmt(b.FZ, 1),
        fmt(b.MX, 1), fmt(b.MY, 1), fmt(b.MZ, 1)];
    }));

  /* ---- v0.11: load takedown — gravity landing at each support per gravity
     case/combo, with grid labels, grand-total FZ and a balance chip. */
  let takedownHtml = "";
  const tdAll = r.takedown || {};
  if (Object.keys(tdAll).length) {
    takedownHtml = Object.entries(tdAll).map(([name, td]) => {
      const sups = (td.supports || []).slice().sort((a, b) =>
        (!!a.grid !== !!b.grid) ? (a.grid ? -1 : 1)
          : ((a.grid || "").localeCompare(b.grid || "", undefined, { numeric: true })
            || (a.y - b.y) || (a.x - b.x)));
      const rows = sups.map(s => [T(s.grid || "—"), D(s.node),
        fmt(s.x, 2), fmt(s.y, 2), fmt(s.FZ, 1), fmt(s.FX, 1), fmt(s.FY, 1)]);
      rows.push([T("Σ total"), D(""), "", "", fmt(td.total_FZ, 1),
        fmt(sups.reduce((a, s) => a + (s.FX || 0), 0), 1),
        fmt(sups.reduce((a, s) => a + (s.FY || 0), 0), 1)]);
      const t = table([{ label: "Grid", txt: true }, { label: "Node", txt: true },
        "X (m)", "Y (m)", "FZ (kN)", "FX (kN)", "FY (kN)"], rows);
      const ok = !!td.balance_ok;
      const chipColor = ok ? "#1a7f4b" : "#c0392b";
      const chip = `<span style="font-weight:650;color:${chipColor}">${ok
        ? `● balanced — ΣFZ ${fmt(td.total_FZ, 1)} = applied ${fmt(td.applied_FZ, 1)} kN`
        : `▲ unbalanced — ΣFZ ${fmt(td.total_FZ, 1)} vs applied ${fmt(td.applied_FZ, 1)} kN`}</span>`;
      return `<div class="case-block"><h3>${esc(name)} <span class="tag">gravity takedown</span></h3>
        <p class="note">${chip}</p>${t}</div>`;
    }).join("");
  }

  /* ---- member force envelope (top 30 by |M3| across cases + combos) */
  const env = new Map();
  for (const [, cd] of [...Object.entries(r.cases || {}), ...Object.entries(r.combos || {})]) {
    for (const [uid, f] of Object.entries(cd.member_forces || {})) {
      const e = env.get(uid) || { N: 0, V2: 0, M3: 0 };
      e.N = Math.max(e.N, Math.abs(f[0]), Math.abs(f[6]));
      e.V2 = Math.max(e.V2, Math.abs(f[1]), Math.abs(f[7]));
      e.M3 = Math.max(e.M3, Math.abs(f[5]), Math.abs(f[11]));
      env.set(uid, e);
    }
  }
  const memInfo = {};
  for (const m of r.members || []) memInfo[m.uid] = m;
  const envRows = [...env.entries()]
    .sort((a, b) => b[1].M3 - a[1].M3)
    .slice(0, 30)
    .map(([uid, e]) => {
      const m = memInfo[uid] || {};
      return [T(uid), D(m.kind || "—"), D(m.story || "—"), D(m.section || "—"),
        fmt(e.N, 1), fmt(e.V2, 1), fmt(e.M3, 1)];
    });
  const envTable = table(
    [{ label: "Member", txt: true }, { label: "Kind", txt: true },
     { label: "Story", txt: true }, { label: "Section", txt: true },
     "|N|max (kN)", "|V2|max (kN)", "|M3|max (kN·m)"],
    envRows);

  /* ---- assemble */
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SkyFrame report — ${esc(model.name || "model")}</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 12.5px; line-height: 1.5; color: #1c232c; background: #fff;
    padding: 40px 48px 56px; max-width: 1040px; margin: 0 auto;
  }
  header.rhead { display: flex; align-items: flex-start; justify-content: space-between;
    border-bottom: 3px solid #1274ab; padding-bottom: 14px; margin-bottom: 22px; }
  .rhead h1 { font-size: 22px; font-weight: 700; letter-spacing: .2px; }
  .rhead .sub { color: #5c6672; font-size: 12px; margin-top: 3px; }
  .rhead .stamp { text-align: right; color: #5c6672; font-size: 11.5px; }
  .rhead .stamp b { color: #1274ab; font-size: 13px; display: block; }
  .printbtn { font: inherit; font-weight: 600; color: #fff; background: #1274ab;
    border: none; border-radius: 6px; padding: 7px 16px; cursor: pointer; margin-top: 8px; }
  .printbtn:hover { background: #0d5c88; }
  .rsec { margin-bottom: 26px; break-inside: avoid-page; }
  .rsec h2 { font-size: 14px; font-weight: 700; color: #10344d;
    border-bottom: 1px solid #d9e0e7; padding-bottom: 5px; margin-bottom: 10px; }
  .case-block { margin-bottom: 14px; page-break-inside: avoid; break-inside: avoid; }
  .case-block h3 { font-size: 12.5px; font-weight: 650; margin-bottom: 5px; }
  .tag { font-size: 10px; font-weight: 600; color: #5c6672; background: #eef1f5;
    border: 1px solid #d9e0e7; border-radius: 99px; padding: 1px 8px; vertical-align: 1px; }
  .minihead { font-size: 10.5px; font-weight: 650; color: #5c6672;
    text-transform: uppercase; letter-spacing: .5px; margin: 8px 0 3px; }
  .note { color: #5c6672; font-size: 11.5px; margin-bottom: 8px; }
  .note b { color: #1c232c; }
  .po-warn { color: #8a5a00; background: #fdf4e3; border: 1px solid #ecd9ad;
    border-radius: 6px; padding: 6px 10px; }
  table.rt { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums;
    margin-bottom: 10px; page-break-inside: avoid; break-inside: avoid; }
  table.rt th { font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .4px; color: #5c6672; background: #f4f6f9;
    border: 1px solid #d9e0e7; padding: 5px 9px; text-align: right; }
  table.rt td { border: 1px solid #e3e8ee; padding: 4px 9px; text-align: right; }
  table.rt th.txt, table.rt td.txt { text-align: left; }
  table.rt td.dim { color: #5c6672; }
  table.rt tbody tr:nth-child(even) td { background: #fafbfc; }
  .chart-grid { display: flex; flex-wrap: wrap; gap: 14px; }
  .chart-card { flex: 1 1 280px; max-width: 360px; border: 1px solid #e3e8ee;
    border-radius: 8px; padding: 10px 12px; page-break-inside: avoid; break-inside: avoid; }
  .chart-title { font-size: 11.5px; font-weight: 650; color: #38424d; margin-bottom: 4px; }
  .chart-title .unit { color: #8a94a0; font-weight: 400; }
  .chart-card svg { display: block; width: 100%; height: auto; }
  footer.rfoot { border-top: 1px solid #d9e0e7; margin-top: 30px; padding-top: 12px;
    color: #5c6672; font-size: 11px; }
  @media print {
    body { padding: 0; font-size: 11px; }
    .printbtn { display: none; }
    .rsec { break-inside: auto; }
    table.rt, .case-block, .chart-card { page-break-inside: avoid; break-inside: avoid; }
  }
</style>
</head>
<body>
<header class="rhead">
  <div>
    <h1>${esc(model.name || "Untitled model")}</h1>
    <div class="sub">Structural analysis report · Units kN, m, s · E in kPa</div>
  </div>
  <div class="stamp">
    <b>SkyFrame — OpenSees inside</b>
    ${esc(now.toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" }))}
    · ${esc(now.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false }))}<br>
    <button class="printbtn" onclick="window.print()">Print / Save as PDF</button>
  </div>
</header>

${section("1 · Model summary", summaryTable + matTable + secTable + shellSecTable)}
${section("2 · Loads", patTable +
  `<p class="note">Mass source: <b>${esc(massStr)}</b></p>` +
  caseTable + comboTable + rsTable + thTable,
  "Member/area totals are unfactored sums of the raw pattern loads.")}
${section("3 · Modal analysis", modalHtml,
  "Mass-participation ratios per mode; Γ = modal participation factor (L/M*).")}
${section("4 · Story results by case", storyBlocks + diagBlocks)}
${section("5 · Base reactions", baseTable)}
${takedownHtml ? section("6 · Load takedown (gravity)", takedownHtml,
  "Where vertical load reaches the foundation per gravity case/combo — support reactions with grid labels and a balance check (support ΣFZ vs applied gravity).") : ""}
${section(`${takedownHtml ? 7 : 6} · Member force envelope`, envTable,
  `Top ${envRows.length} members by |M3| — absolute envelope across all static cases and combinations.`)}
${(() => { let n = takedownHtml ? 7 : 6;
  const pushN = pushoverHtml ? ++n : n;
  const chartN = chartsHtml ? ++n : n;
  const specN = spectraHtml ? ++n : n;
  return `${pushoverHtml ? section(`${pushN} · Pushover analysis`, pushoverHtml,
    "Displacement-controlled nonlinear static — base shear vs roof displacement; amber markers = slope drop > 20 % (yield). Hinge table: top plastic rotations at target drift.") : ""}
${chartsHtml ? section(`${chartN} · Story drift & response charts`, chartsHtml) : ""}
${spectraHtml ? section(`${specN} · Response spectra`, spectraHtml) : ""}`; })()}

<footer class="rfoot">
  Analysis: OpenSees 3.7 · SkyFrame validation suite: 60+ benchmarks ·
  Generated by SkyFrame Building Analysis Studio.
</footer>
</body>
</html>`;
}

/** Open the report in a new tab. Returns the window (null if blocked). */
export function openReport(model, results, opts = {}) {
  const html = buildReportHtml(model, results, opts);
  const win = window.open("", "_blank");
  if (!win) return null;
  win.document.open();
  win.document.write(html);
  win.document.close();
  return win;
}
