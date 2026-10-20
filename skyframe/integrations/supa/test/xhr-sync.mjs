// A synchronous XMLHttpRequest for Node, so the kit's own tools (npm run check / npm run solve) can run the
// drop-in exactly as a browser worker would. Load with: node --import ./xhr-sync.mjs  (or tsx --import).
// Each request runs in a short child process that does the fetch; the parent waits for it.
import { execFileSync } from 'node:child_process'

const CHILD = `
let s = ''; process.stdin.on('data', (d) => (s += d)); process.stdin.on('end', async () => {
  const r = JSON.parse(s)
  try {
    const res = await fetch(r.url, { method: r.method, headers: r.headers, body: r.body ?? undefined })
    process.stdout.write(JSON.stringify({ status: res.status, text: await res.text() }))
  } catch (e) { process.stdout.write(JSON.stringify({ status: 0, error: String(e) })) }
})`

globalThis.XMLHttpRequest = class {
  open(method, url, async) {
    if (async !== false) throw new Error('only synchronous requests are supported here')
    this.method = method; this.url = url; this.headers = {}
  }
  setRequestHeader(k, v) { this.headers[k] = v }
  send(body) {
    const out = execFileSync(process.execPath, ['-e', CHILD], {
      input: JSON.stringify({ method: this.method, url: this.url, headers: this.headers, body }),
      maxBuffer: 1 << 30,
    })
    const r = JSON.parse(out.toString())
    if (r.status === 0) throw new Error(`NetworkError: ${r.error}`)
    this.status = r.status; this.responseText = r.text
  }
}
