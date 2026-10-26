import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')

test('help stays above first-run guide and offers a closeable dialog', () => {
  const css = source('../src/warm-studio.css')
  const onboarding = source('../src/warm-product-surfaces.css')
  const helpLayer = Number(css.match(/\.warm-help-backdrop\{[^}]*z-index:(\d+)/)?.[1])
  const guideLayer = Number(onboarding.match(/\.warm-onboarding\{[^}]*z-index:(\d+)/)?.[1])
  assert.ok(helpLayer > guideLayer)
  assert.match(css, /\.warm-help-backdrop\{[^}]*--warm-surface:#fbf8f2/)
  assert.match(source('../src/components/WarmHelp.tsx'), /onClose/)
  assert.match(source('../src/WarmStudio.tsx'), /event\.currentTarget\.open = false/)
})

test('task composer keeps the prompt as the only primary entry point', () => {
  const studio = source('../src/WarmStudio.tsx')
  assert.match(studio, /Scientific task description/)
  assert.doesNotMatch(studio, /warm-example-fill/)
})

test('provider setup edits one selected provider and keeps diagnostics optional', () => {
  const settings = source('../src/components/WarmSettings.tsx')
  const connection = source('../src/components/ProviderConnectionSettings.tsx')
  assert.match(settings, /ProviderConnectionSettings/)
  assert.match(connection, /selectedProviderName/)
  assert.match(connection, /selectedProvider\s*&&/)
  assert.match(connection, /warm-provider-mode/)
  assert.match(connection, /setSelectedProviderName\(domesticProviders\[0\]\?\.name \?\? internationalProviders\[0\]\?\.name \?\? ''\)/)
  assert.match(connection, /<details className="warm-provider-advanced"/)
  assert.match(connection, /type="password"/)
  assert.match(connection, /activeProvider !== savedProvider/)
  assert.match(connection, /已选择 · 请保存设置/)
  assert.doesNotMatch(connection, /providers\.map\(\(provider\) => \{[\s\S]*?<section className="warm-settings-provider"/)
})

test('cloud provider groups and masked key state are separate from science status', async () => {
  const { providerGroup, credentialLabel } = await import('../src/providerPresentation.ts')
  assert.equal(providerGroup('deepseek', 'cloud'), 'domestic')
  assert.equal(providerGroup('gemini', 'cloud'), 'international')
  assert.equal(providerGroup('ollama', 'local'), 'local')
  assert.equal(credentialLabel(false, true), 'not-entered')
  assert.equal(credentialLabel(true, true), 'current-session')
  assert.equal(credentialLabel(true, false), 'not-needed')
})
