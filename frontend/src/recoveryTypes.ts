export interface FailureSummary {
  event_id: string
  occurred_at_utc: string
  origin: string
  route_template: string | null
  method: string | null
  http_status: number | null
  error_code: string
}

export interface RecoveryCaseSummary {
  case_id: string
  state: string
  failure: FailureSummary
}

export interface RecoveryCase extends RecoveryCaseSummary {
  report: {
    rule: { classification: string; safe_next_probe: string; counter_evidence: string[]
      http?: { action: string; reason: string; safe_to_retry: boolean; readback_required: boolean; requires_run_approval: boolean; retry_after_seconds: number | null } | null }
    evidence_refs: string[]
    missing_evidence: string[]
    knowledge_refs: string[]
    candidates: Array<{ cause_code: string; explanation: string; supporting_evidence_refs: string[] }>
    model_status: string
  } | null
  proposal: { action: string; digest: string; approval_required: boolean; verification_plan: string[] } | null
  review_decision: 'approve' | 'reject' | null
  proposal_status?: string | null
  events: Array<{ event_id: string; kind: string; time: string; sha256: string
    payload_json?: { connection_verified?: boolean; mode?: string; solve_submissions?: number } }>
}
