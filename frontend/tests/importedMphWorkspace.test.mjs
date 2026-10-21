import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

const source = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')

test('home separates imported MPH from newly created work', () => {
  const ui = source('../src/WarmStudio.tsx')
  assert.match(ui, /homeSection === 'imported'/)
  assert.match(ui, /<ImportedMphWorkspace locale=\{props\.locale\}/)
  assert.match(ui, /homeSection === 'new'/)
})

test('drop import auto-requests real preview without displaying scientific success', () => {
  const ui = source('../src/components/ImportedMphWorkspace.tsx')
  assert.match(ui, /await api\.importMph\(file\)/)
  assert.match(ui, /await loadPreview\(item\.id\)/)
  assert.match(ui, /preview\.model_state_verified/)
  assert.match(ui, /not a solve, a modification, or scientific validation/)
})

test('desktop opens only its fixed imported-model folder', () => {
  const shell = source('../src-tauri/src/main.rs')
  assert.match(shell, /fn open_imported_models/)
  assert.match(shell, /\.join\("models"\)\s*\.join\("imported"\)/)
})
