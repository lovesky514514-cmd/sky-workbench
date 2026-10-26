import type { EvidenceArtifactRecord, Message, Session } from '../types'

export type ArtifactDescriptor = {
  key: string
  name: string
  quantity: string
  unit: string
  filename: string
  record: EvidenceArtifactRecord
  message: Message
}

export type ArtifactLoad = {
  key: string
  state: 'loading' | 'verified' | 'lost' | 'unavailable'
  source: string
}

// React effects run after paint. A previous task's image URL must be hidden
// during the render that switches to a new session, run or attempt.
export function visibleArtifactLoad(loaded: ArtifactLoad, currentKey: string): Pick<ArtifactLoad, 'state' | 'source'> {
  return loaded.key === currentKey
    ? { state: loaded.state, source: loaded.source }
    : { state: 'loading', source: '' }
}

// Only declared keys receive a scientific label. Unknown PNGs keep their
// original filename, so a Python plot cannot be mislabeled as COMSOL data.
const IMAGE_ARTIFACTS: Record<string, { name: string; quantity: string; unit: string }> = {
  temperature_image: { name: 'Temperature field', quantity: 'Temperature', unit: 'K' },
  concentration_image: { name: 'Concentration field', quantity: 'Concentration', unit: 'mol/m³' },
  stress_image: { name: 'Von Mises stress field', quantity: 'Stress', unit: 'Pa' },
  potential_image: { name: 'Electrolyte potential field', quantity: 'Electric potential', unit: 'V' },
  battery_concentration_image: { name: 'Battery electrolyte concentration', quantity: 'Concentration', unit: 'mol/m³' },
  coupled_temperature_image: { name: 'Coupled temperature field', quantity: 'Temperature', unit: 'K' },
  coupled_concentration_image: { name: 'Coupled concentration field', quantity: 'Concentration', unit: 'mol/m³' },
}

function basename(path: string): string { return path.split(/[\\/]/).at(-1) ?? '' }

// The gallery is bound to the current completed run and attempt. The passed
// evidence manifest must identify the same image bytes as the message.
export function verifiedImageArtifacts(session: Session | null, currentRunId?: string, currentAttemptId?: string): ArtifactDescriptor[] {
  // No authoritative current identity means no current gallery. The legacy
  // caller can still show an explicit unavailable state without guessing.
  if (!currentRunId || !currentAttemptId) return []
  for (const message of [...(session?.messages ?? [])].reverse()) {
    const verdict = message.execution_verdict
    const evidence = message.evidence_contract
    if (!message.run_id || message.run_id !== currentRunId) continue
    if (verdict?.completed !== true || verdict.command_success !== true ||
        verdict.model_state_verified !== true || verdict.scientific_validation_success !== true ||
        verdict.evidence_contract_passed !== true || evidence?.passed !== true) continue
    if (verdict.run_id !== message.run_id || evidence.run_id !== message.run_id || evidence.attempt_id !== currentAttemptId) continue
    return Object.entries(message.artifacts ?? {}).flatMap(([key, path]) => {
      const record = evidence.artifacts?.[key]
      const filename = basename(path)
      if (!record || !filename.toLowerCase().endsWith('.png') || basename(record.path) !== filename || !record.sha256) return []
      const metadata = IMAGE_ARTIFACTS[key] ?? { name: filename, quantity: '', unit: '' }
      return [{ key, ...metadata, filename, record, message }]
    })
  }
  return []
}
