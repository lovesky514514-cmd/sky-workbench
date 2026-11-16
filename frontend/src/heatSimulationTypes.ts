export type HeatScenario = 'normal' | 'wrong_boundary' | 'missing_evidence' | 'interrupted' | 'revision' | 'unknown_submission'

export interface HeatSimulation {
  mode: 'simulation'
  scientific_validation_passed: false
  state: 'IDLE' | 'RUNNING' | 'PAUSED' | 'INTERRUPTED' | 'PASSED' | 'BLOCKED' | 'UNVERIFIED'
    | 'REVISION_REQUIRED' | 'CANCELLED' | 'STOPPED'
  can_start: boolean
  version?: number
  reason?: string | null
  tasks?: Array<{ id: string; state: 'PENDING' | 'IN_FLIGHT' | 'VERIFIED' | 'NOT_DISPATCHED' }>
  report?: {
    score_total: number | null
    evidence: Array<{
      evidence_id: string; source_ref: string
      artifact?: { run_id: string; attempt_id: string; sha256: string } | null
    }>
    claims: Array<{
      claim: { claim_id: string; statement: string; evidence_refs: string[] }
      status: 'PASS' | 'FAIL' | 'UNVERIFIED'
    }>
    revision: {
      revision_id: string
      issues: Array<{ issue_id: string; reason: string; evidence_refs: string[] }>
      actions: Array<{ task_id: string; action: string; target: string }>
    } | null
  } | null
}
