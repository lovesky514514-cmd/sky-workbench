import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

test('academic export is a separate presentation PNG, never a replacement for evidence', () => {
  const source = readFileSync(new URL('../src/components/academicFigure.ts', import.meta.url), 'utf8')
  assert.match(source, /drawImage\(image, left, imageTop\)/)
  assert.match(source, /canvas\.toBlob/)
  assert.match(source, /Presentation copy/)
  assert.match(source, /originalSha256/)
  assert.doesNotMatch(source, /api\.|fetch\(/)
})

test('verified gallery exposes the presentation export only after artifact verification', () => {
  const source = readFileSync(new URL('../src/components/VerifiedArtifactViewer.tsx', import.meta.url), 'utf8')
  assert.match(source, /state === 'verified'/)
  assert.match(source, /exportAcademicFigure/)
})
