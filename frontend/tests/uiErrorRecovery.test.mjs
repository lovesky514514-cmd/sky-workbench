import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { safeUiError } from '../src/safeUiError.ts'

const plan = readFileSync(new URL('../src/components/PlanCard.tsx', import.meta.url), 'utf8')
const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')

test('ordinary UI failures do not reveal paths or credential shaped values', () => {
  const blocked = [
    'E:/private/session.log',
    'Bearer abcdefghijklmnop',
    'sk-abcdefghijklmnop',
    'user@example.com',
  ]
  for (const value of blocked) assert.doesNotMatch(safeUiError(value), new RegExp(value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  assert.equal(safeUiError('Connection timed out'), 'Connection timed out')
})

test('failed plan save keeps editing open and exposes a safe error', () => {
  assert.match(plan, /catch \(cause\) \{ setSaveError\(safeUiError\(cause\)\) \}/)
  assert.match(plan, /saveError && <p className="plan-save-error" role="alert">/)
  assert.match(app, /void action\(\)\.catch\(\(cause\) => setWarning\(safeUiError\(cause\)\)\)/)
})
