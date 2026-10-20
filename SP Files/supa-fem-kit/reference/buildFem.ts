import { rules } from './rules'
import { sectionByName, wcOf, wsScale } from './design'
import { floorOf, slabPsf } from './floors'
import { inRegion, inRegionOrOn } from './geom'
import { gravityOf } from './gravity'
import type { Building } from './resolve'
import { criteriaOf, floorForces } from './seismic'
import type { Sizing } from './sizing'
import type { Design, Doc, ProjectParams } from './types'
import { windOf } from './wind'

// A linear static analysis of the building as derived: a 3D frame of beam elements and core walls as
// wide columns, each floor a rigid diaphragm, fixed at the ground; dead, live, wind each way and the
// earthquake each way, solved directly.
//
// - Members at their preliminary sizes. Floor beams, girders and edges of steel floors, braces, truss
//   webs and drawn beams are pinned at their ends; columns, frame beams, concrete beams, chords and
//   link beams continuous. A level member is split wherever another's end lands on it.
// - A wall panel, story by story, is a column at its middle as stiff as the panel — t L³/12 in its
//   plane — joined by stiff arms to every node along its top and foot, so panels that meet share
//   nodes and a core works as one section. Concrete cracked: walls 0.7 Ig, beams 0.35 Ig.
// - Each floor a rigid diaphragm: every node on it moves with one point in plan, its two ways and its
//   turn; up and down, and rocking, each node its own.
// - Dead: the slab, superdead, partitions and cladding, self-weight of every piece; live: the floor's
//   live. Each piece of floor goes to its nearest beam at that level; a floor without beams to its
//   nearest column or wall. Wind and earthquake: each floor's story force at its diaphragm's centre.
//
// Units: kip and foot.

export type Case = 'D' | 'L' | 'Wx' | 'Wy' | 'Ex' | 'Ey'
export const CASES: Case[] = ['D', 'L', 'Wx', 'Wy', 'Ex', 'Ey']
export const CASE_NAMES: Record<Case, string> = { D: 'Dead', L: 'Live', Wx: 'Wind along x', Wy: 'Wind along y', Ex: 'Earthquake along x', Ey: 'Earthquake along y' }

/** The model, as plain arrays, to hand to the solver. */
export interface FemModel {
  /** x y z a node. */
  xyz: number[]
  fixed: boolean[]
  /** Each element: its nodes, stiffness, the way its local y lies, its end releases, where it came from. */
  els: {
    i: number; j: number
    E: number; G: number; A: number; Iy: number; Iz: number; J: number
    /** A hint for local y: any vector not along it. Local z is x × y. */
    yh: [number, number, number]
    /** Bending released at end i, end j: a pin. */
    ri: boolean; rj: boolean
    /** The piece it stands for, by id; arms none. */
    src: string
    kind: 'frame' | 'wall' | 'arm'
    /** Load along it, global, k/ft, a case each. */
    q: Partial<Record<Case, [number, number, number]>>
  }[]
  /** Rigid diaphragms: their centre, and the nodes on each. */
  dia: { id: string; x: number; y: number; z: number; nodes: number[] }[]
  /** Loads at nodes (fx fy fz a node) and at diaphragm centres (fx fy mz a diaphragm), a case each. */
  nodeLoads: Record<Case, number[]>
  diaLoads: Record<Case, number[]>
  /** What could not be put in: a floor's lateral load with no diaphragm to take it. */
  notes: string[]
}

export interface FemResult {
  /** Per piece (member or wall), per case: axial (tension +), the largest moment, the largest shear. */
  pieces: Record<string, Record<Case, { N: number; M: number; V: number }>>
  /** Per diaphragm, per case: its movement in plan, ft, and its z; for drift. */
  dia: { id: string; z: number; u: Record<Case, [number, number]> }[]
  /** Per case: the loads put in and the reactions at the ground, each [fx, fy, fz]. */
  balance: Record<Case, { applied: [number, number, number]; reactions: [number, number, number] }>
  /** Every node's movement, x y z a node, ft, per case: the deflected shape. */
  nodeU: Record<Case, number[]>
  /** Largest node movement, ft, per case; and the nodes that moved more than a foot — unstable. */
  maxU: Record<Case, number>
  /** The node that moved most, by index, per case. */
  maxAt: Record<Case, number>
  loose: number
  /** Some of those nodes, by index, to look at. */
  looseNodes?: number[]
  dofs: number
  notes: string[]
}

const KSI = 144 // ksi to ksf
const keyOf = (x: number, y: number, z: number) => `${Math.round(x * 20)},${Math.round(y * 20)},${Math.round(z * 20)}`

/**
 * `mesh`, ft: no wall strip wider — each wall panel cut into strips side by side, each its own wide
 * column tied to its neighbours at their shared edge.
 */
export function buildFem(doc: Doc, b: Building, design: Design, params: ProjectParams, sz: Sizing, mesh = 10): FemModel {
  const xyz: number[] = []
  const index = new Map<string, number>()
  const node = (x: number, y: number, z: number) => {
    const k = keyOf(x, y, z)
    let i = index.get(k)
    if (i === undefined) { i = xyz.length / 3; xyz.push(x, y, z); index.set(k, i) }
    return i
  }
  const notes: string[] = []
  const conc = design.materials.find((m) => m.kind === 'concrete' && m.id !== design.slab.material) ?? design.materials.find((m) => m.kind === 'concrete')
  const slabConc = design.materials.find((m) => m.id === design.slab.material) ?? conc
  const R = rules(), Ec = (f: number) => R.Ec(f, design.ec, wcOf(design)) * KSI
  const Es = R.Es * KSI, Gs = R.Gs * KSI
  const Ew = Ec(conc?.strength ?? 8), Eb = Ec(slabConc?.strength ?? 5)
  const apps = new Map(doc.apps.map((a) => [a.id, a]))

  // ── Frame members ───────────────────────────────────────────────────────────────────────────
  type Piece = { a: number; b: number; src: string; E: number; G: number; A: number; Iy: number; Iz: number; J: number; ri: boolean; rj: boolean; wt: number; level: boolean }
  const pieces: Piece[] = []
  for (const m of b.members) {
    const s = sz.members.get(m.id)
    const app = apps.get(m.source)
    const level = Math.abs(m.a[2] - m.b[2]) < 0.05
    let E = Es, G = Gs, A: number, Iy: number, Iz: number, J: number, wt: number
    if (m.role === 'link') {
      const w = m.width ?? 1.5, d = m.depth ?? 2
      E = Ew; G = Ew / 2.4; A = w * d; Iy = 0.35 * (w * d ** 3) / 12; Iz = 0.35 * (d * w ** 3) / 12; J = 0.2 * w * d * Math.min(w, d) ** 2; wt = (A * wcOf(design)) / 1000
    } else if (s?.concrete) {
      const w = s.concrete.b / 12, d = s.concrete.h / 12
      E = Eb; G = Eb / 2.4; A = w * d; Iy = 0.35 * (w * d ** 3) / 12; Iz = 0.35 * (d * w ** 3) / 12; J = 0.2 * w * d * Math.min(w, d) ** 2; wt = (A * wcOf(design)) / 1000
      // Round: a disc of its diameter.
      if (s.concrete.round) { A = (Math.PI * w * w) / 4; Iy = Iz = 0.35 * (Math.PI * w ** 4) / 64; J = (Math.PI * w ** 4) / 32 * 0.2; wt = (A * wcOf(design)) / 1000 }
    } else {
      const p = s ? sectionByName(design, s.section) : undefined
      if (!p) continue
      A = p.A / 144; Iy = p.Ix / 20736; Iz = p.Iy / 20736; J = Math.max(p.J, 0.01) / 20736; wt = (p.wt * wsScale(design)) / 1000
    }
    // Pinned: floor beams of steel floors, braces and webs, drawn beams; continuous otherwise.
    const steelFloor = app?.kind === 'floorFraming' && !s?.concrete
    const braced = app?.kind === 'perimeterFrame' && app.type !== 'moment'
    const pin = m.role === 'brace' || m.role === 'web' || (steelFloor && m.role !== 'column') || (braced && m.role === 'beam') || (!app && m.role !== 'column')
    pieces.push({ a: node(...m.a), b: node(...m.b), src: m.id, E, G, A, Iy, Iz, J, ri: pin, rj: pin, wt, level })
  }

  // ── Walls: wide columns, their arms found once every node is in ──────────────────────────────
  type WallPiece = { src: string; mb: number; mt: number; foot: [number[], number[]]; head: [number[], number[]]; z0: number; z1: number; L: number; t: number; ux: number; uy: number }
  const walls: WallPiece[] = []
  for (const w of b.walls) {
    const [a, c, , d] = w.quad
    const L = Math.hypot(c[0] - a[0], c[1] - a[1])
    if (L < 0.5) continue
    const t = (w.t ?? 12) / 12, z0 = a[2], z1 = d[2]
    // Strips no wider than the mesh, side by side along the panel.
    const n = Math.max(1, Math.ceil(L / Math.max(1, mesh)))
    for (let k = 0; k < n; k++) {
      const s0 = [a[0] + ((c[0] - a[0]) * k) / n, a[1] + ((c[1] - a[1]) * k) / n], s1 = [a[0] + ((c[0] - a[0]) * (k + 1)) / n, a[1] + ((c[1] - a[1]) * (k + 1)) / n]
      const mx = (s0[0] + s1[0]) / 2, my = (s0[1] + s1[1]) / 2
      node(s0[0], s0[1], z0); node(s1[0], s1[1], z0); node(s0[0], s0[1], z1); node(s1[0], s1[1], z1)
      walls.push({ src: w.id, mb: node(mx, my, z0), mt: node(mx, my, z1), foot: [s0, s1], head: [s0, s1], z0, z1, L: L / n, t, ux: (c[0] - a[0]) / L, uy: (c[1] - a[1]) / L })
    }
  }

  // Nodes by level and cell, for what lands on a line.
  const CELL = 10
  const grid = new Map<string, number[]>()
  const cell = (x: number, y: number, z: number) => `${Math.floor(x / CELL)},${Math.floor(y / CELL)},${Math.round(z * 20)}`
  const n0 = xyz.length / 3
  for (let i = 0; i < n0; i++) { const k = cell(xyz[3 * i], xyz[3 * i + 1], xyz[3 * i + 2]); const l = grid.get(k); if (l) l.push(i); else grid.set(k, [i]) }
  /** Nodes on the segment p–q at height z, with how far along each is, 0 to 1. */
  const onLine = (p: number[], q: number[], z: number, tol: number) => {
    const out: { n: number; t: number }[] = []
    const L2 = (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2
    if (L2 < 1e-6) return out
    const x0 = Math.floor((Math.min(p[0], q[0]) - tol) / CELL), x1 = Math.floor((Math.max(p[0], q[0]) + tol) / CELL)
    const y0 = Math.floor((Math.min(p[1], q[1]) - tol) / CELL), y1 = Math.floor((Math.max(p[1], q[1]) + tol) / CELL)
    const zk = Math.round(z * 20)
    for (let cx = x0; cx <= x1; cx++) for (let cy = y0; cy <= y1; cy++) for (const n of grid.get(`${cx},${cy},${zk}`) ?? []) {
      const x = xyz[3 * n], y = xyz[3 * n + 1]
      const t = ((x - p[0]) * (q[0] - p[0]) + (y - p[1]) * (q[1] - p[1])) / L2
      if (t < -1e-3 || t > 1 + 1e-3) continue
      const dx = p[0] + t * (q[0] - p[0]) - x, dy = p[1] + t * (q[1] - p[1]) - y
      if (dx * dx + dy * dy <= tol * tol) out.push({ n, t })
    }
    return out
  }

  const P = (n: number) => [xyz[3 * n], xyz[3 * n + 1], xyz[3 * n + 2]]
  // A column or brace stopping on a floor where nothing stands under it — a sloped face's column on a
  // podium roof — lands on the nearest level member there, by a stiff arm to where it meets it: in
  // place of a transfer girder, so what it carries reaches the ground.
  const z00 = b.levels[0]?.z ?? 0
  const below = new Set<number>()
  for (const pc of pieces) if (!pc.level) { const [lo] = P(pc.a)[2] <= P(pc.b)[2] ? [pc.b] : [pc.a]; below.add(lo) }
  const landings: { from: number; to: number }[] = []
  for (const pc of pieces) {
    if (pc.level) continue
    const foot = P(pc.a)[2] <= P(pc.b)[2] ? pc.a : pc.b
    const f = P(foot)
    if (Math.abs(f[2] - z00) < 0.05 || below.has(foot)) continue
    if (walls.some((w) => Math.abs(w.z1 - f[2]) < 0.05 && onLine(w.head[0], w.head[1], w.z1, 0.25).some((x) => x.n === foot))) continue
    let best: number[] | null = null, bd = 15 * 15
    for (const o of pieces) {
      if (!o.level) continue
      const pa = P(o.a), pb = P(o.b)
      if (Math.abs(pa[2] - f[2]) > 0.05) continue
      const L2 = (pb[0] - pa[0]) ** 2 + (pb[1] - pa[1]) ** 2
      if (L2 < 1e-6) continue
      const t = Math.max(0, Math.min(1, ((f[0] - pa[0]) * (pb[0] - pa[0]) + (f[1] - pa[1]) * (pb[1] - pa[1])) / L2))
      const q = [pa[0] + t * (pb[0] - pa[0]), pa[1] + t * (pb[1] - pa[1])]
      const d = (q[0] - f[0]) ** 2 + (q[1] - f[1]) ** 2
      if (d < bd) { bd = d; best = q }
    }
    if (!best) continue
    const n = node(best[0], best[1], f[2])
    if (n === foot) continue
    const k = cell(best[0], best[1], f[2]); const l = grid.get(k); if (l) { if (!l.includes(n)) l.push(n) } else grid.set(k, [n])
    landings.push({ from: n, to: foot })
  }
  const els: FemModel['els'] = []
  // What holds a node up from below: a column, brace or other member that is not level, or a wall. A pin is put
  // only where something holds the node up; elsewhere the member runs on continuous, so a girder
  // between two grid points with no column under them does not fold.
  const held = new Set<number>()
  // A member with an end that meets nothing — a cantilever — is held fast at its other end.
  const degree = new Map<number, number>()
  for (const pc of pieces) for (const n of [pc.a, pc.b]) degree.set(n, (degree.get(n) ?? 0) + 1)
  const free = (pc: Piece) => (degree.get(pc.a) === 1 && !held.has(pc.a)) || (degree.get(pc.b) === 1 && !held.has(pc.b))
  // From below only: the head of a column or brace, a wall's head; a column standing on a joint does not hold it.
  for (const pc of pieces) if (!pc.level) held.add(P(pc.a)[2] >= P(pc.b)[2] ? pc.a : pc.b)
  for (const w of walls) {
    for (const { n } of onLine(w.foot[0], w.foot[1], w.z0, 0.25)) held.add(n)
    for (const { n } of onLine(w.head[0], w.head[1], w.z1, 0.25)) held.add(n)
  }
  // Level members split wherever a node lands on them; the pins kept at the ends only.
  const segs: { e: number; z: number; p: number[]; q: number[] }[] = []
  for (const pc of pieces) {
    const pa = P(pc.a), pb = P(pc.b)
    const stops = pc.level ? onLine(pa, pb, pa[2], 1).filter((x) => x.t > 1e-3 && x.t < 1 - 1e-3 && x.n !== pc.a && x.n !== pc.b).sort((u, v) => u.t - v.t) : []
    // A beam element is exact under its load between joints: members are not cut finer than that.
    const chain = [pc.a, ...stops.map((x) => x.n), pc.b]
    for (let k = 0; k + 1 < chain.length; k++) {
      const yh: [number, number, number] = Math.abs(pb[2] - pa[2]) > 0.999 * Math.hypot(pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]) ? [1, 0, 0] : [-(pb[1] - pa[1]), pb[0] - pa[0], 0]
      els.push({ i: chain[k], j: chain[k + 1], E: pc.E, G: pc.G, A: pc.A, Iy: pc.Iy, Iz: pc.Iz, J: pc.J, yh: Math.hypot(...yh) < 1e-9 ? [1, 0, 0] : yh, ri: k === 0 && pc.ri && held.has(pc.a) && !free(pc), rj: k === chain.length - 2 && pc.rj && held.has(pc.b) && !free(pc), src: pc.src, kind: 'frame', q: { D: [0, 0, -pc.wt] } })
      if (pc.level) segs.push({ e: els.length - 1, z: pa[2], p: P(chain[k]), q: P(chain[k + 1]) })
    }
  }
  const ARM = { E: Ew * 10, G: Ew * 4, A: 20, Iy: 2000, Iz: 2000, J: 2000 }
  for (const l of landings) els.push({ i: l.from, j: l.to, ...ARM, yh: [0, 0, 1], ri: false, rj: false, src: '', kind: 'arm', q: {} })
  for (const w of walls) {
    const Gw = Ew / 2.4
    els.push({ i: w.mb, j: w.mt, E: Ew, G: Gw, A: w.L * w.t, Iy: 0.7 * (w.L * w.t ** 3) / 12, Iz: 0.7 * (w.t * w.L ** 3) / 12, J: (w.L * w.t ** 3) / 3, yh: [w.ux, w.uy, 0], ri: false, rj: false, src: w.src, kind: 'wall', q: {} })
    for (const [m, z, ends] of [[w.mb, w.z0, w.foot], [w.mt, w.z1, w.head]] as const) {
      for (const { n } of onLine(ends[0], ends[1], z, 0.25)) if (n !== m) els.push({ i: m, j: n, ...ARM, yh: [0, 0, 1], ri: false, rj: false, src: '', kind: 'arm', q: {} })
    }
  }

  // ── Supports and diaphragms ───────────────────────────────────────────────────────────────────
  const nn = xyz.length / 3
  const z0 = b.levels[0]?.z ?? 0
  const fixed = Array.from({ length: nn }, (_, i) => Math.abs(xyz[3 * i + 2] - z0) < 0.05)
  const plates = b.masses.flatMap((m) => m.plates)
  const dia: FemModel['dia'] = []
  const taken = new Set<number>()
  for (const p of plates) {
    const nodes: number[] = []
    for (let i = 0; i < nn; i++) {
      if (taken.has(i) || fixed[i] || Math.abs(xyz[3 * i + 2] - p.z) > 0.05) continue
      if (inRegionOrOn([xyz[3 * i], xyz[3 * i + 1]], p.full, 0.5)) nodes.push(i)
    }
    if (nodes.length < 2) continue
    for (const i of nodes) taken.add(i)
    const cx = nodes.reduce((t, i) => t + xyz[3 * i], 0) / nodes.length, cy = nodes.reduce((t, i) => t + xyz[3 * i + 1], 0) / nodes.length
    dia.push({ id: p.id, x: cx, y: cy, z: p.z, nodes })
  }

  // ── Loads ─────────────────────────────────────────────────────────────────────────────────────
  const nodeLoads = Object.fromEntries(CASES.map((c) => [c, new Array(3 * nn).fill(0)])) as Record<Case, number[]>
  const diaLoads = Object.fromEntries(CASES.map((c) => [c, new Array(3 * dia.length).fill(0)])) as Record<Case, number[]>
  const addQ = (e: number, c: Case, qz: number) => { const q = els[e].q[c] ?? [0, 0, 0]; q[2] += qz; els[e].q[c] = q }

  // Floors: each piece of floor, a few feet square, to its nearest level member — or node.
  const STEP = 3
  for (const p of plates) {
    const f = floorOf(design, b, p)
    const dPsf = slabPsf(design, f) + f.sdl + f.part, lPsf = f.live
    const mine = segs.filter((s) => Math.abs(s.z - p.z) < 0.05)
    const bucket = new Map<string, typeof mine>()
    const BC = 15
    for (const s of mine) {
      const x0 = Math.floor(Math.min(s.p[0], s.q[0]) / BC), x1 = Math.floor(Math.max(s.p[0], s.q[0]) / BC)
      const y0 = Math.floor(Math.min(s.p[1], s.q[1]) / BC), y1 = Math.floor(Math.max(s.p[1], s.q[1]) / BC)
      for (let cx = x0; cx <= x1; cx++) for (let cy = y0; cy <= y1; cy++) { const k = `${cx},${cy}`; const l = bucket.get(k); if (l) l.push(s); else bucket.set(k, [s]) }
    }
    const atZ: number[] = []
    for (let i = 0; i < nn; i++) if (Math.abs(xyz[3 * i + 2] - p.z) < 0.05 && !fixed[i]) atZ.push(i)
    const sumD = new Map<number, number>(), sumL = new Map<number, number>()
    const all = p.region.flat(2)
    if (!all.length) continue
    const bx0 = Math.min(...all.map((q) => q[0])), bx1 = Math.max(...all.map((q) => q[0]))
    const by0 = Math.min(...all.map((q) => q[1])), by1 = Math.max(...all.map((q) => q[1]))
    const a = STEP * STEP
    for (let x = bx0 + STEP / 2; x < bx1; x += STEP) for (let y = by0 + STEP / 2; y < by1; y += STEP) {
      if (!inRegion([x, y], p.region)) continue
      let best = -1, bd = Infinity
      for (let r = 0; r <= 3 && best < 0; r++) {
        const cx = Math.floor(x / BC), cy = Math.floor(y / BC)
        for (let i = cx - r; i <= cx + r; i++) for (let j = cy - r; j <= cy + r; j++) for (const s of bucket.get(`${i},${j}`) ?? []) {
          const L2 = (s.q[0] - s.p[0]) ** 2 + (s.q[1] - s.p[1]) ** 2
          const t = L2 > 0 ? Math.max(0, Math.min(1, ((x - s.p[0]) * (s.q[0] - s.p[0]) + (y - s.p[1]) * (s.q[1] - s.p[1])) / L2)) : 0
          const d = (s.p[0] + t * (s.q[0] - s.p[0]) - x) ** 2 + (s.p[1] + t * (s.q[1] - s.p[1]) - y) ** 2
          if (d < bd) { bd = d; best = s.e }
        }
      }
      if (best >= 0) { sumD.set(best, (sumD.get(best) ?? 0) + (dPsf * a) / 1000); sumL.set(best, (sumL.get(best) ?? 0) + (lPsf * a) / 1000); continue }
      // No beams: the nearest node at this level carries it.
      let bn = -1; bd = Infinity
      for (const i of atZ) { const d = (xyz[3 * i] - x) ** 2 + (xyz[3 * i + 1] - y) ** 2; if (d < bd) { bd = d; bn = i } }
      if (bn >= 0) { nodeLoads.D[3 * bn + 2] -= (dPsf * a) / 1000; nodeLoads.L[3 * bn + 2] -= (lPsf * a) / 1000 }
    }
    for (const [e, v] of sumD) { const L = len(els[e]); addQ(e, 'D', -v / L) }
    for (const [e, v] of sumL) { const L = len(els[e]); addQ(e, 'L', -v / L) }
  }
  function len(e: FemModel['els'][number]) { const a = P(e.i), c = P(e.j); return Math.max(0.01, Math.hypot(c[0] - a[0], c[1] - a[1], c[2] - a[2])) }

  // Cladding on the edges, the facade's psf over the story's share of height.
  {
    // Each floor's tier's cladding, else the design's.
    const heightAt = (lid: string) => { const l = b.levels.find((x) => x.id === lid); const up = b.levels.find((x) => x.index === (l?.index ?? 0) + 1); return ((l?.height ?? 0) + (up?.height ?? 0)) / 2 }
    const edges = new Map(b.members.filter((m) => m.role === 'edge').map((m) => [m.id, (b.floorTiers?.get(m.level)?.clad ?? design.clad) * heightAt(m.level)]))
    els.forEach((e, k) => { const w = edges.get(e.src); if (w) addQ(k, 'D', -w / 1000) })
  }
  // Walls' weight, half at the foot and half at the head of each panel.
  for (const w of walls) {
    const W = w.L * w.t * (w.z1 - w.z0) * (wcOf(design) / 1000)
    nodeLoads.D[3 * w.mb + 2] -= W / 2; nodeLoads.D[3 * w.mt + 2] -= W / 2
  }

  // Lateral: each floor's story force at its diaphragm.
  const diaOf = new Map(dia.map((d, k) => [d.id, k]))
  const put = (c: Case, plate: string, fx: number, fy: number) => {
    const k = diaOf.get(plate)
    if (k === undefined) { if (fx || fy) notes.push(`${plate}: no diaphragm to take the ${c} story force`); return }
    diaLoads[c][3 * k] += fx; diaLoads[c][3 * k + 1] += fy
  }
  for (const s of windOf(b, params, 'W').stories) put('Wx', `plate:${s.mass}:${s.level}`, s.force, 0)
  for (const s of windOf(b, params, 'S').stories) put('Wy', `plate:${s.mass}:${s.level}`, 0, s.force)
  const g = gravityOf(b, design)
  // Each mass its own equivalent lateral force.
  const forces = floorForces(g, params, criteriaOf(b))
  for (const x of g) {
    const F = forces.get(`${x.mass}|${x.level}`) ?? 0
    if (!F) continue
    put('Ex', `plate:${x.mass}:${x.level}`, F, 0)
    put('Ey', `plate:${x.mass}:${x.level}`, 0, F)
  }

  return { xyz, fixed, els, dia, nodeLoads, diaLoads, notes }
}
