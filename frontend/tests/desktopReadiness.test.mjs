import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const root = new URL('../', import.meta.url)
const config = JSON.parse(readFileSync(new URL('src-tauri/tauri.conf.json', root), 'utf8'))
const settings = readFileSync(new URL('src/components/WarmSettings.tsx', root), 'utf8')
const general = readFileSync(new URL('src/components/SettingsGeneral.tsx', root), 'utf8')
const localModels = readFileSync(new URL('src/components/LocalModelSettings.tsx', root), 'utf8')
const onboarding = readFileSync(new URL('src/components/WarmOnboarding.tsx', root), 'utf8')
const app = readFileSync(new URL('src/App.tsx', root), 'utf8')
const scanView = readFileSync(new URL('src/components/InstallationScanProgress.tsx', root), 'utf8')

test('installer carries an offline WebView2 prerequisite', () => {
  assert.equal(config.bundle.windows.webviewInstallMode.type, 'offlineInstaller')
})

test('first-run environment scan starts automatically and reports measured progress', () => {
  assert.match(onboarding, /api\.startInstallationScan\(\)/)
  assert.match(onboarding, /api\.installationScanStatus\(\)/)
  assert.match(onboarding, /InstallationScanProgress/)
  assert.match(scanView, /scan\.checked/)
  assert.match(scanView, /scan\.total/)
  assert.match(scanView, /scan\.steps/)
  assert.match(settings, /SettingsGeneral/)
  assert.match(general, /InstallationScanProgress/)
  assert.doesNotMatch(app, /warm-startup-overlay/)
})

test('fast scans reveal their actual completed results one at a time', () => {
  assert.match(scanView, /setRevealed\(index \+ 1\)/)
  assert.match(scanView, /scan\.state === 'completed' && index >= revealed/)
  assert.match(scanView, /待展示/)
})

test('model actions remain unavailable without a running Ollama service', () => {
  assert.match(settings, /LocalModelSettings/)
  assert.match(localModels, /const serviceReady = /)
  assert.match(localModels, /disabled=\{!serviceReady/)
  assert.match(localModels, /pullState\.percent/)
})
