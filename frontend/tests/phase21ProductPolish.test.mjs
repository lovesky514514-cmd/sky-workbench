import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import path from 'node:path'

const root = path.resolve(import.meta.dirname, '..')
const read = (relative) => readFileSync(path.join(root, relative), 'utf8')

const app = read('src/App.tsx')
const warm = read('src/WarmStudio.tsx')
const fixture = read('src/Phase2VisualFixture.tsx')
const canvas = read('src/components/ScientificCanvas.tsx')
const intervention = read('src/components/HumanInterventionPanel.tsx')
const main = read('src/main.tsx')
const html = read('index.html')

assert.ok(existsSync(path.join(root, 'src/assets/brand/workbench-icon-source.png')), 'immutable source brand asset must be preserved')
for (const size of [16, 24, 32, 48, 64, 128, 256, 512]) {
  assert.ok(existsSync(path.join(root, `src/assets/brand/workbench-icon-${size}.png`)), `missing ${size}px brand derivative`)
}
assert.ok(existsSync(path.join(root, 'src-tauri/icons/icon.ico')), 'multi-resolution Windows ICO is required')
assert.ok(warm.includes('<BrandIcon'), 'runtime top bar must use BrandIcon')
assert.ok(fixture.includes('<BrandIcon'), 'visual fixture top bar must use BrandIcon')
assert.ok(!app.includes('className="brand-mark">CS<'), 'legacy CS mark remains in App')
assert.ok(!fixture.includes('className="brand-mark">CS<'), 'legacy CS mark remains in visual fixture')
assert.ok(warm.includes('warm-tabs'), 'warm shell must expose scientific workspace tabs')
assert.ok(canvas.includes('verified-2d-viewport'), 'verified 2D scientific viewport is missing')
assert.ok(canvas.includes('rectangle-readback'), 'rectangle readback schematic is missing')
assert.ok(intervention.includes('interventionActions'), 'state-specific intervention action selection is missing')
assert.ok(main.includes("'./phase21.css'"), 'Phase 2.1 typography/product polish layer is missing')
assert.match(html, /rel="icon"[^>]+workbench-icon-32\.png/, 'favicon must use the new brand')

console.log('Frontend Phase 2.1 brand/product polish contract: PASS')
