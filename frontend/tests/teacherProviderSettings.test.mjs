import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const settings = readFileSync(new URL('../src/components/WarmSettings.tsx', import.meta.url), 'utf8')
const general = readFileSync(new URL('../src/components/SettingsGeneral.tsx', import.meta.url), 'utf8')
const connection = readFileSync(new URL('../src/components/ProviderConnectionSettings.tsx', import.meta.url), 'utf8')
const localModels = readFileSync(new URL('../src/components/LocalModelSettings.tsx', import.meta.url), 'utf8')
const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
const scanView = readFileSync(new URL('../src/components/InstallationScanProgress.tsx', import.meta.url), 'utf8')

test('first-time setup prefers cloud APIs; explicit local choice is retained', async () => {
  assert.match(settings, /initialSection = 'general'/)
  assert.match(settings, /ProviderConnectionSettings/)
  assert.match(connection, /initialProviderSelection\(providers, activeProvider\)/)
  assert.match(connection, /showInternational, setShowInternational/)
  assert.match(connection, /warm-provider-mode/)
  assert.match(connection, /aria-expanded=\{showInternational\}/)
  assert.match(connection, /selectedProviderName/)
  const { providerGroup, initialProviderSelection } = await import('../src/providerPresentation.ts')
  for (const name of ['deepseek', 'kimi', 'doubao']) assert.equal(providerGroup(name, 'cloud'), 'domestic')
  for (const name of ['openai', 'claude', 'gemini']) assert.equal(providerGroup(name, 'cloud'), 'international')
  const providers = [
    { name: 'ollama', kind: 'local' },
    { name: 'deepseek', kind: 'cloud' },
    { name: 'claude', kind: 'cloud' },
  ]
  assert.deepEqual(initialProviderSelection(providers, ''), { name: 'deepseek', cloud: true, international: false })
  assert.deepEqual(initialProviderSelection(providers, 'ollama'), { name: 'ollama', cloud: false, international: false })
  assert.deepEqual(initialProviderSelection(providers, 'claude'), { name: 'claude', cloud: true, international: true })
  assert.deepEqual(initialProviderSelection([providers[0]], ''), { name: 'ollama', cloud: false, international: false })
})

test('desktop model label follows explicit provider instead of stale Ollama bootstrap', () => {
  assert.match(app, /const activeAi = providers\.find/)
  assert.match(app, /selectedModel=\{displayedModel\} modelAvailability=\{displayedAvailability\}/)
})

test('scan and download bars use measured progress only', () => {
  assert.match(settings, /SettingsGeneral/)
  assert.match(general, /InstallationScanProgress/)
  assert.match(scanView, /scan\.checked/)
  assert.match(scanView, /scan\.total/)
  assert.match(settings, /LocalModelSettings/)
  assert.match(localModels, /pullState\.percent/)
  assert.match(localModels, /current layer|当前文件层/i)
})
