import { solveFem } from '../src/solve'
import { CASES, type Case, type FemModel } from '../src/types'

// npm run check
// Small models with answers known in closed form. Each must come back within 0.1%.

const E = 29000 * 144, G = 11200 * 144 // steel, ksf
const sec = { E, G, A: 0.1, Iy: 0.01, Iz: 0.01, J: 0.001 }
const loads = (n: number) => Object.fromEntries(CASES.map((c) => [c, new Array(3 * n).fill(0)])) as Record<Case, number[]>

let failed = 0
const near = (what: string, got: number, want: number) => {
  const ok = Math.abs(got - want) <= 1e-3 * Math.abs(want)
  if (!ok) failed++
  console.log(`${ok ? 'ok  ' : 'FAIL'}  ${what.padEnd(46)} ${got.toPrecision(6).padStart(12)}  want ${want.toPrecision(6)}`)
}

// A column 12 ft tall, fixed at its foot, 10 kip across its head: Δ = PL³/3EI, M = PL at the foot.
{
  const L = 12, P = 10
  const m: FemModel = {
    xyz: [0, 0, 0, 0, 0, L], fixed: [true, false],
    els: [{ i: 0, j: 1, ...sec, yh: [1, 0, 0], ri: false, rj: false, src: 'col', kind: 'frame', q: {} }],
    dia: [], nodeLoads: loads(2), diaLoads: loads(0), notes: [],
  }
  m.nodeLoads.Wx[3] = P
  const r = solveFem(m)
  near('cantilever: head moves, ft', r.nodeU.Wx[3], (P * L ** 3) / (3 * E * sec.Iz))
  near('cantilever: moment at the foot, kip ft', r.pieces.col.Wx.M, P * L)
  near('cantilever: shear, kip', r.pieces.col.Wx.V, P)
  near('cantilever: reaction, kip', -r.balance.Wx.reactions[0], P)
}

// A beam 30 ft, pinned at both ends, 2 k/ft down along it, in two halves: M = wL²/8, Δ = 5wL⁴/384EI.
{
  const L = 30, w = 2
  const m: FemModel = {
    xyz: [0, 0, 0, L / 2, 0, 0, L, 0, 0], fixed: [true, false, true],
    els: [
      { i: 0, j: 1, ...sec, yh: [0, 1, 0], ri: true, rj: false, src: 'beam', kind: 'frame', q: { D: [0, 0, -w] } },
      { i: 1, j: 2, ...sec, yh: [0, 1, 0], ri: false, rj: true, src: 'beam', kind: 'frame', q: { D: [0, 0, -w] } },
    ],
    dia: [], nodeLoads: loads(3), diaLoads: loads(0), notes: [],
  }
  const r = solveFem(m)
  near('simple beam: middle moves down, ft', -r.nodeU.D[5], (5 * w * L ** 4) / (384 * E * sec.Iy))
  near('simple beam: largest moment, kip ft', r.pieces.beam.D.M, (w * L * L) / 8)
  near('simple beam: largest shear, kip', r.pieces.beam.D.V, (w * L) / 2)
  near('simple beam: reactions, kip', r.balance.D.reactions[2], w * L)
}

// Two columns 12 ft, 20 ft apart, their heads on one rigid diaphragm, 10 kip on it along x: each
// column half, Δ = (P/2)L³/3EI. Then 10 kip along y 5 ft off the centre, which turns the floor too.
{
  const L = 12, P = 10, s = 20
  const m: FemModel = {
    xyz: [0, 0, 0, 0, 0, L, s, 0, 0, s, 0, L], fixed: [true, false, true, false],
    els: [
      { i: 0, j: 1, ...sec, yh: [1, 0, 0], ri: false, rj: false, src: 'c1', kind: 'frame', q: {} },
      { i: 2, j: 3, ...sec, yh: [1, 0, 0], ri: false, rj: false, src: 'c2', kind: 'frame', q: {} },
    ],
    dia: [{ id: 'roof', x: s / 2, y: 0, z: L, nodes: [1, 3] }], nodeLoads: loads(4), diaLoads: loads(1), notes: [],
  }
  m.diaLoads.Wx[0] = P
  m.diaLoads.Wy[1] = P; m.diaLoads.Wy[2] = P * 5
  const r = solveFem(m)
  const k = (3 * E * sec.Iz) / L ** 3
  near('diaphragm: moves along x, ft', r.dia[0].u.Wx[0], P / 2 / k)
  near('diaphragm: each column\'s shear, kip', r.pieces.c1.Wx.V, P / 2)
  // Along y the two columns turn the floor too, by their sway and their own twist: Rz = P e / (2 k (s/2)² + 2 GJ/L),
  // and each head moves Uy ± (s/2) Rz.
  const turn = (P * 5) / (2 * k * (s / 2) ** 2 + (2 * G * sec.J) / L)
  near('diaphragm: moves along y at its centre, ft', r.dia[0].u.Wy[1], P / 2 / k)
  near('diaphragm: far column\'s head along y, ft', r.nodeU.Wy[3 * 3 + 1], P / 2 / k + (s / 2) * turn)
}

// Supa's demo building: what goes in comes out at the ground, every case.
{
  const { readFileSync } = await import('node:fs')
  const m = JSON.parse(readFileSync('models/demo.model.json', 'utf8')) as FemModel
  const r = solveFem(m)
  for (const c of CASES) {
    const { applied: a, reactions: q } = r.balance[c]
    const off = Math.hypot(a[0] + q[0], a[1] + q[1], a[2] + q[2]) / Math.hypot(...a)
    const ok = off < 1e-3
    if (!ok) failed++
    console.log(`${ok ? 'ok  ' : 'FAIL'}  ${`demo building ${c}: out of balance`.padEnd(46)} ${`${(off * 100).toFixed(3)}%`.padStart(12)}  want under 0.1%`)
  }
}

console.log(failed ? `\n${failed} failed` : '\nall passed')
process.exit(failed ? 1 : 0)
