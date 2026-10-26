import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')

test('model readback link reaches the live inspector without treating pixels as evidence', () => {
  const app = read('../src/App.tsx')
  const studio = read('../src/WarmStudio.tsx')
  const live = read('../src/components/LiveComsolView.tsx')
  assert.match(studio, /onOpenLiveInspector\(\)[\s\S]*?setTechnicalOpen\(true\)[\s\S]*?setPage\('evidence'\)/)
  assert.match(app, /onOpenLiveInspector=\{\(\) => setInspectorTab\('live'\)\}/)
  assert.match(app, /bridgeReadback=\{bridgeView\.readback\}/)
  assert.match(app, /sameBridgeAttachment\(status, latest\)/)
  assert.match(live, /not window pixels or scientific certification/)
  assert.doesNotMatch(studio, /<ExternalWindowPreview/)
})

test('settings has a visible fallback when Runtime is unavailable', () => {
  const app = read('../src/App.tsx')
  const studio = read('../src/WarmStudio.tsx')
  assert.match(studio, /className="warm-header-settings"[^>]*onClick=\{props\.onOpenSettings\}/)
  assert.match(app, /settingsOpen && \(!providerSettings \|\| !runtimeReady \|\| runtimeUnavailable\)/)
  assert.match(app, /settingsUnavailable = runtimeUnavailable \|\| \(runtimeReady && !providerSettings\)/)
  assert.match(app, /Runtime is unavailable\. Connection settings have not loaded/)
  assert.match(app, /settingsUnavailable && <div className="runtime-actions">[\s\S]*?api\.openLogs[\s\S]*?restartRuntime/)
})
