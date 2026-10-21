import { useCallback, useEffect, useRef, useState } from 'react'
import { ExecutionVerdictBadge } from './ExecutionVerdictBadge'
import { api, type DesktopBootstrap } from './api'
import { AgentControlPanel } from './components/AgentControlPanel'
import { BrandIcon } from './components/BrandIcon'
import { CitationList } from './components/CitationList'
import { EvidenceTimeline } from './components/EvidenceTimeline'
import { Inspector, inspectorTabs } from './components/Inspector'
import { ModelTree, type ModelTreeNode } from './components/ModelTree'
import { PlanCard } from './components/PlanCard'
import { WarmSettings } from './components/WarmSettings'
import { WarmOnboarding, warmOnboardingStorageKey } from './components/WarmOnboarding'
import { WarmHelp } from './components/WarmHelp'
import { ScientificCanvas, type CanvasView } from './components/ScientificCanvas'
import { TaskDock } from './components/TaskDock'
import { initialLocale, localeStorageKey, translate, type Locale } from './i18n'
import type { AgentMode, EnvironmentItem, ExecutionEta, InspectorTab, JavaBridgeEvent, JavaBridgeStatus, Plan, Project, Provider, ProviderSettings, RuntimeDomain, ScientificIntent, Session, TimelineItem, TreeNode } from './types'
import { WarmStudio } from './WarmStudio'
import { safeUiError } from './safeUiError'
import { currentScientificDomain, isCurrentSelection, sessionIsNotOlder, type SelectionTicket } from './scientificView'
import { reviewedBoundary } from './modelIdentity'

const activeStates = new Set(['RESEARCHING', 'PLANNING', 'PRECHECK', 'BUILDING', 'BUILD_VALIDATION', 'SOLVING', 'SOLUTION_VALIDATION', 'REPAIRING', 'REVALIDATING', 'EXPORTING', 'REPORTING', 'PAUSED', 'CANCEL_REQUESTED'])

function App() {
  const [locale, setLocale] = useState<Locale>(initialLocale)
  document.documentElement.lang = locale
  const [projects, setProjects] = useState<Project[]>([])
  const [tree, setTree] = useState<TreeNode[]>([])
  const [sessions, setSessions] = useState<Session[]>([])
  const [session, setSession] = useState<Session | null>(null)
  const [environment, setEnvironment] = useState<EnvironmentItem[]>([])
  const [resources, setResources] = useState<Record<string, unknown>>({})
  const [capabilities, setCapabilities] = useState<Record<string, { status: string; reason: string }>>({})
  const [providers, setProviders] = useState<Provider[]>([])
  const [providerSettings, setProviderSettings] = useState<ProviderSettings | null>(null)
  const [knowledge, setKnowledge] = useState<Record<string, unknown>>({})
  const [mode, setMode] = useState<AgentMode>('PLAN')
  const [prompt, setPrompt] = useState('')
  const [pendingIntent, setPendingIntent] = useState<ScientificIntent | null>(null)
  const [busy, setBusy] = useState(false)
  const [streaming, setStreaming] = useState<{ text: string; provider: string; model: string } | null>(null)
  const [warning, setWarning] = useState('')
  const [criticalError, setCriticalError] = useState('')
  const [inspectorOpen, setInspectorOpen] = useState(false)
  const [indexTask, setIndexTask] = useState<Record<string, unknown>>({ state: 'idle' })
  const [semanticIndexTask, setSemanticIndexTask] = useState<Record<string, unknown>>({ state: 'idle' })
  const [bridge, setBridge] = useState<JavaBridgeStatus>({ status: 'Unavailable', bridge_running: false, comsol_api_loaded: false, model_connected: false, connected: false, real_comsol: false, detail: 'Not inspected', base_url: 'http://127.0.0.1:8765', comsol_version: '', model_tag: '', bridge_mode: '', connection_mode: '', detection_method: 'unavailable', poll_interval_ms: 0 })
  const [bridgeTree, setBridgeTree] = useState<Record<string, unknown>>({})
  const [bridgeParameters, setBridgeParameters] = useState<Record<string, string>>({})
  const [bridgeEvents, setBridgeEvents] = useState<JavaBridgeEvent[]>([])
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [aboutOpen, setAboutOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  // This key is intentionally new: completion of the old RC4.1 guide must not
  // suppress the first launch of the approved Warm Studio experience.
  const [firstRunOpen, setFirstRunOpen] = useState(() => localStorage.getItem(warmOnboardingStorageKey) !== 'complete')
  const [runtimeReady, setRuntimeReady] = useState(false)
  const [runtimeUnavailable, setRuntimeUnavailable] = useState(false)
  const [settingsMigration, setSettingsMigration] = useState<Record<string, unknown>>({ status: 'not_required' })
  const [databaseRecovery, setDatabaseRecovery] = useState('not_required')
  const [dark, setDark] = useState(() => localStorage.getItem('workbench-theme') === 'dark')
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>('environment')
  const [executionEta, setExecutionEta] = useState<ExecutionEta | null>(null)
  const [runtimeDomain, setRuntimeDomain] = useState<RuntimeDomain | null>(null)
  const [desktopBootstrap, setDesktopBootstrap] = useState<DesktopBootstrap | null>(api.desktop())
  const [selectedModelNode, setSelectedModelNode] = useState<ModelTreeNode>('Geometry')
  const [canvasView, setCanvasView] = useState<CanvasView>('model')
  const askAbort = useRef<AbortController | null>(null)
  const sessionStateRef = useRef('IDLE')
  const selectionRef = useRef<SelectionTicket>({ generation: 0, sessionId: '' })
  const sessionRef = useRef<Session | null>(null)

  const activeProject = projects.find((project) => project.active) ?? projects[0]
  const activePlan = phase2Plan(session?.plan)
  const t = (source: string) => translate(locale, source)
  useEffect(() => { localStorage.setItem(localeStorageKey, locale) }, [locale])
  sessionStateRef.current = session?.state ?? 'IDLE'
  sessionRef.current = session

  // Invalidate the old view before starting an asynchronous selection. The
  // generation also rejects A→B→A responses that arrive after returning to A.
  const beginSelection = (sessionId: string): SelectionTicket => {
    const ticket = { generation: selectionRef.current.generation + 1, sessionId }
    selectionRef.current = ticket
    sessionRef.current = null
    askAbort.current?.abort()
    setSession(null); setRuntimeDomain(null); setExecutionEta(null)
    setPrompt(''); setPendingIntent(null); setStreaming(null); setBusy(false)
    return ticket
  }

  const refreshBridge = useCallback(async () => {
    const status = await api.bridgeStatus()
    setBridge(status)
    if (status.model_connected) {
      const [treeData, parameterData] = await Promise.all([api.bridgeTree(), api.bridgeParameters()])
      setBridgeTree(treeData)
      setBridgeParameters((parameterData.parameters ?? {}) as Record<string, string>)
    } else {
      setBridgeTree({}); setBridgeParameters({})
    }
    return status
  }, [])

  const refreshSession = useCallback(async (id: string, ticket: SelectionTicket = selectionRef.current) => {
    if (!isCurrentSelection(ticket, selectionRef.current) || ticket.sessionId !== id) return null
    const next = await api.session(id)
    if (!isCurrentSelection(ticket, selectionRef.current)) return null
    if (!sessionIsNotOlder(next, sessionRef.current)) return null
    sessionRef.current = next
    setSession(next)
    setSessions((current) => current.some((item) => item.id === next.id) ? current.map((item) => item.id === next.id ? next : item) : [next, ...current])
    return next
  }, [])

  const bootstrap = useCallback(async () => {
    try {
      const desktopState = await api.initialize()
      setDesktopBootstrap(desktopState)
      const runtimeHealth = await api.health()
      setDatabaseRecovery(runtimeHealth.database_recovery?.status ?? 'not_required')
      const [projectData, sessionData, environmentData, providerData, bridgeData, resourceData, capabilityData, migrationData] = await Promise.all([api.projects(), api.sessions(), api.environment(), api.providers(), api.bridgeStatus(), api.systemResources(), api.capabilities(), api.settingsMigration()])
      setProjects(projectData.projects); setEnvironment(environmentData.items); setProviders(providerData.providers); setProviderSettings(providerData.settings); setKnowledge(environmentData.knowledge)
      setBridge(bridgeData)
      setResources(resourceData); setCapabilities(capabilityData.capabilities)
      setSettingsMigration(migrationData)
      const project = projectData.projects.find((item) => item.active) ?? projectData.projects[0]
      if (project) setTree((await api.projectTree(project.id)).tree)
      setSessions(sessionData.sessions)
      const savedId = localStorage.getItem('workbench-session')
      const candidate = sessionData.sessions.find((item) => item.id === savedId) ?? sessionData.sessions[0]
      if (candidate) {
        const ticket = beginSelection(candidate.id)
        const selected = await api.session(candidate.id)
        if (isCurrentSelection(ticket, selectionRef.current)) { sessionRef.current = selected; setSession(selected) }
      } else if (project) {
        const ticket = beginSelection('')
        const created = await api.createSession(project.id)
        if (selectionRef.current.generation === ticket.generation) {
          selectionRef.current = { ...ticket, sessionId: created.id }
          sessionRef.current = created; setSession(created); setSessions([created])
        }
      }
      setRuntimeReady(true); setRuntimeUnavailable(false)
    } catch (cause) { setRuntimeUnavailable(true); setCriticalError(cause instanceof Error ? cause.message : String(cause)) }
  }, [])

  useEffect(() => { void bootstrap() }, [bootstrap])
  useEffect(() => { document.documentElement.dataset.theme = dark ? 'dark' : 'light'; localStorage.setItem('workbench-theme', dark ? 'dark' : 'light') }, [dark])
  useEffect(() => { if (session) localStorage.setItem('workbench-session', session.id) }, [session])
  useEffect(() => {
    if (!runtimeReady) return
    const source = api.streamEvents('/api/comsol/bridge/stream?after=0', (payload) => {
      const event = payload as unknown as JavaBridgeEvent
      setBridgeEvents((current) => current.some((item) => item.sequence === event.sequence && item.created_at === event.created_at) ? current : [...current, event].slice(-100))
      if (event.event_type === 'critical_failure' && activeStates.has(sessionStateRef.current)) setCriticalError(event.detail || 'Java Bridge disconnected during RUN.')
      if (event.event_type === 'model_change' || ['finished', 'failed', 'disconnected'].includes(event.status)) void refreshBridge().catch(() => undefined)
    }, () => setBridge((current) => ({ ...current, status: 'Unavailable', bridge_running: false, model_connected: false, connected: false })))
    return () => source.abort()
  }, [refreshBridge, runtimeReady])
  useEffect(() => {
    if (!session || !runtimeReady) return
    const ticket = selectionRef.current
    const cursor = Math.max(0, ...(session.events ?? []).map((event) => event.sequence))
    const source = api.streamEvents(`/api/sessions/${session.id}/events?after=${cursor}`, (event) => {
      if (!isCurrentSelection(ticket, selectionRef.current)) return
      // A new run/attempt or invalidated evidence revokes the visible claim
      // before the debounced full-session refresh returns.
      if (['run.started', 'run.attempt', 'evidence.invalidated',
        'validation.invalidated_by_human_correction', 'run.canceled', 'run.failed'].includes(String(event.type)))
        setRuntimeDomain(null)
      window.clearTimeout(refreshTimer)
      refreshTimer = window.setTimeout(() => { void refreshSession(session.id, ticket).catch((cause: unknown) => {
        if (isCurrentSelection(ticket, selectionRef.current)) setWarning(String(cause))
      }) }, 120)
    }, () => {
      if (isCurrentSelection(ticket, selectionRef.current) && activeStates.has(session.state)) setWarning(t('Live session event stream disconnected; reconnecting from the last event ID.'))
    })
    let refreshTimer = 0
    return () => { window.clearTimeout(refreshTimer); source.abort() }
  }, [refreshSession, runtimeReady, session?.id])
  useEffect(() => {
    if (!runtimeReady) return
    const timer = window.setInterval(() => {
      void api.health().then(() => setRuntimeUnavailable(false)).catch(() => {
        setRuntimeUnavailable(true)
        if (activeStates.has(sessionStateRef.current)) setCriticalError(t('Scientific Runtime unavailable during RUN. The owned sidecar must be restarted before continuing.'))
      })
    }, 5000)
    return () => window.clearInterval(timer)
  }, [runtimeReady])
  useEffect(() => {
    if (!runtimeReady || !session) { setExecutionEta(null); return }
    let active = true
    const ticket = selectionRef.current
    const refreshEta = () => { void api.executionEta(session.id).then((value) => { if (active && isCurrentSelection(ticket, selectionRef.current)) setExecutionEta(value) }).catch(() => { if (active && isCurrentSelection(ticket, selectionRef.current)) setExecutionEta(null) }) }
    refreshEta()
    const timer = activeStates.has(session.state) ? window.setInterval(refreshEta, 5000) : 0
    return () => { active = false; if (timer) window.clearInterval(timer) }
  }, [runtimeReady, session?.id, session?.state])

  // Keep one projection cache, but never let a previous selection's response
  // or an earlier attempt cross into the selected task's visible state.
  useEffect(() => {
    if (!runtimeReady || !session) { setRuntimeDomain(null); return }
    let active = true
    const ticket = selectionRef.current
    // Retain only a projection for the same journal run and attempt. This
    // avoids resetting the verified multi-image gallery on ordinary refresh.
    setRuntimeDomain((current) => currentScientificDomain(session, current))
    void api.runtimeDomain(session.id).then((value) => {
      if (active && isCurrentSelection(ticket, selectionRef.current))
        setRuntimeDomain(currentScientificDomain(sessionRef.current, value))
    }).catch(() => { if (active && isCurrentSelection(ticket, selectionRef.current)) setRuntimeDomain(null) })
    return () => { active = false }
  }, [runtimeReady, session?.id, session?.updated_at, session?.events?.length])

  const selectedDomain = currentScientificDomain(session, runtimeDomain)

  // Explicit mode avoids a stale React state read when a warm-page button
  // switches from PLAN review to RUN in the same user interaction.
  const send = async (requestedMode: AgentMode = mode) => {
    if (!session || busy || (requestedMode !== 'RUN' && !prompt.trim())) return
    const ticket = selectionRef.current
    if (requestedMode === 'RUN' && !activePlan?.runnable) { setWarning(t('RUN requires a saved Phase 2 ScientificPlan that is Ready To Run.')); return }
    setBusy(true); setWarning('')
    try {
      if (requestedMode === 'PLAN' && !pendingIntent) {
        const intent = await api.compileIntent(prompt.trim())
        if (isCurrentSelection(ticket, selectionRef.current)) setPendingIntent(intent)
        return
      }
      let next: Session
      if (requestedMode === 'ASK') {
        const controller = new AbortController()
        askAbort.current = controller
        setStreaming({ text: '', provider: '', model: '' })
        next = await api.streamMessage(session.id, requestedMode, prompt.trim(), (event) => {
          if (!isCurrentSelection(ticket, selectionRef.current)) return
          if (event.type === 'meta') setStreaming((current) => ({ text: current?.text ?? '', provider: String(event.provider_label ?? ''), model: String(event.model ?? '') }))
          if (event.type === 'delta') setStreaming((current) => ({ text: `${current?.text ?? ''}${String(event.text ?? '')}`, provider: current?.provider ?? '', model: current?.model ?? '' }))
        }, controller.signal)
      } else {
        const content = requestedMode === 'RUN' ? `Execute saved ScientificPlan ${activePlan?.plan_id}` : pendingIntent?.original_text ?? prompt.trim()
        next = await api.sendMessage(session.id, requestedMode, content)
      }
      if (!isCurrentSelection(ticket, selectionRef.current)) return
      sessionRef.current = next; setSession(next); setPendingIntent(null)
      const refreshed = await api.sessions()
      if (isCurrentSelection(ticket, selectionRef.current)) setSessions(refreshed.sessions)
    } catch (cause) {
      const message = cause instanceof DOMException && cause.name === 'AbortError' ? 'ASK generation cancelled.' : cause instanceof Error ? cause.message : String(cause)
      if (isCurrentSelection(ticket, selectionRef.current)) {
        if (/COMSOL worker|Java Bridge|corrupt|Failed to fetch|runtime unavailable/i.test(message)) setCriticalError(message)
        else setWarning(message)
      }
    }
    finally { if (isCurrentSelection(ticket, selectionRef.current)) { askAbort.current = null; setBusy(false); setStreaming(null) } }
  }

  const newSession = async () => {
    if (!activeProject) return
    const ticket = beginSelection('')
    try {
      const created = await api.createSession(activeProject.id)
      if (selectionRef.current.generation !== ticket.generation) return
      selectionRef.current = { ...ticket, sessionId: created.id }
      sessionRef.current = created; setSession(created); setSessions((current) => [created, ...current])
    } catch (cause) { if (selectionRef.current.generation === ticket.generation) setWarning(String(cause)) }
  }
  const selectSession = async (id: string) => {
    const ticket = beginSelection(id)
    try { await refreshSession(id, ticket) }
    catch (cause) { if (isCurrentSelection(ticket, selectionRef.current)) setWarning(String(cause)) }
  }
  const archiveSession = async (id: string) => {
    // Archive removes a task from Recent but deliberately retains its events,
    // run journal and artifacts for audit and possible manual recovery.
    if (!window.confirm(t('Archive this task from Recent? Its data will be preserved.'))) return
    try {
      await api.archiveSession(id)
      const remaining = (await api.sessions()).sessions
      setSessions(remaining)
      if (session?.id === id) {
        if (remaining.length) await selectSession(remaining[0].id)
        else if (activeProject) await newSession()
      }
    } catch (cause) { setWarning(cause instanceof Error ? cause.message : String(cause)) }
  }
  const changeProject = async (projectId: string) => { await api.selectProject(projectId); const data = await api.projects(); setProjects(data.projects); setTree((await api.projectTree(projectId)).tree) }
  const refreshEnvironment = async () => { const [environmentData, providerData, knowledgeData, resourceData, capabilityData] = await Promise.all([api.environment(), api.providers(), api.knowledgeStatus(), api.systemResources(), api.capabilities()]); setEnvironment(environmentData.items); setProviders(providerData.providers); setProviderSettings(providerData.settings); setKnowledge(knowledgeData); setResources(resourceData); setCapabilities(capabilityData.capabilities); setIndexTask((knowledgeData.index_task as Record<string, unknown>) ?? { state: 'idle' }); setSemanticIndexTask((knowledgeData.semantic_index_task as Record<string, unknown>) ?? { state: 'idle' }); await refreshBridge() }
  const startBridge = async () => {
    setWarning(''); setInspectorTab('live'); const accepted = await api.startBridge(); setBridge((current) => ({ ...current, process: accepted, status: 'Starting' }))
    for (let attempt = 0; attempt < 90; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 1000))
      const status = await refreshBridge()
      const process = (status.process ?? {}) as Record<string, unknown>
      if (status.model_connected) return
      if (['failed', 'crashed'].includes(String(process.state ?? ''))) { setWarning(String(process.error ?? 'Real COMSOL Java Bridge failed to start.')); return }
    }
    setWarning(t('Real COMSOL Java Bridge startup timed out; status remains available in Live COMSOL.'))
  }
  const stopBridge = async () => { await api.stopBridge(); await refreshBridge() }
  const cancelSolve = async () => {
    if (!session) return
    try {
      const result = await api.cancelRun(session.id)
      const status = String(result.status ?? '')
      setWarning(status === 'safe_cancel_unavailable'
        ? t('COMSOL does not expose a verified safe cancellation API for this active solve. The process was not terminated.')
        : `${t('Cancellation status')}: ${status}`)
      await refreshSession(session.id)
    } catch (cause) {
      setWarning(cause instanceof Error ? cause.message : String(cause))
    }
  }
  const emergencyStop = async () => {
    if (!session) return
    // This is deliberately different from resumable pause. The backend may
    // terminate only a process whose Workbench ownership it has verified;
    // the canceled attempt must never be represented as resumable.
    if (!window.confirm(t('Emergency stop ends this attempt and may lose unsaved COMSOL state. Continue?'))) return
    try {
      const result = await api.cancelRun(session.id, true)
      const status = String(result.status ?? 'unknown')
      setWarning(status === 'force_terminated'
        ? t('Owned process stopped. This attempt cannot resume; review the saved plan before a new run.')
        : result.cancel_requested && status === 'not_running'
          ? t('Stop intent recorded. Waiting for the active run to settle; no worker was terminated.')
          : result.cancel_requested
            ? `${t('Stop requested, but process cleanup is not verified. Inspect diagnostics.')}: ${status}`
            : `${t('Stop not confirmed')}: ${status}`)
      await refreshSession(session.id)
    } catch (cause) { setWarning(cause instanceof Error ? cause.message : String(cause)) }
  }
  const pauseLiveRun = async () => {
    if (!session) return
    try {
      await api.pauseRun(session.id)
      setWarning(t('Pause requested; waiting for a verified safe COMSOL boundary.'))
      await refreshSession(session.id)
    } catch (cause) { setWarning(cause instanceof Error ? cause.message : String(cause)) }
  }
  const correctLiveRun = async (correction: { path: string; value: number; unit: string; reason: string }) => {
    if (!session) return
    try {
      await api.correctRun(session.id, correction)
      setWarning(t('Correction verified by real COMSOL readback. Pre-correction downstream evidence is invalidated.'))
      await refreshSession(session.id)
    } catch (cause) { setCriticalError(cause instanceof Error ? cause.message : String(cause)) }
  }
  const resumeLiveRun = async () => {
    if (!session) return
    try {
      await api.resumeRun(session.id)
      setWarning(t('Resumed from the earliest invalidated node. Scientific certification remains pending new evidence.'))
      await refreshSession(session.id)
    } catch (cause) { setWarning(cause instanceof Error ? cause.message : String(cause)) }
  }
  const openHumanModification = () => {
    setInspectorOpen(true)
    setInspectorTab('live')
    setWarning(t('Modify opens typed correction. Changes require PAUSED state and verified COMSOL readback.'))
  }
  const approveHumanAction = async () => {
    // Approval applies only the persisted review for the current attempt.
    // It never silently resumes or certifies a scientific result.
    const review = selectedDomain?.reviewRequest
    if (session?.state === 'PAUSED' && review && reviewedBoundary(selectedDomain, review)) {
      try {
        await api.correctBoundary(session.id, review.reviewId)
        setWarning(t('Correction verified by real COMSOL readback. Resume separately to regenerate validation evidence.'))
        await refreshSession(session.id)
      } catch (cause) { setCriticalError(cause instanceof Error ? cause.message : String(cause)) }
      return
    }
    if (pendingIntent && !review) { await send('PLAN'); return }
    setWarning(t('No human approval is currently pending. Approval never bypasses scientific certification.'))
  }
  const rejectHumanAction = async () => {
    // An active model review takes precedence over an unrelated new draft.
    // Reject never silently discards a live review in favor of draft state.
    if (selectedDomain?.reviewRequest && session) {
      if (window.confirm(t('Reject this active action and request cancellation?'))) await cancelSolve()
      return
    }
    if (pendingIntent) { setPendingIntent(null); setWarning(t('Pending intent rejected. No model action was executed.')); return }
    if (!session || !window.confirm(t('Reject this active action and request cancellation?'))) return
    await cancelSolve()
  }
  const startKnowledgeIndex = async () => {
    setWarning(''); const accepted = await api.refreshKnowledge(); setIndexTask((accepted.index_task as Record<string, unknown>) ?? { state: 'running' })
    const source = api.streamEvents('/api/knowledge/events?after=0', (payload) => { setIndexTask(payload); if (payload.type === 'completed' || payload.type === 'error') { source.abort(); void refreshEnvironment() } }, () => { source.abort(); setWarning(t('Knowledge index event stream disconnected. Status remains recoverable from the right sidebar.')); void refreshEnvironment() })
  }
  const startSemanticIndex = async () => {
    setWarning(''); const accepted = await api.startSemanticIndex(); setSemanticIndexTask((accepted.semantic_index_task as Record<string, unknown>) ?? { state: 'running' })
    const source = api.streamEvents('/api/knowledge/semantic/events?after=0', (payload) => { setSemanticIndexTask(payload); if (payload.type === 'completed' || payload.type === 'error') { source.abort(); void refreshEnvironment() } }, () => { source.abort(); setWarning(t('Semantic index event stream disconnected. Status remains recoverable from the right sidebar.')); void refreshEnvironment() })
  }
  const restartRuntime = async () => {
    setCriticalError(''); setWarning(''); setRuntimeReady(false)
    try { setDesktopBootstrap(await api.restartDesktopRuntime()); await bootstrap() } catch (cause) { setRuntimeUnavailable(true); setCriticalError(cause instanceof Error ? cause.message : String(cause)) }
  }
  const retrySettingsMigration = async () => setSettingsMigration(await api.retrySettingsMigration())
  const useDefaultSettings = async () => {
    if (!window.confirm(locale === 'zh-CN' ? '使用安全默认设置？原配置备份会保留。' : 'Use safe defaults? The previous configuration backup will be preserved.')) return
    await api.useDefaultSettings()
    setSettingsMigration(await api.settingsMigration())
  }
  const savePlan = async (plan: Plan) => {
    if (!session) return
    const ticket = selectionRef.current
    const next = await api.updatePlan(session.id, plan)
    if (!isCurrentSelection(ticket, selectionRef.current)) return
    sessionRef.current = next; setSession(next); setSessions((current) => current.map((item) => item.id === next.id ? next : item))
  }
  const bridgeTimeline: TimelineItem[] = bridgeEvents.map((event) => ({
    id: `bridge-${event.sequence}-${event.created_at ?? 'live'}`,
    category: 'COMSOL',
    phase: event.phase,
    label: event.message || event.detail,
    status: ['finished', 'verified', 'ready'].includes(event.status) ? 'success' : ['failed', 'error', 'unavailable', 'disconnected'].includes(event.status) ? 'error' : event.status === 'warning' ? 'warning' : 'info',
    tool: 'java_bridge.real_comsol',
    input_summary: event.target ? { target: event.target, action: event.action, detection_method: event.detection_method } : null,
    output_summary: event.old_value != null || event.new_value != null ? `${event.old_value || '∅'} → ${event.new_value || '∅'}` : event.detail,
    event_type: event.event_type,
    duration_ms: event.duration_ms ?? null,
    fallback: event.detection_method === 'polling_fallback',
    created_at: event.created_at ?? new Date().toISOString(),
  }))
  const timelineItems = [...(session?.timeline ?? []), ...bridgeTimeline].slice(-250)
  const latestCorrectionEvent = [...bridgeEvents].reverse().find((event) => event.old_value != null || event.new_value != null)
  const selectModelNode = (node: ModelTreeNode) => {
    setSelectedModelNode(node)
    setCanvasView(node === 'Results' ? 'results' : node === 'Evidence' ? 'evidence' : node === 'Mesh' || node === 'Study / Solver' ? 'mesh-solver' : 'model')
  }
  const selectEvidenceStage = (stage: string) => {
    if (['Readback', 'Validate', 'Certify'].includes(stage)) { setSelectedModelNode('Evidence'); setCanvasView('evidence'); return }
    if (stage === 'Solve') { setSelectedModelNode('Study / Solver'); setCanvasView('mesh-solver'); return }
    if (stage === 'Plan' || stage === 'Proposal') { setSelectedModelNode('Geometry'); setCanvasView('model'); return }
    setSelectedModelNode(stage === 'Build' ? 'Geometry' : stage === 'Precheck' ? 'Evidence' : selectedModelNode)
  }
  const taskHistory = <div className="task-history-list">
    {session?.messages.map((message, index) => <MessageCard locale={locale} key={message.created_at + '-' + String(index)} message={message} currentPlan={activePlan} onSavePlan={savePlan} />)}
    {streaming && <article className="message assistant streaming-message"><div className="message-meta"><span>{t('Scientific Agent')}</span><em>ASK · STREAMING</em><small>{streaming.provider} {streaming.model}</small></div><div className="message-content">{streaming.text || t('Generating…')}<span className="stream-caret" /></div></article>}
  </div>
  // Full plan disclosure precedes the warm interface's explicit RUN consent.
  const planReview = activePlan ? <PlanCard plan={activePlan} editable onSave={savePlan} /> : null
  const intentConfirmation = pendingIntent
    ? <IntentConfirmation locale={locale} intent={pendingIntent} onContinue={() => void send()} onModify={() => { setPrompt(pendingIntent.original_text); setPendingIntent(null) }} onReset={() => { setPrompt(''); setPendingIntent(null) }} />
    : null

  return <div className="app-shell warm-app-shell">
    {desktopBootstrap?.lowVramMode && <section className="runtime-status model-tier-status" role="status"><div><strong>{t(desktopBootstrap.modelState === 'Detected' ? 'LOW_VRAM_MODE active' : 'LOW_VRAM_MODE recommended')}</strong><span>{t(desktopBootstrap.modelState === 'Detected' ? 'The detected qwen3:4b model matches constrained VRAM mode. No silent fallback occurred.' : 'Detected VRAM is below the standard threshold. Install qwen3:4b explicitly; no model is downloaded or substituted silently.')}</span><small>{desktopBootstrap.detectedVramMib} MiB VRAM · {desktopBootstrap.selectedModel} · {t(desktopBootstrap.modelState)}</small></div></section>}
    {String(settingsMigration.status) === 'failed' && <section className="migration-status" role="alert"><div><strong>{t('Configuration migration needs attention')}</strong><span>{t('Your previous configuration is preserved. Retry migration or explicitly use safe defaults.')}</span><small>{String(settingsMigration.detail ?? '')}</small></div><div><button onClick={() => void api.openConfigBackup()}>{t('Open backup location')}</button><button onClick={() => void retrySettingsMigration()}>{t('Retry')}</button><button onClick={() => void useDefaultSettings()}>{t('Use safe defaults')}</button></div></section>}
    {runtimeUnavailable && <section className="runtime-status unavailable"><div><strong>{t('Scientific Runtime unavailable')}</strong><span>{activeStates.has(session?.state ?? '') ? t('An active RUN was interrupted. The saved plan and session remain preserved; execution is not reported as successful.') : t('The UI remains open; restart the owned local sidecar.')}</span></div><div className="runtime-actions"><button onClick={() => void api.openLogs()}>{t('Open Logs')}</button><button onClick={() => void restartRuntime()}>{t('Restart Runtime')}</button><button onClick={() => setRuntimeUnavailable(false)}>{t('Dismiss')}</button></div></section>}
    {/* The guide renders from the first React frame, including while the
        runtime is still starting, so first launch never skips onboarding. */}
    {firstRunOpen && <WarmOnboarding locale={locale} environment={environment} providers={providers} desktop={desktopBootstrap} runtimeReady={runtimeReady} runtimeUnavailable={runtimeUnavailable} onRetryRuntime={() => void restartRuntime()} onOpenLogs={() => void api.openLogs()} onOpenHelp={() => setHelpOpen(true)} onComplete={() => { localStorage.setItem(warmOnboardingStorageKey, 'complete'); setFirstRunOpen(false) }} />}
    <WarmStudio
      locale={locale} projects={projects} activeProject={activeProject} sessions={sessions}
      session={session} plan={activePlan} domain={runtimeReady && !runtimeUnavailable ? selectedDomain : null} bridge={bridge}
      comsolDetected={environment.some((item) => item.name === 'COMSOL' && item.status === 'Ready')}
      databaseRecovery={databaseRecovery}
      eta={executionEta} selectedModel={desktopBootstrap?.selectedModel ?? ''} modelAvailability={desktopBootstrap?.modelState ?? 'Unavailable'}
      literatureEnabled={Boolean(providerSettings?.extensions?.literature_modeling?.enabled)}
      mode={mode} prompt={prompt} busy={busy} askStreaming={Boolean(streaming)} warning={warning}
      pendingIntent={Boolean(pendingIntent)} taskHistory={taskHistory} planReview={planReview}
      intentConfirmation={intentConfirmation} timeline={timelineItems}
      technicalInspector={<><div className="inspector-tabs-v2">{inspectorTabs.map(([key, label]) => <button key={key} className={inspectorTab === key ? 'active' : ''} onClick={() => setInspectorTab(key)}>{t(label)}</button>)}</div><Inspector locale={locale} tab={inspectorTab} session={session} items={environment} providers={providers} knowledge={knowledge} indexTask={indexTask} semanticIndexTask={semanticIndexTask} resources={resources} capabilities={capabilities} bridge={bridge} bridgeTree={bridgeTree} bridgeParameters={bridgeParameters} bridgeEvents={bridgeEvents} onRefresh={() => void refreshEnvironment()} onStartIndex={() => void startKnowledgeIndex()} onStartSemanticIndex={() => void startSemanticIndex()} onStartBridge={() => void startBridge()} onStopBridge={() => void stopBridge()} onPauseRun={() => void pauseLiveRun()} onCorrectRun={(correction) => void correctLiveRun(correction)} onResumeRun={() => void resumeLiveRun()} /></>}
      onNewSession={() => newSession()} onSelectSession={(id) => { void selectSession(id) }} onArchiveSession={(id) => void archiveSession(id)}
      onSelectProject={(id) => void changeProject(id)}
      onPromptChange={(value) => { setPrompt(value); setPendingIntent(null) }}
      onModeChange={(value) => { setMode(value); setPendingIntent(null) }}
      onSend={(value) => void send(value)} onCancelAsk={() => askAbort.current?.abort()} onPause={() => void pauseLiveRun()}
      onResume={() => void resumeLiveRun()} onApprove={() => void approveHumanAction()}
      onReject={() => void rejectHumanAction()} onModify={openHumanModification}
      onCancel={() => void cancelSolve()} onEmergencyStop={() => void emergencyStop()} onOpenSettings={() => setSettingsOpen(true)}
      onOpenSetup={() => setFirstRunOpen(true)} onOpenAbout={() => setAboutOpen(true)} onOpenHelp={() => setHelpOpen(true)}
      onOpenLogs={() => void api.openLogs()} onOpenWorkspace={() => void api.openWorkspace()} onLocaleChange={setLocale}
    />
    {settingsOpen && providerSettings && <WarmSettings locale={locale} providers={providers} settings={providerSettings} onLocaleChange={setLocale} onOpenSetup={() => { setSettingsOpen(false); setFirstRunOpen(true) }} onClose={() => setSettingsOpen(false)} onSaved={async () => { await refreshEnvironment(); setSettingsOpen(false) }} />}
    {helpOpen && <WarmHelp locale={locale} desktop={desktopBootstrap} environment={environment} onClose={() => setHelpOpen(false)} onOpenLogs={() => void api.openLogs()} />}
    {aboutOpen && <div className="dialog-backdrop"><div className="about-dialog" role="dialog" aria-modal="true" aria-label="About SKY Workbench"><BrandIcon size={96} className="about-icon" label="SKY Workbench" /><span className="eyebrow">V2.0</span><h2>SKY Workbench</h2><p><b>v2.0.0</b><br />{t('Build signature')}: <b>{t('Unsigned')}</b></p><p>{t('Independent third-party scientific modeling workbench. Not affiliated with, endorsed by, or sponsored by COMSOL AB.')}</p><p>{t('COMSOL® is a trademark of COMSOL AB. Official COMSOL software and a valid license are required for real model execution.')}</p><p>{t('Local-first by default. This candidate detects your local AI provider and never downloads or substitutes model weights silently.')}</p><button className="button primary" onClick={() => setAboutOpen(false)}>{t('Close')}</button></div></div>}
    {criticalError && <div className="dialog-backdrop critical-backdrop"><div className="critical-dialog" role="alertdialog" aria-modal="true"><span className="eyebrow">{t('BLOCKING FAILURE')}</span><h2>{t('Workbench action stopped')}</h2><p>{safeUiError(criticalError)}</p><div className="critical-actions"><button className="button secondary" onClick={() => void api.openLogs()}>{t('Open Logs')}</button>{runtimeUnavailable && <button className="button primary" onClick={() => void restartRuntime()}>{t('Retry Runtime')}</button>}<button className="button primary" onClick={() => setCriticalError('')}>{t('Acknowledge')}</button></div></div></div>}
  </div>
}

function MessageCard({ locale, message, currentPlan, onSavePlan }: { locale: Locale; message: Session['messages'][number]; currentPlan: Plan | null; onSavePlan: (plan: Plan) => Promise<void> }) {
  const t = (value: string) => translate(locale, value)
  const structured = phase2Plan(message.structured)
  const displayedPlan = structured?.plan_id === currentPlan?.plan_id ? currentPlan : structured
  return <article className={`message ${message.role}`}><div className="message-meta"><span>{message.role === 'user' ? t('You') : t('Scientific Agent')}</span><em>{message.mode}</em>{message.provider && <small>{message.provider}{message.model ? ` / ${message.model}` : ''}</small>}</div><div className="message-content">{message.content}</div>{message.visible_reasoning_summary && <div className="reasoning-summary"><strong>{t('Visible reasoning summary')}</strong><span>{message.visible_reasoning_summary}</span></div>}{displayedPlan && <PlanCard plan={displayedPlan} editable={displayedPlan.plan_id === currentPlan?.plan_id} onSave={onSavePlan} />}{message.structured && !structured && <div className="legacy-plan-notice">{t('Legacy Phase 1 plan retained for audit. Create a new PLAN to migrate it into ScientificPlan 2.0 before RUN.')}</div>}{message.citations && message.citations.length > 0 && !message.structured && <details className="citation-details"><summary>{message.citations.length} {t('traceable source(s)')}</summary><CitationList locale={locale} citations={message.citations} /></details>}{message.run_id && message.artifacts?.concentration_image && <ConcentrationResult runId={message.run_id} validation={message.validation ?? {}} />}{message.artifacts && <div className="artifacts">{Object.entries(message.artifacts).map(([name, path]) => <div key={name}><span>{name}</span><code title={path}>{path}</code></div>)}</div>}<ExecutionVerdictBadge verdict={message.execution_verdict} t={t} /></article>
}

function IntentConfirmation({ locale, intent, onContinue, onModify, onReset }: { locale: Locale; intent: ScientificIntent; onContinue: () => void; onModify: () => void; onReset: () => void }) {
  const zh = locale === 'zh-CN'
  const rows = [
    [zh ? '目标' : 'Goal', intent.goal],
    [zh ? '物理场' : 'Physics', intent.physics.join(', ') || (zh ? '未确定' : 'Unresolved')],
    [zh ? '几何' : 'Geometry', intent.geometry.join(', ') || (zh ? '未确定' : 'Unresolved')],
    [zh ? '研究' : 'Study', intent.study.join(', ') || (zh ? '未确定' : 'Unresolved')],
    [zh ? '边界条件' : 'Boundary conditions', intent.boundary_conditions.map((item) => `${item.location}: ${item.condition}${item.value == null ? '' : ` ${item.value} ${item.unit}`}`).join(' · ') || (zh ? '未确定' : 'Unresolved')],
  ]
  const blocked = intent.unknowns.length > 0 || intent.validation_issues.length > 0
  return <section className="intent-confirmation"><header><div><span className="eyebrow">{zh ? '意图确认' : 'INTENT CONFIRMATION'}</span><strong>{zh ? '我理解的是：' : "Here's how I understood your request:"}</strong></div><em className={blocked ? 'warning' : 'ready'}>{blocked ? (zh ? '需要补充信息' : 'Missing information') : (zh ? '高可信度' : 'High confidence')}</em></header><details><summary>{zh ? '原始输入' : 'Original input'}</summary><p>{intent.original_text}</p></details><dl>{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>{intent.unknowns.length > 0 && <div className="intent-missing"><strong>{zh ? '需要补充：' : 'Required:'}</strong>{intent.unknowns.map((item) => <span key={item}>{item}</span>)}</div>}{intent.validation_issues.length > 0 && <div className="intent-errors">{intent.validation_issues.map((item) => <span key={item}>{item}</span>)}</div>}<footer><button onClick={onModify}>{zh ? '修改' : 'Modify'}</button><button onClick={onReset}>{zh ? '重新描述' : 'Describe again'}</button><button className="button primary" disabled={blocked} onClick={onContinue}>{zh ? '正确，继续' : 'Correct, continue'}</button></footer></section>
}

function ConcentrationResult({ runId, validation }: { runId: string; validation: Record<string, unknown> }) {
  const [source, setSource] = useState('')
  useEffect(() => { let current = ''; void api.artifactObjectUrl(runId, 'concentration.png').then((value) => { current = value; setSource(value) }).catch(() => setSource('')); return () => { if (current) URL.revokeObjectURL(current) } }, [runId])
  return <figure className="result-viewer concentration-result">{source ? <img src={source} alt="COMSOL 2D concentration field" /> : <div className="result-loading">Loading concentration artifact…</div>}<figcaption><strong>Concentration · mol/m³</strong><span>{String(validation.concentration_min_mol_per_m3 ?? '—')} to {String(validation.concentration_max_mol_per_m3 ?? '—')}</span><small>RMSE {String(validation.analytical_rmse_mol_per_m3 ?? '—')} · Flux relative error {String(validation.flux_relative_error ?? '—')} · Mass balance {String(validation.mass_balance_relative_error ?? '—')}</small></figcaption></figure>
}

function RunBadge({ state, locale }: { state: string; locale: Locale }) { const tone = state === 'COMPLETED' || state === 'PLAN_READY' ? 'ready' : state === 'FAILED' || state === 'VALIDATION_FAILED' || state === 'INTERRUPTED' ? 'error' : activeStates.has(state) ? 'working' : 'idle'; return <span className={`run-badge ${tone}`}><span />{translate(locale, state.replaceAll('_', ' '))}</span> }
function phase2Plan(value: unknown): Plan | null { if (!value || typeof value !== 'object') return null; const candidate = value as Partial<Plan>; return candidate.schema_version === '2.0' && typeof candidate.plan_id === 'string' ? candidate as Plan : null }

export default App
