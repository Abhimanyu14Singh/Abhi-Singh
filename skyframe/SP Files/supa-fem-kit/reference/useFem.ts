import { create } from 'zustand'
import { designOf, paramsOf } from '../model/design'
import { buildFem, type Case, type FemResult } from '../model/fem'
import type { Building } from '../model/resolve'
import { sizingOf } from '../model/sizing'
import { useApp } from '../store/store'
import { nomFt, units } from '../model/units'

// The analysis, run when asked — it is heavy — off the page's thread; its result kept against the
// building it was run on, so it is known to be out of date the moment the building changes.

interface FemState {
  status: 'idle' | 'running' | 'done' | 'error'
  step: string
  result: FemResult | null
  /** The building the result is for. */
  of: Building | null
  /** The model's size, and the systems the unheld nodes are in. */
  size: { nodes: number; els: number } | null
  looseIn: Record<string, number>
  error: string
  /** The case the view shows. */
  case: Case
  /** The model's nodes and the lines between them — its members and walls — to draw it bent. */
  /** The model's lines, node to node, and the piece each stands for — none for an arm. */
  geom: { xyz: number[]; lines: number[]; srcs: (string | undefined)[] } | null
  /** The deflected shape drawn over the building. */
  deflect: boolean
  /** The widest wall strip, ft. */
  mesh: number
  /** What the force colours are scaled to: the frame's members or the walls. */
  scope: 'frames' | 'walls'
  set: (p: Partial<FemState>) => void
  run: () => void
}

let worker: Worker | null = null

export const useFem = create<FemState>((setState, get) => ({
  status: 'idle', step: '', result: null, of: null, size: null, looseIn: {}, error: '', case: 'D', geom: null, deflect: false, mesh: nomFt(10, 3), scope: 'frames',
  set: (p) => setState(p),
  run: () => {
    if (get().status === 'running') return
    const { doc, building, project } = useApp.getState()
    const design = designOf(doc), params = paramsOf(project)
    const model = buildFem(doc, building, design, params, sizingOf(doc, building, design, params), get().mesh)
    setState({ status: 'running', step: 'Building the model', error: '', size: { nodes: model.xyz.length / 3, els: model.els.length } })
    worker?.terminate()
    worker = new Worker(new URL('../model/fem.worker.ts', import.meta.url), { type: 'module' })
    worker.onmessage = (e: MessageEvent<{ step?: string; result?: FemResult; error?: string }>) => {
      if (e.data.step) setState({ step: e.data.step })
      if (e.data.error) setState({ status: 'error', error: e.data.error })
      if (e.data.result) {
        // The systems the unheld nodes belong to, by name.
        const srcOf = new Map(building.members.map((m) => [m.id, m.source]))
        const names = new Map(doc.apps.map((a) => [a.id, a.name]))
        const loose = new Set(e.data.result.looseNodes ?? [])
        const looseIn: Record<string, number> = {}
        const seen = new Set<string>()
        for (const el of model.els) {
          if (!el.src || (!loose.has(el.i) && !loose.has(el.j))) continue
          const name = names.get(srcOf.get(el.src) ?? '') ?? 'Walls'
          if (seen.has(el.src)) continue
          seen.add(el.src)
          looseIn[name] = (looseIn[name] ?? 0) + 1
        }
        const lines: number[] = [], srcs: (string | undefined)[] = []
        for (const el of model.els) if (el.kind !== 'arm') { lines.push(el.i, el.j); srcs.push(el.src) }
        setState({ status: 'done', step: '', result: e.data.result, of: building, looseIn, geom: { xyz: model.xyz, lines, srcs } })
        worker?.terminate(); worker = null
      }
    }
    worker.postMessage(model)
  },
}))

/** A mesh size on the units' step: the nearest 0.1 m, or the nearest foot. */
export const meshOn = (ft: number) => (units() === 'metric' ? Math.max(0.5, Math.round(ft * 0.3048 * 10) / 10) / 0.3048 : Math.max(2, Math.round(ft)))

// Switched, the mesh goes to the other units' step — 10 ft is 3 m, and 3 m 10 ft.
useApp.subscribe((s, prev) => {
  if ((paramsOf(s.project).units ?? 'imperial') !== (paramsOf(prev.project).units ?? 'imperial')) useFem.setState({ mesh: meshOn(useFem.getState().mesh) })
})
