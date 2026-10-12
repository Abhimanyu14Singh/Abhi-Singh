/* SkyFrame D4 — structural-system template geometry (JS port of
   skyframe/core/templates_sys.py builders).

   tplSysGeometry(id, p) -> { grid: {xs, ys}, stories: [{name, height,
   elevation, tower?}], members: [{uid, kind, section, pi, pj, story,
   releases, angle, tower?}], shells: [{uid, kind, behavior, section,
   corners, story, roof, pier?, spandrel?, one_way?, tower?}], links:
   [{uid, pi, pj}], supports: [[x, y, z]], towers, grid_systems, secs:
   {sectionName: {type: "steel", key} | {type: "rect", b, h}},
   shellSecs: {name: {t, mat: "steel"|"conc", deck?}}, active_dof?,
   diaphragm? }

   Used by the template browser thumbnails (js/tpl_library.js) and the
   ?mock=1 builder (js/mock_tpl_library.js). Section names of steel
   parameters are resolved by the caller (`steelName(key)`); geometry,
   uids, releases and labels follow the backend one-to-one. SI units. */

const r9 = v => Math.round(v * 1e9) / 1e9;
export const tplAlpha = i => { let s = ""; i += 1; while (i) { const r = (i - 1) % 26; s = String.fromCharCode(65 + r) + s; i = Math.floor((i - 1) / 26); } return s; };
const A = tplAlpha;
const lines = (n, s) => Array.from({ length: n + 1 }, (_, i) => r9(i * s));
const near = (a, b) => Math.abs(a - b) < 1e-9;
const between = (v, a, b, tol = 1e-6) => a - tol <= v && v <= b + tol;
const PINNED = "Mi,Mj";

export function tplBracedBays(n) { return [...new Set([Math.floor((n - 1) / 2), Math.floor(n / 2)])].sort((a, b) => a - b); }

export function tplOutriggerLevels(spec, n) {
  const out = new Set();
  for (let tok of String(spec).replace(/;/g, ",").split(",")) {
    tok = tok.trim().toLowerCase();
    if (!tok) continue;
    if (tok === "mid") out.add(Math.max(2, Math.floor((n + 1) / 2)));
    else if (tok === "top") out.add(n);
    else {
      const k = Number(tok);
      if (!Number.isInteger(k)) throw new Error(`outrigger: bad level '${tok}' (use story numbers, 'mid' or 'top')`);
      if (k < 2 || k > n) throw new Error(`outrigger: level ${k} must be in [2, ${n}]`);
      out.add(k);
    }
  }
  if (!out.size) throw new Error("outrigger: at least one outrigger level needed");
  return [...out].sort((a, b) => a - b);
}

function perimeterAngle(x, y, xs, ys) {
  const onX = near(y, ys[0]) || near(y, ys[ys.length - 1]);
  const onY = near(x, xs[0]) || near(x, xs[xs.length - 1]);
  if (onX && onY) return (near(x, xs[0]) === near(y, ys[0])) ? 0 : 90;
  return onY ? 90 : 0;
}

function makeCtx(id, p, steelName) {
  const g = { grid: null, stories: [], members: [], shells: [], links: [], supports: [], towers: [], grid_systems: null,
    secs: {}, shellSecs: {}, spandrel_labels: [] };
  const c = {
    g, p,
    steel(key) { const n = steelName(key); g.secs[n] = { type: "steel", key }; return n; },
    rect(name, b, h) { g.secs[name] = { type: "rect", b, h }; return name; },
    shellSec(name, t, mat = "conc", extra = {}) { g.shellSecs[name] = { t, mat, ...extra }; return name; },
    setStories(heights, names) { let z = 0; g.stories = heights.map((h, i) => { z = r9(z + h); return { name: names ? names[i] : `Story${i + 1}`, height: h, elevation: z }; }); },
    stories() { const h1 = p.first_story_height || p.story_height; c.setStories([h1, ...Array(p.stories - 1).fill(p.story_height)]); },
    grid(xs, ys) { g.grid = { xs: xs.map(r9), ys: ys.map(r9) }; },
    member(kind, section, a, b, story, uid, o = {}) {
      g.members.push({ uid, kind, section, pi: a.map(r9), pj: b.map(r9), story, releases: o.releases || "", angle: o.angle || 0, ...(o.tower ? { tower: o.tower } : {}) });
    },
    slab(section, corners, story, uid, roof, o = {}) {
      g.shells.push({ uid, kind: "slab", behavior: o.behavior || "membrane", section, corners: corners.map(q => q.map(r9)), story, roof: !!roof,
        mesh_size: o.mesh || 1, ...(o.oneWay ? { one_way: o.oneWay } : {}), ...(o.tower ? { tower: o.tower } : {}) });
    },
    wall(section, corners, story, uid, o = {}) {
      g.shells.push({ uid, kind: "wall", behavior: "shell", section, corners: corners.map(q => q.map(r9)), story, roof: false,
        mesh_size: o.mesh || c.wallMesh(), pier: o.pier || "", ...(o.spandrel ? { spandrel: o.spandrel } : {}) });
    },
    wallMesh() { if (p.mesh_size) return p.mesh_size; const h = Math.min(...g.stories.map(s => s.height)); return r9(Math.max(0.5, Math.min(h / 2, 2))); },
    isRoof(si) { return si === g.stories.length - 1; },
  };
  return c;
}

function deckSlabs(c, xs, ys, sec, skip) {
  c.g.stories.forEach((st, si) => {
    const z = st.elevation;
    for (let yi = 0; yi + 1 < ys.length; yi++) for (let xi = 0; xi + 1 < xs.length; xi++) {
      const [x0, x1, y0, y1] = [xs[xi], xs[xi + 1], ys[yi], ys[yi + 1]];
      if (skip && skip(x0, x1, y0, y1)) continue;
      c.slab(sec, [[x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z]], st.name, `F${si + 1}-${A(xi)}${yi + 1}`, c.isRoof(si));
    }
  });
}

function gridFrame(c, xs, ys, colOf, beamOf, { colKw, skipCol, skipBeam } = {}) {
  c.g.stories.forEach((st, si) => {
    const zt = st.elevation, zb = r9(st.elevation - st.height);
    ys.forEach((y, yi) => xs.forEach((x, xi) => {
      if (skipCol && skipCol(x, y)) return;
      c.member("column", colOf(x, y), [x, y, zb], [x, y, zt], st.name, `C${si + 1}-${A(xi)}${yi + 1}`, colKw ? colKw(x, y, si) : {});
    }));
    ys.forEach((y, yi) => { for (let xi = 0; xi + 1 < xs.length; xi++) {
      const a = xs[xi], b = xs[xi + 1];
      if (skipBeam && skipBeam("X", y, a, b)) continue;
      const [sec, rel] = beamOf("X", y, a, b);
      c.member("beam", sec, [a, y, zt], [b, y, zt], st.name, `BX${si + 1}-${A(xi)}${yi + 1}`, { releases: rel });
    } });
    xs.forEach((x, xi) => { for (let yi = 0; yi + 1 < ys.length; yi++) {
      const a = ys[yi], b = ys[yi + 1];
      if (skipBeam && skipBeam("Y", x, a, b)) continue;
      const [sec, rel] = beamOf("Y", x, a, b);
      c.member("beam", sec, [x, a, zt], [x, b, zt], st.name, `BY${si + 1}-${A(xi)}${yi + 1}`, { releases: rel });
    } });
  });
}

function coreBox(c, x0, x1, y0, y1, xs, ys, sec) {
  const gx = xs.filter(x => between(x, x0, x1)), gy = ys.filter(y => between(y, y0, y1));
  c.g.stories.forEach((st, si) => {
    const zt = st.elevation, zb = r9(st.elevation - st.height);
    let k = 0;
    for (const [pier, pts] of [["P1", gx.map(x => [x, y0])], ["P2", gy.map(y => [x1, y])], ["P3", gx.map(x => [x, y1])], ["P4", gy.map(y => [x0, y])]]) {
      for (let i = 0; i + 1 < pts.length; i++) {
        const [a, b] = [pts[i], pts[i + 1]];
        k++;
        c.wall(sec, [[a[0], a[1], zb], [b[0], b[1], zb], [b[0], b[1], zt], [a[0], a[1], zt]], st.name, `CW${si + 1}-${k}`, { pier });
      }
    }
  });
}

/* ------------------------------------------------------------ builders */
const B = {};

B.frame_2d = c => {
  const p = c.p, xs = lines(p.bays_x, p.bay_x);
  c.grid(xs, [0]); c.stories();
  const col = c.steel("column"), beam = c.steel("beam");
  c.g.stories.forEach((st, si) => {
    const zt = st.elevation, zb = r9(zt - st.height);
    xs.forEach((x, xi) => c.member("column", col, [x, 0, zb], [x, 0, zt], st.name, `C${si + 1}-${A(xi)}`));
    for (let xi = 0; xi + 1 < xs.length; xi++) c.member("beam", beam, [xs[xi], 0, zt], [xs[xi + 1], 0, zt], st.name, `B${si + 1}-${A(xi)}`);
  });
  c.g.active_dof = ["UX", "UZ", "RY"];
  c.g.beamLines = true;
};

B.steel_smf = c => {
  const p = c.p, xs = lines(p.bays_x, p.bay_x), ys = lines(p.bays_y, p.bay_y);
  c.grid(xs, ys); c.stories();
  const mfc = c.steel("mf_column"), mfb = c.steel("mf_beam"), gc = c.steel("gravity_column"), gb = c.steel("gravity_beam");
  const slab = c.shellSec("SLAB", p.slab);
  const perim = (x, y) => near(x, xs[0]) || near(x, xs[xs.length - 1]) || near(y, ys[0]) || near(y, ys[ys.length - 1]);
  gridFrame(c, xs, ys, (x, y) => perim(x, y) ? mfc : gc, (axis, v) => {
    const e = axis === "X" ? ys : xs;
    return (near(v, e[0]) || near(v, e[e.length - 1])) ? [mfb, ""] : [gb, PINNED];
  }, { colKw: (x, y, si) => perim(x, y) ? { angle: perimeterAngle(x, y, xs, ys) } : (si === 0 ? { releases: "Mi" } : {}) });
  deckSlabs(c, xs, ys, slab);
};

function braced(c, ebf) {
  const p = c.p, xs = lines(p.bays_x, p.bay_x), ys = lines(p.bays_y, p.bay_y);
  c.grid(xs, ys); c.stories();
  const col = c.steel("column"), beam = c.steel("beam"), br = c.steel("brace");
  const slab = c.shellSec("SLAB", p.slab);
  const bx = tplBracedBays(p.bays_x), by = tplBracedBays(p.bays_y);
  const chevron = ebf || p.brace_type === "chevron";
  const e = ebf ? p.link_length : 0;
  const isBraced = (axis, v, a) => {
    const [ln, bays, grid] = axis === "X" ? [ys, bx, xs] : [xs, by, ys];
    if (!(near(v, ln[0]) || near(v, ln[ln.length - 1]))) return false;
    return bays.some(i => near(a, grid[i]));
  };
  gridFrame(c, xs, ys, () => col, () => [beam, PINNED], {
    colKw: (x, y, si) => ({ ...(si === 0 ? { releases: "Mi" } : {}), angle: perimeterAngle(x, y, xs, ys) }),
    skipBeam: (axis, v, a) => chevron && isBraced(axis, v, a),
  });
  c.g.stories.forEach((st, si) => {
    const zt = st.elevation, zb = r9(zt - st.height);
    for (const [axis, ln, bays, grid] of [["X", ys, bx, xs], ["Y", xs, by, ys]]) {
      for (const li of [0, ln.length - 1]) {
        const v = ln[li];
        for (const bi of bays) {
          const a = grid[bi], b = grid[bi + 1];
          const P = (u, z) => axis === "X" ? [u, v, z] : [v, u, z];
          const tag = `${axis}${si + 1}-${axis === "X" ? A(bi) : bi + 1}${li + 1}`;
          if (chevron) {
            const mid = 0.5 * (a + b), l0 = mid - e / 2, l1 = mid + e / 2;
            if (ebf) {
              c.member("beam", beam, P(a, zt), P(l0, zt), st.name, `BB${tag}a`, { releases: "Mi" });
              c.member("beam", beam, P(l0, zt), P(l1, zt), st.name, `LK${tag}`);
              c.member("beam", beam, P(l1, zt), P(b, zt), st.name, `BB${tag}b`, { releases: "Mj" });
            } else {
              c.member("beam", beam, P(a, zt), P(mid, zt), st.name, `BB${tag}a`, { releases: "Mi" });
              c.member("beam", beam, P(mid, zt), P(b, zt), st.name, `BB${tag}b`, { releases: "Mj" });
            }
            c.member("brace", br, P(a, zb), P(l0, zt), st.name, `D${tag}a`, { releases: PINNED });
            c.member("brace", br, P(b, zb), P(l1, zt), st.name, `D${tag}b`, { releases: PINNED });
          } else {
            c.member("brace", br, P(a, zb), P(b, zt), st.name, `D${tag}a`, { releases: PINNED });
            c.member("brace", br, P(b, zb), P(a, zt), st.name, `D${tag}b`, { releases: PINNED });
          }
        }
      }
    }
  });
  deckSlabs(c, xs, ys, slab);
}
B.steel_cbf = c => braced(c, false);
B.steel_ebf = c => braced(c, true);

export function tplDeckSection(tc, hr) {
  return { type: "deck_filled", slab_depth: tc, rib_depth: hr, rib_width_top: 0.1778, rib_width_bottom: 0.1270, rib_spacing: 0.3048,
    deck_thickness: 0.000912, deck_unit_weight: 0.10, rib_dir: 1 };
}

B.steel_deck = c => {
  const p = c.p, xs = lines(p.bays_x, p.bay_x), ys = lines(p.bays_y, p.bay_y);
  c.grid(xs, ys); c.stories();
  const col = c.steel("column"), gir = c.steel("girder"), inf = c.steel("infill");
  c.shellSec("DECK", 0, "conc", { deck: tplDeckSection(p.deck_slab, p.deck_rib) });
  const n = p.infill_beams, alongY = p.infill_dir === "Y", allmf = p.moment_frames === "all";
  const perimLine = (axis, v) => { const ln = axis === "X" ? ys : xs; return near(v, ln[0]) || near(v, ln[ln.length - 1]); };
  c.g.stories.forEach((st, si) => {
    const zt = st.elevation, zb = r9(zt - st.height);
    ys.forEach((y, yi) => xs.forEach((x, xi) => c.member("column", col, [x, y, zb], [x, y, zt], st.name, `C${si + 1}-${A(xi)}${yi + 1}`,
      { angle: allmf ? 0 : perimeterAngle(x, y, xs, ys) })));
    for (const [axis, ln, grid] of [["X", ys, xs], ["Y", xs, ys]]) {
      const split = ((axis === "X") === alongY) ? n : 0;
      ln.forEach((v, li) => {
        const pinnedEnds = !(allmf || perimLine(axis, v));
        for (let gi = 0; gi + 1 < grid.length; gi++) {
          const a = grid[gi], b = grid[gi + 1];
          const pts = Array.from({ length: split + 2 }, (_, k) => a + (b - a) * k / (split + 1));
          for (let k = 0; k + 1 < pts.length; k++) {
            const rel = [];
            if (pinnedEnds && k === 0) rel.push("Mi");
            if (pinnedEnds && k === pts.length - 2) rel.push("Mj");
            const P = u => axis === "X" ? [u, v, zt] : [v, u, zt];
            c.member("beam", gir, P(pts[k]), P(pts[k + 1]), st.name, `G${axis}${si + 1}-${axis === "X" ? A(gi) : gi + 1}${li + 1}-${k + 1}`, { releases: rel.join(",") });
          }
        }
      });
    }
    for (let yi = 0; yi + 1 < ys.length; yi++) for (let xi = 0; xi + 1 < xs.length; xi++) {
      const [x0, x1, y0, y1] = [xs[xi], xs[xi + 1], ys[yi], ys[yi + 1]];
      if (alongY) {
        const cuts = Array.from({ length: n + 2 }, (_, k) => x0 + (x1 - x0) * k / (n + 1));
        for (let k = 1; k <= n; k++) c.member("beam", inf, [cuts[k], y0, zt], [cuts[k], y1, zt], st.name, `IB${si + 1}-${A(xi)}${yi + 1}-${k}`, { releases: PINNED });
        for (let k = 0; k <= n; k++) c.slab("DECK", [[cuts[k], y0, zt], [cuts[k + 1], y0, zt], [cuts[k + 1], y1, zt], [cuts[k], y1, zt]], st.name, `F${si + 1}-${A(xi)}${yi + 1}-${k + 1}`, c.isRoof(si), { oneWay: 1 });
      } else {
        const cuts = Array.from({ length: n + 2 }, (_, k) => y0 + (y1 - y0) * k / (n + 1));
        for (let k = 1; k <= n; k++) c.member("beam", inf, [x0, cuts[k], zt], [x1, cuts[k], zt], st.name, `IB${si + 1}-${A(xi)}${yi + 1}-${k}`, { releases: PINNED });
        for (let k = 0; k <= n; k++) c.slab("DECK", [[x0, cuts[k], zt], [x0, cuts[k + 1], zt], [x1, cuts[k + 1], zt], [x1, cuts[k], zt]], st.name, `F${si + 1}-${A(xi)}${yi + 1}-${k + 1}`, c.isRoof(si), { oneWay: 1 });
      }
    }
  });
};

B.flat_slab_perimeter = c => {
  const p = c.p, xs = lines(p.bays_x, p.bay_x), ys = lines(p.bays_y, p.bay_y);
  c.grid(xs, ys); c.stories();
  const col = c.rect("COL", p.column, p.column), pb = c.rect("PBEAM", p.beam_b, p.beam_h), slab = c.shellSec("SLAB", p.slab);
  const perim = (axis, v) => { const ln = axis === "X" ? ys : xs; return near(v, ln[0]) || near(v, ln[ln.length - 1]); };
  gridFrame(c, xs, ys, () => col, () => [pb, ""], { skipBeam: (axis, v) => !perim(axis, v) });
  const mesh = p.mesh_size || r9(Math.max(0.5, Math.min(p.bay_x, p.bay_y) / 4));
  c.g.stories.forEach((st, si) => {
    const z = st.elevation;
    for (let yi = 0; yi + 1 < ys.length; yi++) for (let xi = 0; xi + 1 < xs.length; xi++) {
      const [x0, x1, y0, y1] = [xs[xi], xs[xi + 1], ys[yi], ys[yi + 1]];
      c.slab(slab, [[x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z]], st.name, `F${si + 1}-${A(xi)}${yi + 1}`, c.isRoof(si), { behavior: "shell", mesh });
    }
  });
};

B.rc_dual_core = c => {
  const p = c.p, xs = lines(p.bays_x, p.bay_x), ys = lines(p.bays_y, p.bay_y);
  c.grid(xs, ys); c.stories();
  const col = c.rect("COL", p.column, p.column), bm = c.rect("BEAM", p.beam_b, p.beam_h);
  const slab = c.shellSec("SLAB", p.slab), wall = c.shellSec("WALL", p.wall);
  const i0 = (p.bays_x - p.core_bays_x) / 2, j0 = (p.bays_y - p.core_bays_y) / 2;
  const [cx0, cx1, cy0, cy1] = [xs[i0], xs[i0 + p.core_bays_x], ys[j0], ys[j0 + p.core_bays_y]];
  const inCore = (x, y) => between(x, cx0, cx1) && between(y, cy0, cy1);
  gridFrame(c, xs, ys, () => col, () => [bm, ""], { skipCol: inCore,
    skipBeam: (axis, v, a, b) => axis === "X" ? inCore(a, v) && inCore(b, v) : inCore(v, a) && inCore(v, b) });
  coreBox(c, cx0, cx1, cy0, cy1, xs, ys, wall);
  deckSlabs(c, xs, ys, slab, (x0, x1, y0, y1) => inCore((x0 + x1) / 2, (y0 + y1) / 2));
};

export function tplShearWallLines(p) {
  const Lx = p.bays_x * p.bay_x, Ly = p.bays_y * p.bay_y, pl = p.pier_length, o = p.opening;
  const xa = Lx / 2 - o / 2 - pl, xo0 = Lx / 2 - o / 2, xo1 = Lx / 2 + o / 2, xb = xo1 + pl;
  const ya = Ly / 2 - p.planar_length / 2, yb = Ly / 2 + p.planar_length / 2;
  const xs = [...new Set([...lines(p.bays_x, p.bay_x), xa, xo0, xo1, xb].map(r9))].sort((a, b) => a - b);
  const ys = [...new Set([...lines(p.bays_y, p.bay_y), ya, yb].map(r9))].sort((a, b) => a - b);
  return { xs, ys, e: { xa: r9(xa), xo0: r9(xo0), xo1: r9(xo1), xb: r9(xb), ya: r9(ya), yb: r9(yb), Lx: r9(Lx), Ly: r9(Ly) } };
}

B.rc_shear_wall = c => {
  const p = c.p, { xs, ys, e } = tplShearWallLines(p), gx = lines(p.bays_x, p.bay_x), gy = lines(p.bays_y, p.bay_y);
  c.grid(xs, ys); c.stories();
  const col = c.rect("COL", p.column, p.column), bm = c.rect("BEAM", p.beam_b, p.beam_h);
  const slab = c.shellSec("SLAB", p.slab), wall = c.shellSec("WALL", p.wall);
  const { Lx, Ly } = e, d = p.spandrel_depth;
  const onXF = y => near(y, 0) || near(y, Ly), onYF = x => near(x, 0) || near(x, Lx);
  const inWall = (x, y) => (onXF(y) && between(x, e.xa, e.xb)) || (onYF(x) && between(y, e.ya, e.yb));
  c.g.stories.forEach((st, si) => {
    const zt = st.elevation, zb = r9(zt - st.height);
    gy.forEach((y, yi) => gx.forEach((x, xi) => { if (!inWall(x, y)) c.member("column", col, [x, y, zb], [x, y, zt], st.name, `C${si + 1}-${A(xi)}${yi + 1}`); }));
    let k = 0;
    for (const y of gy) for (let i = 0; i + 1 < xs.length; i++) { const a = xs[i], b = xs[i + 1]; if (inWall(a, y) && inWall(b, y)) continue; k++; c.member("beam", bm, [a, y, zt], [b, y, zt], st.name, `BX${si + 1}-${k}`); }
    for (const x of gx) for (let i = 0; i + 1 < ys.length; i++) { const a = ys[i], b = ys[i + 1]; if (inWall(x, a) && inWall(x, b)) continue; k++; c.member("beam", bm, [x, a, zt], [x, b, zt], st.name, `BY${si + 1}-${k}`); }
    const zs = r9(zt - d);
    [0, Ly].forEach((y, fi) => {
      const pl = `P${2 * fi + 1}`, pr = `P${2 * fi + 2}`, spd = `S${fi + 1}`;
      for (const [lab, a, b] of [[pl, e.xa, e.xo0], [pr, e.xo1, e.xb]]) {
        c.wall(wall, [[a, y, zb], [b, y, zb], [b, y, zs], [a, y, zs]], st.name, `W${si + 1}-${lab}L`, { pier: lab });
        c.wall(wall, [[a, y, zs], [b, y, zs], [b, y, zt], [a, y, zt]], st.name, `W${si + 1}-${lab}U`);
      }
      c.wall(wall, [[e.xo0, y, zs], [e.xo1, y, zs], [e.xo1, y, zt], [e.xo0, y, zt]], st.name, `W${si + 1}-${spd}`, { spandrel: spd });
    });
    [0, Lx].forEach((x, fi) => c.wall(wall, [[x, e.ya, zb], [x, e.yb, zb], [x, e.yb, zt], [x, e.ya, zt]], st.name, `W${si + 1}-PX${fi + 1}`, { pier: `PX${fi + 1}` }));
  });
  c.g.spandrel_labels = ["S1", "S2"];
  deckSlabs(c, xs, ys, slab);
};

B.rc_framed_tube = c => {
  const p = c.p, gx = lines(p.bays_x, p.bay_x), gy = lines(p.bays_y, p.bay_y), n = p.tube_subdiv;
  const tx = lines(p.bays_x * n, p.bay_x / n), ty = lines(p.bays_y * n, p.bay_y / n);
  const Lx = gx[gx.length - 1], Ly = gy[gy.length - 1];
  c.grid(gx, gy); c.stories();
  const tcx = c.rect("TCOL", p.tube_column_b, p.tube_column_h), spd = c.rect("SPANDREL", p.spandrel_b, p.spandrel_h);
  const col = c.rect("COL", p.column, p.column), bm = c.rect("BEAM", p.beam_b, p.beam_h), slab = c.shellSec("SLAB", p.slab);
  const core = p.inner_core === "yes";
  let cx0, cx1, cy0, cy1, wall;
  if (core) {
    wall = c.shellSec("WALL", p.wall);
    if (p.bays_x % 2) { const i0 = Math.floor(p.bays_x / 2), j0 = Math.floor(p.bays_y / 2); [cx0, cx1, cy0, cy1] = [gx[i0], gx[i0 + 1], gy[j0], gy[j0 + 1]]; }
    else { const i0 = p.bays_x / 2 - 1, j0 = p.bays_y / 2 - 1; [cx0, cx1, cy0, cy1] = [gx[i0], gx[i0 + 2], gy[j0], gy[j0 + 2]]; }
  }
  const inCore = (x, y) => core && between(x, cx0, cx1) && between(y, cy0, cy1);
  const perim = (x, y) => near(x, 0) || near(x, Lx) || near(y, 0) || near(y, Ly);
  const ring = [...tx.map(x => [x, 0]), ...ty.slice(1).map(y => [Lx, y]), ...tx.slice(0, -1).reverse().map(x => [x, Ly]), ...ty.slice(1, -1).reverse().map(y => [0, y])];
  c.g.stories.forEach((st, si) => {
    const zt = st.elevation, zb = r9(zt - st.height);
    ring.forEach(([x, y], k) => c.member("column", tcx, [x, y, zb], [x, y, zt], st.name, `TC${si + 1}-${k + 1}`, { angle: perimeterAngle(x, y, [0, Lx], [0, Ly]) }));
    ring.forEach((a, k) => { const b = ring[(k + 1) % ring.length]; c.member("beam", spd, [a[0], a[1], zt], [b[0], b[1], zt], st.name, `SP${si + 1}-${k + 1}`); });
    gy.forEach((y, yi) => gx.forEach((x, xi) => { if (!perim(x, y) && !inCore(x, y)) c.member("column", col, [x, y, zb], [x, y, zt], st.name, `C${si + 1}-${A(xi)}${yi + 1}`); }));
    gy.slice(1, -1).forEach((y, j) => { for (let xi = 0; xi + 1 < gx.length; xi++) { const a = gx[xi], b = gx[xi + 1]; if (inCore(a, y) && inCore(b, y)) continue; c.member("beam", bm, [a, y, zt], [b, y, zt], st.name, `BX${si + 1}-${A(xi)}${j + 2}`); } });
    gx.slice(1, -1).forEach((x, j) => { for (let yi = 0; yi + 1 < gy.length; yi++) { const a = gy[yi], b = gy[yi + 1]; if (inCore(x, a) && inCore(x, b)) continue; c.member("beam", bm, [x, a, zt], [x, b, zt], st.name, `BY${si + 1}-${A(j + 1)}${yi + 1}`); } });
  });
  if (core) coreBox(c, cx0, cx1, cy0, cy1, gx, gy, wall);
  deckSlabs(c, gx, gy, slab, (x0, x1, y0, y1) => inCore((x0 + x1) / 2, (y0 + y1) / 2));
};

B.outrigger = c => {
  const p = c.p, xs = lines(p.bays_x, p.bay_x), ys = lines(p.bays_y, p.bay_y);
  c.grid(xs, ys); c.stories();
  const col = c.steel("column"), bm = c.steel("beam"), ch = c.steel("chord"), dg = c.steel("diagonal");
  const slab = c.shellSec("SLAB", p.slab), wall = c.shellSec("WALL", p.wall);
  const i0 = Math.floor(p.bays_x / 2), j0 = Math.floor(p.bays_y / 2);
  const [cx0, cx1, cy0, cy1] = [xs[i0], xs[i0 + 1], ys[j0], ys[j0 + 1]];
  const levels = tplOutriggerLevels(p.outrigger_levels, p.stories);
  const inCore = (x, y) => between(x, cx0, cx1) && between(y, cy0, cy1);
  const coreLine = (axis, v) => axis === "X" ? near(v, cy0) || near(v, cy1) : near(v, cx0) || near(v, cx1);
  const perim = (axis, v) => { const ln = axis === "X" ? ys : xs; return near(v, ln[0]) || near(v, ln[ln.length - 1]); };
  const chordZ = new Set();
  for (const k of levels) { const st = c.g.stories[k - 1]; chordZ.add(r9(st.elevation)); chordZ.add(r9(st.elevation - st.height)); }
  c.g.stories.forEach((st, si) => {
    const zt = st.elevation, zb = r9(zt - st.height), chordLevel = chordZ.has(r9(zt));
    ys.forEach((y, yi) => xs.forEach((x, xi) => {
      if (inCore(x, y)) return;
      const edge = near(x, xs[0]) || near(x, xs[xs.length - 1]) || near(y, ys[0]) || near(y, ys[ys.length - 1]);
      c.member("column", col, [x, y, zb], [x, y, zt], st.name, `C${si + 1}-${A(xi)}${yi + 1}`, { angle: edge ? perimeterAngle(x, y, xs, ys) : 0, releases: si === 0 ? "Mi" : "" });
    }));
    for (const [axis, ln, grid] of [["X", ys, xs], ["Y", xs, ys]]) ln.forEach((v, li) => {
      for (let gi = 0; gi + 1 < grid.length; gi++) {
        const a = grid[gi], b = grid[gi + 1], pa = axis === "X" ? [a, v] : [v, a], pb = axis === "X" ? [b, v] : [v, b];
        if (inCore(...pa) && inCore(...pb)) continue;
        const isChord = chordLevel && (coreLine(axis, v) || perim(axis, v));
        c.member("beam", isChord ? ch : bm, [...pa, zt], [...pb, zt], st.name, `B${axis}${si + 1}-${axis === "X" ? A(gi) : gi + 1}${li + 1}`, { releases: isChord ? "" : PINNED });
      }
    });
    if (levels.includes(si + 1)) {
      let k = 0;
      for (const [axis, ln, grid] of [["X", ys, xs], ["Y", xs, ys]]) for (const v of ln) {
        if (!(coreLine(axis, v) || perim(axis, v))) continue;
        for (let gi = 0; gi + 1 < grid.length; gi++) {
          const a = grid[gi], b = grid[gi + 1], pa = axis === "X" ? [a, v] : [v, a], pb = axis === "X" ? [b, v] : [v, b];
          if (inCore(...pa) && inCore(...pb)) continue;
          let pairs, tag;
          if (coreLine(axis, v) && !perim(axis, v)) {
            const mid = 0.5 * (grid[0] + grid[grid.length - 1]);
            const upB = Math.abs(b - mid) < Math.abs(a - mid);
            pairs = [upB ? [pa, pb] : [pb, pa]]; tag = "OT";
          } else { pairs = [[pa, pb], [pb, pa]]; tag = "BT"; }
          for (const [lo, hi] of pairs) { k++; c.member("brace", dg, [...lo, zb], [...hi, zt], st.name, `${tag}${si + 1}-${k}`, { releases: PINNED }); }
        }
      }
    }
  });
  coreBox(c, cx0, cx1, cy0, cy1, xs, ys, wall);
  deckSlabs(c, xs, ys, slab, (x0, x1, y0, y1) => inCore((x0 + x1) / 2, (y0 + y1) / 2));
};

export function tplPodiumLayout(p) {
  const tbx = p.tower_bays_x, tby = p.tower_bays_y, j0 = Math.floor((p.bays_y - tby) / 2);
  if (p.towers === 1) return [[Math.floor((p.bays_x - tbx) / 2), j0]];
  const gap = p.bays_x - 2 * tbx, e = gap >= 4 ? Math.floor(gap / 4) : 0;
  return [[e, j0], [p.bays_x - e - tbx, j0]];
}

B.podium_tower = c => {
  const p = c.p, xs = lines(p.bays_x, p.bay_x), ys = lines(p.bays_y, p.bay_y);
  const col = c.rect("COL", p.column, p.column), bm = c.rect("BEAM", p.beam_b, p.beam_h), slab = c.shellSec("SLAB", p.slab);
  c.g.grid = { xs, ys, name: "GP" };
  c.g.grid_systems = [{ name: "GP", xs, ys, origin: [0, 0] }];
  const h1 = p.first_story_height || p.story_height;
  let z = 0;
  const pod = [];
  [h1, ...Array(p.stories - 1).fill(p.story_height)].forEach((h, i) => { z = r9(z + h); const s = { name: `P${i + 1}`, height: h, elevation: z, tower: "P" }; c.g.stories.push(s); pod.push(s); });
  const zp = z;
  c.g.towers = [{ name: "P", grid_system: "GP", base_elevation: 0 }];
  const blocks = [[xs, ys, pod, "", "P"]];
  tplPodiumLayout(p).forEach(([i0, j0], ti) => {
    const tn = `T${ti + 1}`, tx = xs.slice(i0, i0 + p.tower_bays_x + 1), ty = ys.slice(j0, j0 + p.tower_bays_y + 1);
    c.g.grid_systems.push({ name: "G" + tn, xs: tx.map(x => r9(x - tx[0])), ys: ty.map(y => r9(y - ty[0])), origin: [tx[0], ty[0]] });
    let zz = zp;
    const sts = [];
    for (let k = 0; k < p.tower_stories; k++) { zz = r9(zz + p.tower_story_height); const s = { name: `${tn}-${k + 1}`, height: p.tower_story_height, elevation: zz, tower: tn }; c.g.stories.push(s); sts.push(s); }
    c.g.towers.push({ name: tn, grid_system: "G" + tn, base_elevation: zp });
    blocks.push([tx, ty, sts, tn, tn]);
  });
  for (const [bx, by, sts, prefix, tn] of blocks) {
    for (const st of sts) {
      const zt = st.elevation, zb = r9(zt - st.height), roof = tn !== "P" && st === sts[sts.length - 1];
      by.forEach((y, yi) => bx.forEach((x, xi) => c.member("column", col, [x, y, zb], [x, y, zt], st.name, `${prefix}C${st.name}-${A(xi)}${yi + 1}`, { tower: tn })));
      by.forEach((y, yi) => { for (let xi = 0; xi + 1 < bx.length; xi++) c.member("beam", bm, [bx[xi], y, zt], [bx[xi + 1], y, zt], st.name, `${prefix}BX${st.name}-${A(xi)}${yi + 1}`, { tower: tn }); });
      bx.forEach((x, xi) => { for (let yi = 0; yi + 1 < by.length; yi++) c.member("beam", bm, [x, by[yi], zt], [x, by[yi + 1], zt], st.name, `${prefix}BY${st.name}-${A(xi)}${yi + 1}`, { tower: tn }); });
      for (let yi = 0; yi + 1 < by.length; yi++) for (let xi = 0; xi + 1 < bx.length; xi++) {
        const [x0, x1, y0, y1] = [bx[xi], bx[xi + 1], by[yi], by[yi + 1]];
        c.slab(slab, [[x0, y0, zt], [x1, y0, zt], [x1, y1, zt], [x0, y1, zt]], st.name, `${prefix}F${st.name}-${A(xi)}${yi + 1}`, roof, { tower: tn });
      }
    }
  }
};

B.base_isolated = c => {
  const p = c.p, xs = lines(p.bays_x, p.bay_x), ys = lines(p.bays_y, p.bay_y);
  c.grid(xs, ys);
  const h1 = p.first_story_height || p.story_height;
  c.setStories([p.isolator_height, h1, ...Array(p.stories - 1).fill(p.story_height)], ["ISO", ...Array.from({ length: p.stories }, (_, i) => `Story${i + 1}`)]);
  const col = c.rect("COL", p.column, p.column), bm = c.rect("BEAM", p.beam_b, p.beam_h), bb = c.rect("BASEBEAM", p.base_beam_b, p.base_beam_h);
  const slab = c.shellSec("SLAB", p.slab);
  const iso = c.g.stories[0], z0 = iso.elevation;
  ys.forEach((y, yi) => { for (let xi = 0; xi + 1 < xs.length; xi++) c.member("beam", bb, [xs[xi], y, z0], [xs[xi + 1], y, z0], "ISO", `BBX-${A(xi)}${yi + 1}`); });
  xs.forEach((x, xi) => { for (let yi = 0; yi + 1 < ys.length; yi++) c.member("beam", bb, [x, ys[yi], z0], [x, ys[yi + 1], z0], "ISO", `BBY-${A(xi)}${yi + 1}`); });
  for (let yi = 0; yi + 1 < ys.length; yi++) for (let xi = 0; xi + 1 < xs.length; xi++) {
    const [x0, x1, y0, y1] = [xs[xi], xs[xi + 1], ys[yi], ys[yi + 1]];
    c.slab(slab, [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0]], "ISO", `FB-${A(xi)}${yi + 1}`, false);
  }
  c.g.stories.slice(1).forEach((st, k) => {
    const si = k + 1, zt = st.elevation, zb = r9(zt - st.height);
    ys.forEach((y, yi) => xs.forEach((x, xi) => c.member("column", col, [x, y, zb], [x, y, zt], st.name, `C${si}-${A(xi)}${yi + 1}`)));
    ys.forEach((y, yi) => { for (let xi = 0; xi + 1 < xs.length; xi++) c.member("beam", bm, [xs[xi], y, zt], [xs[xi + 1], y, zt], st.name, `BX${si}-${A(xi)}${yi + 1}`); });
    xs.forEach((x, xi) => { for (let yi = 0; yi + 1 < ys.length; yi++) c.member("beam", bm, [x, ys[yi], zt], [x, ys[yi + 1], zt], st.name, `BY${si}-${A(xi)}${yi + 1}`); });
    for (let yi = 0; yi + 1 < ys.length; yi++) for (let xi = 0; xi + 1 < xs.length; xi++) {
      const [x0, x1, y0, y1] = [xs[xi], xs[xi + 1], ys[yi], ys[yi + 1]];
      c.slab(slab, [[x0, y0, zt], [x1, y0, zt], [x1, y1, zt], [x0, y1, zt]], st.name, `F${si}-${A(xi)}${yi + 1}`, c.isRoof(si));
    }
  });
  ys.forEach((y, yi) => xs.forEach((x, xi) => { c.g.links.push({ uid: `ISO-${A(xi)}${yi + 1}`, pi: [x, y, 0], pj: [x, y, z0] }); c.g.supports.push([x, y, 0]); }));
};

export function tplShedGeometry(p) {
  const span = p.bays_x * p.bay_x, eave = p.story_height;
  const rise = p.roof === "portal" ? 0.5 * span * Math.tan(p.pitch * Math.PI / 180) : 0;
  return { span: r9(span), eave: r9(eave), ridge: r9(eave + rise), length: r9(p.bays_y * p.bay_y) };
}

B.industrial_shed = c => {
  const p = c.p, gm = tplShedGeometry(p), { span, eave } = gm, ys = lines(p.bays_y, p.bay_y);
  const ridge = gm.ridge, xm = span / 2;
  c.grid(p.roof === "portal" ? [0, span / 2, span] : [0, span], ys);
  c.setStories([eave], ["Roof"]);
  const st = "Roof", col = c.steel("column"), strut = c.steel("strut"), wbr = c.steel("brace");
  const clad = c.shellSec("CLADDING", 0.0007, "steel");
  ys.forEach((y, yi) => {
    const tag = `${yi + 1}`;
    [0, span].forEach((x, xi) => c.member("column", col, [x, y, 0], [x, y, eave], st, `C-${"AB"[xi]}${tag}`));
    if (p.roof === "portal") {
      const raf = c.steel("rafter");
      c.member("beam", raf, [0, y, eave], [xm, y, ridge], st, `R-${tag}a`);
      c.member("beam", raf, [xm, y, ridge], [span, y, eave], st, `R-${tag}b`);
    } else {
      const chd = c.steel("chord"), web = c.steel("web"), n = p.truss_panels, zt = eave + p.truss_depth, zb = eave;
      const px = Array.from({ length: n + 1 }, (_, i) => span * i / n);
      c.member("column", web, [0, y, zb], [0, y, zt], st, `TP-${tag}a`, { releases: PINNED });
      c.member("column", web, [span, y, zb], [span, y, zt], st, `TP-${tag}b`, { releases: PINNED });
      for (let i = 0; i < n; i++) {
        c.member("beam", chd, [px[i], y, zt], [px[i + 1], y, zt], st, `TT-${tag}-${i + 1}`);
        c.member("beam", chd, [px[i], y, zb], [px[i + 1], y, zb], st, `TB-${tag}-${i + 1}`);
        if (i > 0) c.member("column", web, [px[i], y, zb], [px[i], y, zt], st, `TV-${tag}-${i}`, { releases: PINNED });
        const [a, b] = i < n / 2 ? [[px[i], y, zt], [px[i + 1], y, zb]] : [[px[i], y, zb], [px[i + 1], y, zt]];
        c.member("brace", web, a, b, st, `TD-${tag}-${i + 1}`, { releases: PINNED });
      }
    }
  });
  for (let yi = 0; yi + 1 < ys.length; yi++) {
    const y0 = ys[yi], y1 = ys[yi + 1], tag = `${yi + 1}`, top = p.roof === "portal" ? eave : eave + p.truss_depth;
    [0, span].forEach((x, xi) => {
      c.member("beam", strut, [x, y0, top], [x, y1, top], st, `ES-${"AB"[xi]}${tag}`, { releases: PINNED });
      if (p.roof === "truss") c.member("beam", strut, [x, y0, eave], [x, y1, eave], st, `EB-${"AB"[xi]}${tag}`, { releases: PINNED });
    });
    let planes;
    if (p.roof === "portal") {
      c.member("beam", strut, [xm, y0, ridge], [xm, y1, ridge], st, `RP-${tag}`, { releases: PINNED });
      planes = [[[0, y0, eave], [0, y1, eave], [xm, y1, ridge], [xm, y0, ridge]], [[xm, y0, ridge], [xm, y1, ridge], [span, y1, eave], [span, y0, eave]]];
    } else planes = [[[0, y0, top], [0, y1, top], [span, y1, top], [span, y0, top]]];
    planes.forEach((pl, k) => c.slab(clad, pl, st, `RF-${tag}${"ab"[k]}`, true, { oneWay: 1 }));
    if (yi === 0 || yi === ys.length - 2) [0, span].forEach((x, xi) => {
      c.member("brace", wbr, [x, y0, 0], [x, y1, eave], st, `WB-${"AB"[xi]}${tag}a`, { releases: PINNED });
      c.member("brace", wbr, [x, y1, 0], [x, y0, eave], st, `WB-${"AB"[xi]}${tag}b`, { releases: PINNED });
    });
  }
  c.g.diaphragm = "none";
};

export function tplDiagridNodes(n, a, level) {
  const L = n * a;
  if (level % 2 === 0) return Array.from({ length: n + 1 }, (_, i) => r9(i * a));
  return [0, ...Array.from({ length: n }, (_, i) => r9((i + 0.5) * a)), r9(L)];
}

B.diagrid = c => {
  const p = c.p, n = p.bays_x, a = p.bay_x, L = n * a, xs = lines(n, a);
  c.stories();
  const dg = c.steel("diagonal"), rg = c.steel("ring"), cc = c.steel("corner");
  const slab = c.shellSec("SLAB", p.slab), wall = c.shellSec("WALL", p.wall);
  const cs = p.core_size, c0 = r9(L / 2 - cs / 2), c1 = r9(L / 2 + cs / 2);
  const gl = [...new Set([...xs, c0, c1])].sort((u, v) => u - v);
  c.grid(gl, gl);
  const S = c.g.stories, nst = S.length, zl = [0, ...S.map(s => s.elevation)];
  const fp = (f, u, z) => [[u, 0, z], [L, u, z], [L - u, L, z], [0, L - u, z]][f];
  const fn = Array.from({ length: nst + 1 }, () => new Set());
  for (let mod = 0; mod < Math.floor((nst + 1) / 2); mod++) {
    const kb = 2 * mod, kt = Math.min(2 * mod + 2, nst), kmid = kt - kb === 2 ? kb + 1 : null;
    const nb = tplDiagridNodes(n, a, mod), nt = tplDiagridNodes(n, a, mod + 1), zb = zl[kb], zt = zl[kt];
    for (let f = 0; f < 4; f++) {
      let dk = 0;
      for (const u of nb) for (const v of nt) {
        if (Math.abs(Math.abs(v - u) - a / 2) > 1e-6) continue;
        dk++;
        const st = S[kt - 1];
        if (kmid == null) { c.member("brace", dg, fp(f, u, zb), fp(f, v, zt), st.name, `DG${kb}-${f}-${dk}`); fn[kb].add(`${f}|${u}`); fn[kt].add(`${f}|${v}`); continue; }
        const um = r9(0.5 * (u + v)), zm = zl[kmid];
        c.member("brace", dg, fp(f, u, zb), fp(f, um, zm), S[kmid - 1].name, `DG${kb}-${f}-${dk}a`);
        c.member("brace", dg, fp(f, um, zm), fp(f, v, zt), st.name, `DG${kb}-${f}-${dk}b`);
        fn[kb].add(`${f}|${u}`); fn[kmid].add(`${f}|${um}`); fn[kt].add(`${f}|${v}`);
      }
    }
  }
  S.forEach((st, si) => {
    const zt = st.elevation, zb = r9(zt - st.height);
    for (let f = 0; f < 4; f++) {
      c.member("column", cc, fp(f, 0, zb), fp(f, 0, zt), st.name, `CC${si + 1}-${f}`);
      const us = [...new Set([...[...fn[si + 1]].filter(k => k.startsWith(`${f}|`)).map(k => Number(k.split("|")[1])), 0, r9(L)])].sort((u, v) => u - v);
      for (let k = 0; k + 1 < us.length; k++) c.member("beam", rg, fp(f, us[k], zt), fp(f, us[k + 1], zt), st.name, `RB${si + 1}-${f}-${k + 1}`);
    }
  });
  S.forEach((st, si) => {
    const zt = st.elevation, zb = r9(zt - st.height), cp = [[c0, c0], [c1, c0], [c1, c1], [c0, c1]], outer = [[0, 0], [L, 0], [L, L], [0, L]];
    for (let k = 0; k < 4; k++) { const [p0, p1] = [cp[k], cp[(k + 1) % 4]]; c.wall(wall, [[p0[0], p0[1], zb], [p1[0], p1[1], zb], [p1[0], p1[1], zt], [p0[0], p0[1], zt]], st.name, `CW${si + 1}-${k + 1}`, { pier: `P${k + 1}` }); }
    for (let k = 0; k < 4; k++) { const [oa, ob, ia, ib] = [outer[k], outer[(k + 1) % 4], cp[k], cp[(k + 1) % 4]]; c.slab(slab, [[oa[0], oa[1], zt], [ob[0], ob[1], zt], [ib[0], ib[1], zt], [ia[0], ia[1], zt]], st.name, `F${si + 1}-${k + 1}`, c.isRoof(si)); }
  });
};

export const TPL_SYS_BUILDERS = B;

/** Geometry of system template `id` with normalised params `p`;
    `steelName(key)` resolves a steel-section parameter to a shape name. */
export function tplSysGeometry(id, p, steelName = key => p[key] || key) {
  if (!B[id]) throw new Error(`unknown template '${id}'`);
  const c = makeCtx(id, p, steelName);
  B[id](c);
  return c.g;
}

export default { tplSysGeometry, tplAlpha, tplBracedBays, tplOutriggerLevels, tplPodiumLayout, tplShedGeometry, tplDiagridNodes, tplShearWallLines, tplDeckSection };
