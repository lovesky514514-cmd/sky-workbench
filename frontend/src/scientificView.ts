import type { RuntimeDomain, Session } from './types'

// A selection ticket belongs to one user-visible task, even if the user later
// returns to the same session. The generation prevents a late A→B→A response
// from being mistaken for the new A selection.
export interface SelectionTicket { generation: number; sessionId: string }
export function isCurrentSelection(ticket: SelectionTicket, current: SelectionTicket): boolean {
  return ticket.generation === current.generation && ticket.sessionId === current.sessionId
}

// Concurrent event refreshes for one selection can finish out of order. An
// older response must not roll the current attempt or evidence state back.
export function sessionIsNotOlder(incoming: Session, current: Session | null): boolean {
  if (!current || incoming.id !== current.id) return true
  const lastSequence = (value: Session) => Math.max(0, ...(value.events ?? []).map((item) => item.sequence))
  const incomingSequence = lastSequence(incoming)
  const currentSequence = lastSequence(current)
  return incomingSequence > currentSequence ||
    (incomingSequence === currentSequence && incoming.updated_at >= current.updated_at)
}

// The journal-backed session events, not an old assistant message, identify
// the latest run and its latest attempt. A run without an attempt is pending.
function latestRun(session: Session | null): { runId: string; attemptId: string; completedAt: number; completion: Record<string, unknown> | null } | null {
  const events = session?.events ?? []
  const start = [...events].reverse().find((item) => item.type === 'run.started' && typeof item.payload.run_id === 'string')
  if (!start) return null
  const runId = String(start.payload.run_id)
  const later = events.filter((item) => item.sequence > start.sequence && item.payload.run_id === runId)
  const attempt = [...later].reverse().find((item) => item.type === 'run.attempt' && typeof item.payload.attempt_id === 'string')
  if (!attempt) return null
  const attemptId = String(attempt.payload.attempt_id)
  const completion = [...later].reverse().find((item) => item.type === 'run.completed' &&
    item.payload.attempt_id === attemptId)
  return { runId, attemptId, completedAt: completion?.sequence ?? -1, completion: completion?.payload ?? null }
}

// A cached projection is useful only while it still describes this exact
// session, run and attempt. This check runs during render, before effects paint.
export function currentScientificDomain(session: Session | null, domain: RuntimeDomain | null): RuntimeDomain | null {
  const run = latestRun(session)
  return domain?.contractVersion === 'rc42-v1' && domain.studyId === session?.id && run &&
    domain.runState?.runId === run.runId && domain.runState.attemptId === run.attemptId ? domain : null
}

// Event delivery can precede the next /domain response. An invalidation after
// the latest completion immediately removes the current verified state.
export function currentEvidenceState(session: Session | null, domain: RuntimeDomain | null): string {
  const current = currentScientificDomain(session, domain)
  const run = latestRun(session)
  if (!current || !run) return 'unavailable'
  const invalidated = (session?.events ?? []).some((item) => item.sequence > run.completedAt &&
    ['evidence.invalidated', 'validation.invalidated_by_human_correction'].includes(item.type) &&
    item.payload.run_id === run.runId &&
    (!item.payload.attempt_id || item.payload.attempt_id === run.attemptId))
  return invalidated ? 'stale' : current.evidenceState
}

// Scientific success remains authored by the Runtime. The UI only withholds
// stale/mismatched claims and requires a matching frozen message/artifact set.
export function verifiedCurrentResult(session: Session | null, domain: RuntimeDomain | null): boolean {
  const current = currentScientificDomain(session, domain)
  const run = latestRun(session)
  if (!current || !run || session?.state !== 'COMPLETED' || run.completedAt < 0 ||
      currentEvidenceState(session, current) !== 'verified') return false
  const verdict = current.authoritativeVerdict
  if (!verdict || verdict.run_id !== run.runId || !verdict.completed ||
      !verdict.command_success || !verdict.model_state_verified ||
      !verdict.scientific_validation_success || !verdict.evidence_contract_passed ||
      !Array.isArray(verdict.issues) || verdict.issues.length !== 0) return false
  // The completion event and frozen evidence contract must identify the same
  // attempt. This is a consistency check, never a frontend-issued verdict.
  const completedVerdict = run.completion?.execution_verdict as Record<string, unknown> | undefined
  const completedEvidence = run.completion?.evidence_contract as Record<string, unknown> | undefined
  if (run.completion?.run_id !== run.runId || run.completion.attempt_id !== run.attemptId ||
      completedVerdict?.run_id !== run.runId || completedVerdict?.completed !== true ||
      completedVerdict?.command_success !== true || completedVerdict?.model_state_verified !== true ||
      completedVerdict?.scientific_validation_success !== true || completedVerdict?.evidence_contract_passed !== true ||
      completedEvidence?.passed !== true || completedEvidence.run_id !== run.runId ||
      completedEvidence.attempt_id !== run.attemptId) return false
  const artifacts = current.resultArtifacts
  if (!artifacts.length || artifacts.some((item) => item.runId !== run.runId ||
      item.attemptId !== run.attemptId || item.evidenceState !== 'verified')) return false
  return (session.messages ?? []).some((message) => message.run_id === run.runId &&
    message.execution_verdict?.run_id === run.runId && message.evidence_contract?.passed === true &&
    message.evidence_contract.run_id === run.runId &&
    message.evidence_contract.attempt_id === run.attemptId)
}
