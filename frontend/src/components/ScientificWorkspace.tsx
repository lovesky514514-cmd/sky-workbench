import type { ReactNode } from 'react'
import { translate, type Locale } from '../i18n'
import type { ExecutionEta, ExecutionVerdict, JavaBridgeEvent, JavaBridgeStatus, Session, TimelineItem } from '../types'

export type ScientificStageStatus = 'Pending' | 'Running' | 'Passed' | 'Failed' | 'Need Review'

const stageDefinitions = [
  { name: 'Geometry', keywords: ['geometry', 'build'] },
  { name: 'Physics', keywords: ['physics', 'interface'] },
  { name: 'Boundary', keywords: ['boundary', 'selection'] },
  { name: 'Mesh', keywords: ['mesh', 'dof'] },
  { name: 'Solver', keywords: ['solve', 'solver', 'convergence'] },
  { name: 'Evidence', keywords: ['evidence', 'validation', 'readback', 'certif'] },
] as const

const activeStates = new Set([
  'PRECHECK', 'BUILDING', 'BUILD_VALIDATION', 'SOLVING', 'SOLUTION_VALIDATION',
  'VALIDATING', 'REPAIRING', 'REVALIDATING', 'EXPORTING', 'REPORTING',
])

function latestVerdict(session: Session | null): ExecutionVerdict | undefined {
  return [...(session?.messages ?? [])].reverse().find((message) => message.execution_verdict)?.execution_verdict
}

function currentStageName(state: string): string | null {
  if (state === 'BUILDING') return 'Geometry'
  if (state === 'BUILD_VALIDATION') return 'Boundary'
  if (state === 'SOLVING') return 'Solver'
  if (['SOLUTION_VALIDATION', 'VALIDATING', 'REVALIDATING', 'EXPORTING', 'REPORTING'].includes(state)) return 'Evidence'
  return null
}

export function scientificStageStatuses(session: Session | null, timeline: TimelineItem[]): Array<{ name: string; status: ScientificStageStatus }> {
  const state = session?.state ?? 'IDLE'
  const verdict = latestVerdict(session)
  const current = currentStageName(state)
  return stageDefinitions.map((definition) => {
    const event = [...timeline].reverse().find((item) => {
      const text = [item.phase, item.label, item.output_summary, item.tool, item.event_type].join(' ').toLowerCase()
      return definition.keywords.some((keyword) => text.includes(keyword))
    })
    if (event?.status === 'success') return { name: definition.name, status: 'Passed' }
    if (event?.status === 'error') return { name: definition.name, status: 'Failed' }
    if (event?.status === 'warning') return { name: definition.name, status: 'Need Review' }
    if (event && ['running', 'info'].includes(event.status) && activeStates.has(state) && current === definition.name) {
      return { name: definition.name, status: 'Running' }
    }
    if (state === 'COMPLETED') {
      const passed = definition.name === 'Evidence'
        ? Boolean(verdict?.completed && verdict.evidence_contract_passed && verdict.scientific_validation_success)
        : Boolean(verdict?.command_success && verdict.model_state_verified)
      return { name: definition.name, status: passed ? 'Passed' : 'Need Review' }
    }
    if (['FAILED', 'VALIDATION_FAILED', 'INTERRUPTED', 'CANCELED'].includes(state) && current === definition.name) {
      return { name: definition.name, status: 'Failed' }
    }
    if (state === 'PAUSED' && current === definition.name) return { name: definition.name, status: 'Need Review' }
    if (current === definition.name && activeStates.has(state)) return { name: definition.name, status: 'Running' }
    return { name: definition.name, status: 'Pending' }
  })
}

function modelValue(value: unknown, empty: string): string {
  if (Array.isArray(value) && value.length) return value.slice(0, 2).map(String).join(' · ') + (value.length > 2 ? ' +' + String(value.length - 2) : '')
  if (value && typeof value === 'object' && Object.keys(value as Record<string, unknown>).length) return String(Object.keys(value as Record<string, unknown>).length) + ' verified item(s)'
  return empty
}

function actionLabel(state: string): string {
  const labels: Record<string, string> = {
    IDLE: 'Waiting for a scientific request',
    RESEARCHING: 'Retrieving scientific evidence',
    PLANNING: 'Compiling Scientific Plan',
    PLAN_READY: 'Waiting for plan approval',
    PRECHECK: 'Checking execution prerequisites',
    WAITING_FOR_EXECUTION: 'Waiting for execution approval',
    BUILDING: 'Building Geometry',
    BUILD_VALIDATION: 'Verifying model build and selections',
    SOLVING: 'Running COMSOL Solver',
    SOLUTION_VALIDATION: 'Checking solver evidence',
    VALIDATING: 'Running scientific validation',
    VALIDATION_FAILED: 'Scientific validation needs review',
    REPAIRING: 'Applying bounded repair',
    REVALIDATING: 'Revalidating repaired model',
    EXPORTING: 'Exporting verified results',
    REPORTING: 'Preparing Model Card',
    PAUSED: 'Paused at a verified safe boundary',
    COMPLETED: 'Execution completed',
    FAILED: 'Execution failed',
    CANCEL_REQUESTED: 'Waiting for owned process cancellation',
    CANCELED: 'Execution canceled',
    INTERRUPTED: 'Execution interrupted',
  }
  return labels[state] ?? state
}

function formatEta(locale: Locale, eta: ExecutionEta | null): string {
  if (!eta || eta.status === 'unknown' || eta.estimated_remaining_seconds == null) return translate(locale, 'unknown')
  if (eta.status === 'complete') return translate(locale, 'Complete')
  const seconds = eta.estimated_remaining_seconds
  if (seconds < 60) return '≈ ' + String(seconds) + ' ' + translate(locale, 'seconds')
  return '≈ ' + String(Math.ceil(seconds / 60)) + ' ' + translate(locale, 'minutes')
}

function statusClass(status: ScientificStageStatus): string {
  return status.toLowerCase().replace(' ', '-')
}

export function ScientificModelTree({ locale, session, timeline }: { locale: Locale; session: Session | null; timeline: TimelineItem[] }) {
  const t = (value: string) => translate(locale, value)
  const stages = scientificStageStatuses(session, timeline)
  const labels: Record<string, string> = { Boundary: 'Boundary Condition', Evidence: 'Evidence' }
  return <section className="scientific-model-tree">
    <header><span>{t('Scientific Model Tree')}</span><small>{session?.plan?.workflow_id ?? t('No workflow')}</small></header>
    <ul>{stages.map((stage) => <li key={stage.name}><span className={'stage-dot ' + statusClass(stage.status)} /><b>{t(labels[stage.name] ?? stage.name)}</b><em>{t(stage.status)}</em></li>)}</ul>
    <div className="model-tree-results"><span className="stage-dot pending" /><b>{t('Result')}</b><em>{session?.state === 'COMPLETED' ? t('Available') : t('Pending')}</em></div>
  </section>
}

export function LiveScientificWorkspace({
  locale,
  session,
  timeline,
  bridge,
  bridgeEvents,
  eta,
}: {
  locale: Locale
  session: Session | null
  timeline: TimelineItem[]
  bridge: JavaBridgeStatus
  bridgeEvents: JavaBridgeEvent[]
  eta: ExecutionEta | null
}) {
  const t = (value: string) => translate(locale, value)
  const stages = scientificStageStatuses(session, timeline)
  const model = session?.model
  const waiting = t('Awaiting verified COMSOL readback')
  const rows = [
    ['Geometry', modelValue(model?.geometry, waiting)],
    ['Material', modelValue(model?.materials, waiting)],
    ['Physics', modelValue(model?.physics, waiting)],
    ['Boundary', modelValue(model?.boundary_conditions, waiting)],
    ['Mesh', modelValue(model?.mesh, waiting)],
    ['Solver', modelValue(model?.solver, waiting)],
  ]
  const actualProgress = [...bridgeEvents].reverse().find((event) => typeof (event.percent ?? event.progress) === 'number')
  const actualPercent = actualProgress ? Number(actualProgress.percent ?? actualProgress.progress) : null
  const passedCount = stages.filter((stage) => stage.status === 'Passed').length
  const displayProgress = actualPercent == null ? Math.round((passedCount / stages.length) * 100) : Math.max(0, Math.min(100, actualPercent))
  const progressLabel = actualPercent == null
    ? String(passedCount) + ' / ' + String(stages.length) + ' ' + t('verified stages')
    : String(Math.round(displayProgress)) + '% · ' + t('COMSOL progress')
  return <section className="panel live-scientific-workspace">
    <header className="workspace-title">
      <div><span className="eyebrow">{t('LIVE SCIENTIFIC WORKSPACE')}</span><h1>{session?.title ?? t('New scientific task')}</h1></div>
      <div className={'live-connection ' + (bridge.model_connected ? 'connected' : 'disconnected')}><span />{t(bridge.model_connected ? 'Live COMSOL connected' : 'Live COMSOL disconnected')}</div>
    </header>
    <div className="workspace-body">
      <section className="live-model-state">
        <div className="workspace-section-label"><span>{t('Current model state')}</span><small>{bridge.model_connected ? t('verified readback') : t('readback pending')}</small></div>
        <dl>{rows.map(([name, value]) => <div key={name}><dt>{t(name)}:</dt><dd title={value}>{value}</dd></div>)}</dl>
      </section>
      <section className="live-execution-state">
        <div className="current-action"><span>{t('Current Action')}</span><strong>{t(actionLabel(session?.state ?? 'IDLE'))}</strong></div>
        <div className="execution-progress"><div><span>{t('Progress')}</span><strong>{progressLabel}</strong></div><div className="progress-track"><i style={{ width: String(displayProgress) + '%' }} /></div></div>
        <div className="execution-eta"><span>{t('Estimated remaining time')}</span><strong>{formatEta(locale, eta)}</strong><small>{eta?.status === 'estimated' ? String(eta.sample_count) + ' ' + t('comparable ledger runs') : t('No precise estimate is invented')}</small></div>
      </section>
    </div>
    <div className="stage-status-grid">{stages.map((stage) => <article className={statusClass(stage.status)} key={stage.name}><span>{t(stage.name)}</span><strong>{t(stage.status)}</strong></article>)}</div>
  </section>
}

function agentLaneStatus(session: Session | null, lane: 'Planner' | 'Builder' | 'Verifier' | 'Certification'): ScientificStageStatus {
  const state = session?.state ?? 'IDLE'
  const verdict = latestVerdict(session)
  if (lane === 'Planner') {
    if (['RESEARCHING', 'PLANNING'].includes(state)) return 'Running'
    return session?.plan ? 'Passed' : 'Pending'
  }
  if (lane === 'Builder') {
    if (['BUILDING', 'BUILD_VALIDATION'].includes(state)) return 'Running'
    if (['FAILED', 'INTERRUPTED'].includes(state)) return 'Failed'
    return verdict?.command_success && verdict.model_state_verified ? 'Passed' : 'Pending'
  }
  if (lane === 'Verifier') {
    if (['SOLVING', 'SOLUTION_VALIDATION', 'VALIDATING', 'REVALIDATING'].includes(state)) return 'Running'
    if (state === 'VALIDATION_FAILED') return 'Failed'
    return verdict?.model_state_verified ? 'Passed' : 'Pending'
  }
  if (['EXPORTING', 'REPORTING'].includes(state)) return 'Running'
  if (state === 'COMPLETED') return verdict?.completed ? 'Passed' : 'Need Review'
  if (['FAILED', 'VALIDATION_FAILED'].includes(state)) return 'Failed'
  return 'Pending'
}

export function AgentControlPanel({
  locale,
  session,
  selectedModel,
  lowVramMode,
  warning,
  blockingError,
  pendingApproval,
  inspectorOpen,
  inspector,
  onToggleInspector,
  onPause,
  onModify,
  onApprove,
  onReject,
  onResume,
}: {
  locale: Locale
  session: Session | null
  selectedModel: string
  lowVramMode: boolean
  warning: string
  blockingError: string
  pendingApproval: boolean
  inspectorOpen: boolean
  inspector: ReactNode
  onToggleInspector: () => void
  onPause: () => void
  onModify: () => void
  onApprove: () => void
  onReject: () => void
  onResume: () => void
}) {
  const t = (value: string) => translate(locale, value)
  const lanes = (['Planner', 'Builder', 'Verifier', 'Certification'] as const).map((name) => ({ name, status: agentLaneStatus(session, name) }))
  const planIssue = session?.plan?.missing_information?.[0] || session?.plan?.warnings?.[0] || ''
  const uncertainty = blockingError || warning || planIssue || t('No unresolved uncertainty recorded')
  const needHuman = pendingApproval || session?.state === 'PAUSED' || lanes.some((lane) => lane.status === 'Need Review') || Boolean(blockingError)
  const risk = blockingError ? 'High' : warning || planIssue ? 'Review' : activeStates.has(session?.state ?? '') ? 'Guarded' : 'Low'
  const canPause = activeStates.has(session?.state ?? '')
  const canReject = pendingApproval || canPause || session?.state === 'PAUSED'
  const canApprove = pendingApproval || session?.state === 'PAUSED'
  return <aside className="panel agent-control-panel">
    <header><div><span className="eyebrow">{t('AGENT CONTROL PANEL')}</span><strong>{t('Execution supervision')}</strong></div><button className="icon-button" onClick={onToggleInspector} title={t('Toggle technical details')}>{inspectorOpen ? '−' : '+'}</button></header>
    <section className="control-model"><span>{t('Model')}</span><strong>{selectedModel || t('Unavailable')}</strong><small>{lowVramMode ? 'LOW_VRAM_MODE' : 'STANDARD'}</small></section>
    <section className="agent-state-lanes"><h3>{t('Agent State')}</h3>{lanes.map((lane) => <div key={lane.name}><span className={'stage-dot ' + statusClass(lane.status)} /><b>{t(lane.name)}</b><em>{t(lane.status)}</em></div>)}</section>
    <section className={'risk-card ' + risk.toLowerCase()}><div><span>{t('Risk')}</span><strong>{t(risk)}</strong></div><div><span>{t('Current uncertainty')}</span><p>{uncertainty}</p></div><div><span>{t('Need human confirmation')}</span><strong>{t(needHuman ? 'Yes' : 'No')}</strong></div></section>
    <section className="human-controls" aria-label={t('Human intervention')}>
      <button disabled={!canPause} onClick={onPause}>{t('Pause')}</button>
      <button disabled={!session?.plan} onClick={onModify}>{t('Modify')}</button>
      <button className="approve" disabled={!canApprove} onClick={onApprove}>{t('Approve')}</button>
      <button className="reject" disabled={!canReject} onClick={onReject}>{t('Reject')}</button>
      <button disabled={session?.state !== 'PAUSED'} onClick={onResume}>{t('Resume')}</button>
    </section>
    <section className={'technical-inspector ' + (inspectorOpen ? 'open' : '')}>
      <button className="technical-inspector-toggle" onClick={onToggleInspector}><span>{t('Technical details')}</span><small>{inspectorOpen ? t('Collapse') : t('Expand')}</small></button>
      {inspectorOpen && inspector}
    </section>
  </aside>
}
