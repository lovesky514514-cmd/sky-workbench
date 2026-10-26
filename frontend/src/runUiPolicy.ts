import type { Plan } from './types'

const executionStates = new Set([
  'PRECHECK', 'BUILDING', 'BUILD_VALIDATION', 'SOLVING',
  'SOLUTION_VALIDATION', 'REPAIRING', 'REVALIDATING', 'EXPORTING', 'REPORTING',
])

export function isExecutionActive(state: string | undefined): boolean {
  return executionStates.has(state ?? '')
}

// This is a UI affordance only. Runtime still performs its own precheck and
// scientific validation before any execution can be certified.
export function canRunReviewedPlan(
  plan: Plan | null,
  sessionId: string | undefined,
  reviewedPlanId: string,
  busy: boolean,
  pendingIntent: boolean,
): boolean {
  if (!plan?.runnable || !sessionId || busy || pendingIntent || !plan.plan_id || !plan.updated_at) return false
  return reviewedPlanId === `${sessionId}:${plan.plan_id}:${plan.updated_at}`
}
