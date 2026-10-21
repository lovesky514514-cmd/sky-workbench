import { translate, type Locale } from '../i18n'
import type { ExecutionEta, ExecutionVerdict, Session, TimelineItem } from '../types'

export type ScientificStageStatus = 'Pending' | 'Running' | 'Verified' | 'Failed' | 'Need Review'

export const executionStates = new Set([
  'PRECHECK', 'BUILDING', 'BUILD_VALIDATION', 'SOLVING', 'SOLUTION_VALIDATION',
  'VALIDATING', 'REPAIRING', 'REVALIDATING', 'EXPORTING', 'REPORTING',
])

export const scientificStages = [
  { name: 'Geometry', keywords: ['geometry', 'build'] },
  { name: 'Physics', keywords: ['physics', 'interface'] },
  { name: 'Boundary', keywords: ['boundary', 'selection'] },
  { name: 'Mesh', keywords: ['mesh', 'dof'] },
  { name: 'Solver', keywords: ['solve', 'solver', 'convergence'] },
  { name: 'Evidence', keywords: ['evidence', 'validation', 'readback', 'certif'] },
] as const

export function latestVerdict(session: Session | null): ExecutionVerdict | undefined {
  return [...(session?.messages ?? [])].reverse().find((message) => message.execution_verdict)?.execution_verdict
}

export function currentScientificStage(state: string): string | null {
  if (state === 'BUILDING') return 'Geometry'
  if (state === 'BUILD_VALIDATION') return 'Boundary'
  if (state === 'SOLVING') return 'Solver'
  if (['SOLUTION_VALIDATION', 'VALIDATING', 'REVALIDATING', 'EXPORTING', 'REPORTING'].includes(state)) return 'Evidence'
  return null
}

export function scientificStageStatuses(session: Session | null, timeline: TimelineItem[]): Array<{ name: string; status: ScientificStageStatus }> {
  const state = session?.state ?? 'IDLE'
  const verdict = latestVerdict(session)
  const current = currentScientificStage(state)
  return scientificStages.map((definition) => {
    const event = [...timeline].reverse().find((item) => {
      const text = [item.phase, item.label, item.output_summary, item.tool, item.event_type].join(' ').toLowerCase()
      return definition.keywords.some((keyword) => text.includes(keyword))
    })
    if (event?.status === 'success') return { name: definition.name, status: 'Verified' }
    if (event?.status === 'error') return { name: definition.name, status: 'Failed' }
    if (event?.status === 'warning') return { name: definition.name, status: 'Need Review' }
    if (event && ['running', 'info'].includes(event.status) && executionStates.has(state) && current === definition.name) return { name: definition.name, status: 'Running' }
    if (state === 'COMPLETED') {
      const verified = definition.name === 'Evidence'
        ? Boolean(verdict?.completed && verdict.evidence_contract_passed && verdict.scientific_validation_success)
        : Boolean(verdict?.command_success && verdict.model_state_verified)
      return { name: definition.name, status: verified ? 'Verified' : 'Need Review' }
    }
    if (['FAILED', 'VALIDATION_FAILED', 'INTERRUPTED', 'CANCELED'].includes(state) && current === definition.name) return { name: definition.name, status: 'Failed' }
    if (state === 'PAUSED' && current === definition.name) return { name: definition.name, status: 'Need Review' }
    if (current === definition.name && executionStates.has(state)) return { name: definition.name, status: 'Running' }
    return { name: definition.name, status: 'Pending' }
  })
}

export function actionLabel(state: string): string {
  const labels: Record<string, string> = {
    IDLE: 'Waiting for a scientific request', RESEARCHING: 'Retrieving scientific evidence',
    PLANNING: 'Compiling Scientific Plan', PLAN_READY: 'Waiting for plan approval',
    PRECHECK: 'Checking execution prerequisites', WAITING_FOR_EXECUTION: 'Waiting for execution approval',
    BUILDING: 'Building Geometry', BUILD_VALIDATION: 'Verifying model build and selections',
    SOLVING: 'Running COMSOL Solver', SOLUTION_VALIDATION: 'Checking solver evidence',
    VALIDATING: 'Running scientific validation', VALIDATION_FAILED: 'Scientific validation needs review',
    REPAIRING: 'Applying bounded repair', REVALIDATING: 'Revalidating repaired model',
    EXPORTING: 'Exporting verified results', REPORTING: 'Preparing Model Card',
    PAUSED: 'Paused at a verified safe boundary', COMPLETED: 'Execution completed',
    FAILED: 'Execution failed', CANCEL_REQUESTED: 'Waiting for owned process cancellation',
    CANCELED: 'Execution canceled', INTERRUPTED: 'Execution interrupted',
  }
  return labels[state] ?? state
}

export function formatEta(locale: Locale, eta: ExecutionEta | null): string {
  if (!eta || eta.status === 'unknown' || eta.estimated_remaining_seconds == null) return translate(locale, 'unknown')
  if (eta.status === 'complete') return translate(locale, 'Complete')
  if (eta.estimated_remaining_seconds < 60) return '≈ ' + String(eta.estimated_remaining_seconds) + ' ' + translate(locale, 'seconds')
  return '≈ ' + String(Math.ceil(eta.estimated_remaining_seconds / 60)) + ' ' + translate(locale, 'minutes')
}

export function statusClass(status: ScientificStageStatus): string {
  return status.toLowerCase().replace(' ', '-')
}

export function modelValue(value: unknown, empty: string): string {
  if (Array.isArray(value) && value.length) return value.slice(0, 3).map(String).join(' · ') + (value.length > 3 ? ' +' + String(value.length - 3) : '')
  if (value && typeof value === 'object' && Object.keys(value as Record<string, unknown>).length) return String(Object.keys(value as Record<string, unknown>).length) + ' item(s)'
  return empty
}
