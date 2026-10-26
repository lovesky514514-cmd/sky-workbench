import type { Plan } from './types'

export function phase2Plan(value: unknown): Plan | null {
  if (!value || typeof value !== 'object') return null
  const candidate = value as Partial<Plan>
  return candidate.schema_version === '2.0' && typeof candidate.plan_id === 'string'
    ? candidate as Plan : null
}

export function displayPlanForMessage(structured: unknown, currentPlan: Plan | null): Plan | null {
  const historical = phase2Plan(structured)
  if (!historical) return null
  // A saved revision may change without changing plan_id. History must still
  // show the revision that belonged to the original message.
  return historical.plan_id === currentPlan?.plan_id && historical.updated_at === currentPlan.updated_at
    ? currentPlan : historical
}
