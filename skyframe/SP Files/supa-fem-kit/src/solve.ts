import { CASES, type Case, type FemModel, type FemResult } from './types'

// The solve: every node's six movements as equations — none where it is fixed; on a diaphragm its
// two ways and its turn taken from the diaphragm's centre (ux = Ux − (y − yc) Rz, uy = Uy + (x − xc) Rz,
// rz = Rz) — ordered floor by floor so the stiffness is a narrow skyline, factored once as L D Lᵀ and
// solved for every case. Each element's end forces come back from its ends' movements; reactions
// from what the elements push on the ground.

type Terms = { eq: number[]; c: number[] }[]

export function solveFem(m: FemModel, progress?: (s: string) => void): FemResult {
  const nn = m.xyz.length / 3
  const X = (i: number) => m.xyz[3 * i], Y = (i: number) => m.xyz[3 * i + 1], Z = (i: number) => m.xyz[3 * i + 2]
  const diaOf = new Int32Array(nn).fill(-1)
  m.dia.forEach((d, k) => d.nodes.forEach((i) => { diaOf[i] = k }))

  // ── Equations, floor by floor ────────────────────────────────────────────────────────────────
  const order = Array.from({ length: nn }, (_, i) => i).sort((a, c) => Z(a) - Z(c) || X(a) - X(c) || Y(a) - Y(c))
  const dofs: Terms[] = new Array(nn)
  const master = new Int32Array(m.dia.length).fill(-1)
  let neq = 0
  for (const i of order) {
    const t: Terms = []
    if (m.fixed[i]) { for (let d = 0; d < 6; d++) t.push({ eq: [], c: [] }); dofs[i] = t; continue }
    const k = diaOf[i]
    if (k >= 0) {
      if (master[k] < 0) { master[k] = neq; neq += 3 }
      const M = master[k], dx = X(i) - m.dia[k].x, dy = Y(i) - m.dia[k].y
      t.push({ eq: [M, M + 2], c: [1, -dy] }, { eq: [M + 1, M + 2], c: [1, dx] })
      t.push({ eq: [neq++], c: [1] }, { eq: [neq++], c: [1] }, { eq: [neq++], c: [1] })
      t.push({ eq: [M + 2], c: [1] })
    } else for (let d = 0; d < 6; d++) t.push({ eq: [neq++], c: [1] })
    dofs[i] = t
  }
  // A diaphragm with no node free to carry it still has its three.
  m.dia.forEach((_, k) => { if (master[k] < 0) { master[k] = neq; neq += 3 } })

  // ── Element stiffness, local, released, turned to global ─────────────────────────────────────
  const ne = m.els.length
  const Kg: Float64Array[] = new Array(ne)
  const Kl: Float64Array[] = new Array(ne)
  const Rm: Float64Array[] = new Array(ne)
  const F0: Record<Case, Float64Array>[] = new Array(ne)
  const Ls = new Float64Array(ne)
  for (let e = 0; e < ne; e++) {
    const el = m.els[e]
    const ax = X(el.j) - X(el.i), ay = Y(el.j) - Y(el.i), az = Z(el.j) - Z(el.i)
    const L = Math.max(1e-3, Math.hypot(ax, ay, az)); Ls[e] = L
    const ex = [ax / L, ay / L, az / L]
    let ey = [el.yh[0], el.yh[1], el.yh[2]]
    const d = ey[0] * ex[0] + ey[1] * ex[1] + ey[2] * ex[2]
    ey = [ey[0] - d * ex[0], ey[1] - d * ex[1], ey[2] - d * ex[2]]
    let n = Math.hypot(ey[0], ey[1], ey[2])
    if (n < 1e-6) { ey = Math.abs(ex[2]) < 0.9 ? [-ex[1], ex[0], 0] : [1, 0, 0]; n = Math.hypot(ey[0], ey[1], ey[2]) }
    ey = ey.map((v) => v / n)
    const ez = [ex[1] * ey[2] - ex[2] * ey[1], ex[2] * ey[0] - ex[0] * ey[2], ex[0] * ey[1] - ex[1] * ey[0]]
    const R = new Float64Array([...ex, ...ey, ...ez])
    Rm[e] = R
    const k = local(el.E, el.G, el.A, el.Iy, el.Iz, el.J, L)
    // Loads along it, local, and their fixed-end forces.
    const f0s = {} as Record<Case, Float64Array>
    for (const c of CASES) {
      const qg = el.q[c]
      const f = new Float64Array(12)
      if (qg) {
        const q = [0, 1, 2].map((r) => R[3 * r] * qg[0] + R[3 * r + 1] * qg[1] + R[3 * r + 2] * qg[2])
        f[0] = f[6] = -q[0] * L / 2
        f[1] = f[7] = -q[1] * L / 2; f[5] = -q[1] * L * L / 12; f[11] = q[1] * L * L / 12
        f[2] = f[8] = -q[2] * L / 2; f[4] = q[2] * L * L / 12; f[10] = -q[2] * L * L / 12
      }
      f0s[c] = f
    }
    const rel = [...(el.ri ? [4, 5] : []), ...(el.rj ? [10, 11] : [])]
    if (rel.length) condense(k, f0s, rel)
    Kl[e] = k; F0[e] = f0s
    // Global: Tᵀ k T, T the 3×3 turn four times down the diagonal.
    const g = new Float64Array(144)
    const kt = new Float64Array(144)
    for (let r = 0; r < 12; r++) for (let c = 0; c < 12; c++) {
      const b = Math.floor(c / 3) * 3, cc = c % 3
      kt[r * 12 + c] = k[r * 12 + b] * R[cc] + k[r * 12 + b + 1] * R[3 + cc] + k[r * 12 + b + 2] * R[6 + cc]
    }
    for (let r = 0; r < 12; r++) for (let c = 0; c < 12; c++) {
      const b = Math.floor(r / 3) * 3, rr = r % 3
      g[r * 12 + c] = R[rr] * kt[b * 12 + c] + R[3 + rr] * kt[(b + 1) * 12 + c] + R[6 + rr] * kt[(b + 2) * 12 + c]
    }
    Kg[e] = g
  }

  // ── The skyline: each column's height ────────────────────────────────────────────────────────
  progress?.('Assembling')
  const termsOf = (el: FemModel['els'][number]) => [...dofs[el.i], ...dofs[el.j]]
  const top = new Int32Array(neq).fill(0).map((_, j) => j)
  const touch = (eqs: number[]) => { const lo = Math.min(...eqs); for (const q of eqs) if (lo < top[q]) top[q] = lo }
  for (const el of m.els) { const eqs = termsOf(el).flatMap((t) => t.eq); if (eqs.length) touch(eqs) }
  const start = new Float64Array(neq + 1)
  for (let j = 0; j < neq; j++) start[j + 1] = start[j] + (j - top[j] + 1)
  const size = start[neq]
  const K = new Float64Array(size)
  const at = (i: number, j: number) => (i <= j ? start[j] + i - top[j] : start[i] + j - top[i])
  const add = (i: number, j: number, v: number) => { K[at(i, j)] += v }
  for (let e = 0; e < ne; e++) {
    const T = termsOf(m.els[e]), g = Kg[e]
    for (let a = 0; a < 12; a++) for (const [p, cp] of T[a].eq.map((q, s) => [q, T[a].c[s]])) {
      for (let c = 0; c < 12; c++) {
        const v = g[a * 12 + c]
        if (v === 0) continue
        const tc = T[c]
        for (let s = 0; s < tc.eq.length; s++) { const q = tc.eq[s]; if (q >= p) add(p, q, cp * tc.c[s] * v) }
      }
    }
  }
  // A small spring on every free movement of every node: a node no element holds still stands.
  for (let i = 0; i < nn; i++) dofs[i].forEach((t, d) => { if (t.eq.length === 1 && t.c[0] === 1) add(t.eq[0], t.eq[0], d < 3 ? 1e-3 : 1e-2) })

  // ── L D Lᵀ ──────────────────────────────────────────────────────────────────────────────────
  progress?.('Factoring')
  for (let j = 0; j < neq; j++) {
    const tj = top[j], sj = start[j]
    for (let i = tj + 1; i < j; i++) {
      const ti = top[i], si = start[i]
      const k0 = Math.max(ti, tj)
      let sum = 0
      for (let k = k0; k < i; k++) sum += K[si + k - ti] * K[sj + k - tj]
      K[sj + i - tj] -= sum
    }
    let djj = K[sj + j - tj]
    for (let i = tj; i < j; i++) {
      const gij = K[sj + i - tj]
      const t = gij / K[start[i] + i - top[i]]
      djj -= t * gij
      K[sj + i - tj] = t
    }
    K[sj + j - tj] = Math.abs(djj) < 1e-9 ? 1e-9 : djj
  }
  const solve = (v: Float64Array) => {
    for (let j = 0; j < neq; j++) { const tj = top[j], sj = start[j]; let s = 0; for (let i = tj; i < j; i++) s += K[sj + i - tj] * v[i]; v[j] -= s }
    for (let j = 0; j < neq; j++) v[j] /= K[start[j] + j - top[j]]
    for (let j = neq - 1; j >= 0; j--) { const tj = top[j], sj = start[j], x = v[j]; for (let i = tj; i < j; i++) v[i] -= K[sj + i - tj] * x }
    return v
  }

  // ── Every case ──────────────────────────────────────────────────────────────────────────────
  progress?.('Solving')
  const result: FemResult = {
    pieces: {}, dia: m.dia.map((d) => ({ id: d.id, z: d.z, u: {} as Record<Case, [number, number]> })),
    balance: {} as FemResult['balance'], nodeU: {} as Record<Case, number[]>, maxU: {} as Record<Case, number>, maxAt: {} as Record<Case, number>, loose: 0, dofs: neq, notes: [...m.notes],
  }
  const loose = new Set<number>()
  for (const c of CASES) {
    const F = new Float64Array(neq)
    const applied: [number, number, number] = [0, 0, 0]
    // At nodes.
    for (let i = 0; i < nn; i++) for (let d = 0; d < 3; d++) {
      const v = m.nodeLoads[c][3 * i + d]
      if (!v) continue
      applied[d] += v
      const t = dofs[i][d]
      t.eq.forEach((q, s) => { F[q] += t.c[s] * v })
    }
    // Along elements: their fixed-end forces, turned to global, the other way.
    for (let e = 0; e < ne; e++) {
      const q = m.els[e].q[c]
      if (q) for (let d = 0; d < 3; d++) applied[d] += q[d] * Ls[e]
      const f0 = F0[e][c], R = Rm[e], T = termsOf(m.els[e])
      if (!q) continue
      for (let b = 0; b < 4; b++) for (let r = 0; r < 3; r++) {
        const gv = -(R[r] * f0[3 * b] + R[3 + r] * f0[3 * b + 1] + R[6 + r] * f0[3 * b + 2])
        if (!gv) continue
        const t = T[3 * b + r]
        t.eq.forEach((qq, s) => { F[qq] += t.c[s] * gv })
      }
    }
    // At diaphragms.
    m.dia.forEach((_, k) => {
      for (let d = 0; d < 3; d++) {
        const v = m.diaLoads[c][3 * k + d]
        if (!v) continue
        if (d < 2) applied[d] += v
        F[master[k] + d] += v
      }
    })
    const U = solve(F)
    const ug = (i: number, d: number) => dofs[i][d].eq.reduce((s, q, k) => s + dofs[i][d].c[k] * U[q], 0)
    let maxU = 0
    const nu = new Array(3 * nn)
    for (let i = 0; i < nn; i++) {
      nu[3 * i] = ug(i, 0); nu[3 * i + 1] = ug(i, 1); nu[3 * i + 2] = ug(i, 2)
      const u = Math.hypot(nu[3 * i], nu[3 * i + 1], nu[3 * i + 2])
      if (u > maxU) { maxU = u; result.maxAt[c] = i }
      // Under its own weight nothing should move a foot: where it does, it is not held.
      if ((c === 'D' || c === 'L') && u > 1) loose.add(i)
    }
    result.maxU[c] = maxU
    result.nodeU[c] = nu
    m.dia.forEach((_, k) => { result.dia[k].u[c] = [U[master[k]], U[master[k] + 1]] })
    // Element forces, and reactions from what pushes on the ground.
    const R0: [number, number, number] = [0, 0, 0]
    for (let e = 0; e < ne; e++) {
      const el = m.els[e], R = Rm[e], k = Kl[e]
      const ul = new Float64Array(12)
      for (let b = 0; b < 4; b++) {
        const n = b < 2 ? el.i : el.j, o = (b % 2) * 3
        const g = [ug(n, o), ug(n, o + 1), ug(n, o + 2)]
        for (let r = 0; r < 3; r++) ul[3 * b + r] = R[3 * r] * g[0] + R[3 * r + 1] * g[1] + R[3 * r + 2] * g[2]
      }
      const f = new Float64Array(F0[e][c])
      for (let r = 0; r < 12; r++) { let s = 0; for (let q = 0; q < 12; q++) s += k[r * 12 + q] * ul[q]; f[r] += s }
      for (const [n, o] of [[el.i, 0], [el.j, 6]] as const) {
        if (!m.fixed[n]) continue
        for (let r = 0; r < 3; r++) R0[r] += R[r] * f[o] + R[3 + r] * f[o + 1] + R[6 + r] * f[o + 2]
      }
      if (!el.src) continue
      // Along it: the axial, the largest moment and shear at five points, the load on it as it is.
      const qg = el.q[c] ?? [0, 0, 0]
      const ql = [0, 1, 2].map((r) => R[3 * r] * qg[0] + R[3 * r + 1] * qg[1] + R[3 * r + 2] * qg[2])
      const L = Ls[e]
      let Mx = 0, Vx = 0
      for (let s = 0; s <= 4; s++) {
        const x = (L * s) / 4
        const My = f[4] + x * f[2] + (x * x / 2) * ql[2], Mz = f[5] - x * f[1] - (x * x / 2) * ql[1]
        Mx = Math.max(Mx, Math.hypot(My, Mz))
        Vx = Math.max(Vx, Math.hypot(f[1] + ql[1] * x, f[2] + ql[2] * x))
      }
      const N = -f[0]
      const rec = (result.pieces[el.src] ??= {} as Record<Case, { N: number; M: number; V: number }>)
      const was = rec[c]
      // A wall's strips side by side add up to the panel; a member's pieces end to end, the largest.
      rec[c] = !was ? { N, M: Mx, V: Vx } : el.kind === 'wall' ? { N: was.N + N, M: Math.max(was.M, Mx), V: was.V + Vx }
        : { N: Math.abs(N) > Math.abs(was.N) ? N : was.N, M: Math.max(was.M, Mx), V: Math.max(was.V, Vx) }
    }
    // Loads at fixed nodes go straight to the ground.
    for (let i = 0; i < nn; i++) if (m.fixed[i]) for (let d = 0; d < 3; d++) R0[d] -= m.nodeLoads[c][3 * i + d]
    result.balance[c] = { applied, reactions: R0 }
  }
  result.loose = loose.size
  result.looseNodes = [...loose]
  return result
}

/** A 3D beam's stiffness in its own axes: [u v w θx θy θz] at i, then at j. */
function local(E: number, G: number, A: number, Iy: number, Iz: number, J: number, L: number): Float64Array {
  const k = new Float64Array(144)
  const s = (r: number, c: number, v: number) => { k[r * 12 + c] = v; k[c * 12 + r] = v }
  const a = E * A / L, t = G * J / L
  const z12 = 12 * E * Iz / L ** 3, z6 = 6 * E * Iz / L ** 2, z4 = 4 * E * Iz / L, z2 = 2 * E * Iz / L
  const y12 = 12 * E * Iy / L ** 3, y6 = 6 * E * Iy / L ** 2, y4 = 4 * E * Iy / L, y2 = 2 * E * Iy / L
  s(0, 0, a); s(0, 6, -a); s(6, 6, a)
  s(3, 3, t); s(3, 9, -t); s(9, 9, t)
  s(1, 1, z12); s(1, 5, z6); s(1, 7, -z12); s(1, 11, z6)
  s(5, 5, z4); s(5, 7, -z6); s(5, 11, z2)
  s(7, 7, z12); s(7, 11, -z6); s(11, 11, z4)
  s(2, 2, y12); s(2, 4, -y6); s(2, 8, -y12); s(2, 10, -y6)
  s(4, 4, y4); s(4, 8, y6); s(4, 10, y2)
  s(8, 8, y12); s(8, 10, y6); s(10, 10, y4)
  return k
}

/** Released movements condensed out: K = Kkk − Kkr Krr⁻¹ Krk, and the fixed-end forces the same way. */
function condense(k: Float64Array, f0s: Record<Case, Float64Array>, rel: number[]) {
  const n = rel.length
  // Krr⁻¹ by Gauss–Jordan, n at most four.
  const A = rel.map((r) => rel.map((c) => k[r * 12 + c]))
  const I = rel.map((_, r) => rel.map((__, c) => (r === c ? 1 : 0)))
  for (let p = 0; p < n; p++) {
    let piv = A[p][p]
    if (Math.abs(piv) < 1e-12) piv = 1e-12
    for (let c = 0; c < n; c++) { A[p][c] /= piv; I[p][c] /= piv }
    for (let r = 0; r < n; r++) if (r !== p) { const f = A[r][p]; for (let c = 0; c < n; c++) { A[r][c] -= f * A[p][c]; I[r][c] -= f * I[p][c] } }
  }
  const keep = Array.from({ length: 12 }, (_, i) => i).filter((i) => !rel.includes(i))
  const kr = keep.map((i) => rel.map((r) => k[i * 12 + r]))
  const old = new Float64Array(k)
  for (const i of keep) for (const j of keep) {
    let s = 0
    for (let a = 0; a < n; a++) for (let b = 0; b < n; b++) s += kr[keep.indexOf(i)][a] * I[a][b] * old[rel[b] * 12 + j]
    k[i * 12 + j] = old[i * 12 + j] - s
  }
  for (const r of rel) for (let c = 0; c < 12; c++) { k[r * 12 + c] = 0; k[c * 12 + r] = 0 }
  for (const f of Object.values(f0s)) {
    const fr = rel.map((r) => f[r])
    for (const i of keep) { let s = 0; for (let a = 0; a < n; a++) for (let b = 0; b < n; b++) s += kr[keep.indexOf(i)][a] * I[a][b] * fr[b]; f[i] -= s }
    for (const r of rel) f[r] = 0
  }
}
