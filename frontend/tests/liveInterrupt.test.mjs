import { readFileSync } from 'node:fs'
import assert from 'node:assert/strict'

const api = readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8')
const inspector = readFileSync(new URL('../src/components/Inspector.tsx', import.meta.url), 'utf8')
const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')

for (const endpoint of ['/run/pause', '/run/correction', '/run/resume']) {
  assert.ok(api.includes(endpoint), `missing typed live endpoint ${endpoint}`)
}
assert.ok(inspector.includes("capabilities.live_interrupt?.status === 'VERIFIED'"))
assert.ok(inspector.includes("session?.plan?.workflow_id === 'tds_2d_stationary_diffusion'"))
assert.ok(inspector.includes('parameters.diffusion_coefficient'))
assert.ok(!inspector.includes("<button disabled title={t('Interruptible execution is disabled"))
assert.ok(app.includes('pauseLiveRun'))
assert.ok(app.includes('correctLiveRun'))
assert.ok(app.includes('resumeLiveRun'))

console.log('Live interrupt UI/API contract: PASS')
