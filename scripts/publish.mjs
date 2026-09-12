// Copy the exported site into the directory FastAPI serves it from.
//
// vite.config.js did this with `build.outDir`. Next's static export always
// writes to `out/`, so the move is a build step instead of a setting -- and it
// stays one line in package.json, with the destination still written down in
// exactly one place.
import { cp, mkdir, rm, access } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const from = resolve(here, '..', 'out')
const to = resolve(here, '..', '..', 'backend', 'app', 'static')

try {
  await access(from)
} catch {
  console.error(`publish: nothing at ${from} — did \`next build\` run?`)
  process.exit(1)
}

// Emptied first, so a file that a build stops producing does not linger and
// get served alongside the new ones. This is `emptyOutDir: true` from the Vite
// config, kept for the same reason.
await rm(to, { recursive: true, force: true })
await mkdir(to, { recursive: true })
await cp(from, to, { recursive: true })
console.log(`publish: ${from} -> ${to}`)
