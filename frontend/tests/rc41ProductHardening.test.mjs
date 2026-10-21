import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const canvas = readFileSync(new URL('../src/components/ScientificCanvas.tsx', import.meta.url), 'utf8')
const viewer = readFileSync(new URL('../src/components/VerifiedArtifactViewer.tsx', import.meta.url), 'utf8')
const selection = readFileSync(new URL('../src/components/verifiedImageArtifacts.ts', import.meta.url), 'utf8')
const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
const api = readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8')
const tauri = readFileSync(new URL('../src-tauri/src/main.rs', import.meta.url), 'utf8')

test('RC4.1 uses frozen evidence to reopen real result artifacts', () => {
  assert.match(canvas, /VerifiedArtifactViewer/)
  assert.match(selection, /evidence_contract/)
  assert.match(viewer, /sha256/)
  assert.match(viewer, /artifactObjectUrl/)
  assert.match(viewer, /Evidence integrity lost/)
  assert.match(viewer, /No verified result artifact available/)
  assert.match(api, /\/api\/artifacts\//)
})

test('RC4.1 keeps fixture rendering outside the production artifact branch', () => {
  assert.match(canvas, /view === 'results' && \(visualState \?/)
  assert.match(canvas, /VISUAL QA FIXTURE · NOT SCIENTIFIC DATA/)
  assert.match(canvas, /visualState \? /)
})

test('Warm Studio first-run opens before runtime discovery and uses a new completion key', () => {
  const guide = readFileSync(new URL('../src/components/WarmOnboarding.tsx', import.meta.url), 'utf8')
  assert.match(app, /firstRunOpen && <WarmOnboarding/)
  assert.doesNotMatch(app, /firstRunOpen && runtimeReady &&/)
  assert.match(guide, /warm-studio-onboarding-v2/)
  assert.match(guide, /pullOllama/)
  assert.match(guide, /Scientific Runtime/)
  assert.match(guide, /COMSOL 6\.3/)
})

test('Warm settings retain provider and cache APIs behind product navigation', () => {
  const settings = readFileSync(new URL('../src/components/WarmSettings.tsx', import.meta.url), 'utf8')
  assert.match(app, /<WarmSettings/)
  assert.match(settings, /saveProviders/)
  assert.match(settings, /testProvider/)
  assert.match(settings, /acceptProvider/)
  assert.match(settings, /clearStorage/)
  assert.match(settings, /onOpenSetup/)
})

test('RC4.1 Tauri bootstrap accepts local Ollama and no packaged model bytes', () => {
  assert.match(tauri, /query_ollama_models/)
  assert.match(tauri, /OLLAMA_MODELS/)
  assert.match(tauri, /model_setup_required/)
  assert.match(tauri, /ollama_owned: false/)
  assert.doesNotMatch(tauri, /spawn_packaged_ollama/)
})

test('RC4.1 reclaims the exact Workbench-owned PyInstaller process tree', () => {
  assert.match(tauri, /child\.pid\(\)/)
  assert.match(tauri, /taskkill\.exe/)
  assert.match(tauri, /\["\/PID", &owned_pid, "\/T", "\/F"\]/)
})
