/* SkyFrame Wave H — main ↔ pop-out window sync protocol (pure; node-testable).

   Every message is one envelope

     { v: 1, ch, id, seq, from, to, type, body }

     ch    channel (one per main-window session, random)
     id    unique message id (dedupe: a message may arrive over BOTH the
           BroadcastChannel and the server relay)
     seq   sender's running number
     from  "main" | window id ("w3")
     to    "*" | "main" | window id

   Types (body fields; ✓ = sent by main / ◀ = sent by a pop-out):

     hello    ◀ {win}                         a pop-out booted
     snap     ✓ {win, state, model, results, sel, g, rev, mode}
                                               full state for that pop-out
     model    ✓ {set: {key: json}, del: [key], rev}
                                               model edit in main (top-level keys)
     edit     ◀ {set, del, base, label}       model edit made in a pop-out
     sel      ✓◀ {sel}                        shared selection
     res      ✓◀ {g, win?, wres?}             results toolbar state (g = case /
                                               step / envelope) and the pop-out
                                               window's own results state
     results  ✓ {results}                     new analysis results
     cmd      ◀ {cmd: "undo"|"redo"|"run"}    command executed by main
     cam      ✓◀ {win, cam}                   linked-camera update
     dock     ◀ {win, state}                  pop-out docks back (then closes)
     ping     ◀ {win, rect}                   heartbeat + OS window rectangle
     bye      ✓ {}                            main window is closing → close
     close    ✓ {win}                         main closes that pop-out

   Model edits travel as per-top-level-key JSON strings (the same unit as the
   undo history, js/history.js), so drawing one beam sends only "members".
   CONTRACT "Windows, pop-outs and side-by-side compare (Wave H)". */

export const HS_VERSION = 1;
export const HS_TYPES = {
  hello: ["win"], snap: ["win", "model"], model: ["rev"], edit: [], sel: ["sel"], res: ["g"],
  results: [], cmd: ["cmd"], cam: ["win", "cam"], dock: ["win", "state"], ping: ["win"],
  bye: [], close: ["win"], welcome: ["win"],
};
export const HS_CMDS = ["undo", "redo"];
const ID_RE = /^(main|w\d{1,4})$/;

let hsCounter = 0;
/** A message envelope. */
export function hsMake(ch, from, type, body = {}, to = "*") {
  hsCounter = (hsCounter + 1) % 1e9;
  return { v: HS_VERSION, ch, id: `${from}-${Date.now().toString(36)}-${hsCounter.toString(36)}-${Math.random().toString(36).slice(2, 7)}`,
    seq: hsCounter, from, to, type, body };
}
/** "" when the envelope is valid for channel `ch`, else the reason. */
export function hsCheck(m, ch = null) {
  if (!m || typeof m !== "object") return "not an object";
  if (m.v !== HS_VERSION) return "wrong protocol version";
  if (ch != null && m.ch !== ch) return "other channel";
  if (typeof m.id !== "string" || !m.id) return "missing id";
  if (!ID_RE.test(m.from || "")) return "bad sender";
  if (!(m.to === "*" || ID_RE.test(m.to || ""))) return "bad recipient";
  const req = HS_TYPES[m.type];
  if (!req) return `unknown type ${m.type}`;
  if (!m.body || typeof m.body !== "object") return "missing body";
  for (const k of req) if (!(k in m.body)) return `${m.type}: missing ${k}`;
  if (m.type === "cmd" && !HS_CMDS.includes(m.body.cmd)) return `cmd: unknown ${m.body.cmd}`;
  if ((m.type === "model" || m.type === "edit") && (m.body.set != null && typeof m.body.set !== "object")) return `${m.type}: bad set`;
  return "";
}
/** Is message `m` for receiver `me`? (own messages are ignored) */
export const hsForMe = (m, me) => m.from !== me && (m.to === "*" || m.to === me);

/** Bounded set of seen message ids (dedupe across transports). */
export function hsSeen(limit = 2000) {
  const set = new Set(), order = [];
  return id => {
    if (set.has(id)) return true;
    set.add(id); order.push(id);
    if (order.length > limit) set.delete(order.shift());
    return false;
  };
}

/** Per-top-level-key JSON snapshot of a model (history.js unit). */
export function hsSnapshot(model) {
  const out = {};
  for (const [k, v] of Object.entries(model || {})) {
    if (k === "_mock_params" || v === undefined || typeof v === "function") continue;
    out[k] = JSON.stringify(v);
  }
  return out;
}
/** Changed keys between two snapshots: {set: {k: json}, del: [k]} or null when equal. */
export function hsDelta(prev, cur) {
  const set = {}, del = [];
  for (const [k, v] of Object.entries(cur)) if (prev[k] !== v) set[k] = v;
  for (const k of Object.keys(prev)) if (!(k in cur)) del.push(k);
  return Object.keys(set).length || del.length ? { set, del } : null;
}
/** Apply a delta to a model object in place; returns the changed keys. */
export function hsApply(model, delta) {
  const keys = [];
  for (const [k, v] of Object.entries((delta && delta.set) || {})) { model[k] = JSON.parse(v); keys.push(k); }
  for (const k of (delta && delta.del) || []) { delete model[k]; keys.push(k); }
  return keys;
}
/** Order-insensitive selection signature. */
export const hsSelSig = sel => (sel || []).map(r => `${r.type}:${r.uid}`).sort().join("|");
/** Results toolbar state signature. */
export const hsResSig = g => (g ? `${g.key}|${g.step}|${g.env}` : "");
/** A camera tuple signature (6 significant digits). */
export function hsCamSig(c) {
  if (!c) return "";
  const r = v => (Array.isArray(v) ? v.map(r).join(",") : Number.isFinite(v) ? (+v).toPrecision(6) : "");
  return Object.keys(c).sort().map(k => `${k}=${r(c[k])}`).join(";");
}
