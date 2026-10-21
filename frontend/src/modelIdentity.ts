import type { RuntimeDomain } from './types'

export interface DisplayIdentity { sourceId: string | null; presentationId: string }
function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

// sourceId is a backend readback identity. presentationId is only a visual
// label; it is never sent as a mutation target or treated as a COMSOL tag.
export function geometryIdentity(snapshot: Record<string, unknown> | null): DisplayIdentity {
  const geometry = record(snapshot?.geometry)
  const feature = Array.isArray(geometry?.features) ? record(geometry.features[0]) : null
  return { sourceId: typeof feature?.id === 'string' && feature.id ? feature.id : null,
    presentationId: typeof feature?.label === 'string' && feature.label ? feature.label : 'Geometry' }
}

// The current snapshot gives a domain count, not a stable domain-selection ID.
// A neutral label avoids turning the design example's "Domain 1" into a claim.
export function domainIdentity(_snapshot: Record<string, unknown> | null): DisplayIdentity {
  return { sourceId: null, presentationId: 'Domain' }
}

export function reviewedBoundary(domain: RuntimeDomain | null, review: RuntimeDomain['reviewRequest']):
  { sourceId: string; presentationId: string; boundary: Record<string, unknown> } | null {
  if (!domain || !review || review.runId !== domain.runState?.runId ||
      review.attemptId !== domain.runState.attemptId ||
      !Object.values(domain.objectIds.boundaries).includes(review.objectId)) return null
  const snapshot = record(domain.modelSnapshot)
  const boundaries = Array.isArray(snapshot?.boundaries) ? snapshot.boundaries : []
  const boundary = boundaries.map(record).find((item) => item?.id === review.objectId)
  if (!boundary) return null
  return { sourceId: review.objectId,
    presentationId: typeof boundary.label === 'string' && boundary.label ? boundary.label : 'Boundary', boundary }
}
