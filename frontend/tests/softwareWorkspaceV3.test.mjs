import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')

test('settings remains reachable when the narrow layout hides its left rail', () => {
  const studio = source('../src/WarmStudio.tsx')
  const css = source('../src/warm-studio.css')
  assert.match(css, /@media\(max-width:720px\)[\s\S]*?\.warm-rail\s*\{\s*display:none/)
  assert.match(studio, /className="warm-header-settings"[^>]*onClick=\{props\.onOpenSettings\}/)
  assert.match(studio, /<details className="warm-more"[\s\S]*?<button onClick=\{props\.onOpenSettings\}>/)
  assert.doesNotMatch(css, /\.warm-header-settings\s*\{\s*display:none/)
})

test('software pages provide a separate editor without automatic screen sharing or scientific certification', () => {
  const studio = source('../src/WarmStudio.tsx')
  const workspace = source('../src/components/ScientificSoftwareWorkspace.tsx')
  const editor = source('../src/components/CodeWorkbenchEditor.tsx')
  assert.match(studio, /\['Python', 'Mathics3', 'Wolfram', 'MATLAB', 'Materials Studio'\]/)
  assert.doesNotMatch(studio, /<ExternalWindowPreview/)
  assert.match(editor, /monaco-editor-core/)
  assert.match(workspace, /api\.simulationAdapters\(\)/)
  assert.match(workspace, /api\.probeSimulationAdapter\(engine\)/)
  assert.match(workspace, /image\/png.*image\/jpeg.*image\/webp/)
  assert.doesNotMatch(workspace, /getDisplayMedia|scientific_validation_passed|model_state_verified/)
  assert.match(workspace, /Mathics3.*?not Wolfram Engine and never falls back/s)
  assert.match(workspace, /run\.mathics_result\.latex/)
})

test('script execution is explicit, stoppable and visually separated from scientific evidence', () => {
  const workspace = source('../src/components/ScientificSoftwareWorkspace.tsx')
  const runtime = source('../../backend/app/script_workspace.py')
  assert.match(workspace, /api\.startLocalScript\(engine, draft\)/)
  assert.match(workspace, /api\.cancelLocalScript\(run\.run_id\)/)
  assert.match(workspace, /checked=\{confirmed\}/)
  assert.match(workspace, /runnerAvailable/)
  assert.match(runtime, /scientific_validation_passed": False/)
  assert.match(runtime, /model_state_verified": False/)
  assert.match(runtime, /OwnedProcess\.spawn/)
  assert.doesNotMatch(runtime, /shell=True/)
})
