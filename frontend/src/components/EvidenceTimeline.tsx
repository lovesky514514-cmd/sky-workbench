import { useState } from 'react'
import { translate, type Locale } from '../i18n'
import type { ExecutionEta, Session, TimelineItem } from '../types'
import { actionLabel, formatEta, latestVerdict, scientificStageStatuses, statusClass, type ScientificStageStatus } from './workspaceState'
import { WorkspaceIcon } from './WorkspaceIcon'

const evidenceStages = ['Proposal', 'Plan', 'Precheck', 'Build', 'Readback', 'Solve', 'Validate', 'Certify'] as const

function hasEvent(items: TimelineItem[], pattern: RegExp, status?: TimelineItem['status']): boolean {
  return items.some((item) => pattern.test([item.phase, item.label, item.tool, item.event_type].join(' ')) && (!status || item.status === status))
}

function evidenceStatuses(session: Session | null, items: TimelineItem[]): Array<{ name: typeof evidenceStages[number]; status: ScientificStageStatus }> {
  const verdict = latestVerdict(session)
  const scientific = scientificStageStatuses(session, items)
  const stage = (name: string) => scientific.find((item) => item.name === name)?.status ?? 'Pending'
  const state = session?.state ?? 'IDLE'
  const running = (states: string[]) => states.includes(state)
  return [
    { name: 'Proposal', status: session?.messages.length ? 'Verified' : 'Pending' },
    { name: 'Plan', status: running(['RESEARCHING', 'PLANNING']) ? 'Running' : session?.plan ? 'Verified' : 'Pending' },
    { name: 'Precheck', status: running(['PRECHECK']) ? 'Running' : hasEvent(items, /precheck/i, 'error') ? 'Failed' : hasEvent(items, /precheck/i, 'success') ? 'Verified' : 'Pending' },
    { name: 'Build', status: stage('Geometry') },
    { name: 'Readback', status: verdict?.model_state_verified ? 'Verified' : hasEvent(items, /readback|selection/i, 'error') ? 'Failed' : running(['BUILD_VALIDATION']) ? 'Running' : 'Pending' },
    { name: 'Solve', status: stage('Solver') },
    { name: 'Validate', status: verdict?.scientific_validation_success ? 'Verified' : state === 'VALIDATION_FAILED' ? 'Failed' : running(['SOLUTION_VALIDATION', 'VALIDATING', 'REVALIDATING']) ? 'Running' : 'Pending' },
    { name: 'Certify', status: verdict?.completed && verdict.evidence_contract_passed ? 'Verified' : state === 'COMPLETED' ? 'Need Review' : running(['EXPORTING', 'REPORTING']) ? 'Running' : 'Pending' },
  ]
}

function identity(items: TimelineItem[], key: 'run_id' | 'attempt_id'): string {
  for (const item of [...items].reverse()) {
    const value = item.input_summary?.[key]
    if (typeof value === 'string' && value) return value
  }
  return ''
}

export function EvidenceTimeline({ locale, session, items, eta = null, forceExpanded, onSelect }: { locale: Locale; session: Session | null; items: TimelineItem[]; eta?: ExecutionEta | null; forceExpanded?: boolean; onSelect: (stage: string) => void }) {
  const t = (value: string) => translate(locale, value)
  const [requestedExpanded, setRequestedExpanded] = useState(false)
  const expanded = forceExpanded ?? requestedExpanded
  const [eventOpen, setEventOpen] = useState<string | null>(null)
  const verdict = latestVerdict(session)
  const stages = evidenceStatuses(session, items)
  const verifiedCount = stages.filter((stage) => stage.status === 'Verified').length
  const activeStage = stages.find((stage) => stage.status === 'Running' || stage.status === 'Need Review' || stage.status === 'Failed') ?? [...stages].reverse().find((stage) => stage.status === 'Verified') ?? stages[0]
  const evidenceCount = items.filter((item) => item.status === 'success').length + (verdict ? 1 : 0)
  const runId = verdict?.run_id || identity(items, 'run_id')
  const attemptId = identity(items, 'attempt_id')
  const recent = [...items].reverse().slice(0, 8)
  return <section className={'execution-strip ' + (expanded ? 'expanded' : 'collapsed')}>
    <button className="execution-strip-summary" onClick={() => setRequestedExpanded(!expanded)} aria-expanded={expanded}>
      <span className={'execution-state-mark ' + statusClass(activeStage.status)}><i /><b>{t(activeStage.name)}</b></span>
      <strong>{t(actionLabel(session?.state ?? 'IDLE'))}</strong>
      <span className="execution-progress-line"><i style={{ width: String(Math.round(verifiedCount / stages.length * 100)) + '%' }} /></span>
      <span className="execution-eta-copy"><small>{t('ETA')}</small><b>{formatEta(locale, eta)}</b></span>
      <span className="execution-evidence-count"><small>{String(evidenceCount)}</small>{t('evidence items')}</span>
      <WorkspaceIcon name="chevron" size={15} />
    </button>
    {expanded && <div className="execution-drawer">
      <header><div><span>{t('EXECUTION EVIDENCE')}</span><strong>{t('Proposal to certification')}</strong></div><div><code>run {runId || t('Unavailable')}</code><code>attempt {attemptId || t('Unavailable')}</code></div></header>
      <div className="execution-stage-path">{stages.map((stage, index) => <button className={statusClass(stage.status)} key={stage.name} onClick={() => onSelect(stage.name)}><i>{stage.status === 'Verified' ? <WorkspaceIcon name="check" size={11} /> : String(index + 1)}</i><span>{t(stage.name)}</span><small>{t(stage.status)}</small></button>)}</div>
      <div className="execution-events">{recent.length ? recent.map((item) => <article className={item.status} key={item.id}><button onClick={() => setEventOpen(eventOpen === item.id ? null : item.id)}><i /><span><b>{item.phase}</b><small>{item.label}</small></span><em>{item.duration_ms == null ? t(item.status) : String(item.duration_ms) + ' ms'}</em></button>{eventOpen === item.id && <div><code>{item.tool}</code><p>{item.output_summary || t('No summary yet')}</p></div>}</article>) : <div className="execution-empty"><strong>{t('No execution evidence yet')}</strong><span>{t('State/Event Ledger evidence will appear here after activity begins.')}</span></div>}</div>
    </div>}
  </section>
}
