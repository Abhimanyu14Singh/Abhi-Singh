import type { FemModel } from './types'
import { solveFem } from './solve'

// The solve off the page's own thread: a model in, its result out, where it has got to between.

self.onmessage = (e: MessageEvent<FemModel>) => {
  try {
    const r = solveFem(e.data, (step) => self.postMessage({ step }))
    self.postMessage({ result: r })
  } catch (err) {
    self.postMessage({ error: String(err) })
  }
}
