import { useEffect, useState, type ReactNode } from 'react'
import { BrandIcon } from './components/BrandIcon'
import { api } from './api'
import { SkyPetController } from './components/SkyPetController'
import { VerifiedArtifactViewer } from './components/VerifiedArtifactViewer'
import { ImportedMphWorkspace } from './components/ImportedMphWorkspace'
import { ComsolModelView } from './components/ComsolModelView'
import { LiteratureModelingWorkspace } from './components/LiteratureModelingWorkspace'
import { ScientificSoftwareWorkspace, type SoftwareEngine } from './components/ScientificSoftwareWorkspace'
import { EngineTaskWorkspace } from './components/EngineTaskWorkspace'
import { ExpertWorkflow, expertCases } from './components/ExpertWorkflow'
import { scientificDomains } from './scientificDomains'
import { WarmPlanOverview } from './components/WarmPlanOverview'
import { translate, type Locale } from './i18n'
import { safeUiError } from './safeUiError'
import { canRunReviewedPlan, isExecutionActive } from './runUiPolicy'
import { currentEvidenceState, currentScientificDomain, verifiedCurrentCoolingResult, verifiedCurrentDiffusionImage, verifiedCurrentResult } from './scientificView'
import { reviewedBoundary } from './modelIdentity'
import { comsolHomeStatus } from './installationStatus'
import type { AgentMode, ExecutionEta, ImportedMph, JavaBridgeStatus, Plan, Project, RuntimeDomain, Session, TimelineItem } from './types'

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
  comsolVersion?: string
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
  planCompletionReview?: ReactNode
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
  onOpenLiveInspector: () => void
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

function safeSnapshot(domain: RuntimeDomain | null): Record<string, unknown> | null {
  return domain?.contractVersion === 'rc42-v1' && domain.modelSnapshot && typeof domain.modelSnapshot === 'object'
    ? domain.modelSnapshot : null
}

function snapshotSection(snapshot: Record<string, unknown> | null, key: string): Record<string, unknown> {
  const value = snapshot?.[key]
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function readable(value: unknown): string {
  if (typeof value === 'string' && value) return value
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return '—'
}

export function WarmStudio(props: WarmStudioProps) {
  const [page, setPage] = useState<StudioPage>(props.visualFixture ?? 'task')
  const [workflowOpen, setWorkflowOpen] = useState(false)
  const [homeSection, setHomeSection] = useState<'new' | 'imported' | 'literature'>('new')
  const [openedImport, setOpenedImport] = useState<{ item: ImportedMph; project: string } | null>(null)
  // A script workspace belongs to the task that opened it. Switching tasks
  // returns to the COMSOL view before any older draft is rendered.
  const [softwareOwner, setSoftwareOwner] = useState<string | null>(null)
  const [softwareEngine, setSoftwareEngine] = useState<SoftwareEngine>('Mathics3')
  const [domainId, setDomainId] = useState<string>('continuum')
  // Turning an extension off while its page is open must not leave a blank
  // workspace. The backend gate also rejects new extraction requests.
  useEffect(() => { if (!props.literatureEnabled && homeSection === 'literature') setHomeSection('new') }, [props.literatureEnabled, homeSection])
  const [showProjects, setShowProjects] = useState(false)
  const [reviewedPlanId, setReviewedPlanId] = useState('')
  // Review belongs to this session and this exact plan revision.
  const reviewKey = props.plan && props.session
    ? `${props.session.id}:${props.plan.plan_id}:${props.plan.updated_at}` : ''
  const [technicalOpen, setTechnicalOpen] = useState(false)
  const [artifactLoss, setArtifactLoss] = useState<{ sessionId: string; runId: string; attemptId: string; filename: string } | null>(null)
  const [artifactRetrying, setArtifactRetrying] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState('')
  const [heatResultKey, setHeatResultKey] = useState('')
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
  const retryArtifact = async () => {
    if (!artifactLossCurrent || !artifactLoss || artifactRetrying) return
    const failed = artifactLoss
    setArtifactRetrying(true)
    try {
      // The Runtime checks the frozen manifest and SHA-256 before serving.
      // A click alone must never restore the Verified badge.
      const url = await api.artifactObjectUrl(failed.runId, failed.filename)
      URL.revokeObjectURL(url)
      setArtifactLoss((current) => current === failed ? null : current)
    } catch { /* Keep evidence blocked; the existing integrity message remains visible. */ }
    finally { setArtifactRetrying(false) }
  }
  const evidenceState = artifactLossCurrent ? 'unavailable' : props.visualFixture ? domain?.evidenceState ?? 'unavailable' : currentEvidenceState(props.session, domain)
  const verdict = evidenceState === 'stale' ? null : domain?.authoritativeVerdict
  const importProject = props.activeProject?.id ?? props.session?.project_id ?? ''
  const importedModel = openedImport?.project === importProject ? openedImport.item : null
  const completed = !importedModel && (props.visualFixture
    ? Boolean(verdict?.completed && verdict.command_success && verdict.model_state_verified &&
      verdict.scientific_validation_success && verdict.evidence_contract_passed && evidenceState === 'verified')
    : verifiedCurrentResult(props.session, domain) && !artifactLossCurrent)
  const diffusionImage = !importedModel && !artifactLossCurrent && verifiedCurrentDiffusionImage(props.session, domain)
  const coolingResult = !importedModel && !artifactLossCurrent && verifiedCurrentCoolingResult(props.session, domain)
  const executionActive = isExecutionActive(props.session?.state)
  const runState = domain?.runState
  const heatKey = !props.visualFixture && coolingResult && runState?.attemptId
    ? `${props.session?.id}:${runState.runId}:${runState.attemptId}` : ''
  const checkpoint = executionActive && runState && [...props.session?.events ?? []].reverse()
    .find(event => event.type === 'run.model_checkpoint' && event.payload.run_id === runState.runId &&
      event.payload.attempt_id === runState.attemptId && typeof event.payload.index === 'number')
  const taskStarted = Boolean(props.session?.messages.length || props.pendingIntent || props.askStreaming || props.plan)
  const hasWorkflow = Boolean(props.session && expertCases(props.session).length)
  const zh = props.locale === 'zh-CN'
  const historicalPlan = props.session?.state === 'COMPLETED'
  const softwareModel = page === 'model' && softwareOwner === (props.session?.id ?? '')
  const model = snapshotSection(snapshot, 'geometry')
  const material = snapshotSection(snapshot, 'material')
  const physics = snapshotSection(snapshot, 'physics')
  const size = snapshotSection(model, 'sizeMm')
  const reviewerName = readable(selectedReviewBoundary?.boundary?.location).replace(' edge', '')
  const projectName = props.activeProject?.name ?? t('Local workspace')
  const title = (page === 'model' || page === 'results') && importedModel
    ? importedModel.original_filename : props.session?.title || props.plan?.title || t('New scientific task')
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
        {!props.visualFixture && <SkyPetController locale={props.locale} state={{
          warning: props.warning, busy: props.busy,
          askStreaming: props.askStreaming, pendingIntent: props.pendingIntent,
          sessionState: props.session?.state, scientificVerified: completed,
        }} />}
        {/* Installation detection is not a live Java Bridge attachment; keep the two states distinct. */}
        <span className={`warm-connection ${props.bridge.model_connected && props.bridge.real_comsol ? 'is-connected' : ''}`}>
          <i />{comsolHomeStatus(props.comsolVersion ?? '', Boolean(props.comsolDetected),
            props.bridge.comsol_version, props.bridge.model_connected && props.bridge.real_comsol,
            props.locale === 'zh-CN')}
        </span>
        <button className="warm-language"
          onClick={() => props.onLocaleChange(props.locale === 'zh-CN' ? 'en-US' : 'zh-CN')}
          aria-label={t('Switch language')}>
          {props.locale === 'zh-CN' ? '中文 / EN' : 'EN / 中文'}
        </button>
        <button className="warm-header-settings" type="button" onClick={props.onOpenSettings}
          aria-label={t('Settings')} title={t('Settings')}>
          <span aria-hidden="true">⚙</span><span className="warm-header-settings-label">{t('Settings')}</span>
        </button>
        <details className="warm-more" onClick={(event) => {
          if ((event.target as HTMLElement).closest('button')) event.currentTarget.open = false
        }}>
          <summary aria-label={t('More')}>•••</summary>
          <div>
            <button onClick={props.onOpenSettings}>{t('Settings')}</button>
            <button onClick={props.onOpenSetup}>{t('Environment')}</button>
            <button onClick={props.onOpenHelp}>{t('Help')}</button>
            <button onClick={props.onOpenLogs}>{t('Open Logs')}</button>
            <button onClick={props.onOpenAbout}>{t('About')}</button>
          </div>
        </details>
      </div>
    </header>
    <aside className="warm-rail" aria-label={t('Scientific projects')}>
      <button className="warm-new-task" onClick={() => { setOpenedImport(null); void props.onNewSession(); setPage('task'); setHomeSection('new') }}>＋ {t('New task')}</button>
      <button className="warm-import-rail" onClick={() => { setPage('task'); setHomeSection('imported') }}>↥ {t('Imported MPH')}</button>
      {props.literatureEnabled && <button className="warm-import-rail" onClick={() => { setPage('task'); setHomeSection('literature') }}>▤ {t('Literature modeling')}</button>}
      <span className="warm-rail-caption">{t('RECENT')}</span>
      <div className="warm-recent-list">
        {props.sessions.slice(0, 7).map((item) => <div className="warm-recent-item" key={item.id}>
          <button className={item.id === props.session?.id ? 'is-current' : ''}
            onClick={() => { setOpenedImport(null); props.onSelectSession(item.id); setPage('task'); setHomeSection('new') }}>
            <strong>{item.title || t('Untitled task')}</strong><small>{t(item.state)}</small>
          </button>
          <button className="warm-archive-task" aria-label={`${t('Archive task')}: ${item.title}`}
            title={t('Archive task')} onClick={() => props.onArchiveSession(item.id)}>×</button>
        </div>)}
      </div>
      <button className="warm-all-projects" onClick={() => setShowProjects((value) => !value)} aria-expanded={showProjects}>▱ <span>{t('All projects')}</span></button>
      {showProjects && <div className="warm-project-list">
        {props.projects.map((item) => <button key={item.id}
          className={item.active ? 'is-current' : ''}
          onClick={() => { setOpenedImport(null); props.onSelectProject(item.id) }}>{item.name}</button>)}
        <button onClick={props.onOpenWorkspace}>{t('Open workspace folder')}</button>
        <small>{t('The managed workspace is never deleted from this menu.')}</small>
      </div>}
      <button className="warm-rail-settings" onClick={props.onOpenSettings}>☷ <span>{t('Settings')}</span></button>
    </aside>
    <main className="warm-main">
      <nav className="warm-tabs" aria-label={t('Workspace views')}>
        <div>{pageLabels.map(([id, label]) => <button key={id} className={page === id ? 'is-active' : ''} onClick={() => setPage(id)}>{t(label)}</button>)}</div>
        <span>{title}</span>
        {page !== 'task' && <button className="warm-task-jump" onClick={() => setPage('task')}>{t('Enter a scientific request →')}</button>}
      </nav>
      {props.databaseRecovery === 'history_quarantined'
        ? <div className="warm-recovery-banner" role="alert">
          <strong>{t('Historical database isolated')}</strong>
          <span>{t('The original database is backed up. Previous sessions and results are unavailable and unverified; new work uses a separate database.')}</span>
        </div>
        : recoveredHistory && <div className="warm-recovery-banner" role="status">
          <strong>{t('Recovered history is incomplete')}</strong>
          <span>{t('Earlier events could not be restored. Previous runs are interrupted and unverified; the original database backup is retained.')}</span>
        </div>}
      {/* Emergency control is always visible during execution, regardless of
          which page is open. Pause is resumable only after a verified safe point;
          emergency termination is explicitly non-resumable for this attempt. */}
      {(executionActive || props.session?.state === 'PAUSED' || props.session?.state === 'CANCEL_REQUESTED') &&
        <div className="warm-emergency-controls" role="status">
          <span><strong>{t('Execution control')}</strong> · {t(props.session?.state ?? 'IDLE')}</span>
          <div>
            {executionActive && domain?.runState?.pauseCapability === 'available' &&
              <button onClick={props.onPause}>{t('Pause at safe point')}</button>}
            {props.session?.state === 'PAUSED' &&
              <button className="warm-resume" onClick={props.onResume} disabled={Boolean(review)}>
                {t('Resume execution')}
              </button>}
            {executionActive &&
              <button className="warm-emergency-stop" onClick={props.onEmergencyStop}>{t('Emergency stop')}</button>}
          </div>
          <small>{props.session?.state === 'PAUSED'
            ? t('Resume only after verified pause; a rejected review must be resolved first.')
            : props.session?.state === 'CANCEL_REQUESTED'
              ? t('Stop requested; waiting for owned process cleanup.')
              : t('Emergency stop ends this attempt. Only a verified safe pause can resume it.')}</small>
        </div>}

      {/* A task starts with a scientific question, not a chat transcript or a fake model. */}
      {page === 'task' && <div className="warm-home-sections">
        <button className={homeSection === 'new' ? 'is-active' : ''}
          onClick={() => setHomeSection('new')}>{t('New models')}</button>
        <button className={homeSection === 'imported' ? 'is-active' : ''}
          onClick={() => setHomeSection('imported')}>{t('Imported MPH')}</button>
        {props.literatureEnabled && <button className={homeSection === 'literature' ? 'is-active' : ''}
          onClick={() => setHomeSection('literature')}>{t('Literature modeling')}</button>}
      </div>}
      {page === 'task' && homeSection === 'imported' && <section className="warm-page warm-task-page">
        <ImportedMphWorkspace locale={props.locale} key={importProject} onOpen={(item) => {
          setOpenedImport({ item, project: importProject }); setSoftwareOwner(null); setPage('model')
        }} />
      </section>}
      {page === 'task' && homeSection === 'literature' && props.literatureEnabled &&
        <section className="warm-page warm-task-page">
          <LiteratureModelingWorkspace locale={props.locale} onChoose={(excerpt) => {
            void props.onNewSession().then(() => {
              props.onPromptChange(excerpt)
              setHomeSection('new')
            })
          }} />
        </section>}
      {page === 'task' && homeSection === 'new' && <section className="warm-page warm-task-page">
        <div className="warm-page-heading">
          <h1>{t('Create a scientific task')}</h1>
          <span className="warm-status-pill">
            {props.plan ? t('Scientific plan available') : t('Draft · no model attached')}
          </span>
        </div>
        <div className={`warm-task-focus ${taskStarted ? 'warm-task-dialogue' : ''}`}>
          {!taskStarted && <div className="sky-task-intro"><h2>{t('What would you like to investigate?')}</h2></div>}
          {taskStarted && <div className="warm-conversation" aria-label={zh ? '科研对话' : 'Research conversation'}>
            {props.taskHistory}
          </div>}
          {hasWorkflow && <button className="warm-workflow-link" type="button" onClick={() => {
            setWorkflowOpen(true); setPage('evidence')
          }}>{zh ? '查看专家工作流' : 'View expert workflow'}</button>}
          {props.pendingIntent && <div className="warm-plan-review" role="region" aria-label={t('Intent confirmation')}>{props.intentConfirmation}</div>}
          {props.plan && <div className="warm-plan-summary">
            {props.planCompletionReview}
            <WarmPlanOverview plan={props.plan} locale={props.locale} historical={historicalPlan} />
            <details className="warm-full-plan">
              <summary>{t('Open complete ScientificPlan and checks')}</summary>{props.planReview}
            </details>
            {historicalPlan ? <p role="status">{props.locale === 'zh-CN' ? '此计划对应已结束的运行。可查看结果与证据；新计算请先创建或更新计划并重新审核。' : 'This plan belongs to a finished run. View Results and Evidence; create or update a plan and review it for a new calculation.'}</p> : <><label className="warm-review-check">
              <input type="checkbox" checked={reviewedPlanId === reviewKey}
                onChange={(event) => setReviewedPlanId(event.target.checked ? reviewKey : '')} />
              {t('I reviewed the full plan and its assumptions.')}
            </label>
            <div className="warm-plan-actions">
              <button className="warm-primary" onClick={() => props.onSend('RUN')}
                disabled={!canRunReviewedPlan(props.plan, props.session?.id, reviewedPlanId, props.busy, props.pendingIntent, props.session?.state)}>
                {t('Confirm & run')}
              </button>
            </div></>}
          </div>}
          {!props.pendingIntent && <div className="warm-compose">
            <textarea value={props.prompt} onChange={(event) => props.onPromptChange(event.target.value)}
              disabled={props.busy}
              placeholder={taskStarted ? (zh ? '补充条件或询问进展…' : 'Add conditions or ask about progress…')
                : t('Describe the geometry, material, physics, boundary conditions and desired result…')}
              aria-label={t('Scientific task description')} />
            <div className="warm-compose-actions">
              {taskStarted && <div className="warm-mode-row">
                {(['ASK', 'PLAN'] as AgentMode[]).map((value) => <button key={value} type="button"
                  aria-pressed={(props.mode === 'RUN' ? 'PLAN' : props.mode) === value}
                  className={(props.mode === 'RUN' ? 'PLAN' : props.mode) === value ? 'is-selected' : ''}
                  disabled={props.busy || props.pendingIntent} onClick={() => props.onModeChange(value)}>{value}</button>)}
                <button type="button" onClick={() => setPage('evidence')}>{t('Evidence')}</button>
              </div>}
              {props.askStreaming ? <button onClick={props.onCancelAsk}>{t('Cancel ASK')}</button> :
                <button className="warm-primary" onClick={() => props.onSend(taskStarted && props.mode === 'ASK' ? 'ASK' : 'PLAN')}
                  disabled={props.busy || props.pendingIntent || !props.prompt.trim()}>
                  {props.busy ? t('Working…') : taskStarted ? t('Send') : t('Create scientific plan →')}
                </button>}
            </div>
          </div>}
        </div>
      </section>}

      {page === 'model' && <nav className="scientific-domain-nav" aria-label={props.locale === 'zh-CN' ? '科学任务领域' : 'Scientific domains'}>
        {scientificDomains.map((domain) => <button type="button" key={domain.id} className={domainId === domain.id ? 'is-active' : ''}
          onClick={() => { setDomainId(domain.id); const engine = domain.engines[0];
            if (engine === 'COMSOL') setSoftwareOwner(null)
            else { setSoftwareEngine(engine); setSoftwareOwner(props.session?.id ?? '') }
          }}>{props.locale === 'zh-CN' ? domain.zh : domain.en}</button>)}
      </nav>}
      {page === 'model' && <div className="warm-model-sources" aria-label={t('Model source')}>
        {domainId === 'continuum' && <button type="button" className={!softwareModel ? 'is-active' : ''} onClick={() => setSoftwareOwner(null)}>
          {props.locale === 'zh-CN' ? 'COMSOL 模型' : 'COMSOL model'}
        </button>}
        {(['MATLAB', 'LAMMPS', 'VASP', 'Materials Studio', 'Mathics3', 'Python'] as SoftwareEngine[])
          .filter((engine) => (scientificDomains.find((domain) => domain.id === domainId)?.engines as readonly string[] | undefined)?.includes(engine)).map((engine) =>
          <button type="button" key={engine} className={softwareModel && softwareEngine === engine ? 'is-active' : ''}
            onClick={() => { setSoftwareOwner(props.session?.id ?? ''); setSoftwareEngine(engine) }}>{engine}</button>)}
      </div>}
      {page === 'model' && domainId === 'quantum' && softwareEngine === 'Materials Studio' && softwareModel &&
        <p className="engine-scope-note">{props.locale === 'zh-CN' ? 'Materials Studio 当前仅接入 Forcite 单点能；量子模块 CASTEP / DMol³ 尚未完成接入与实机验证。' : 'Materials Studio currently exposes Forcite single-point energy. CASTEP / DMol³ quantum modules are not connected or live-validated.'}</p>}
      {softwareModel && (softwareEngine === 'LAMMPS' || softwareEngine === 'VASP' || softwareEngine === 'Materials Studio'
        ? <EngineTaskWorkspace key={`${props.session?.project_id ?? 'scratch'}:${softwareEngine}`} engine={softwareEngine}
            projectId={props.session?.project_id ?? 'scratch'} locale={props.locale} />
        : <ScientificSoftwareWorkspace key={`${props.session?.id ?? 'no-session'}:${softwareEngine}`}
            engine={softwareEngine} sessionId={props.session?.id ?? null}
            locale={props.locale} onOpenSettings={props.onOpenSettings} />)}

      {/* COMSOL images come from the current run's integrity-checked artifact.
          A model readback alone does not create pixels or certify science. */}
      {page === 'model' && !softwareModel && importedModel && <section className="warm-page warm-science-page">
        <div className="warm-page-heading"><div><h1>{importedModel.original_filename}</h1>
          <p>{zh ? '导入的 MPH · 只读模型查看' : 'Imported MPH · read-only inspection'}</p></div>
          <button onClick={() => { setHomeSection('imported'); setPage('task') }}>{t('Imported MPH')}</button>
        </div>
        <div className="warm-viewport warm-comsol-native">
          <ComsolModelView key={`${importProject}:${importedModel.id}`} zh={zh}
            source={{ kind: 'imported', id: importedModel.id, projectId: importProject }} />
        </div>
      </section>}
      {page === 'model' && !softwareModel && !importedModel && <section className="warm-page warm-science-page">
        <div className="warm-page-heading">
          <div>
            <h1>{t('Model & human review')}</h1>
            <p>{checkpoint
              ? (zh ? '当前尝试已发布网格检查点。' : 'The current attempt published a mesh checkpoint.')
              : snapshot
              ? `${readable(snapshot.name)} · ${readable(physics.interface)}`
              : t('A live model readback is not available yet.')}</p>
          </div>
          <span className={`warm-status-pill ${review ? 'needs-review' : ''}`}>
            {review ? t('1 change needs approval') : checkpoint ? (zh ? '网格检查点可用' : 'Mesh checkpoint available')
              : snapshot ? t('COMSOL readback available') : t('Readback unavailable')}
          </span>
        </div>
        <div className="warm-science-layout warm-model-layout">
          <div className="warm-viewport warm-result-viewport warm-comsol-native">
            {props.session && !props.visualFixture && (executionActive ? (checkpoint && runState ? <ComsolModelView
              key={`${props.session.id}:${runState.attemptId}:checkpoint`} zh={zh}
              source={{ kind: 'checkpoint', id: props.session.id, projectId: props.session.project_id,
                runId: runState.runId, attemptId: runState.attemptId, index: Number(checkpoint.payload.index) }} />
              : <div className="warm-viewport-empty" role="status">{zh ? '等待当前尝试发布网格检查点。' : 'Waiting for a mesh checkpoint from the current attempt.'}</div>)
              : props.session.state !== 'CANCEL_REQUESTED' && <ComsolModelView
              key={`${props.session.id}:${domain?.runState?.attemptId ?? ''}`} zh={zh}
              source={{ kind: 'session', id: props.session.id, projectId: props.session.project_id }}
              fallback={<p>{zh ? '打开导入的 MPH，或刷新此任务的已保存模型。' : 'Open an imported MPH or refresh this task’s saved model.'}</p>} />)}
            {props.visualFixture && (artifactLossCurrent ? <div className="warm-viewport-empty" role="alert">
                <strong>{t('Evidence integrity lost')}</strong>
                <p>{t('The artifact bytes no longer match the frozen SHA-256. Certified/verified display is disabled.')}</p>
                <button onClick={() => void retryArtifact()} disabled={artifactRetrying}>
                  {artifactRetrying ? t('Verifying artifact integrity…') : t('Retry')}
                </button>
              </div>
              : completed ? <VerifiedArtifactViewer locale={props.locale} session={props.session}
                presentation="gallery" currentRunId={verdict?.run_id}
                currentAttemptId={domain?.runState?.attemptId}
                onIntegrityLost={(runId, attemptId, filename) => setArtifactLoss({
                  sessionId: props.session?.id ?? '', runId, attemptId, filename,
                })} />
              : <div className="warm-viewport-empty">
                  <strong>{props.locale === 'zh-CN' ? '暂无 COMSOL 原生画面' : 'No COMSOL-rendered image yet'}</strong>
                  <p>{props.locale === 'zh-CN'
                    ? '完成当前运行并验证导出图片后自动显示；模型读回仍在右侧。'
                    : 'An image appears after the current run exports and verifies it. Model readback remains on the right.'}</p>
                </div>)}
          </div>
          <aside className="warm-science-inspector">
            {review ? <>
              <h2>{t(boundaryReview ? 'Review boundary change' : 'Human review')}</h2>
              <small>{t('HUMAN REVIEW')}</small>
              <strong className="warm-review-object">
                {boundaryReview ? `${reviewerName} · ${selectedReviewBoundary?.presentationId}` : t('Readback unavailable')}
              </strong>
              <div className="warm-review-values">
                <div><strong>{review.current} {review.unit}</strong><span>{t('Current')}</span></div>
                <i>→</i>
                <div><strong>{review.proposed} {review.unit}</strong><span>{t('Proposed')}</span></div>
              </div>
              <p>{review.reason}</p>
              <p className="warm-review-note">
                {t('Paused · No changes applied')}<br />
                <span>{t(boundaryReview
                  ? 'Approval applies this edit. Scientific validation follows the solve.'
                  : 'No model-state verification is claimed.')}</span>
              </p>
              <button className="warm-primary" disabled={props.session?.state !== 'PAUSED' || !boundaryReview}
                onClick={props.onApprove}>{t('Approve & apply')}</button>
              <div className="warm-review-actions">
                <button disabled={!boundaryReview} onClick={() => {
                  props.onModify()
                  setPage('evidence')
                  setTechnicalOpen(true)
                }}>{t('Open correction controls')}</button>
                <button onClick={props.onReject}>{t('Reject & cancel run')}</button>
              </div>
            </> : <>
              <h2>{t('Model state')}</h2>
              <small>{t('SOURCE: RUNTIME READBACK')}</small>
              <dl className="warm-fact-list">
                <div><dt>{t('Geometry')}</dt><dd>{snapshot
                  ? `${readable(model.kind)} · ${readable(size.x)} × ${readable(size.y)} mm` : '—'}</dd></div>
                <div><dt>{t('Material')}</dt><dd>{snapshot ? readable(material.name) : '—'}</dd></div>
                <div><dt>{t('Physics')}</dt><dd>{snapshot ? readable(physics.interface) : '—'}</dd></div>
                <div><dt>{t('Mesh')}</dt><dd>{snapshot ? readable(snapshotSection(snapshot, 'mesh').readback) : '—'}</dd></div>
                <div><dt>{t('Solver')}</dt><dd>{snapshot ? readable(snapshotSection(snapshot, 'solver').solver) : '—'}</dd></div>
              </dl>
              <p className="warm-review-note">{snapshot
                ? t('Readback is not scientific certification.') : t('No model-state verification is claimed.')}</p>
              <button type="button" className="warm-live-readback-link" onClick={() => {
                props.onOpenLiveInspector()
                setTechnicalOpen(true)
                setPage('evidence')
              }}>{props.locale === 'zh-CN' ? '查看实时 COMSOL 模型读回 →' : 'View live COMSOL model readback →'}</button>
            </>}
            <div className="warm-run-controls">
              <span>{domain?.runState?.headline ?? t(props.session?.state ?? 'IDLE')}
                {' · '}{t('ETA')} {etaText}
                {' · '}{props.selectedModel || t('Model unavailable')}
                {' · '}{t(props.modelAvailability)}</span>
            </div>
          </aside>
        </div>
      </section>}

      {/* A result image is loaded by the existing SHA-checked artifact viewer.
          Neither missing artifacts nor incomplete verdicts are painted as success. */}
      {page === 'results' && importedModel && <section className="warm-page warm-science-page">
        <div className="warm-page-heading"><div><h1>{t('Scientific results')}</h1><p>{importedModel.original_filename}</p></div></div>
        <div className="warm-viewport warm-viewport-empty"><strong>{zh ? '当前导入模型没有已验证的解' : 'No verified solution for this imported model'}</strong>
          <p>{zh ? '可在 Model 页面查看已读回的几何和网格。' : 'Geometry and mesh remain available on the Model page.'}</p>
          <button className="warm-primary" onClick={() => setPage('model')}>{t('Model')}</button>
        </div>
      </section>}
      {page === 'results' && !importedModel && <section className="warm-page warm-science-page">
        <div className="warm-page-heading">
          <div>
            <h1>{t(completed ? 'Verified scientific result' : 'Scientific results')}</h1>
            <p>{t('Only run-bound, integrity-checked artifacts appear here.')}</p>
          </div>
          <span className={`warm-status-pill ${completed ? 'is-verified' : ''}`}>
            {props.visualFixture ? t('Illustrative layout only')
              : completed ? t('Scientific validation passed') : coolingResult
                ? (zh ? '三维 Heat 结果证据已校验' : '3D Heat result evidence verified') : diffusionImage
                ? (zh ? '浓度图已校验' : 'Concentration image verified') : t('Result unavailable or unverified')}
          </span>
        </div>
        <div className={`warm-science-layout ${heatKey && heatResultKey === heatKey ? 'warm-model-layout' : ''}`}>
          <div className={`warm-viewport warm-result-viewport ${heatKey && heatResultKey === heatKey ? 'warm-comsol-native' : ''}`}>
          {heatKey && heatResultKey === heatKey && props.session && runState ? <ComsolModelView
              key={heatKey} zh={zh} source={{ kind: 'heat-result', id: props.session.id, projectId: props.session.project_id,
                runId: runState.runId, attemptId: runState.attemptId }} />
            : props.visualFixture === 'results' && props.illustrativeResult ? props.illustrativeResult
            : artifactLossCurrent ? <div className="warm-viewport-empty" role="alert">
                <strong>{t('Evidence integrity lost')}</strong>
                <p>{t('The artifact bytes no longer match the frozen SHA-256. Certified/verified display is disabled.')}</p>
                <button onClick={() => void retryArtifact()} disabled={artifactRetrying}>
                  {artifactRetrying ? t('Verifying artifact integrity…') : t('Retry')}
                </button>
              </div>
              : (completed || diffusionImage || coolingResult) ? <VerifiedArtifactViewer locale={props.locale} session={props.session}
                  presentation="gallery" currentRunId={verdict?.run_id}
                  currentAttemptId={domain?.runState?.attemptId}
                  onIntegrityLost={(runId, attemptId, filename) => setArtifactLoss({
                    sessionId: props.session?.id ?? '', runId, attemptId, filename,
                  })} />
                : <div className="warm-viewport-empty">
                    <strong>{t('No verified result artifact available.')}</strong>
                    <p>{t('A completed current run and intact frozen evidence are required.')}</p>
                  </div>}
          </div>
          <aside className="warm-science-inspector">
            <h2>{t('Result evidence')}</h2>
            <small>{t('AUTHORITATIVE VERDICT')}</small>
            <dl className="warm-fact-list">
          <div><dt>{t('Command Success')}</dt><dd>{verdict?.command_success === true ? t('Passed') : t('Not passed')}</dd></div>
          <div><dt>{t('Model State Verified')}</dt><dd>{verdict?.model_state_verified === true ? t('Passed') : t('Not passed')}</dd></div>
          <div><dt>{t('Scientific Validation Passed')}</dt><dd>{verdict?.scientific_validation_success === true ? t('Passed') : t('Not passed')}</dd></div>
          <div><dt>{t('Evidence Contract')}</dt><dd>{verdict?.evidence_contract_passed === true && !artifactLossCurrent ? t('Passed') : t('Not passed')}</dd></div>
            </dl>
            <p className="warm-review-note">{completed
              ? t('See Evidence for the complete record and Model Card.')
              : coolingResult ? (zh ? '三维 Heat 结果已匹配当前运行的证据。可在证据页查看审核记录。'
                : 'The 3D Heat result matches the current run evidence. See Evidence for the review record.')
              : diffusionImage ? (zh ? '浓度图已匹配当前运行的证据。完整模型快照暂不可用。'
                : 'The concentration image matches the current run evidence. The full model snapshot is unavailable.')
              : t('No scientific success is claimed without all required checks.')}</p>
            {heatKey && <button className="warm-primary" onClick={() => setHeatResultKey(heatResultKey === heatKey ? '' : heatKey)}>
              {heatResultKey === heatKey ? (zh ? '返回结果图' : 'Show exported plots') : (zh ? '查看三维温度' : 'View 3D temperature')}
            </button>}
            <button onClick={() => setPage('evidence')}>{t('Inspect evidence →')}</button>
            {completed && verdict?.run_id && <button disabled={exporting} onClick={() => {
              setExporting(true); setExportError('')
              void api.downloadScientificResult(verdict.run_id).catch((error) => setExportError(safeUiError(error)))
                .finally(() => setExporting(false))
            }}>{props.locale === 'zh-CN' ? (exporting ? '正在校验与打包…' : '导出科研成果包') : (exporting ? 'Verifying and packing…' : 'Export scientific result')}</button>}
            {exportError && <p role="alert">{exportError}</p>}
          </aside>
        </div>
      </section>}

      {/* Evidence and maintenance detail remain available on demand instead of
          crowding the primary scientific work surface. */}
      {page === 'evidence' && <section className="warm-page warm-evidence-page">
        <div className="warm-page-heading">
          <div>
            <h1>{t('Evidence & activity')}</h1>
            <p>{t('Trace decisions, COMSOL events, validation and artifacts without changing their status.')}</p>
          </div>
          <span className={`warm-status-pill ${completed ? 'is-verified' : ''}`}>{t(evidenceState)}</span>
        </div>
        {hasWorkflow && props.session && <details className="warm-evidence-workflow" open={workflowOpen}
          onToggle={(event) => setWorkflowOpen(event.currentTarget.open)}>
          <summary>{zh ? '专家工作流' : 'Expert workflow'}</summary>
          {workflowOpen && <ExpertWorkflow key={props.session.id} session={props.session} locale={props.locale} />}
        </details>}
        <div className="warm-evidence-layout">
          <section>
            <h2>{t('Evidence timeline')}</h2>
            {props.timeline.length
              ? <ol className="warm-timeline">
                {[...props.timeline].reverse().slice(0, 60).map((event) => <li key={event.id}>
                  <span className={`warm-timeline-dot ${event.status}`} />
                  <div><strong>{event.label}</strong><small>{event.phase} · {event.created_at}</small></div>
                </li>)}
              </ol>
              : <p>{t('No activity recorded yet.')}</p>}
          </section>
          <aside>
            <h2>{t('Scientific record')}</h2>
            <p>{t('Run ID')}: <code>{verdict?.run_id ?? '—'}</code></p>
            <p>{t('Attempt ID')}: <code>{domain?.runState?.attemptId ?? '—'}</code></p>
            <p>{t('Evidence Contract')}: {t(evidenceState)}</p>
            {domain?.contractGaps?.length ? <details>
              <summary>{t('Contract gaps')}</summary>
              <ul>{domain.contractGaps.map((gap) => <li key={gap}>{gap}</li>)}</ul>
            </details> : null}
            <details className="warm-technical" open={technicalOpen}
              onToggle={(event) => setTechnicalOpen(event.currentTarget.open)}>
              <summary>{t('Technical inspector')}</summary>{props.technicalInspector}
            </details>
            <details><summary>{t('Model Card and session record')}</summary>{props.taskHistory}</details>
          </aside>
        </div>
      </section>}

      {props.warning && <div className="warm-inline-warning" role="status">{safeUiError(props.warning)}</div>}
      <footer className="warm-page-footer">
        <span>{t('Independent scientific workbench · COMSOL requires a valid installation and license')}</span>
        <span>{t('Scientific claims require verified evidence')}</span>
      </footer>
    </main>
  </div>
}
