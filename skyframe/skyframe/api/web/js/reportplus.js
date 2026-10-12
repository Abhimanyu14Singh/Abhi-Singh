/* SkyFrame — Create Report additions (File › Create Report…, js/reportx.js):

     · "Results tables": any Show-Tables table, user-selected, rendered in the
       display units (live: one POST /api/report/data {tables}; ?mock=1: the
       mock table router), capped at N rows per table;
     · "3D snapshots per case": an isometric picture of the deformed shape of
       each selected case (undeformed grey + deformed blue, auto-scaled),
       drawn from results.nodes / members / shell_quads / node_disp — PNG;
     · "Download .docx": the finished report converted to Word blocks
       (headings, notes, tables, PNG images; SVG plots rasterised) and
       written by POST /api/report/docx (skyframe/io/docx.py) — or in the
       browser under ?mock=1 (js/mock_restables.js, same block format).

   Hooks in js/reportx.js (3 small lines): dialogGroups(body) in the dialog,
   sections(opts) before assembly, downloadDocx(html, opts) on the button. */

import rpU from "./units.js";
import { colSpec as rpColSpec, cellText as rpCellText } from "./tables.js";
import { mrtDocx as rpMockDocx } from "./mock_restables.js";
import { reDownload as rpDownload } from "./resexport.js";

let SKY = null;
const RP = { tables: null, maxRows: 200, snapCases: null, last: null };
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const el = (tag, attrs = {}, kids = []) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "text") n.textContent = v; else if (k === "html") n.innerHTML = v; else if (k === "class") n.className = v;
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of [].concat(kids)) if (c != null) n.append(c);
  return n;
};

/* ================================================================
   dialog groups
   ================================================================ */
function rpCaseNames(r) {
  if (!r) return [];
  return [...Object.keys(r.cases || {}), ...Object.entries(r.combos || {}).filter(([, cd]) => !cd.min).map(([n]) => n)];
}
export function rpDialogGroups(body) {
  const S = SKY.store, r = S.results;
  const g1 = el("fieldset", { class: "dlg-group", id: "rpTablesGroup" }, [el("legend", { text: "Results tables (Show Tables)" })]);
  const host = el("div", { class: "rp-tables", id: "rpTableList", style: "max-height:170px;overflow:auto;columns:2;font-size:12px" },
    [el("span", { class: "muted", text: r ? "Loading tables…" : "Run the analysis to add result tables." })]);
  const maxRows = el("input", { type: "number", id: "rpMaxRows", min: "10", max: "5000", step: "10", value: String(RP.maxRows) });
  g1.append(host, el("label", { class: "rx-row" }, [el("span", { text: "Rows per table (max)" }), maxRows]));
  const boxes = {};
  if (r) {
    SKY.skyApi("/api/tables/list").then(d => {
      host.textContent = "";
      for (const t of d.tables || []) {
        const b = el("input", { type: "checkbox", "data-key": t.key });
        b.checked = !!(RP.tables && RP.tables.includes(t.key));
        boxes[t.key] = b;
        host.appendChild(el("label", { class: "dlg-chk", title: t.group, style: "display:flex;gap:5px;break-inside:avoid" }, [b, el("span", { text: t.title })]));
      }
    }).catch(e => { host.textContent = "Tables unavailable: " + e.message; });
  }
  const g2 = el("fieldset", { class: "dlg-group", id: "rpSnapGroup" }, [el("legend", { text: "3D snapshots per case (deformed shape)" })]);
  const names = rpCaseNames(r);
  const csel = el("select", { id: "rpSnapCases", multiple: true, size: String(Math.min(5, Math.max(2, names.length))) });
  const pick = RP.snapCases || [];
  csel.innerHTML = names.map(n => `<option value="${esc(n)}"${pick.includes(n) ? " selected" : ""}>${esc(n)}</option>`).join("");
  g2.append(names.length ? el("label", { class: "rx-row" }, [el("span", { text: "Cases (max 12)" }), csel])
    : el("span", { class: "muted", text: "Run the analysis to add per-case snapshots." }));
  body.append(g1, g2);
  return {
    opts: () => {
      const o = { rpTables: Object.keys(boxes).filter(k => boxes[k].checked),
        rpMaxRows: Math.max(10, Math.min(5000, parseInt(maxRows.value, 10) || 200)),
        rpSnapCases: [...csel.selectedOptions].map(x => x.value).slice(0, 12) };
      RP.tables = o.rpTables; RP.maxRows = o.rpMaxRows; RP.snapCases = o.rpSnapCases;
      return o;
    },
  };
}

/* ================================================================
   report sections
   ================================================================ */
async function rpFetchTables(keys) {
  const S = SKY.store;
  const out = {};
  if (!S.mock) {
    try {
      const res = await fetch("/api/report/data", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tables: keys }) });
      if (res.ok) { const d = await res.json(); if (d.results_available) return d.tables || {}; }
    } catch { /* fall back to one call per table */ }
  }
  for (const k of keys) {
    try { out[k] = await SKY.skyApi(`/api/tables/${encodeURIComponent(k)}`, {}); }
    catch (e) { out[k] = { error: e.message }; }
  }
  return out;
}
function rpTableHtml(t, maxRows) {
  const specs = t.columns.map(rpColSpec);
  const head = t.columns.map((c, i) => `<th class="${specs[i].text ? "txt" : ""}">${esc(c.label)}${specs[i].unit ? ` (${esc(specs[i].unit)})` : ""}</th>`).join("");
  const rows = t.rows.slice(0, maxRows).map(r => `<tr>${t.columns.map((c, i) => `<td class="${specs[i].text ? "txt" : ""}">${esc(rpCellText(specs[i], r[c.key]))}</td>`).join("")}</tr>`).join("");
  return `<table class="rt" data-table="${esc(t.key)}"><thead><tr>${head}</tr></thead><tbody>${rows ||
    `<tr><td class="txt" colspan="${t.columns.length}">— no rows —</td></tr>`}</tbody></table>`;
}

/** Isometric deformed-shape PNG of one case (data URL). */
export function rpSnapshot(caseName, { w = 900, h = 600 } = {}) {
  const r = SKY.store.results || {};
  const cd = (r.cases || {})[caseName] || (r.combos || {})[caseName] || (r.rs_cases || {})[caseName];
  const nodes = r.nodes || {};
  const disp = (cd && cd.node_disp) || {};
  const cy = Math.cos(0.7), sy = Math.sin(0.7), cp = Math.cos(0.42), sp = Math.sin(0.42);
  const proj = p => { const x = p[0] * cy - p[1] * sy, y = p[0] * sy + p[1] * cy; return [x, p[2] * cp + y * sp]; };
  const segs = (r.members || []).map(m => [String(m.ni), String(m.nj)]).filter(([a, b]) => nodes[a] && nodes[b]);
  const quads = (r.shell_quads || []).map(q => (q.nodes || []).map(String)).filter(q => q.every(t => nodes[t]));
  const used = new Set([...segs.flat(), ...quads.flat()]);
  const pts = [...used].map(t => nodes[t]);
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  const g = c.getContext("2d");
  g.fillStyle = "#ffffff"; g.fillRect(0, 0, w, h);
  if (!pts.length) { g.fillStyle = "#5c6672"; g.font = "14px sans-serif"; g.fillText("(no results geometry)", 20, 30); return c.toDataURL("image/png"); }
  const ext = Math.max(1e-9, ...[0, 1, 2].map(k => Math.max(...pts.map(p => p[k])) - Math.min(...pts.map(p => p[k]))));
  let umax = 0;
  for (const t of used) { const d = disp[t]; if (d) umax = Math.max(umax, Math.hypot(d[0], d[1], d[2])); }
  const scale = umax > 0 ? 0.08 * ext / umax : 0;
  const def = t => { const p = nodes[t], d = disp[t] || [0, 0, 0]; return [p[0] + scale * d[0], p[1] + scale * d[1], p[2] + scale * d[2]]; };
  const P = [...used].flatMap(t => [proj(nodes[t]), proj(def(t))]);
  const xs = P.map(p => p[0]), ys = P.map(p => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const pad = 40, sc = Math.min((w - 2 * pad) / Math.max(1e-9, x1 - x0), (h - 2 * pad - 24) / Math.max(1e-9, y1 - y0));
  const T = q => [pad + (q[0] - x0) * sc + ((w - 2 * pad) - (x1 - x0) * sc) / 2, h - pad - (q[1] - y0) * sc];
  const line = (a, b, col, lw) => { const p = T(a), q = T(b); g.beginPath(); g.moveTo(p[0], p[1]); g.lineTo(q[0], q[1]); g.strokeStyle = col; g.lineWidth = lw; g.stroke(); };
  for (const q of quads) {                                   // shells
    for (const [pick, col, fill] of [[t => proj(nodes[t]), "rgba(150,160,172,0.35)", null], [t => proj(def(t)), "rgba(18,116,171,0.55)", "rgba(18,116,171,0.08)"]]) {
      g.beginPath();
      q.forEach((t, i) => { const p = T(pick(t)); i ? g.lineTo(p[0], p[1]) : g.moveTo(p[0], p[1]); });
      g.closePath();
      if (fill) { g.fillStyle = fill; g.fill(); }
      g.strokeStyle = col; g.lineWidth = 0.6; g.stroke();
    }
  }
  for (const [a, b] of segs) line(proj(nodes[a]), proj(nodes[b]), "#c3cad3", 1.2);
  for (const [a, b] of segs) line(proj(def(a)), proj(def(b)), "#1274ab", 1.9);
  g.fillStyle = "#10344d"; g.font = "bold 14px sans-serif";
  g.fillText(`${caseName} — deformed shape`, 12, 20);
  g.fillStyle = "#5c6672"; g.font = "12px sans-serif";
  g.fillText(cd ? `max |u| = ${rpU.fmtU("disp", umax, 2)} · scale ×${scale ? scale.toPrecision(3) : 0}` : "case not in the results", 12, 38);
  return c.toDataURL("image/png");
}

/** Extra report parts (appended before assembly). */
export async function rpSections(opts = {}) {
  const parts = [];
  const r = SKY.store.results;
  if (!r) return parts;
  const keys = opts.rpTables || [];
  if (keys.length) {
    const tabs = await rpFetchTables(keys);
    const maxRows = opts.rpMaxRows || 200;
    let html = "";
    for (const k of keys) {
      const t = tabs[k];
      if (!t || t.error) { html += `<h3>${esc(k)}</h3><p class="note">Table unavailable: ${esc((t && t.error) || "no data")}</p>`; continue; }
      const more = t.rows.length > maxRows ? ` — first ${maxRows} of ${t.rows.length} rows (export the full table from Show Tables)` : ` — ${t.rows.length} rows`;
      html += `<h3>${esc(t.title)}</h3><p class="note">${esc(t.group.replace(/^Analysis Results\s*>\s*/, ""))}${esc(more)}` +
        `${(t.warnings || []).length ? " · ⚠ " + esc(t.warnings.join("; ")) : ""}</p>` + rpTableHtml(t, maxRows);
    }
    parts.push({ key: "rptables", title: "Results tables", html, note: `Units: ${esc(rpU.unitSet().label)}. Post-processed from the last analysis.` });
  }
  const snaps = opts.rpSnapCases || [];
  if (snaps.length) {
    const figs = snaps.map(n => `<figure data-case="${esc(n)}"><img alt="${esc(n)} deformed shape" src="${rpSnapshot(n)}"><figcaption>${esc(n)} — deformed shape (isometric, auto-scaled)</figcaption></figure>`).join("");
    parts.push({ key: "snapshots", title: "3D snapshots per case", html: `<div class="imgs">${figs}</div>` });
  }
  return parts;
}

/* ================================================================
   HTML report → .docx blocks
   ================================================================ */
async function rpSvgToPng(svg) {
  const xml = new XMLSerializer().serializeToString(svg);
  const vb = (svg.getAttribute("viewBox") || "").split(/\s+/).map(Number);
  const w = +svg.getAttribute("width") || vb[2] || 330, h = +svg.getAttribute("height") || vb[3] || 260;
  const img = new Image();
  const url = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(xml.includes("xmlns=") ? xml : xml.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"'));
  await new Promise((res, rej) => { img.onload = res; img.onerror = () => rej(new Error("svg raster failed")); img.src = url; });
  const c = document.createElement("canvas");
  const k = 2;                                                       // 2x for print
  c.width = w * k; c.height = h * k;
  const g = c.getContext("2d");
  g.fillStyle = "#fff"; g.fillRect(0, 0, c.width, c.height);
  g.drawImage(img, 0, 0, c.width, c.height);
  return { src: c.toDataURL("image/png"), w };
}
async function rpImgToPng(src) {
  if (/^data:image\/png;base64,/.test(src)) return src;
  const img = new Image();
  await new Promise((res, rej) => { img.onload = res; img.onerror = () => rej(new Error("image load failed")); img.src = src; });
  const c = document.createElement("canvas");
  c.width = img.naturalWidth || 600; c.height = img.naturalHeight || 400;
  c.getContext("2d").drawImage(img, 0, 0);
  return c.toDataURL("image/png");
}
const rpText = n => (n.textContent || "").replace(/\s+/g, " ").trim();
function rpTableBlock(t) {
  const head = [...t.querySelectorAll("thead th")].map(rpText);
  const rows = [...t.querySelectorAll("tbody tr")].map(tr => [...tr.children].map(rpText));   // display-unit text
  const align = [...t.querySelectorAll("thead th")].map(th => th.classList.contains("txt") ? "l" : "r");
  return { type: "table", columns: head.length ? head : (rows[0] || []).map((_, i) => `#${i + 1}`), rows, align };
}
/** Convert the self-contained report HTML into the docx document spec. */
export async function rpHtmlToDoc(html, opts = {}) {
  const dom = new DOMParser().parseFromString(html, "text/html");
  const cover = dom.querySelector("header.cover");
  const meta = cover ? [...cover.querySelectorAll(".meta > span")].map(s => [rpText(s), rpText(s.nextElementSibling || s)]) : [];
  const doc = { title: cover ? rpText(cover.querySelector("h1")) : (dom.title || "Report"),
    subtitle: cover && cover.querySelector(".sub") ? rpText(cover.querySelector(".sub")) : "",
    meta, paper: opts.paper === "Letter" ? "Letter" : "A4", landscape: opts.orientation === "landscape", toc: true, blocks: [] };
  const B = doc.blocks;
  const walk = async node => {
    for (const n of node.children) {
      const tag = n.tagName.toLowerCase();
      if (tag === "h2") B.push({ type: "heading", level: 1, text: rpText(n) });
      else if (tag === "h3") B.push({ type: "heading", level: 2, text: rpText(n) });
      else if (tag === "p") { const t = rpText(n); if (t) B.push({ type: "paragraph", text: t, style: n.classList.contains("note") ? "note" : undefined }); }
      else if (tag === "table") B.push(rpTableBlock(n));
      else if (tag === "ul" || tag === "ol") for (const li of n.querySelectorAll("li")) B.push({ type: "paragraph", text: "• " + rpText(li) });
      else if (tag === "figure") {
        const img = n.querySelector("img"), svg = n.querySelector("svg"), cap = n.querySelector("figcaption");
        try {
          const r = img ? { src: await rpImgToPng(img.getAttribute("src")) } : svg ? await rpSvgToPng(svg) : null;
          if (r) B.push({ type: "image", data_url: r.src, width_px: r.w, caption: cap ? rpText(cap) : "" });
        } catch (e) { B.push({ type: "paragraph", text: `[image omitted: ${e.message}]`, style: "note" }); }
      } else if (tag === "svg") {
        try { const r = await rpSvgToPng(n); B.push({ type: "image", data_url: r.src, width_px: r.w }); } catch (e) { /* skip */ }
      } else if (n.classList.contains("kv")) {
        B.push({ type: "table", columns: ["Item", "Value"], align: ["l", "l"],
          rows: [...n.children].map(d => [rpText(d.querySelector("span") || d), rpText(d.querySelector("b") || d)]) });
      } else if (n.classList.contains("plot")) {
        const s = n.querySelector("svg");
        if (s) { try { const r = await rpSvgToPng(s); B.push({ type: "image", data_url: r.src, width_px: r.w }); } catch (e) { /* skip */ } }
      } else await walk(n);
    }
  };
  for (const sec of dom.querySelectorAll("section.sec")) {
    if (B.length && sec.classList.contains("newpage")) B.push({ type: "page_break" });
    await walk(sec);
  }
  const foot = dom.querySelector("body > footer");
  if (foot) B.push({ type: "paragraph", text: rpText(foot), style: "note" });
  return doc;
}

function rpB64(bytes) {
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}
/** Build + save the .docx of a finished report HTML. */
export async function rpDownloadDocx(html, opts = {}) {
  const S = SKY.store;
  const doc = await rpHtmlToDoc(html, opts);
  const name = String((S.model && S.model.name) || "model").replace(/[^\w.-]+/g, "_");
  const mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  let rec;
  if (S.mock) rec = { filename: `${name}_report.docx`, mime, content_b64: rpB64(rpMockDocx(doc)) };
  else {
    const res = await fetch("/api/report/docx", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(doc) });
    const d = await res.json().catch(() => null);
    if (!res.ok || !d) throw new Error((d && d.error) || `${res.status} ${res.statusText}`);
    rec = { filename: d.filename, mime: d.mime, content_b64: d.content_b64 };
  }
  rec.blocks = doc.blocks.length;
  rec.images = doc.blocks.filter(b => b.type === "image").length;
  RP.last = rec;
  rpDownload(rec);
  return rec;
}

export function initReportPlus(sky) {
  if (!sky) return;
  SKY = sky;
  sky.reportPlus = {
    dialogGroups: body => rpDialogGroups(body),
    sections: o => rpSections(o),
    downloadDocx: (html, o) => rpDownloadDocx(html, o),
    htmlToDoc: (html, o) => rpHtmlToDoc(html, o),
    snapshot: n => rpSnapshot(n),
    get last() { return RP.last; },
  };
}
