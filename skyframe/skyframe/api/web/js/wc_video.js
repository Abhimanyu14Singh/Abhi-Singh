/* SkyFrame — Wave C: animation video export (ETABS File › Create Video).

     Display › Export Animation Video…              act dis-wc-video

   Records the 3D view canvas with canvas.captureStream(fps) + MediaRecorder
   into a WebM file and saves it through the usual download path (an <a
   download> blob link — the browser and the pywebview desktop window both
   save it). Sources:

     deformed   the deformed shape of a case / combo, cycled
                sin(2πt / cycle period) at the display scale;
     mode       a mode shape (the viewer's own 1.8 s mode animation);
     th         the time-history response: the recorded deformed-shape
                frames (Load Case Data › Time History › Output ›
                animation frames — results.th_cases[name].frames) or, without
                them, the story displacement histories spread over each
                story's nodes (rigid-diaphragm translation, interpolated
                linearly in z). TH time is mapped onto the video duration.

   The WebM duration element is written into the file header after
   recording (MediaRecorder leaves it out, so players would show no length).
   The 3D view must be visible while recording (the dialog closes first).
   Nothing in the model changes. */

import wcvU from "./units.js";
import { dkForm as wvForm, dkGroup as wvGroup, dkDialog as wvDialog, dkNote as wvNote } from "./dlgkit.js";

const MIMES = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"];
export const wcVideoState = { source: "deformed", case: null, mode: 0, th: null, fps: 30, duration: 4, cycle: 2, scale: 1 };

/* ---------------------------------------------------------------- WebM */
function readVint(u8, pos, keepMarker) {
  const b = u8[pos];
  let len = 1, mask = 0x80;
  while (len <= 8 && !(b & mask)) { len++; mask >>= 1; }
  if (len > 8) return null;
  let v = keepMarker ? b : (b & (mask - 1));
  let allOnes = (b & (mask - 1)) === mask - 1;
  for (let i = 1; i < len; i++) { v = v * 256 + u8[pos + i]; if (u8[pos + i] !== 0xff) allOnes = false; }
  return { v, len, unknown: !keepMarker && allOnes };
}
function vint8(n) {                    // 8-byte EBML size
  const out = new Uint8Array(8);
  out[0] = 0x01;
  for (let i = 7; i >= 1; i--) { out[i] = n % 256; n = Math.floor(n / 256); }
  return out;
}
/** Write the Segment › Info › Duration element (ms → TimecodeScale units). */
export async function wcFixWebmDuration(blob, ms) {
  const u8 = new Uint8Array(await blob.arrayBuffer());
  try {
    let pos = 0;
    const id0 = readVint(u8, pos, true);
    if (!id0 || id0.v !== 0x1A45DFA3) return { blob, fixed: false };
    const s0 = readVint(u8, pos + id0.len, false);
    pos += id0.len + s0.len + s0.v;
    const segId = readVint(u8, pos, true);
    if (!segId || segId.v !== 0x18538067) return { blob, fixed: false };
    const segSize = readVint(u8, pos + segId.len, false);
    let p = pos + segId.len + segSize.len;
    while (p < u8.length) {
      const id = readVint(u8, p, true), sz = readVint(u8, p + id.len, false);
      if (!id || !sz) break;
      const bodyStart = p + id.len + sz.len;
      if (id.v === 0x1549A966) {                        // Info
        const bodyEnd = bodyStart + sz.v;
        let q = bodyStart, scale = 1e6, durAt = null, durLen = 0;
        while (q < bodyEnd) {
          const cid = readVint(u8, q, true), cs = readVint(u8, q + cid.len, false);
          const cb = q + cid.len + cs.len;
          if (cid.v === 0x2AD7B1) { scale = 0; for (let i = 0; i < cs.v; i++) scale = scale * 256 + u8[cb + i]; }
          if (cid.v === 0x4489) { durAt = cb; durLen = cs.v; }
          q = cb + cs.v;
        }
        const val = ms * 1e6 / (scale || 1e6);
        if (durAt != null) {
          const dv = new DataView(u8.buffer);
          if (durLen === 8) dv.setFloat64(durAt, val); else dv.setFloat32(durAt, val);
          return { blob: new Blob([u8], { type: blob.type }), fixed: true };
        }
        const el = new Uint8Array(11);
        el.set([0x44, 0x89, 0x88]);
        new DataView(el.buffer).setFloat64(3, val);
        const body = u8.subarray(bodyStart, bodyEnd);
        const parts = [u8.subarray(0, p), u8.subarray(p, p + id.len), vint8(sz.v + el.length), body, el, u8.subarray(bodyEnd)];
        if (!segSize.unknown) {                          // keep the Segment size consistent
          const delta = (8 - sz.len) + el.length;
          const sp = pos + segId.len;
          const nw = vint8(segSize.v + delta);
          parts.splice(0, 1, u8.subarray(0, sp), nw, u8.subarray(sp + segSize.len, p));
        }
        return { blob: new Blob(parts, { type: blob.type }), fixed: true };
      }
      if (sz.unknown) break;
      p = bodyStart + sz.v;
    }
  } catch { /* leave the file as recorded */ }
  return { blob, fixed: false };
}

/* ---------------------------------------------------------------- frames */
/** Displacement maps of a TH case: [{t, disp}] (recorded frames or the
    story-translation approximation). */
export function wcThFrames(r, name) {
  const th = (r && r.th_cases || {})[name];
  if (!th) return { frames: [], exact: false };
  if (th.frames && th.frames.node_disp) {
    const fr = th.frames;
    return { exact: true, frames: fr.steps.map((s, i) => ({ t: fr.t[i], disp: fr.node_disp[String(s)] || {} })) };
  }
  const order = (r.story_order || []).filter(s => r.story_elev && r.story_elev[s] != null)
    .sort((a, b) => r.story_elev[a] - r.story_elev[b]);
  if (!order.length || !th.t) return { frames: [], exact: false };
  const zs = order.map(s => +r.story_elev[s]);
  const zs0 = Object.values(r.nodes || {}).map(p => p[2]);
  const zBase = zs0.length ? Math.min(...zs0) : 0;
  const nodes = Object.entries(r.nodes || {});
  const weights = nodes.map(([t, p]) => {          // (index below, fraction)
    const z = p[2];
    if (z <= zBase + 1e-9) return [t, -1, 0];
    let k = 0;
    while (k < zs.length && zs[k] < z - 1e-9) k++;
    if (k >= zs.length) return [t, zs.length - 1, 1];
    const z0 = k ? zs[k - 1] : zBase, f = (z - z0) / ((zs[k] - z0) || 1);
    return [t, k, f];
  });
  const n = th.t.length, stride = Math.max(1, Math.floor(n / 240));
  const frames = [];
  for (let i = stride - 1; i < n; i += stride) {
    const ux = order.map(s => (th.story_ux[s] || [])[i] || 0), uy = order.map(s => (th.story_uy[s] || [])[i] || 0);
    const disp = {};
    for (const [t, k, f] of weights) {
      if (k < 0) { disp[t] = [0, 0, 0, 0, 0, 0]; continue; }
      const lo = k ? [ux[k - 1], uy[k - 1]] : [0, 0];
      disp[t] = [lo[0] + f * (ux[k] - lo[0]), lo[1] + f * (uy[k] - lo[1]), 0, 0, 0, 0];
    }
    frames.push({ t: th.t[i], disp });
  }
  return { frames, exact: false };
}

function sourceOptions(r) {
  const deformed = [...Object.keys(r.cases || {}), ...Object.keys(r.combos || {})];
  const modes = ((r.modal || {}).periods || []).map((T, i) => [i, `Mode ${i + 1} — T = ${wcvU.fmt("period", T, 3)} ${wcvU.label("period")}`]);
  const th = Object.keys(r.th_cases || {});
  return { deformed, modes, th };
}

/* ---------------------------------------------------------------- record */
export function wcRecord(sky, o) {
  const S = sky.store, r = S.results, viewer = sky.viewer;
  return new Promise((resolve, reject) => {
    if (!r) return reject(new Error("Run the analysis first."));
    if (typeof MediaRecorder === "undefined" || !viewer || !viewer.canvas || !viewer.canvas.captureStream)
      return reject(new Error("Video recording (MediaRecorder / canvas.captureStream) is not supported by this browser engine."));
    const fps = Math.max(1, Math.min(60, Math.round(+o.fps || 30)));
    const dur = Math.max(0.2, +o.duration || 4);
    const mime = MIMES.find(m => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m)) || "video/webm";
    sky.setMode && sky.setMode("analyze");
    sky.switchTab && sky.switchTab("view3d");
    const prevCase = viewer.grpCaseData;
    let thFr = null, K = 1, label = "";
    if (o.source === "th") {
      thFr = wcThFrames(r, o.th);
      if (!thFr.frames.length) return reject(new Error(`Time-history case “${o.th}” has no recorded response.`));
      let peak = null, pm = 0;
      for (const f of thFr.frames) for (const d of Object.values(f.disp)) {
        const m = Math.max(Math.abs(d[0]), Math.abs(d[1]), Math.abs(d[2]));
        if (m > pm) { pm = m; peak = f.disp; }
      }
      K = (peak ? viewer.autoScale(peak) : 1) * (+o.scale || 1);
      viewer.grpCaseData = (name, rr) => name === "__wcvideo__" ? cur.data : (prevCase ? prevCase(name, rr) : null);
      viewer.setOverlay({ deformed: true, modal: false, buckling: false, caseName: "__wcvideo__", scaleMult: 1 });
    } else if (o.source === "mode") {
      viewer.setOverlay({ deformed: false, modal: true, buckling: false, modeIndex: +o.mode || 0, scaleMult: +o.scale || 1 });
      label = `Mode ${(+o.mode || 0) + 1}`;
    } else {
      viewer.setOverlay({ deformed: true, modal: false, buckling: false, caseName: o.case, scaleMult: +o.scale || 1 });
      label = `${o.case} — deformed shape`;
    }
    const cur = { data: { node_disp: {} }, t: 0 };
    const stream = viewer.canvas.captureStream(fps);
    let rec;
    try { rec = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 4e6 }); }
    catch (e) { cleanup(); return reject(e); }
    const chunks = [];
    rec.ondataavailable = e => { if (e.data && e.data.size) chunks.push(e.data); };
    const t0 = performance.now();
    let raf = 0, stopped = false;
    function cleanup() {
      cancelAnimationFrame(raf);
      viewer.grpCaseData = prevCase;
      state.recording = false;
      state.caption = null;
      sky.syncOverlayUI && sky.syncOverlayUI();
      viewer._dirty = true;
    }
    function tick() {
      const el = (performance.now() - t0) / 1000;
      const u = Math.min(1, el / dur);
      if (o.source === "th") {
        const fr = thFr.frames, x = u * (fr.length - 1), i = Math.min(fr.length - 2, Math.floor(x)), f = fr.length > 1 ? x - i : 0;
        const A = fr[Math.max(0, i)], B = fr[Math.min(fr.length - 1, i + 1)];
        const disp = {};
        for (const [t, a] of Object.entries(A.disp)) { const b = B.disp[t] || a; disp[t] = a.map((v, k) => v + f * ((b[k] || 0) - v)); }
        cur.data = { node_disp: disp };
        cur.t = A.t + f * (B.t - A.t);
        const auto = viewer.autoScale(disp);
        viewer.overlay.scaleMult = K / (auto || 1);
        label = `${o.th} — t = ${wcvU.fmt("period", cur.t, 2)} ${wcvU.label("period")}`;
      } else if (o.source === "deformed") {
        viewer.overlay.scaleMult = (+o.scale || 1) * Math.sin(2 * Math.PI * el / Math.max(0.05, +o.cycle || 2));
      }
      state.label = label;
      viewer._dirty = true;
      if (el >= dur) { if (!stopped) { stopped = true; rec.stop(); } return; }
      raf = requestAnimationFrame(tick);
    }
    rec.onstop = async () => {
      const ms = performance.now() - t0;
      cleanup();
      stream.getTracks().forEach(t => t.stop());
      const raw = new Blob(chunks, { type: mime.split(";")[0] });
      const fx = await wcFixWebmDuration(raw, dur * 1000);
      const out = { blob: fx.blob, size: fx.blob.size, mime: raw.type, duration_ms: dur * 1000, wall_ms: ms, fps,
        fixed: fx.fixed, exact: thFr ? thFr.exact : true, source: o.source };
      state.last = out;
      resolve(out);
    };
    state.recording = true;
    state.caption = (ctx, v) => {
      const w = v.canvas.clientWidth, h = v.canvas.clientHeight;
      ctx.save();
      ctx.font = "600 12px -apple-system, 'Segoe UI', sans-serif";
      ctx.textAlign = "right"; ctx.textBaseline = "bottom";
      ctx.fillStyle = "rgba(232,237,243,0.92)";
      ctx.fillText(state.label || "", w - 12, h - 10);
      ctx.restore();
    };
    rec.start(250);
    raf = requestAnimationFrame(tick);
  });
}

export function wcSaveVideo(sky, res, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(res.blob);
  a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 8000);
}

const state = { recording: false, last: null, caption: null, label: "" };

/* ---------------------------------------------------------------- dialog */
export function wcOpenVideo(sky) {
  const r = sky.store.results;
  const opts = r ? sourceOptions(r) : { deformed: [], modes: [], th: [] };
  const st = { ...wcVideoState };
  if (!opts.deformed.includes(st.case)) st.case = opts.deformed.includes(sky.store.caseName) ? sky.store.caseName : (opts.deformed[0] || null);
  if (!opts.th.includes(st.th)) st.th = opts.th[0] || null;
  if (!(st.mode < opts.modes.length)) st.mode = 0;
  const F = wvForm();
  const thNote = wvNote("");
  const syncNote = () => {
    const fr = st.th && r ? wcThFrames(r, st.th) : null;
    thNote.innerHTML = st.source !== "th" ? "" : !st.th ? "No time-history results in this run." :
      fr.exact ? `${fr.frames.length} recorded deformed-shape frames.` :
        "No recorded frames: story displacements spread over each story (set <i>Animation frames</i> in the TH case's Output group for the exact shape).";
  };
  const body = [
    wvGroup("Animation", [
      F.radio(st, "source", [["deformed", "Deformed shape (cyclic)"], ["mode", "Mode shape"], ["th", "Time-history response"]],
        { label: "Animate", idPrefix: "wcVidSrc-", onChange: syncNote }),
      F.select(st, "case", opts.deformed.map(n => [n, n]), { label: "Case / combo", id: "wcVidCase" }),
      F.select(st, "mode", opts.modes, { label: "Mode", id: "wcVidMode" }),
      F.select(st, "th", opts.th.map(n => [n, n]), { label: "Time-history case", id: "wcVidTh", onChange: syncNote }),
      thNote,
    ]),
    wvGroup("Video", [
      F.num(st, "fps", { label: "Frame rate", int: true, min: 1, max: 60, unit: "fps", id: "wcVidFps" }),
      F.num(st, "duration", { label: "Duration", kind: "period", gt: 0, max: 120, id: "wcVidDur" }),
      F.num(st, "cycle", { label: "Cycle period (deformed)", kind: "period", gt: 0, id: "wcVidCycle" }),
      F.num(st, "scale", { label: "Scale multiplier", gt: 0, id: "wcVidScale" }),
    ]),
    wvNote("WebM video of the 3D view (current camera and display options); saved as a download."),
  ];
  syncNote();
  return wvDialog({
    id: "wcVideoDlg", title: "Export Animation Video", size: "sm", form: F, body, okLabel: "Record",
    intro: "Animate the deformed shape, a mode shape or the time-history response to a video file.",
    onOk: () => {
      if (!r) return "Run the analysis first.";
      if (st.source === "deformed" && !st.case) return "No case to animate.";
      if (st.source === "mode" && !opts.modes.length) return "No modes in these results.";
      if (st.source === "th" && !st.th) return "No time-history results in this run.";
      Object.assign(wcVideoState, st);
      const toast = sky.toast || (() => {});
      toast("Recording video", `${wcvU.fmt("period", st.duration, 1)} ${wcvU.label("period")} at ${st.fps} fps…`);
      setTimeout(() => wcRecord(sky, st).then(res => {
        const base = String((sky.store.model && sky.store.model.name) || "model").replace(/[^\w.-]+/g, "_");
        wcSaveVideo(sky, res, `${base}_${st.source}.webm`);
        toast("Video saved", `${(res.size / 1024).toFixed(0)} KB WebM`);
      }).catch(e => toast("Video export failed", e.message || String(e), "error", 8000)), 60);
      return true;
    },
  });
}

export function initWcVideo(sky) {
  sky.wcVideo = Object.assign(state, {          // state.caption / .last / .recording live here
    open: () => wcOpenVideo(sky), record: o => wcRecord(sky, o), fixDuration: wcFixWebmDuration,
    thFrames: wcThFrames, settings: wcVideoState,
  });
}
