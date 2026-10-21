import { useEffect, useState, type ReactNode } from 'react'
import { BrandIcon } from './components/BrandIcon'
import { SkyPetController } from './components/SkyPetController'
import { VerifiedArtifactViewer } from './components/VerifiedArtifactViewer'
import { ImportedMphWorkspace } from './components/ImportedMphWorkspace'
import { LiteratureModelingWorkspace } from './components/LiteratureModelingWorkspace'
import { WarmPlanOverview } from './components/WarmPlanOverview'
import { translate, type Locale } from './i18n'
import { safeUiError } from './safeUiError'
import { currentEvidenceState, currentScientificDomain, verifiedCurrentResult } from './scientificView'
import { domainIdentity, geometryIdentity, reviewedBoundary } from './modelIdentity'
import type { AgentMode, ExecutionEta, JavaBridgeStatus, Plan, Project, RuntimeDomain, Session, TimelineItem } from './types'

type StudioPage = 'task' | 'model' | 'results' | 'evidence'

interface WarmStudioProps {
  visualFixture?: StudioPage
  illustrativeResult?: ReactNode
  locale: Locale
  projects: Project[]
  activeProject?: Project
  sessions: Session[]
  session: Session | null
  plan: Plan | null
  domain: RuntimeDomain | null
  bridge: JavaBridgeStatus
  comsolDetected?: boolean
  databaseRecovery?: string
  eta: ExecutionEta | null
  selectedModel: string
  modelAvailability: string
  literatureEnabled: boolean
  mode: AgentMode
  prompt: string
  busy: boolean
  askStreaming: boolean
  warning: string
  pendingIntent: boolean
  taskHistory: ReactNode
  planReview: ReactNode
  intentConfirmation: ReactNode
  technicalInspector: ReactNode
  timeline: TimelineItem[]
  onNewSession: () => Promise<void>
  onSelectSession: (id: string) => void
  onArchiveSession: (id: string) => void
  onSelectProject: (id: string) => void
  onPromptChange: (text: string) => void
  onModeChange: (mode: AgentMode) => void
  onSend: (mode: AgentMode) => void
  onCancelAsk: () => void
  onPause: () => void
  onResume: () => void
  onApprove: () => void
  onReject: () => void
  onModify: () => void
  onCancel: () => void
  onEmergencyStop: () => void
  onOpenSettings: () => void
  onOpenSetup: () => void
  onOpenAbout: () => void
  onOpenHelp: () => void
  onOpenLogs: () => void
  onOpenWorkspace: () => void
  onLocaleChange: (locale: Locale) => void
}

// All visible scientific claims in this component originate from Session or the
// RC4.2 domain projection. Review fixtures are never installed as production
// data or treated as a COMSOL readback.
const pageLabels: Array<[StudioPage, string]> = [
  ['task', 'Task'], ['model', 'Model'], ['results', 'Results'], ['evidence', 'Evidence'],
]
const executionStates = new Set(['PRECHECK', 'BUILDING', 'BUILD_VALIDATION', 'SOLVING', 'SOLUTION_VALIDATION', 'REPAIRING', 'REVALIDATING', 'EXPORTING', 'REPORTING'])

function quantity(value?: { value: number; unit: string } | null): string {
  return value && Number.isFinite(value.value) ? `${value.value} ${value.unit}` : '—'
}

function safeSnapshot(domain: RuntimeDomain | null): Record<string, unknown> | null {
  return domain?.contractVersion === 'rc42-v1' && domain.modelSnapshot && typeof domain.modelSnapshot === 'object'
    ? domain.modelSnapshot : null
}

function snapshotSection(snapshot: Record<string, unknown> | null, key: string): Record<string, unknown> {
  const value = snapshot?.[key]
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function snapshotBoundaries(snapshot: Record<string, unknown> | null): Array<Record<string, unknown>> {
  const value = snapshot?.boundaries
  return Array.isArray(value) ? value.filter((item) => item && typeof item === 'object') : []
}

function readable(value: unknown): string {
  if (typeof value === 'string' && value) return value
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return '—'
}

export function WarmStudio(props: WarmStudioProps) {
  const [page, setPage] = useState<StudioPage>(props.visualFixture ?? 'task')
  const [homeSection, setHomeSection] = useState<'new' | 'imported' | 'literature'>('new')
  // Turning an extension off while its page is open must not leave a blank
  // workspace. The backend gate also rejects new extraction requests.
  useEffect(() => { if (!props.literatureEnabled && homeSection === 'literature') setHomeSection('new') }, [props.literatureEnabled, homeSection])
  const [showProjects, setShowProjects] = useState(false)
  const [reviewedPlanId, setReviewedPlanId] = useState('')
  // Consent expires whenever the saved plan revision changes.
  const reviewKey = props.plan ? `${props.plan.plan_id}:${props.plan.updated_at}` : ''
  const [technicalOpen, setTechnicalOpen] = useState(false)
  const [artifactLoss, setArtifactLoss] = useState<{ sessionId: string; runId: string; attemptId: string } | null>(null)
  const t = (label: string) => translate(props.locale, label)
  // Filter during render as well as in App: an effect cannot prevent one
  // painted frame from showing the prior session's scientific claim.
  const domain = props.visualFixture ? props.domain : currentScientificDomain(props.session, props.domain)
  const snapshot = safeSnapshot(domain)
  const review = domain?.reviewRequest
  const selectedReviewBoundary = reviewedBoundary(domain, review ?? null)
  const boundaryReview = Boolean(selectedReviewBoundary)
  // An artifact endpoint failure can arrive after the projected verdict.
  // Revoke only this session/run/attempt's visible certification; never
  // mutate the Runtime-authored verdict or carry the loss into another task.
  const artifactLossCurrent = !props.visualFixture && artifactLoss !== null && artifactLoss.sessionId === props.session?.id &&
    artifactLoss.runId === domain?.runState?.runId && artifactLoss.attemptId === domain?.runState?.attemptId
  const evidenceState = artifactLossCurrent ? 'unavailable' : props.visualFixture ? domain?.evidenceState ?? 'unavailable' : currentEvidenceState(props.session, domain)
  const verdict = evidenceState === 'stale' ? null : domain?.authoritativeVerdict
  const completed = props.visualFixture
    ? Boolean(verdict?.completed && verdict.command_success && verdict.model_state_verified && verdict.scientific_validation_success && verdict.evidence_contract_passed && evidenceState === 'verified')
    : verifiedCurrentResult(props.session, domain) && !artifactLossCurrent
  const executionActive = executionStates.has(props.session?.state ?? '')
  const model = snapshotSection(snapshot, 'geometry')
  const material = snapshotSection(snapshot, 'material')
  const physics = snapshotSection(snapshot, 'physics')
  const size = snapshotSection(model, 'sizeMm')
  const boundary = snapshotBoundaries(snapshot)
  const selectedBoundary = review ? selectedReviewBoundary?.boundary : boundary[0]
  const reviewerName = readable(selectedBoundary?.location).replace(' edge', '')
  const reviewHighlight = selectedBoundary?.location === 'left edge' ? 'has-review'
    : selectedBoundary?.location === 'right edge' ? 'has-review-right' : ''
  const geometryDisplay = geometryIdentity(snapshot)
  const domainDisplay = domainIdentity(snapshot)
  const projectName = props.activeProject?.name ?? t('Local workspace')
  const title = props.session?.title || props.plan?.title || t('New scientific task')
  // Recovery is profile-wide. A surviving session title must not make the
  // unreadable earlier timeline look complete or scientifically certified.
  const recoveredHistory = props.sessions.some((item) => item.error?.startsWith('Local session database partially recovered.'))
  const etaSeconds = props.eta?.status === 'estimated' ? props.eta.estimated_remaining_seconds : domain?.runState?.estimatedSecondsRemaining
  const etaText = typeof etaSeconds === 'number' && etaSeconds >= 0
    ? `${Math.ceil(etaSeconds / 60)} ${t('minutes')} · ${t('historical estimate')}` : t('unknown')

  // Shared shell: restrained global navigation and a persistent, collapsible
  // project rail. Tabs switch views only; they never trigger a model action.
  return <div className="warm-studio">
    {props.visualFixture && <span className="warm-fixture-watermark">DESIGN REVIEW · NOT SCIENTIFIC DATA</span>}
    <header className="warm-header">
      <div className="warm-brand"><BrandIcon size={32} /><strong>SKY</strong><span>Workbench</span></div>
      <span className="warm-header-divider" />
      <span className="warm-workspace-label">{projectName}</span>
      <div className="warm-header-actions">
        {!props.visualFixture && <SkyPetController locale={props.locale} state={{ warning: props.warning, busy: props.busy, askStreaming: props.askStreaming, pendingIntent: props.pendingIntent, sessionState: props.session?.state, scientificVerified: completed }} />}
        {/* Installation detection is not a live Java Bridge attachment; keep the two states distinct. */}
        <span className={`warm-connection ${props.bridge.model_connected && props.bridge.real_comsol ? 'is-connected' : ''}`}><i />{t(props.bridge.model_connected && props.bridge.real_comsol ? 'COMSOL connected' : props.comsolDetected ? 'COMSOL installed · Bridge disconnected' : 'COMSOL unavailable')}</span>
        <button className="warm-language" onClick={() => props.onLocaleChange(props.locale === 'zh-CN' ? 'en-US' : 'zh-CN')} aria-label={t('Switch language')}>{props.locale === 'zh-CN' ? '中文 / EN' : 'EN / 中文'}</button>
        <details className="warm-more"><summary aria-label={t('More')}>•••</summary><div><button onClick={props.onOpenSetup}>{t('Environment')}</button><button onClick={props.onOpenHelp}>{t('Help')}</button><button onClick={props.onOpenLogs}>{t('Open Logs')}</button><button onClick={props.onOpenAbout}>{t('About')}</button></div></details>
      </div>
    </header>
    <aside className="warm-rail" aria-label={t('Scientific projects')}>
      <button className="warm-new-task" onClick={() => { void props.onNewSession(); setPage('task'); setHomeSection('new') }}>＋ {t('New task')}</button>
      <button className="warm-import-rail" onClick={() => { setPage('task'); setHomeSection('imported') }}>↥ {t('Imported MPH')}</button>
      {props.literatureEnabled && <button className="warm-import-rail" onClick={() => { setPage('task'); setHomeSection('literature') }}>▤ {t('Literature modeling')}</button>}
      <span className="warm-rail-caption">{t('RECENT')}</span>
      <div className="warm-recent-list">{props.sessions.slice(0, 7).map((item) => <div className="warm-recent-item" key={item.id}><button className={item.id === props.session?.id ? 'is-current' : ''} onClick={() => { props.onSelectSession(item.id); setPage('task') }}><strong>{item.title || t('Untitled task')}</strong><small>{t(item.state)}</small></button><button className="warm-archive-task" aria-label={`${t('Archive task')}: ${item.title}`} title={t('Archive task')} onClick={() => props.onArchiveSession(item.id)}>×</button></div>)}</div>
      <button className="warm-all-projects" onClick={() => setShowProjects((value) => !value)} aria-expanded={showProjects}>▱ <span>{t('All projects')}</span></button>
      {showProjects && <div className="warm-project-list">{props.projects.map((item) => <button key={item.id} className={item.active ? 'is-current' : ''} onClick={() => props.onSelectProject(item.id)}>{item.name}</button>)}<button onClick={props.onOpenWorkspace}>{t('Open workspace folder')}</button><small>{t('The managed workspace is never deleted from this menu.')}</small></div>}
      <button className="warm-rail-settings" onClick={props.onOpenSettings}>☷ <span>{t('Settings')}</span></button>
    </aside>
    <main className="warm-main">
      <nav className="warm-tabs" aria-label={t('Workspace views')}>
        <div>{pageLabels.map(([id, label]) => <button key={id} className={page === id ? 'is-active' : ''} onClick={() => setPage(id)}>{t(label)}</button>)}</div>
        <span>{title}</span>
        {page !== 'task' && <button className="warm-task-jump" onClick={() => setPage('task')}>{t('Enter a scientific request →')}</button>}
      </nav>
      {props.databaseRecovery === 'history_quarantined' ? <div className="warm-recovery-banner" role="alert"><strong>{t('Historical database isolated')}</strong><span>{t('The original database is backed up. Previous sessions and results are unavailable and unverified; new work uses a separate database.')}</span></div> : recoveredHistory && <div className="warm-recovery-banner" role="status"><strong>{t('Recovered history is incomplete')}</strong><span>{t('Earlier events could not be restored. Previous runs are interrupted and unverified; the original database backup is retained.')}</span></div>}
      {/* Emergency control is always visible during execution, regardless of
          which page is open. Pause is resumable only after a verified safe point;
          emergency termination is explicitly non-resumable for this attempt. */}
      {(executionActive || props.session?.state === 'PAUSED' || props.session?.state === 'CANCEL_REQUESTED') && <div className="warm-emergency-controls" role="status"><span><strong>{t('Execution control')}</strong> · {t(props.session?.state ?? 'IDLE')}</span><div>{executionActive && domain?.runState?.pauseCapability === 'available' && <button onClick={props.onPause}>{t('Pause at safe point')}</button>}{props.session?.state === 'PAUSED' && <button className="warm-resume" onClick={props.onResume} disabled={Boolean(review)}>{t('Resume execution')}</button>}{executionActive && <button className="warm-emergency-stop" onClick={props.onEmergencyStop}>{t('Emergency stop')}</button>}</div><small>{props.session?.state === 'PAUSED' ? t('Resume only after verified pause; a rejected review must be resolved first.') : props.session?.state === 'CANCEL_REQUESTED' ? t('Stop requested; waiting for owned process cleanup.') : t('Emergency stop ends this attempt. Only a verified safe pause can resume it.')}</small></div>}

      {/* A task starts with a scientific question, not a chat transcript or a fake model. */}
      {page === 'task' && <div className="warm-home-sections"><button className={homeSection === 'new' ? 'is-active' : ''} onClick={() => setHomeSection('new')}>{t('New models')}</button><button className={homeSection === 'imported' ? 'is-active' : ''} onClick={() => setHomeSection('imported')}>{t('Imported MPH')}</button>{props.literatureEnabled && <button className={homeSection === 'literature' ? 'is-active' : ''} onClick={() => setHomeSection('literature')}>{t('Literature modeling')}</button>}</div>}
      {page === 'task' && homeSection === 'imported' && <section className="warm-page warm-task-page"><ImportedMphWorkspace locale={props.locale} /></section>}
      {page === 'task' && homeSection === 'literature' && props.literatureEnabled && <section className="warm-page warm-task-page"><LiteratureModelingWorkspace locale={props.locale} onChoose={(excerpt) => { void props.onNewSession().then(() => { props.onPromptChange(excerpt); setHomeSection('new') }) }} /></section>}
      {page === 'task' && homeSection === 'new' && <section className="warm-page warm-task-page">
        <div className="warm-page-heading"><div><h1>{t('Create a scientific task')}</h1><p>{t('Start with the question. Review the scientific plan before anything runs.')}</p></div><span className="warm-status-pill">{props.plan ? t('Scientific plan available') : t('Draft · no model attached')}</span></div>
        <div className="warm-task-focus"><div className="sky-task-intro"><h2>{t('What would you like to investigate?')}</h2></div><div className="warm-compose"><textarea value={props.prompt} onChange={(event) => props.onPromptChange(event.target.value)} placeholder={t('Describe the geometry, material, physics, boundary conditions and desired result…')} aria-label={t('Scientific task description')} /><div className="warm-compose-actions"><span>{t('Plan first · approval before execution')}</span><button className="warm-primary" onClick={() => props.onSend('PLAN')} disabled={props.busy || !props.prompt.trim()}>{props.busy ? t('Working…') : t('Create scientific plan →')}</button></div></div><button className="warm-example-fill" onClick={() => props.onPromptChange(props.locale === 'zh-CN' ? '示例：建立二维稳态稀物质传递模型，矩形 10 mm × 5 mm，扩散系数 D=1e-9 m^2/s，左边界浓度 1 mol/m^3，右边界 0 mol/m^3，上下边界无通量；求解并导出浓度场。' : 'Example: Build a 2D stationary Transport of Diluted Species rectangle 10 mm x 5 mm, diffusion coefficient D=1e-9 m^2/s, left concentration 1 mol/m^3, right concentration 0 mol/m^3, top and bottom no flux; solve and export the concentration field.')}>{t('Fill TDS example · review before running')}</button>
          {props.pendingIntent && <div className="warm-plan-review" role="region" aria-label={t('Intent confirmation')}>{props.intentConfirmation}</div>}
          {props.plan && <div className="warm-plan-summary"><WarmPlanOverview plan={props.plan} locale={props.locale} /><details className="warm-full-plan"><summary>{t('Open complete ScientificPlan and checks')}</summary>{props.planReview}</details><label className="warm-review-check"><input type="checkbox" checked={reviewedPlanId === reviewKey} onChange={(event) => setReviewedPlanId(event.target.checked ? reviewKey : '')} />{t('I reviewed the full plan and its assumptions.')}</label><div className="warm-plan-actions"><button className="warm-primary" disabled={!props.plan.runnable || props.busy || props.pendingIntent || reviewedPlanId !== reviewKey} onClick={() => props.onSend('RUN')}>{t('Confirm & run')}</button></div></div>}
          <div className="warm-task-next"><article><b>01</b><div><strong>{t('Review the scientific plan')}</strong><p>{t('Confirm geometry, materials and boundaries. Assumptions stay visible before execution.')}</p></div></article><article><b>02</b><div><strong>{t('Run, inspect and verify')}</strong><p>{t('Inspect the model and its readback evidence. Scientific validation follows the solve.')}</p></div></article></div>
          <details className="warm-secondary-tools"><summary>{t('ASK / PLAN / RUN and activity')}</summary><div className="warm-mode-row">{(['ASK', 'PLAN', 'RUN'] as AgentMode[]).map((value) => <button key={value} className={props.mode === value ? 'is-selected' : ''} onClick={() => props.onModeChange(value)}>{value}</button>)}<button onClick={() => props.onSend(props.mode)} disabled={props.busy || (props.mode === 'RUN' && reviewedPlanId !== reviewKey)}>{t('Send')}</button>{props.askStreaming && <button onClick={props.onCancelAsk}>{t('Cancel ASK')}</button>}</div>{props.taskHistory}</details>
        </div>
      </section>}

      {/* The dark model canvas is a schematic of a proven RC4.2 readback only.
          A plan without readback receives an explicit unavailable state. */}
      {page === 'model' && <section className="warm-page warm-science-page"><div className="warm-page-heading"><div><h1>{t('Model & human review')}</h1><p>{snapshot ? `${readable(snapshot.name)} · ${readable(physics.interface)}` : t('A live model readback is not available yet.')}</p></div><span className={`warm-status-pill ${review ? 'needs-review' : ''}`}>{review ? t('1 change needs approval') : snapshot ? t('COMSOL readback available') : t('Readback unavailable')}</span></div>
        <div className="warm-science-layout"><div className="warm-viewport"><header><strong data-source-id={geometryDisplay.sourceId ?? undefined}>{snapshot ? geometryDisplay.sourceId ? geometryDisplay.presentationId : t('Geometry') : t('Scientific model')}</strong><span>{snapshot ? '2D / Fit to view' : t('No verified geometry')}</span></header>{snapshot && model.kind === 'rectangle' ? <><div className="warm-readback-tag">{t('Real COMSOL readback')}</div><div className="warm-schematic"><span className="warm-top-boundary">{t('Top')} · {t('not verified')}</span><span className="warm-left-label">{quantity(boundary.find((item) => item.location === 'left edge')?.value as { value: number; unit: string } | undefined)}</span><div className={`warm-rectangle ${boundaryReview ? reviewHighlight : ''}`} data-source-id={selectedReviewBoundary?.sourceId}><strong>{t(domainDisplay.presentationId)}</strong><span>{readable(material.name)}</span></div><span className="warm-right-label">{quantity(boundary.find((item) => item.location === 'right edge')?.value as { value: number; unit: string } | undefined)}</span><span className="warm-bottom-boundary">{t('Bottom')} · {t('not verified')}</span><span className="warm-dimension-x">{Number.isFinite(Number(size.x)) ? `${size.x} mm` : '—'}</span><span className="warm-dimension-y">{Number.isFinite(Number(size.y)) ? `${size.y} mm` : '—'}</span></div><footer><span>{boundaryReview ? `${reviewerName} · ${t('Human review')}` : t('Model readback')}</span><span>{readable(selectedBoundary?.label)}</span></footer></> : <div className="warm-viewport-empty"><strong>{t('No verified model geometry available')}</strong><p>{t('The plan is not COMSOL readback. Geometry appears after a real attached model reports it.')}</p></div>}</div>
          <aside className="warm-science-inspector">{review ? <><h2>{t(boundaryReview ? 'Review boundary change' : 'Human review')}</h2><small>{t('HUMAN REVIEW')}</small><strong className="warm-review-object">{boundaryReview ? `${reviewerName} · ${selectedReviewBoundary?.presentationId}` : t('Readback unavailable')}</strong><div className="warm-review-values"><div><strong>{review.current} {review.unit}</strong><span>{t('Current')}</span></div><i>→</i><div><strong>{review.proposed} {review.unit}</strong><span>{t('Proposed')}</span></div></div><p>{review.reason}</p><p className="warm-review-note">{t('Paused · No changes applied')}<br /><span>{t(boundaryReview ? 'Approval applies this edit. Scientific validation follows the solve.' : 'No model-state verification is claimed.')}</span></p><button className="warm-primary" disabled={props.session?.state !== 'PAUSED' || !boundaryReview} onClick={props.onApprove}>{t('Approve & apply')}</button><div className="warm-review-actions"><button disabled={!boundaryReview} onClick={() => { props.onModify(); setPage('evidence'); setTechnicalOpen(true) }}>{t('Open correction controls')}</button><button onClick={props.onReject}>{t('Reject & cancel run')}</button></div></> : <><h2>{t('Model state')}</h2><small>{t('SOURCE: RUNTIME READBACK')}</small><dl className="warm-fact-list"><div><dt>{t('Geometry')}</dt><dd>{snapshot ? `${readable(model.kind)} · ${readable(size.x)} × ${readable(size.y)} mm` : '—'}</dd></div><div><dt>{t('Material')}</dt><dd>{snapshot ? readable(material.name) : '—'}</dd></div><div><dt>{t('Physics')}</dt><dd>{snapshot ? readable(physics.interface) : '—'}</dd></div><div><dt>{t('Mesh')}</dt><dd>{snapshot ? readable(snapshotSection(snapshot, 'mesh').readback) : '—'}</dd></div><div><dt>{t('Solver')}</dt><dd>{snapshot ? readable(snapshotSection(snapshot, 'solver').solver) : '—'}</dd></div></dl><p className="warm-review-note">{snapshot ? t('Readback is not scientific certification.') : t('No model-state verification is claimed.')}</p></>}
            <div className="warm-run-controls"><span>{domain?.runState?.headline ?? t(props.session?.state ?? 'IDLE')} · {t('ETA')} {etaText} · {props.selectedModel || t('Model unavailable')} · {t(props.modelAvailability)}</span></div>
          </aside></div></section>}

      {/* A result image is loaded by the existing SHA-checked artifact viewer.
          Neither missing artifacts nor incomplete verdicts are painted as success. */}
      {page === 'results' && <section className="warm-page warm-science-page">
        <div className="warm-page-heading"><div><h1>{t(completed ? 'Verified scientific result' : 'Scientific results')}</h1><p>{t('Only run-bound, integrity-checked artifacts appear here.')}</p></div><span className={`warm-status-pill ${completed ? 'is-verified' : ''}`}>{props.visualFixture ? t('Illustrative layout only') : completed ? t('Scientific validation passed') : t('Result unavailable or unverified')}</span></div>
        <div className="warm-science-layout"><div className="warm-viewport warm-result-viewport">
          {props.visualFixture === 'results' && props.illustrativeResult ? props.illustrativeResult
            : artifactLossCurrent ? <div className="warm-viewport-empty" role="alert"><strong>{t('Evidence integrity lost')}</strong><p>{t('The artifact bytes no longer match the frozen SHA-256. Certified/verified display is disabled.')}</p><button onClick={() => setArtifactLoss(null)}>{t('Retry')}</button></div>
              : completed ? <VerifiedArtifactViewer locale={props.locale} session={props.session} presentation="gallery" currentRunId={verdict?.run_id} currentAttemptId={domain?.runState?.attemptId} onIntegrityLost={(runId, attemptId) => setArtifactLoss({ sessionId: props.session?.id ?? '', runId, attemptId })} />
                : <div className="warm-viewport-empty"><strong>{t('No verified result artifact available.')}</strong><p>{t('A completed current run and intact frozen evidence are required.')}</p></div>}
        </div><aside className="warm-science-inspector"><h2>{t('Result evidence')}</h2><small>{t('AUTHORITATIVE VERDICT')}</small><dl className="warm-fact-list">
          <div><dt>{t('Command Success')}</dt><dd>{verdict?.command_success === true ? t('Passed') : t('Not passed')}</dd></div>
          <div><dt>{t('Model State Verified')}</dt><dd>{verdict?.model_state_verified === true ? t('Passed') : t('Not passed')}</dd></div>
          <div><dt>{t('Scientific Validation Passed')}</dt><dd>{verdict?.scientific_validation_success === true ? t('Passed') : t('Not passed')}</dd></div>
          <div><dt>{t('Evidence Contract')}</dt><dd>{verdict?.evidence_contract_passed === true && !artifactLossCurrent ? t('Passed') : t('Not passed')}</dd></div>
        </dl><p className="warm-review-note">{completed ? t('See Evidence for the complete record and Model Card.') : t('No scientific success is claimed without all required checks.')}</p><button onClick={() => setPage('evidence')}>{t('Inspect evidence →')}</button></aside></div>
      </section>}

      {/* Evidence and maintenance detail remain available on demand instead of
          crowding the primary scientific work surface. */}
      {page === 'evidence' && <section className="warm-page warm-evidence-page"><div className="warm-page-heading"><div><h1>{t('Evidence & activity')}</h1><p>{t('Trace decisions, COMSOL events, validation and artifacts without changing their status.')}</p></div><span className={`warm-status-pill ${completed ? 'is-verified' : ''}`}>{t(evidenceState)}</span></div><div className="warm-evidence-layout"><section><h2>{t('Evidence timeline')}</h2>{props.timeline.length ? <ol className="warm-timeline">{[...props.timeline].reverse().slice(0, 60).map((event) => <li key={event.id}><span className={`warm-timeline-dot ${event.status}`} /><div><strong>{event.label}</strong><small>{event.phase} · {event.created_at}</small></div></li>)}</ol> : <p>{t('No activity recorded yet.')}</p>}</section><aside><h2>{t('Scientific record')}</h2><p>{t('Run ID')}: <code>{verdict?.run_id ?? '—'}</code></p><p>{t('Attempt ID')}: <code>{domain?.runState?.attemptId ?? '—'}</code></p><p>{t('Evidence Contract')}: {t(evidenceState)}</p>{domain?.contractGaps?.length ? <details><summary>{t('Contract gaps')}</summary><ul>{domain.contractGaps.map((gap) => <li key={gap}>{gap}</li>)}</ul></details> : null}<details className="warm-technical" open={technicalOpen} onToggle={(event) => setTechnicalOpen(event.currentTarget.open)}><summary>{t('Technical inspector')}</summary>{props.technicalInspector}</details><details><summary>{t('Model Card and session record')}</summary>{props.taskHistory}</details></aside></div></section>}

      {props.warning && <div className="warm-inline-warning" role="status">{safeUiError(props.warning)}</div>}
      <footer className="warm-page-footer"><span>{t('Independent scientific workbench · COMSOL requires a valid installation and license')}</span><span>{t('Scientific claims require verified evidence')}</span></footer>
    </main>
  </div>
}
