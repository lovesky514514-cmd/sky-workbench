import assert from 'node:assert/strict'
import test from 'node:test'

import { canRunReviewedPlan } from '../src/runUiPolicy.ts'

const plan = { plan_id: 'plan-1', updated_at: 'revision-1', runnable: true }

test('RUN requires a current, runnable plan and explicit review', () => {
  const cases = [
    [plan, 'session-A', 'session-A:plan-1:revision-1', false, false, true],
    [null, 'session-A', '', false, false, false],
    [{ ...plan, runnable: false }, 'session-A', 'session-A:plan-1:revision-1', false, false, false],
    [plan, 'session-A', '', false, false, false],
    [plan, 'session-A', 'session-A:plan-1:old', false, false, false],
    [plan, 'session-B', 'session-A:plan-1:revision-1', false, false, false],
    [plan, '', 'session-A:plan-1:revision-1', false, false, false],
    [plan, 'session-A', 'session-A:plan-1:revision-1', true, false, false],
    [plan, 'session-A', 'session-A:plan-1:revision-1', false, true, false],
    [{ ...plan, updated_at: '' }, 'session-A', 'session-A:plan-1:', false, false, false],
  ]
  for (const [candidate, sessionId, reviewedId, busy, pendingIntent, expected] of cases) {
    assert.equal(canRunReviewedPlan(candidate, sessionId, reviewedId, busy, pendingIntent), expected)
  }
})

test('finished or active attempts cannot reuse a checked plan to submit another RUN', () => {
  for (const state of ['COMPLETED', 'SOLVING', 'BUILDING', 'PAUSED', 'CANCEL_REQUESTED']) {
    assert.equal(canRunReviewedPlan(plan, 'session-A', 'session-A:plan-1:revision-1', false, false, state), false)
  }
  assert.equal(canRunReviewedPlan(plan, 'session-A', 'session-A:plan-1:revision-1', false, false, 'PLAN_READY'), true)
})
