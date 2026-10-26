import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')

test('home COMSOL status follows discovered version and separates Bridge verification', async () => {
  const { comsolHomeStatus } = await import('../src/installationStatus.ts')
  assert.equal(comsolHomeStatus('COMSOL 6.2', true, '', false, true), 'COMSOL 6.2 已检测 · Bridge 未验证')
  assert.equal(comsolHomeStatus('COMSOL 6.4', true, '', false, false), 'COMSOL 6.4 detected · Bridge unverified')
  assert.equal(comsolHomeStatus('COMSOL 6.3', true, '6.4', true, true), 'COMSOL 6.4 已连接')
  assert.equal(comsolHomeStatus('', false, '', false, true), 'COMSOL 不可用')
  assert.equal(comsolHomeStatus('invalid-path', true, '', false, true), 'COMSOL 已检测 · Bridge 未验证')
})

test('first-run readiness does not certify an untested cloud key or an inactive local model', async () => {
  const { environmentReadiness, verifiedProviderReady } = await import('../src/installationStatus.ts')
  assert.equal(environmentReadiness(false, true, true), 'BLOCKED')
  assert.equal(environmentReadiness(true, false, true), 'DEGRADED')
  assert.equal(environmentReadiness(true, true, false), 'DEGRADED')
  assert.equal(environmentReadiness(true, true, true), 'READY')
  const providers = [
    { name: 'ollama', kind: 'local', status: 'Ready', model: 'qwen3:8b', available_models: ['qwen3:4b'] },
    { name: 'deepseek', kind: 'cloud', status: 'Configured', model: 'deepseek-chat', available_models: [] },
  ]
  assert.equal(verifiedProviderReady(providers, 'deepseek'), false)
  assert.equal(verifiedProviderReady(providers, 'ollama'), false)
  providers[0].available_models.push('qwen3:8b')
  assert.equal(verifiedProviderReady(providers, ''), true)
  assert.equal(verifiedProviderReady(providers, 'deepseek'), false)
})

test('onboarding lists only measured optional scientific engines', async () => {
  const { detectedScientificEngines } = await import('../src/installationStatus.ts')
  assert.deepEqual(detectedScientificEngines({ adapters: [
    { engine: 'MATLAB', installed: false },
    { engine: 'Wolfram', installed: true, executable: 'private-path' },
    { engine: 'Materials Studio', installed: true },
    { engine: 'unknown', installed: true },
  ] }), ['Wolfram', 'Materials Studio'])
  assert.deepEqual(detectedScientificEngines({ adapters: null }), [])
})

test('settings describes discovered engines without exposing internal compatibility labels', async () => {
  const { scientificAdapterSummary } = await import('../src/installationStatus.ts')
  assert.equal(scientificAdapterSummary(true, true, true), '已检测到安装；Bridge、模型读回和求解仍需验证')
  assert.equal(scientificAdapterSummary(true, false, false), 'Installation detected; license, model readback and execution remain unverified')
  assert.equal(scientificAdapterSummary(false, false, true), '未检测到受支持的本机入口')
})

test('onboarding and Settings expose the measured optional-engine scan and API setup path', () => {
  const app = source('../src/App.tsx')
  const studio = source('../src/WarmStudio.tsx')
  const onboarding = source('../src/components/WarmOnboarding.tsx')
  const settings = source('../src/components/WarmSettings.tsx')
  const connection = source('../src/components/ProviderConnectionSettings.tsx')
  const progress = source('../src/components/InstallationScanProgress.tsx')
  assert.match(app, /comsol_java_environment/)
  assert.match(studio, /comsolHomeStatus/)
  for (const name of ['matlab', 'wolfram', 'materials_studio']) assert.match(progress, new RegExp(name))
  assert.match(onboarding, /onOpenProviderSettings/)
  assert.match(settings, /initialSection/)
  assert.match(settings, /ProviderConnectionSettings/)
  assert.match(connection, /连接 AI/)
  assert.match(connection, /selectedProviderName/)
})
