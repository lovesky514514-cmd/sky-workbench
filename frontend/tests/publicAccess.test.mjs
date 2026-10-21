import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import test from 'node:test'

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')
const exists = (path) => existsSync(new URL(path, import.meta.url))

test('public desktop starts the Workbench without a tester-license gate', () => {
  const entry = read('../src/main.tsx')
  const desktop = read('../src-tauri/src/main.rs')
  assert.match(entry, /let screen = <App\s*\/>/)
  assert.match(desktop, /let process = spawn_runtime\(app\.handle\(\)\)\.ok\(\)/)
  assert.doesNotMatch(desktop, /beta_activation|beta_import_license|beta_clear_activation|Beta activation is required/)
  assert.equal(exists('../src/BetaActivationGate.tsx'), false)
  assert.equal(exists('../src-tauri/src/beta_activation.rs'), false)
})

test('local Runtime and Java Bridge session authentication remain present', () => {
  const desktop = read('../src-tauri/src/main.rs')
  const bridge = read('../../java_bridge/src/main/java/workbench/bridge/ComsolBridgeServer.java')
  assert.match(desktop, /COMSOL_WORKBENCH_SESSION_TOKEN/)
  assert.match(desktop, /X-Workbench-Token/)
  assert.match(bridge, /COMSOL_WORKBENCH_JAVA_BRIDGE_TOKEN/)
  assert.match(bridge, /X-Workbench-Bridge-Token/)
})
