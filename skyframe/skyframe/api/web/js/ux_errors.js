/* SkyFrame UX pass 2 — plain-language errors + field highlighting.
   Backend 400 messages ("Material 'CONC': E must be finite and > 0 (got
   -5.0)", "could not convert string to float: 'x'", "diaphragm must be
   rigid|none, got 'weird'", "Member C1: unknown section NOPE" …) are
   rewritten in plain language wherever they surface:
     · error toasts (#toasts .toast.error)
     · dialog error lines (.field-error / .dlg-error)
   and, when the message names a field, the matching input of the top-most
   dialog (or the sidebar form) is outlined, marked aria-invalid, given the
   message as its tooltip and focused. The original text stays available in
   the element's title. Pure presentation — no request is changed. */

const OBJ_RE = /^(material|frame section|section|shell section|shell|member|frame|link|group|combo|combination|case|load case|pattern|load pattern|story|joint|node|diaphragm|spring property|spring|tendon|function|grid|wall|pier|hinge property|hinge|stage|section cut|cut)\s+['"“]?([^'":”]+?)['"”]?\s*:\s*(.+)$/i;

const num = s => { const v = Number(s); return isFinite(v) ? String(+v.toPrecision(6)) : s; };
const human = f => String(f).replace(/_/g, " ");

/** Plain-language form of a backend / validation message.
    Returns {text, field, obj:{kind,name}|null, changed}. */
export function humanizeError(raw) {
  let msg = String(raw == null ? "" : raw).trim();
  let obj = null, field = null;
  const orig = msg;
  msg = msg.replace(/^(\d{3})\s+(BAD REQUEST|INTERNAL SERVER ERROR|NOT FOUND)\s*$/i, (_, c, t) =>
    c === "400" ? "The server rejected the request (400). Check the values you changed." :
    c === "404" ? "Not found on the server (404)." : "The server hit an internal error (500). Try again or check the run log.");
  const m = OBJ_RE.exec(msg);
  let rest = msg;
  if (m) { obj = { kind: m[1], name: m[2].trim() }; rest = m[3]; }
  let r;
  if ((r = /^could not convert string to float:\s*'(.*)'$/i.exec(rest))) {
    rest = `“${r[1]}” is not a number — enter a numeric value.`;
  } else if ((r = /^float\(\) argument must be a string or a (real )?number, not '(\w+)'$/i.exec(rest))) {
    rest = `A number was expected, but a ${r[2] === "dict" ? "set of values" : r[2] === "list" ? "list" : r[2]} was given.`;
  } else if ((r = /^['"]?([\w .-]+?)['"]? must be ([\w|/-]+(?:\|[\w/-]+)+),?\s*got\s*'?([^']*)'?$/i.exec(rest))) {
    field = r[1];
    rest = `${human(r[1])} must be one of ${r[2].split("|").join(", ")} (you entered “${r[3]}”).`;
  } else if ((r = /^['"]?([\w .-]+?)['"]? must be (a )?finite( value)? and\s*(>|>=|<|<=)\s*(-?[\d.eE+]+)\s*(?:\(got\s*([^)]*)\))?\.?$/i.exec(rest))
    || (r = /^['"]?([\w .-]+?)['"]? must be (a )?finite( value)?\s*(>|>=|<|<=)\s*(-?[\d.eE+]+)\s*(?:\(?[^,]*?\)?)?\s*,?\s*(?:got\s*([^)]*)\)?)?\.?$/i.exec(rest))) {
    field = r[1];
    const op = { ">": "greater than", ">=": "at least", "<": "less than", "<=": "at most" }[r[4]];
    const lim = num(r[5]);
    const pos = (r[4] === ">" && +r[5] === 0) ? "a positive number" : (r[4] === ">=" && +r[5] === 0) ? "zero or a positive number" : `a number ${op} ${lim}`;
    rest = `${human(r[1])} must be ${pos}${r[6] ? ` (you entered ${num(r[6].trim())})` : ""}.`;
  } else if ((r = /^unknown (section|material|case|pattern|load pattern|combo|function|group|member|shell|story|node|joint|diaphragm|spring property|hinge property|link property)\s*['"]?([^'"]+?)['"]?\.?$/i.exec(rest))) {
    field = r[1];
    rest = `the ${r[1]} “${r[2]}” does not exist — pick one from the list or define it first (it may have been renamed or deleted).`;
  } else if ((r = /^['"]?([\w .-]+?)['"]? must be\s*(>|>=|<|<=)\s*(-?[\d.eE+]+)(.*)$/i.exec(rest))) {
    field = r[1];
    const op = { ">": "greater than", ">=": "at least", "<": "less than", "<=": "at most" }[r[2]];
    rest = `${human(r[1])} must be ${op} ${num(r[3])}${r[4] ? r[4].replace(/\(got\s*([^)]*)\)/i, (_, g) => `(you entered ${num(g.trim())})`) : ""}`;
    if (!/[.!?]$/.test(rest)) rest += ".";
  } else if ((r = /^['"]?([\w.-]+)['"]? (is required|must be (?:non-empty|a number|an integer|positive|a list|an object|a string))(.*)$/i.exec(rest))) {
    field = r[1];
    rest = `${human(r[1])} ${r[2]}${r[3] || ""}`;
  } else if ((r = /^missing (?:required )?(?:field|key)\s*['"]?([\w.-]+)['"]?/i.exec(rest))) {
    field = r[1];
    rest = `${human(r[1])} is required.`;
  }
  let text = rest;
  if (obj) {
    const kind = obj.kind.charAt(0).toUpperCase() + obj.kind.slice(1).toLowerCase();
    text = `${kind} “${obj.name}”: ${rest}`;
  } else text = rest.charAt(0).toUpperCase() + rest.slice(1);
  if (text === orig) return { text: orig, field, obj, changed: false };
  return { text, field, obj, changed: true };
}

/* ---------------- field lookup ---------------- */
const norm = s => String(s || "").toLowerCase().replace(/[\s_\-·.]+/g, "").replace(/\(.*?\)/g, "");
// text of a label with its parts separated ("Modulus of Elasticity E" + "kPa")
const partsText = n => [...n.childNodes].map(c => c.nodeType === 3 ? c.textContent : (c.matches && c.matches("input, select, textarea") ? " " : partsText(c))).join(" ");
function labelText(inp) {
  if (inp.id) {
    const l = document.querySelector(`label[for="${CSS.escape(inp.id)}"]`);
    if (l) return partsText(l);
  }
  const l = inp.closest("label");
  if (l) return partsText(l);
  const f = inp.closest(".field, .rs-field, .dlg-field, .fm-row, .mpd-row, .cd-row, tr");
  const ll = f && f.querySelector("label, .lbl, span, th, td");
  if (ll && !ll.contains(inp)) return partsText(ll);
  const prev = inp.previousElementSibling;
  if (prev && !prev.matches("input, select, textarea")) return partsText(prev);
  return inp.getAttribute("aria-label") || inp.placeholder || "";
}
export function findField(root, field, obj) {
  if (!root || !field) return null;
  // open the collapsed row (<details>) of the named object so its inputs show
  if (obj && obj.name) root.querySelectorAll("details:not([open])").forEach(d => {
    const s = d.querySelector("summary");
    if (s && s.textContent.includes(obj.name)) d.open = true;
  });
  // …or an expandable header row (role=button, aria-expanded=false) naming it
  if (obj && obj.name) {
    const names = h => (h.textContent || "").trim() === obj.name || [...h.querySelectorAll("*")].some(c => c.children.length === 0 && c.textContent.trim() === obj.name);
    const hd = [...root.querySelectorAll("[aria-expanded='false']")].find(h => !h.closest(".modal-head") && names(h));
    if (hd) hd.click();
  }
  const want = norm(field);
  if (!want) return null;
  const inputs = [...root.querySelectorAll("input:not([type=hidden]):not([type=button]), select, textarea")].filter(i => i.offsetParent !== null);
  const keyOf = i => [i.name, i.id, i.dataset.field, i.dataset.k, i.dataset.key, i.dataset.prop, i.getAttribute("aria-label")].filter(Boolean).map(norm);
  const score = i => {
    const keys = keyOf(i);
    if (keys.includes(want)) return 3;
    if (keys.some(k => k.endsWith(want) && want.length >= 1 && k.length - want.length <= 6)) return 2;
    const raw = labelText(i).replace(/\[.*?\]/g, "");
    const toks = raw.split(/[\s(),:·/]+/).filter(Boolean);
    // short symbols ("E", "G", "fc") match a whole label word, case-sensitively
    if (field.length <= 3 ? toks.includes(field) : toks.map(norm).includes(want)) return 3;
    const lt = norm(raw);
    if (lt === want) return 3;
    if (want.length > 2 && lt.startsWith(want)) return 1;
    // a unit suffix: "E kPa" → "ekpa"
    if (want.length <= 3 && new RegExp("^" + want + "(kpa|mpa|gpa|psi|ksi|m|mm|in|ft|kn|kip)").test(lt)) return 2;
    return 0;
  };
  let best = null, bestS = 0;
  for (const i of inputs) {
    let s = score(i);
    if (!s) continue;
    // prefer the row that names the object ("Material CONC")
    if (obj && obj.name) {
      const row = i.closest("tr, .mgr-row, .row, .field-row, li, .card");
      if (row && row.textContent.includes(obj.name)) s += 2;
      else if (row && [...row.querySelectorAll("input")].some(x => x.value === obj.name)) s += 2;
    }
    if (s > bestS) { best = i; bestS = s; }
  }
  return best;
}
export function markField(inp, text) {
  if (!inp) return;
  inp.classList.add("ux-field-err");
  inp.setAttribute("aria-invalid", "true");
  if (!inp.dataset.uxErrTitle) inp.dataset.uxErrTitle = inp.title || "";
  inp.title = text;
  const clear = () => {
    inp.classList.remove("ux-field-err");
    inp.removeAttribute("aria-invalid");
    inp.title = inp.dataset.uxErrTitle || "";
    delete inp.dataset.uxErrTitle;
    inp.removeEventListener("input", clear);
    inp.removeEventListener("change", clear);
  };
  inp.addEventListener("input", clear);
  inp.addEventListener("change", clear);
  try { inp.focus({ preventScroll: false }); if (inp.select) inp.select(); } catch { /* ignore */ }
}

export function installErrors(sky) {
  const topRoot = () => {
    const d = sky.ux && sky.ux.dialogs && sky.ux.dialogs.top && sky.ux.dialogs.top();
    return d ? (d.querySelector(".modal") || d) : null;
  };
  function highlight(h, root) {
    if (!h.field) return null;
    const r = root || topRoot() || document.getElementById("quickForm");
    const inp = findField(r, h.field, h.obj);
    if (inp) markField(inp, h.text);
    return inp;
  }

  /* toasts */
  const toasts = document.getElementById("toasts");
  if (toasts) new MutationObserver(recs => {
    for (const r of recs) for (const n of r.addedNodes) {
      if (n.nodeType !== 1 || !n.classList.contains("error")) continue;
      const m = n.querySelector(".toast-msg");
      if (!m || m.dataset.uxHum) continue;
      const h = humanizeError(m.textContent);
      m.dataset.uxHum = "1";
      if (h.changed) { m.title = "Server message: " + m.textContent; m.textContent = h.text; }
      n.setAttribute("role", "alert");
      const title = (n.querySelector("b") || {}).textContent || "";
      const root = /generation/i.test(title) ? document.getElementById("quickForm") : null;
      const inp = highlight(h, root);
      if (inp) n.appendChild(Object.assign(document.createElement("div"), { className: "ux-toast-field muted", textContent: "The field is highlighted." }));
    }
  }).observe(toasts, { childList: true });

  /* dialog error lines (.field-error / .dlg-error): rewrite + highlight */
  const seen = new WeakSet();
  function watchErr(p) {
    if (seen.has(p)) return;
    seen.add(p);
    const run = () => {
      const t = (p.textContent || "").trim();
      if (!t || p.classList.contains("hidden") || p.dataset.uxHum === t) return;
      const h = humanizeError(t);
      if (h.changed) { p.title = "Server message: " + t; p.textContent = h.text; }
      p.dataset.uxHum = p.textContent.trim();
      p.setAttribute("role", "alert");
      const back = p.closest(".modal-backdrop");
      highlight(h, back ? back.querySelector(".modal") : null);
    };
    new MutationObserver(run).observe(p, { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ["class"] });
    run();
  }
  const scan = root => root.querySelectorAll && root.querySelectorAll(".field-error, .dlg-error").forEach(watchErr);
  scan(document);
  // only dialog subtrees are observed (never the plan / 3D views)
  const watchedB = new WeakSet();
  const watchBack = b => {
    if (watchedB.has(b)) return;
    watchedB.add(b);
    let q = 0;
    new MutationObserver(() => { if (!q) q = requestAnimationFrame(() => { q = 0; scan(b); }); })
      .observe(b, { childList: true, subtree: true });
    scan(b);
  };
  document.querySelectorAll(".modal-backdrop").forEach(watchBack);
  const addObs = new MutationObserver(recs => {
    for (const r of recs) for (const n of r.addedNodes)
      if (n.nodeType === 1 && n.classList.contains("modal-backdrop")) watchBack(n);
  });
  addObs.observe(document.getElementById("app") || document.body, { childList: true });
  addObs.observe(document.body, { childList: true });

  sky.ux = sky.ux || {};
  sky.ux.errors = { humanize: humanizeError, findField, markField, highlight };
}
