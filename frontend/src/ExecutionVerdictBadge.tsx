import type { ExecutionVerdict, ValidationReport } from './types'

export function validationDisplay(report: ValidationReport): ValidationReport {
  if (report.scope !== 'solution') return report
  const verdict = report.execution_verdict
  return { ...report, passed: verdict?.completed ?? false,
    command_success: verdict?.command_success ?? false,
    model_state_verified: verdict?.model_state_verified ?? false,
    scientific_validation_success: verdict?.scientific_validation_success ?? false,
    evidence_contract_passed: verdict?.evidence_contract_passed ?? false }
}

export function ExecutionVerdictBadge({ verdict, t }: { verdict?: ExecutionVerdict; t: (key: string) => string }) {
  if (!verdict) return null // Legacy messages have no execution certification.
  return <div className="success-pair success-triple" data-run-id={verdict.run_id}>
    <span>{t('Command Success')} {verdict.command_success ? '✓' : '✕'}</span>
    <span>{t('Model State Verified')} {verdict.model_state_verified ? '✓' : '✕'}</span>
    <span>{t('Scientific Validation Passed')} {verdict.scientific_validation_success ? '✓' : '✕'}</span>
    <span>{t('Evidence Contract Passed')} {verdict.evidence_contract_passed ? '✓' : '✕'}</span>
  </div>
}
