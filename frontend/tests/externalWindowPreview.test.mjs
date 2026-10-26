import assert from 'node:assert/strict'
import test from 'node:test'

import { captureIsWindowOnly, captureStatus, stopCapture } from '../src/externalWindowPolicy.ts'

test('external preview accepts only an explicitly identified window', () => {
  assert.equal(captureIsWindowOnly({ displaySurface: 'window' }), true)
  for (const displaySurface of ['monitor', 'browser', undefined]) {
    assert.equal(captureIsWindowOnly({ displaySurface }), false)
  }
})

test('a canceled or unsupported picker never reports a live preview', () => {
  assert.equal(captureStatus('NotAllowedError'), 'not-allowed')
  assert.equal(captureStatus('NotFoundError'), 'unavailable')
  assert.equal(captureStatus('NotReadableError'), 'unavailable')
  assert.equal(captureStatus('unknown'), 'unavailable')
})

test('ending a preview stops every track owned by that selection', () => {
  const stopped = [0, 0]
  const tracks = stopped.map((_, index) => ({ stop: () => { stopped[index] += 1 } }))
  stopCapture({ getTracks: () => tracks })
  assert.deepEqual(stopped, [1, 1])
  stopCapture(null)
})
