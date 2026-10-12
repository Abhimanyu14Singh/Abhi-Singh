/* SkyFrame W8 — ETABS master / similar stories.

   CONTRACT "Master / similar stories, selection and snapping".

   * Story Data (Define › Stories and Grid Systems › Story Data): two extra
     columns, Master Story (checkbox) and Similar To (a master story or
     None) — stored as Story.master / Story.similar_to, emitted only when
     set (the backend core/similar_stories.py validates them).
   * The "Apply to" selector of the plan editor gets a third choice, ETABS
     One Story / Similar Stories / All Stories. The scope applies to
       - drawing  (each plan draw is repeated on every story of the scope),
       - selecting (a plan click / box also selects the matching objects —
         same kind, same plan geometry — on the other stories of the scope),
       - erasing  (the erase tool removes the matching objects too),
     and therefore to everything that acts on the selection: Assign dialogs,
     Edit › Copy / Delete / Move / Replicate.
   The similar group of a story = its master + every story similar to that
   master (bottom → top); an independent story is alone.

   Hooks: one line in js/dlgstd_defs.js (story table columns); the plan
   editor callbacks are wrapped here (no app.js edit). store.storyScope =
   "one" | "similar" | "all" (store.applyAll stays true only for "all"). */

import { el as ssEl } from "./dlgkit.js";

const near = (a, b) => Math.abs(a - b) < 1e-6;

/** Similar group of `story` (bottom → top order). */
export function similarGroup(model, story) {
  const ss = (model && model.stories) || [];
  const s = ss.find(x => x.name === story);
  if (!s) return [story];
  const master = s.master ? s.name : (s.similar_to || "");
  if (!master) return [story];
  return ss.filter(t => t.name === master || (t.similar_to || "") === master).map(t => t.name);
}

/** Stories a plan action on `story` applies to under `scope`. */
export function scopeStories(model, story, scope) {
  if (scope === "all") return ((model && model.stories) || []).map(s => s.name);
  if (scope === "similar") return similarGroup(model, story);
  return [story];
}

/** Matching object of `obj` (a member / shell / link) on story `to`. */
function counterpart(model, type, obj, from, to) {
  const sf = model.stories.find(s => s.name === from), stt = model.stories.find(s => s.name === to);
  if (!sf || !stt) return null;
  const dz = stt.elevation - sf.elevation;
  const sameP = (p, q) => near(p[0], q[0]) && near(p[1], q[1]) && near(p[2] + dz, q[2]);
  if (type === "member") {
    return model.members.find(m => m.story === to && m.kind === obj.kind &&
      ((sameP(obj.pi, m.pi) && sameP(obj.pj, m.pj)) || (m.kind !== "column" && sameP(obj.pi, m.pj) && sameP(obj.pj, m.pi)))) || null;
  }
  if (type === "shell") {
    const key = cs => cs.map(c => `${c[0].toFixed(5)},${c[1].toFixed(5)}`).sort().join(";");
    const k0 = key(obj.corners);
    return (model.shells || []).find(r => r.story === to && r.kind === obj.kind && r.corners.length === obj.corners.length &&
      key(r.corners) === k0 && r.corners.every(c => obj.corners.some(o => sameP(o, c)))) || null;
  }
  if (type === "link") {
    return (model.links || []).find(l => sameP(obj.pi, l.pi) && sameP(obj.pj, l.pj)) || null;
  }
  return null;
}

function storyOfLink(model, lk) {
  return (model.stories || []).find(s => lk.pi[2] > s.elevation - s.height - 1e-6 && lk.pi[2] <= s.elevation + 1e-6 &&
    lk.pj[2] > s.elevation - s.height - 1e-6 && lk.pj[2] <= s.elevation + 1e-6) || null;
}

/** Refs + their counterparts on the other stories of the scope. */
export function expandRefs(model, refs, scope) {
  if (!model || scope === "one") return refs.slice();
  const out = [], seen = new Set();
  const push = r => { const k = `${r.type}:${r.uid}`; if (!seen.has(k)) { seen.add(k); out.push(r); } };
  for (const r of refs) {
    push(r);
    let obj = null, from = null;
    if (r.type === "member") { obj = model.members.find(m => m.uid === r.uid); from = obj && obj.story; }
    else if (r.type === "shell") { obj = (model.shells || []).find(s => s.uid === r.uid); from = obj && obj.story; }
    else if (r.type === "link") { obj = (model.links || []).find(l => l.uid === r.uid); const s = obj && storyOfLink(model, obj); from = s && s.name; }
    if (!obj || !from) continue;
    for (const st of scopeStories(model, from, scope)) {
      if (st === from) continue;
      const c = counterpart(model, r.type, obj, from, st);
      if (c) push({ type: r.type, uid: c.uid });
    }
  }
  return out;
}

/** Drop dangling / invalid master-similar references (in place). */
export function sanitizeStories(model) {
  const ss = (model && model.stories) || [];
  const masters = new Set(ss.filter(s => s.master).map(s => s.name));
  for (const s of ss) {
    if (s.master === false) delete s.master;
    if (s.similar_to !== undefined && (!s.similar_to || s.master || !masters.has(s.similar_to) || s.similar_to === s.name)) delete s.similar_to;
  }
}

/* ------------------------------------------------------------ Story Data columns (hook) */
const prevNames = new WeakMap();
/** Called by openGridStories after each render of the story table. */
export function w8StoryTable(table, D, touch) {
  const ss = D.stories || [];
  // follow renames done by the dialog (story objects keep their identity)
  const ren = {};
  for (const s of ss) { const p = prevNames.get(s); if (p && p !== s.name) ren[p] = s.name; }
  if (Object.keys(ren).length) for (const s of ss) if (s.similar_to && ren[s.similar_to]) s.similar_to = ren[s.similar_to];
  sanitizeStories(D);
  for (const s of ss) prevNames.set(s, s.name);
  const head = table.querySelector("thead tr");
  if (!head || head.querySelector(".w8-sim-h")) return;
  const ths = head.children;
  const after = ths[2] || null;
  const h1 = ssEl("th", { class: "txt w8-sim-h", title: "ETABS Master Story: drawing / assigning on one story of a similar group repeats on the others" }, ["Master Story"]);
  const h2 = ssEl("th", { class: "txt w8-sim-h", title: "Similar To: the master story this story follows (Similar Stories scope)" }, ["Similar To"]);
  head.insertBefore(h2, after ? after.nextSibling : null);
  head.insertBefore(h1, h2);
  const rows = [...table.querySelectorAll("tbody tr")];
  rows.forEach((tr, i) => {
    const tds = tr.children;
    const ref = tds[2] ? tds[2].nextSibling : null;
    const s = i < ss.length ? ss[ss.length - 1 - i] : null;
    if (!s) { tr.insertBefore(ssEl("td"), ref); tr.insertBefore(ssEl("td"), ref); return; }
    const cb = ssEl("input", { type: "checkbox", class: "w8-master", "aria-label": `Master story ${s.name}`, "data-story": s.name });
    cb.checked = !!s.master;
    cb.addEventListener("change", () => {
      if (cb.checked) { s.master = true; delete s.similar_to; }
      else { delete s.master; for (const t of ss) if (t.similar_to === s.name) delete t.similar_to; }
      touch();
    });
    const sel = ssEl("select", { class: "dk-select w8-similar", "aria-label": `Similar to (${s.name})`, "data-story": s.name });
    sel.appendChild(ssEl("option", { value: "" }, ["None"]));
    for (const t of ss) if (t.master && t.name !== s.name) sel.appendChild(ssEl("option", { value: t.name, selected: s.similar_to === t.name }, [t.name]));
    sel.disabled = !!s.master;
    sel.addEventListener("change", () => {
      if (sel.value) s.similar_to = sel.value; else delete s.similar_to;
      touch();
    });
    tr.insertBefore(ssEl("td", { class: "txt" }, [cb]), ref);
    tr.insertBefore(ssEl("td", {}, [sel]), ref);
  });
}

/* ------------------------------------------------------------ scope selector + plan wrappers */
export function initW8Stories(sky) {
  const S = sky.store, pe = sky.planEditor;
  S.storyScope = S.applyAll ? "all" : "one";
  const scope = () => S.storyScope || (S.applyAll ? "all" : "one");
  const tg = document.getElementById("applyToggle");
  const setScope = v => {
    S.storyScope = v; S.applyAll = v === "all";
    if (tg) tg.querySelectorAll(".seg-btn").forEach(x => x.classList.toggle("is-active", x.dataset.apply === v));
    if (sky.syncStoryBadges) sky.syncStoryBadges();
    badge();
  };
  if (tg && !tg.querySelector('[data-apply="similar"]')) {
    const b = ssEl("button", { class: "seg-btn", "data-apply": "similar", id: "w8ScopeSimilar",
      title: "Similar Stories: draw / select / assign / edit on every story similar to this one (Story Data › Master Story / Similar To)" }, ["Similar"]);
    const all = tg.querySelector('[data-apply="all"]');
    tg.insertBefore(b, all);
    b.addEventListener("click", () => setScope("similar"));
    tg.querySelectorAll('.seg-btn:not([data-apply="similar"])').forEach(x => x.addEventListener("click", () => setScope(x.dataset.apply === "all" ? "all" : "one")));
  }
  const badgeEl = document.getElementById("planStoryBadge");
  let busy = false;
  function badge() {
    if (!badgeEl || busy || scope() !== "similar" || S.view === "elev") return;
    const g = similarGroup(S.model, S.story);
    if (badgeEl.querySelector(".w8-sim-badge")) return;
    busy = true;
    badgeEl.appendChild(ssEl("span", { class: "w8-sim-badge", style: "color:var(--amber)" }, [` · similar stories (${g.length})`]));
    busy = false;
  }
  if (badgeEl) new MutationObserver(() => badge()).observe(badgeEl, { childList: true });

  if (pe && pe.opts) {
    const origDraw = pe.opts.onDraw, origSel = pe.opts.onSelect, origErase = pe.opts.onErase;
    pe.opts.onDraw = (tool, payload) => {
      if (scope() !== "similar" || tool === "spring" || tool === "linespring") return origDraw(tool, payload);
      const keep = S.story, group = similarGroup(S.model, keep);
      try { for (const st of group) { S.story = st; origDraw(tool, payload); } }
      finally { S.story = keep; }
      if (sky.renderProps) sky.renderProps();
    };
    pe.opts.onSelect = (refs, additive) => {
      const sc = scope();
      if (sc === "one" || !refs.length) return origSel(refs, additive);
      const exp = expandRefs(S.model, refs, sc);
      if (additive && refs.length === 1) {
        const isSel = r => (S.selection || []).some(x => x.type === r.type && x.uid === r.uid);
        const was = isSel(refs[0]);
        for (const r of exp) if (isSel(r) === was) origSel([r], true);
        return;
      }
      return origSel(exp, additive);
    };
    pe.opts.onErase = ref => {
      const sc = scope();
      if (sc === "one") return origErase(ref);
      for (const r of expandRefs(S.model, [ref], sc)) origErase(r);
    };
  }

  sky.w8stories = { similarGroup: st => similarGroup(S.model, st), scopeStories: (st, sc) => scopeStories(S.model, st, sc || scope()),
    expandRefs: (refs, sc) => expandRefs(S.model, refs, sc || scope()), setScope, scope, sanitize: () => sanitizeStories(S.model) };
}
