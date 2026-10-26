import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const settings = readFileSync(new URL('../src/components/WarmSettings.tsx', import.meta.url), 'utf8')
const general = readFileSync(new URL('../src/components/SettingsGeneral.tsx', import.meta.url), 'utf8')
const api = readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8')

test('engine probe requires an explicit settings button', () => {
  assert.match(settings, /onProbe=\{\(engine\) => void probeAdapter\(engine\)\}/)
  assert.match(general, /adapter\.probe === 'AVAILABLE_ON_REQUEST'/)
  assert.match(general, /onClick=\{\(\) => props\.onProbe\(adapter\.engine\)\}/)
  assert.match(api, /probeSimulationAdapter:.*method: 'POST'/)
})

test('successful handshake remains separate from scientific execution', () => {
  assert.match(general, /HANDSHAKE_PASSED/)
  assert.match(general, /模型读回和科学执行仍未验证/)
  assert.match(general, /LICENSE_OR_ACTIVATION_REQUIRED/)
})
