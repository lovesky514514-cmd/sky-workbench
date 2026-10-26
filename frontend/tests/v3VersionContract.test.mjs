import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8').replace(/^\uFEFF/, '')

test('V3 product metadata agrees without renaming the scientific Runtime', () => {
  const packageJson = JSON.parse(read('../package.json'))
  const lock = JSON.parse(read('../package-lock.json'))
  const tauri = JSON.parse(read('../src-tauri/tauri.conf.json'))
  const profile = JSON.parse(read('../src-tauri/resources/knowledge_base/build_profile.json'))
  assert.equal(packageJson.version, '3.0.0')
  assert.equal(lock.version, '3.0.0')
  assert.equal(lock.packages[''].version, '3.0.0')
  assert.equal(tauri.version, '3.0.0')
  assert.equal(profile.version, '3.0.0')
  assert.match(read('../src-tauri/Cargo.toml'), /^version = "3\.0\.0"$/m)
  assert.match(read('../src-tauri/Cargo.lock'), /name = "sky-workbench"\r?\nversion = "3\.0\.0"/)
  assert.match(read('../../scripts/prepare_desktop.ps1'), /version = '3\.0\.0'/)
  assert.match(read('../../scripts/prepare_desktop.ps1'), /SKY_EXPECTED_SIDECAR_SHA256/)
  assert.doesNotMatch(read('../../scripts/prepare_desktop.ps1'), /C882D9724ED18DBAB6038EB1DC4359D9FECA7562D2ADC7D75A4A40E838C468FD/)
  assert.match(read('../src/App.tsx'), /<b>v3\.0\.0<\/b>/)
})
