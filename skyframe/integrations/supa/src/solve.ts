import type { FemModel, FemResult } from './types'
import { solveFem as solveBuiltin } from './builtin'

// Drop-in for supa-fem-kit's src/solve.ts: the same solveFem(model, progress?) => FemResult, solved by the
// SkyFrame engine (OpenSees) running on this machine. When the engine is not running, Supa's own solver
// answers, exactly as before, and progress says so.
//
// Install: rename the kit's src/solve.ts to src/builtin.ts (no edit inside it), put this file in its place.
// The worker (src/worker.ts) is unchanged: solveFem stays synchronous. It talks to the engine with synchronous
// XMLHttpRequest, which browsers allow inside a Web Worker, polling the job for its steps.

/** Where the engine listens. Set `self.SKYFRAME_ENGINE_URL` before the worker starts to change it. */
export const ENGINE_URL: string = (globalThis as { SKYFRAME_ENGINE_URL?: string }).SKYFRAME_ENGINE_URL ?? 'http://127.0.0.1:8766'
/** The engine API this file speaks; a different major version is not used. */
export const ENGINE_API_MAJOR = 0

type Reply = { status: number; text: string }
type Http = (method: 'GET' | 'POST', url: string, body?: string) => Reply

/** The engine turned the model down (malformed). Not retried with the built-in solver: it would hide the fault. */
export class EngineRefused extends Error {}

let http: Http = (method, url, body) => {
  const x = new XMLHttpRequest()
  x.open(method, url, false)
  if (body !== undefined) x.setRequestHeader('Content-Type', 'application/json')
  x.send(body ?? null)
  return { status: x.status, text: x.responseText }
}
/** For tests and other hosts: replace how requests are made. */
export function setTransport(t: Http) { http = t }

/** Which solver produced the last result. */
export let lastSolver: 'skyframe' | 'builtin' = 'builtin'

export function solveFem(model: FemModel, progress?: (step: string) => void): FemResult {
  let job: string
  try {
    const v = http('GET', `${ENGINE_URL}/engine/version`)
    if (v.status !== 200) throw new Error(`engine answered ${v.status}`)
    const ver = JSON.parse(v.text) as { api: string; jobs?: string[] }
    if (Number(ver.api.split('.')[0]) !== ENGINE_API_MAJOR || !(ver.jobs ?? []).includes('supa')) {
      throw new Error(`engine API ${ver.api} is not ${ENGINE_API_MAJOR}.x`)
    }
    const s = http('POST', `${ENGINE_URL}/engine/supa/solve`, JSON.stringify(model))
    if (s.status === 400) throw new EngineRefused(`SkyFrame engine refused the model: ${JSON.parse(s.text).error}`)
    if (s.status !== 202) throw new Error(`engine answered ${s.status}`)
    job = (JSON.parse(s.text) as { job: string }).job
  } catch (err) {
    if (err instanceof EngineRefused) throw err
    progress?.('SkyFrame engine not running: using the built-in solver')
    lastSolver = 'builtin'
    return solveBuiltin(model, progress)
  }

  let after = 0
  for (;;) {
    const r = http('GET', `${ENGINE_URL}/engine/jobs/${job}?after=${after}&wait=20`)
    if (r.status !== 200) throw new Error(`SkyFrame engine lost the job (${r.status})`)
    const p = JSON.parse(r.text) as { state: string; steps: { step: string }[]; next: number; error?: string }
    for (const st of p.steps) progress?.(st.step)
    after = p.next
    if (p.state === 'done') break
    if (p.state === 'failed' || p.state === 'cancelled') throw new Error(`SkyFrame engine: ${p.error || p.state}`)
  }
  const out = http('GET', `${ENGINE_URL}/engine/jobs/${job}/result`)
  if (out.status !== 200) throw new Error(`SkyFrame engine: no result (${out.status})`)
  lastSolver = 'skyframe'
  return JSON.parse(out.text) as FemResult
}
