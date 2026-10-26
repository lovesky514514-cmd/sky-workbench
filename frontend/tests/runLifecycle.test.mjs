import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { isExecutionActive } from '../src/runUiPolicy.ts'

const studio = readFileSync(new URL('../src/WarmStudio.tsx', import.meta.url), 'utf8')

test('the displayed execution controls follow the active lifecycle', () => {
  assert.match(studio, /const executionActive = isExecutionActive\(props\.session\?\.state\)/)
  assert.match(studio, /executionActive && domain\?\.runState\?\.pauseCapability === 'available'/)
  assert.match(studio, /props\.session\?\.state === 'PAUSED'/)
  assert.match(studio, /onClick=\{props\.onEmergencyStop\}/)

  for (const state of ['PRECHECK', 'BUILDING', 'SOLVING', 'EXPORTING']) {
    assert.equal(isExecutionActive(state), true, state)
  }
  for (const state of ['CANCEL_REQUESTED', 'CANCELED', 'INTERRUPTED', 'COMPLETED']) {
    assert.equal(isExecutionActive(state), false, state)
  }
})
