import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { solveFem } from '../src/solve'
import { CASES, type FemModel, type FemResult } from '../src/types'

// npm run solve -- models/demo.model.json
// Solves a model, prints its size, time and each case's balance, writes <name>.out.json beside it,
// and, where <name>.result.json is there, sets the result beside the one Supa's solver gave.

const file = process.argv[2] ?? 'models/demo.model.json'
const model = JSON.parse(readFileSync(file, 'utf8')) as FemModel
const t = performance.now()
const r = solveFem(model, (s) => console.log(`  ${s}`))
const secs = (performance.now() - t) / 1000
console.log(`nodes ${model.xyz.length / 3}, elements ${model.els.length}, diaphragms ${model.dia.length}, equations ${r.dofs}, ${secs.toFixed(1)} s`)
if (r.loose) console.log(`unheld nodes (moved more than 1 ft under D or L): ${r.loose}`)
for (const n of r.notes) console.log(`note: ${n}`)

console.log('\ncase   applied fx fy fz                     reactions fx fy fz                   out of balance')
for (const c of CASES) {
  const { applied: a, reactions: q } = r.balance[c]
  const off = Math.hypot(a[0] + q[0], a[1] + q[1], a[2] + q[2]) / Math.max(1e-9, Math.hypot(...a))
  console.log(`${c.padEnd(5)}  ${a.map((v) => v.toFixed(1).padStart(10)).join('')}   ${q.map((v) => v.toFixed(1).padStart(10)).join('')}   ${(off * 100).toFixed(3)}%`)
}
writeFileSync(file.replace(/\.model\.json$/, '.out.json'), JSON.stringify(r))

const base = file.replace(/\.model\.json$/, '.result.json')
if (existsSync(base) && base !== file) {
  const b = JSON.parse(readFileSync(base, 'utf8')) as FemResult
  // The largest difference, as a share of the largest value: per case, per quantity.
  const diff = (xs: number[], ys: number[]) => {
    let d = 0, m = 0
    for (let i = 0; i < xs.length; i++) { d = Math.max(d, Math.abs(xs[i] - (ys[i] ?? 0))); m = Math.max(m, Math.abs(ys[i] ?? 0)) }
    return m > 0 ? `${((d / m) * 100).toFixed(2)}%` : '-'
  }
  console.log(`\nagainst ${base}: largest difference over largest value`)
  console.log('case   max u      drift      N          M          V')
  for (const c of CASES) {
    const ids = Object.keys(b.pieces)
    const of = (x: FemResult, q: 'N' | 'M' | 'V') => ids.map((id) => x.pieces[id]?.[c]?.[q] ?? 0)
    const drift = (x: FemResult) => x.dia.flatMap((d) => d.u[c])
    const cols = [diff([r.maxU[c]], [b.maxU[c]]), diff(drift(r), drift(b)), diff(of(r, 'N'), of(b, 'N')), diff(of(r, 'M'), of(b, 'M')), diff(of(r, 'V'), of(b, 'V'))]
    console.log(`${c.padEnd(5)}  ${cols.map((s) => s.padEnd(11)).join('')}`)
  }
}
