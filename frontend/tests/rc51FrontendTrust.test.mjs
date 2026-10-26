import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

// Compile pure selectors without a browser, Runtime, fixture route or COMSOL.
// They must fail closed even when an old network response arrives late.
function selector(relative) {
  const source = readFileSync(new URL(relative, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
  const exports = {}
  new Function('exports', js)(exports)
  return exports
}

const view = selector('../src/scientificView.ts')
const identity = selector('../src/modelIdentity.ts')
const source = (relative) => readFileSync(new URL(relative, import.meta.url), 'utf8')
const verdict = (runId) => ({ run_id: runId, completed: true, command_success: true,
  model_state_verified: true, scientific_validation_success: true, evidence_contract_passed: true, issues: [] })
const evidence = (runId, attemptId) => ({ passed: true, run_id: runId, attempt_id: attemptId,
  issues: [],
  artifacts: { temperature_image: { path: 'temperature.png', sha256: 'digest' } } })
const event = (sequence, type, payload) => ({ sequence, type, payload, id: `${sequence}`, created_at: '' })
const session = (id, runId, attemptId, state = 'COMPLETED') => ({ id, state, messages: [{
  role: 'assistant', run_id: runId, execution_verdict: verdict(runId),
  evidence_contract: evidence(runId, attemptId), artifacts: { temperature_image: 'temperature.png' },
}], events: [event(1, 'run.started', { run_id: runId }),
  event(2, 'run.attempt', { run_id: runId, attempt_id: attemptId }),
  event(3, 'run.completed', { run_id: runId, attempt_id: attemptId,
    execution_verdict: verdict(runId), evidence_contract: evidence(runId, attemptId) })] })
const domain = (id, runId, attemptId) => ({ contractVersion: 'rc42-v1', studyId: id,
  runState: { runId, attemptId }, authoritativeVerdict: verdict(runId), evidenceState: 'verified',
  contractGaps: [],
  resultArtifacts: [{ runId, attemptId, evidenceState: 'verified' }],
  objectIds: { boundaries: { left: 'source-left', right: 'source-right' } },
  modelSnapshot: { geometry: { features: [{ id: 'source-geometry', label: 'Rectangle from readback' }], domainCount: 2 },
    boundaries: [{ id: 'source-left', location: 'left edge', label: 'Boundary 7' }] },
})

test('A verified → new B is never verified in the render that still holds A domain', () => {
  const a = session('A', 'run-A', 'attempt-A')
  const b = { id: 'B', state: 'IDLE', messages: [], events: [] }
  assert.equal(view.verifiedCurrentResult(a, domain('A', 'run-A', 'attempt-A')), true)
  assert.equal(view.currentScientificDomain(b, domain('A', 'run-A', 'attempt-A')), null)
  assert.equal(view.verifiedCurrentResult(b, domain('A', 'run-A', 'attempt-A')), false)
})

test('late A response cannot overwrite B, including an A → B → A return', async () => {
  let active = { generation: 1, sessionId: 'A' }
  const oldA = { ...active }
  active = { generation: 2, sessionId: 'B' }
  assert.equal(view.isCurrentSelection(oldA, active), false)
  assert.equal(view.isCurrentSelection({ ...active }, active), true)
  active = { generation: 3, sessionId: 'A' }
  assert.equal(view.isCurrentSelection(oldA, active), false)
  assert.equal(view.isCurrentSelection({ ...active }, active), true)
  const lateResponse = Promise.resolve(domain('A', 'run-A', 'attempt-A'))
  assert.equal(view.isCurrentSelection(oldA, active) ? await lateResponse : null, null)
  assert.match(source('../src/App.tsx'), /isCurrentSelection\(/)
})

test('out-of-order refresh for one selected session cannot roll back the journal cursor', () => {
  const old = { ...session('A', 'run-A', 'attempt-A'), updated_at: '2026-10-17T00:00:00Z' }
  const current = { ...old, updated_at: '2026-10-17T00:00:01Z', events: [...old.events,
    event(4, 'evidence.invalidated', { run_id: 'run-A', attempt_id: 'attempt-A' })] }
  assert.equal(view.sessionIsNotOlder(old, current), false)
  assert.equal(view.sessionIsNotOlder(current, old), true)
  assert.equal(view.sessionIsNotOlder({ ...old, updated_at: '2026-10-16T23:59:59Z' }, old), false)
})

test('different attempt or run cannot reuse a prior completed artifact/verdict', () => {
  const prior = session('A', 'run-A', 'attempt-old')
  const retried = { ...prior, events: [...prior.events, event(4, 'run.attempt', { run_id: 'run-A', attempt_id: 'attempt-new' })] }
  assert.equal(view.verifiedCurrentResult(retried, domain('A', 'run-A', 'attempt-old')), false)
  const nextRun = { ...prior, events: [...prior.events, event(4, 'run.started', { run_id: 'run-new' })], state: 'PRECHECK' }
  assert.equal(view.currentScientificDomain(nextRun, domain('A', 'run-A', 'attempt-old')), null)
  assert.equal(view.verifiedCurrentResult(nextRun, domain('A', 'run-A', 'attempt-old')), false)
})

test('local invalidation immediately revokes verified display before a domain refresh', () => {
  const a = session('A', 'run-A', 'attempt-A')
  const invalidated = { ...a, events: [...a.events, event(4, 'evidence.invalidated', {
    run_id: 'run-A', attempt_id: 'attempt-A', object_id: 'source-left',
  })] }
  assert.equal(view.currentEvidenceState(invalidated, domain('A', 'run-A', 'attempt-A')), 'stale')
  assert.equal(view.verifiedCurrentResult(invalidated, domain('A', 'run-A', 'attempt-A')), false)
})

test('review highlight and approval require exact backend sourceId, never label/index', () => {
  const current = domain('A', 'run-A', 'attempt-A')
  const review = { objectId: 'source-left', runId: 'run-A', attemptId: 'attempt-A' }
  assert.equal(identity.reviewedBoundary(current, review)?.sourceId, 'source-left')
  assert.equal(identity.reviewedBoundary(current, { ...review, objectId: 'Boundary 7' }), null)
  assert.equal(identity.reviewedBoundary(current, { ...review, objectId: 'source-right' }), null)
  assert.equal(identity.reviewedBoundary(current, { ...review, attemptId: 'stale' }), null)
})

test('missing real object map never becomes the illustrative Geometry 1 / Domain 1', () => {
  const current = domain('A', 'run-A', 'attempt-A')
  assert.deepEqual(identity.geometryIdentity(current.modelSnapshot), {
    sourceId: 'source-geometry', presentationId: 'Rectangle from readback',
  })
  assert.equal(identity.geometryIdentity({ geometry: { domainCount: 2 } }).sourceId, null)
  assert.equal(identity.domainIdentity(current.modelSnapshot).sourceId, null)
  assert.doesNotMatch(source('../src/WarmStudio.tsx'), /t\('Domain'\)\} 1|t\('Geometry 1'\)/)
})

test('runtime disconnected or absent evidence never falls back to fabricated science', () => {
  const a = session('A', 'run-A', 'attempt-A')
  assert.equal(view.verifiedCurrentResult(a, null), false)
  assert.equal(view.verifiedCurrentResult(a, { ...domain('A', 'run-A', 'attempt-A'), evidenceState: 'unavailable' }), false)
  assert.equal(view.verifiedCurrentResult(a, { ...domain('A', 'run-A', 'attempt-A'), resultArtifacts: [] }), false)
  const noFrozenEvidence = { ...a, events: a.events.map((item) => item.type === 'run.completed'
    ? { ...item, payload: { run_id: 'run-A', attempt_id: 'attempt-A' } } : item) }
  assert.equal(view.verifiedCurrentResult(noFrozenEvidence, domain('A', 'run-A', 'attempt-A')), false)
  assert.match(source('../src/App.tsx'), /domain=\{runtimeReady && !runtimeUnavailable \? selectedDomain : null\}/)
})

test('runtime JSON cannot turn string or numeric success flags into certification', () => {
  const a = session('A', 'run-A', 'attempt-A')
  const trusted = domain('A', 'run-A', 'attempt-A')
  for (const field of ['completed', 'command_success', 'model_state_verified',
    'scientific_validation_success', 'evidence_contract_passed']) {
    assert.equal(view.verifiedCurrentResult(a, {
      ...trusted, authoritativeVerdict: { ...trusted.authoritativeVerdict, [field]: 'true' },
    }), false, field)
    const badCompletion = { ...a, events: a.events.map((item) => item.type === 'run.completed'
      ? { ...item, payload: { ...item.payload,
        execution_verdict: { ...item.payload.execution_verdict, [field]: 1 } } } : item) }
    assert.equal(view.verifiedCurrentResult(badCompletion, trusted), false, field)
  }
})

test('message, artifact and later terminal failure must agree with the completed attempt', () => {
  const a = session('A', 'run-A', 'attempt-A')
  const trusted = domain('A', 'run-A', 'attempt-A')
  assert.equal(view.verifiedCurrentResult(a, trusted), true)
  assert.equal(view.verifiedCurrentResult({ ...a, messages: [{ ...a.messages[0],
    execution_verdict: { ...a.messages[0].execution_verdict, command_success: false } }] }, trusted), false)
  assert.equal(view.verifiedCurrentResult({ ...a, events: [...a.events,
    event(4, 'run.failed', { run_id: 'run-A', attempt_id: 'attempt-A' })] }, trusted), false)
  assert.equal(view.verifiedCurrentResult(a, { ...trusted, resultArtifacts: null }), false)
})

test('recovery reads the journal before projection and drops a superseded selection', async () => {
  const a = session('A', 'run-A', 'attempt-A')
  const retried = { ...a, events: [...a.events,
    event(4, 'run.attempt', { run_id: 'run-A', attempt_id: 'attempt-new' })] }
  let current = { generation: 1, sessionId: 'A' }
  let journal = a
  const calls = []
  const result = await view.reconcileCurrentEvidence({
    ticket: current, selection: () => current,
    refresh: async () => { calls.push('journal'); journal = retried },
    projection: async () => { calls.push('projection'); return domain('A', 'run-A', 'attempt-A') },
    session: () => journal,
  })
  assert.deepEqual(calls, ['journal', 'projection'])
  assert.equal(result, null)

  calls.length = 0
  current = { generation: 2, sessionId: 'A' }
  const oldTicket = current
  const late = await view.reconcileCurrentEvidence({
    ticket: oldTicket, selection: () => current,
    refresh: async () => { current = { generation: 3, sessionId: 'B' } },
    projection: async () => { calls.push('projection'); return domain('A', 'run-A', 'attempt-A') },
    session: () => a,
  })
  assert.equal(late, undefined)
  assert.deepEqual(calls, [])
})

test('artifact integrity journal failures revoke the result until that artifact is reverified', () => {
  const a = session('A', 'run-A', 'attempt-A')
  const trusted = domain('A', 'run-A', 'attempt-A')
  const failed = { ...a, events: [...a.events,
    event(4, 'artifact.integrity_failed', { run_id: 'run-A', attempt_id: 'attempt-A', filename: 'temperature.png' })] }
  assert.equal(view.currentEvidenceState(failed, trusted), 'stale')
  assert.equal(view.verifiedCurrentResult(failed, trusted), false)
  const restored = { ...failed, events: [...failed.events,
    event(5, 'artifact.integrity_verified', { run_id: 'run-A', attempt_id: 'attempt-A', filename: 'temperature.png' })] }
  assert.equal(view.verifiedCurrentResult(restored, trusted), true)
  const otherFailure = { ...restored, events: [...restored.events,
    event(6, 'artifact.integrity_failed', { run_id: 'run-A', attempt_id: 'attempt-A', filename: 'other.png' })] }
  assert.equal(view.verifiedCurrentResult(otherFailure, trusted), false)
})

test('contract gaps and contradictory evidence issues cannot certify a result', () => {
  const a = session('A', 'run-A', 'attempt-A')
  const trusted = domain('A', 'run-A', 'attempt-A')
  assert.equal(view.verifiedCurrentResult(a, { ...trusted, contractGaps: ['missing mesh readback'] }), false)
  const badCompletion = { ...a, events: a.events.map((item) => item.type === 'run.completed'
    ? { ...item, payload: { ...item.payload,
      evidence_contract: { ...item.payload.evidence_contract, issues: ['missing hash'] } } } : item) }
  assert.equal(view.verifiedCurrentResult(badCompletion, trusted), false)
})

test('an artifact fetch failure revokes the outer Verified claim for only the current attempt', () => {
  const viewer = source('../src/components/VerifiedArtifactViewer.tsx')
  const studio = source('../src/WarmStudio.tsx')
  assert.match(viewer, /onIntegrityLost\?\./)
  assert.match(studio, /artifactLossCurrent/)
  assert.match(studio, /onIntegrityLost=/)
  assert.match(studio, /completed\s*=.*artifactLossCurrent/s)
})

test('approved visual architecture and no default fixture import remain unchanged', () => {
  const entry = source('../src/main.tsx')
  const studio = source('../src/WarmStudio.tsx')
  assert.match(entry, /VITE_VISUAL_FIXTURES === '1'/)
  assert.match(studio, /warm-studio|warm-main/)
  assert.match(studio, /pageLabels/)
  assert.match(studio, /VerifiedArtifactViewer/)
})
