import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

test('illustrative scientific fixtures are not static production imports', () => {
  const source = readFileSync(new URL('../src/main.tsx', import.meta.url), 'utf8')
  for (const fixture of ['WarmStudioFixture', 'WarmProductFixture', 'Phase2VisualFixture']) {
    assert.doesNotMatch(source, new RegExp(`^import\\s+.*${fixture}`, 'm'))
    assert.match(source, new RegExp(`await import\\('\\./${fixture}'\\)`))
  }
  const workspace = readFileSync(new URL('../src/WarmStudio.tsx', import.meta.url), 'utf8')
  assert.doesNotMatch(workspace, /DESIGN REVIEW · NOT COMSOL EVIDENCE|350 K/)
})
