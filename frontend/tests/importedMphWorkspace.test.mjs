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
  const studio = source('../src/WarmStudio.tsx')
  const viewer = source('../src/components/ComsolModelView.tsx')
  assert.match(ui, /await api\.importMph\(file\)/)
  assert.match(ui, /onOpen\(item\)/)
  assert.match(studio, /setOpenedImport\(\{ item, project: importProject \}\); setSoftwareOwner\(null\); setPage\('model'\)/)
  assert.match(viewer, /if \(kind === 'imported'\) void refresh/)
  assert.match(ui, /Import does not run a solver or certify scientific results/)
})

test('desktop opens only its fixed imported-model folder', () => {
  const shell = source('../src-tauri/src/main.rs')
  assert.match(shell, /fn open_imported_models/)
  assert.match(shell, /\.join\("models"\)\s*\.join\("imported"\)/)
})
