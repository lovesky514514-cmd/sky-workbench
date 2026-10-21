import { readFileSync } from 'node:fs'
import assert from 'node:assert/strict'

const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
  + readFileSync(new URL('../src/components/ScientificWorkspace.tsx', import.meta.url), 'utf8')
const api = readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8')
const css = readFileSync(new URL('../src/styles.css', import.meta.url), 'utf8')
const tauri = readFileSync(new URL('../src-tauri/tauri.conf.json', import.meta.url), 'utf8')

for (const label of ['Geometry', 'Physics', 'Boundary', 'Mesh', 'Solver', 'Evidence']) {
  assert.ok(app.includes(label), 'missing live stage ' + label)
}
for (const action of ['Pause', 'Modify', 'Approve', 'Reject', 'Resume']) {
  assert.ok(app.includes(action), 'missing human control ' + action)
}
assert.ok(api.includes('/execution/eta'), 'ETA must come from the runtime ledger API')
assert.ok(app.includes('Estimated remaining time'))
assert.ok(app.includes('Current uncertainty'))
assert.ok(app.includes('Need human confirmation'))
assert.ok(css.includes('.scientific-workbench-grid'))

const windowConfig = JSON.parse(tauri).app.windows[0]
assert.equal(windowConfig.width / windowConfig.height, 16 / 9)
// The historical workspace contract remains, while candidate metadata advances.
assert.equal(JSON.parse(tauri).version, '2.0.0')

console.log('RC4 scientific workspace contract: PASS')
