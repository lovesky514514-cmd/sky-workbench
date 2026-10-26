import assert from 'node:assert/strict'
import test from 'node:test'

import { displayPlanForMessage, phase2Plan } from '../src/planProjection.ts'

const original = { schema_version: '2.0', plan_id: 'p-1', updated_at: 'r-1', title: 'Original' }

test('history keeps its original plan revision after the current plan changes', () => {
  const current = { ...original, updated_at: 'r-2', title: 'Edited' }
  const sameRevision = { ...original }
  assert.equal(displayPlanForMessage(original, current), original)
  assert.equal(displayPlanForMessage(original, original), original)
  assert.equal(displayPlanForMessage(original, sameRevision), sameRevision)
  assert.equal(phase2Plan({ schema_version: '1.0', plan_id: 'p-1' }), null)
})
