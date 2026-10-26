import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type DesktopBootstrap } from './api'
import { BrandIcon } from './components/BrandIcon'
import { Inspector, inspectorTabs } from './components/Inspector'
import { PlanCard } from './components/PlanCard'
import { IntentConfirmation } from './components/IntentConfirmation'
import { TaskHistoryMessage } from './components/TaskHistoryMessage'
import { WarmSettings } from './components/WarmSettings'
import { WarmOnboarding, warmOnboardingStorageKey } from './components/WarmOnboarding'
import { WarmHelp } from './components/WarmHelp'
import { initialLocale, localeStorageKey, translate, type Locale } from './i18n'
import type {
  AgentMode, EnvironmentItem, ExecutionEta, InspectorTab, JavaBridgeEvent,
  JavaBridgeStatus, Plan, Project, Provider, ProviderSettings, RuntimeDomain,
  ScientificIntent, Session,
} from './types'
import { WarmStudio } from './WarmStudio'
import { safeUiError } from './safeUiError'
import { currentScientificDomain, isCurrentSelection, sessionIsNotOlder, type SelectionTicket } from './scientificView'
import { reviewedBoundary } from './modelIdentity'
import { phase2Plan } from './planProjection'
import { useSessionEventFeed } from './useSessionEventFeed'
import { useRuntimeHealth } from './useRuntimeHealth'
import { hasLiveModel, matchingReadback, sameBridgeAttachment, type ModelReadback } from './bridgeReadback'

const activeStates = new Set([
  'RESEARCHING', 'PLANNING', 'PRECHECK', 'BUILDING', 'BUILD_VALIDATION',
  'SOLVING', 'SOLUTION_VALIDATION', 'REPAIRING', 'REVALIDATING',
  'EXPORTING', 'REPORTING', 'PAUSED', 'CANCEL_REQUESTED',
])

type BridgeView = { status: JavaBridgeStatus; readback: ModelReadback | null; loading: boolean }

const unavailableBridge: JavaBridgeStatus = {
  status: 'Unavailable', bridge_running: false, comsol_api_loaded: false,
  model_connected: false, connected: false, real_comsol: false,
  detail: 'Not inspected', base_url: 'http://127.0.0.1:8765',
  comsol_version: '', model_tag: '', bridge_mode: '', connection_mode: '',
  detection_method: 'unavailable', poll_interval_ms: 0,
}

function App() {
  const [locale, setLocale] = useState<Locale>(initialLocale)
  document.documentElement.lang = locale
  const [projects, setProjects] = useState<Project[]>([])
  const [sessions, setSessions] = useState<Session[]>([])
  const [session, setSession] = useState<Session | null>(null)
  const [environment, setEnvironment] = useState<EnvironmentItem[]>([])
  const [comsolInstallation, setComsolInstallation] = useState<{ found: boolean; version: string }>({ found: false, version: '' })
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
  const [indexTask, setIndexTask] = useState<Record<string, unknown>>({ state: 'idle' })
  const [semanticIndexTask, setSemanticIndexTask] = useState<Record<string, unknown>>({ state: 'idle' })
  const [bridgeView, setBridgeView] = useState<BridgeView>({
    status: unavailableBridge, readback: null, loading: false,
  })
  const bridge = bridgeView.status
  const [bridgeEvents, setBridgeEvents] = useState<JavaBridgeEvent[]>([])
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsEntry, setSettingsEntry] = useState<'general' | 'providers'>('general')
  const [aboutOpen, setAboutOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  // This key is intentionally new: completion of the old RC4.1 guide must not
  // suppress the first launch of the approved Warm Studio experience.
  const [firstRunOpen, setFirstRunOpen] = useState(() => localStorage.getItem(warmOnboardingStorageKey) !== 'complete')
  const [runtimeReady, setRuntimeReady] = useState(false)
  const [runtimeUnavailable, setRuntimeUnavailable] = useState(false)
  const [settingsMigration, setSettingsMigration] = useState<Record<string, unknown>>({ status: 'not_required' })
  const [databaseRecovery, setDatabaseRecovery] = useState('not_required')
  const dark = localStorage.getItem('workbench-theme') === 'dark'
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>('environment')
  const [executionEta, setExecutionEta] = useState<ExecutionEta | null>(null)
  const [runtimeDomain, setRuntimeDomain] = useState<RuntimeDomain | null>(null)
  const [desktopBootstrap, setDesktopBootstrap] = useState<DesktopBootstrap | null>(api.desktop())
  const askAbort = useRef<AbortController | null>(null)
  const bridgeRefreshRef = useRef(0)
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
    setPrompt(''); setPendingIntent(null); setStreaming(null); setBusy(false); setWarning('')
    return ticket
  }

  const invalidateBridge = useCallback(() => {
    bridgeRefreshRef.current += 1
    setBridgeView((current) => ({
      status: { ...current.status, status: 'Unavailable', bridge_running: false,
        model_connected: false, connected: false, model_tag: '' },
      readback: null, loading: false,
    }))
  }, [])

  const refreshBridge = useCallback(async () => {
    const generation = ++bridgeRefreshRef.current
    // One state update removes the previous model before a new request begins.
    setBridgeView((current) => ({ ...current, readback: null, loading: true }))
    try {
      const status = await api.bridgeStatus()
      if (generation !== bridgeRefreshRef.current) return null
      setBridgeView({ status, readback: null, loading: hasLiveModel(status) })
      if (hasLiveModel(status)) {
        const [treeData, parameterData] = await Promise.all([api.bridgeTree(), api.bridgeParameters()])
        if (generation !== bridgeRefreshRef.current) return null
        // The attachment may change while either readback is in flight.
        const latest = await api.bridgeStatus()
        if (generation !== bridgeRefreshRef.current) return null
        const readback = sameBridgeAttachment(status, latest)
          ? matchingReadback(latest, treeData, parameterData) : null
        setBridgeView({ status: latest, readback, loading: false })
        return latest
      }
      return status
    } catch (cause) {
      if (generation === bridgeRefreshRef.current) invalidateBridge()
      throw cause
    }
  }, [invalidateBridge])

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
      const [projectData, sessionData, environmentData, providerData,
        bridgeData, resourceData, capabilityData, migrationData] = await Promise.all([
        api.projects(), api.sessions(), api.environment(), api.providers(),
        api.bridgeStatus(), api.systemResources(), api.capabilities(), api.settingsMigration(),
      ])
      setProjects(projectData.projects)
      setEnvironment(environmentData.items)
      setComsolInstallation(environmentData.comsol_java_environment)
      setProviders(providerData.providers)
      setProviderSettings(providerData.settings)
      setKnowledge(environmentData.knowledge)
      setBridgeView({ status: bridgeData, readback: null, loading: false })
      setResources(resourceData); setCapabilities(capabilityData.capabilities)
      setSettingsMigration(migrationData)
      const project = projectData.projects.find((item) => item.active) ?? projectData.projects[0]
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
    } catch (cause) { setRuntimeUnavailable(true); setCriticalError(safeUiError(cause)) }
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
      if (event.event_type === 'model_change' || event.event_type === 'critical_failure' ||
          ['finished', 'failed', 'disconnected', 'unavailable'].includes(event.status))
        void refreshBridge().catch(() => undefined)
    }, invalidateBridge, {
      reconnect: true,
      onConnected: () => { void refreshBridge().catch(() => undefined) },
    })
    return () => source.abort()
  }, [invalidateBridge, refreshBridge, runtimeReady])
  useSessionEventFeed({ session, runtimeReady, locale, selectionRef, sessionRef,
    refreshSession, setRuntimeDomain, setWarning })
  useRuntimeHealth({ runtimeReady, locale, activeStates, selectionRef, sessionRef,
    sessionStateRef, refreshSession, setRuntimeUnavailable, setRuntimeDomain, setCriticalError })
  useEffect(() => {
    if (!runtimeReady || !session) { setExecutionEta(null); return }
    let active = true
    const ticket = selectionRef.current
    const refreshEta = () => {
      void api.executionEta(session.id).then((value) => {
        if (active && isCurrentSelection(ticket, selectionRef.current)) setExecutionEta(value)
      }).catch(() => {
        if (active && isCurrentSelection(ticket, selectionRef.current)) setExecutionEta(null)
      })
    }
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
          if (event.type === 'delta') setStreaming((current) => ({
            text: `${current?.text ?? ''}${String(event.text ?? '')}`,
            provider: current?.provider ?? '', model: current?.model ?? '',
          }))
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
        if (/COMSOL worker|Java Bridge|corrupt|Failed to fetch|runtime unavailable/i.test(message)) setCriticalError(safeUiError(message))
        else setWarning(safeUiError(message))
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
    } catch (cause) { if (selectionRef.current.generation === ticket.generation) setWarning(safeUiError(cause)) }
  }
  const selectSession = async (id: string) => {
    const ticket = beginSelection(id)
    try {
      const target = sessions.find((item) => item.id === id) ?? await api.session(id)
      if (!isCurrentSelection(ticket, selectionRef.current)) return
      if (target.project_id !== activeProject?.id) {
        await api.selectProject(target.project_id)
        const projectsForTask = await api.projects()
        if (!isCurrentSelection(ticket, selectionRef.current)) return
        setProjects(projectsForTask.projects)
      }
      await refreshSession(id, ticket)
    }
    catch (cause) { if (isCurrentSelection(ticket, selectionRef.current)) setWarning(safeUiError(cause)) }
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
    } catch (cause) { setWarning(safeUiError(cause)) }
  }
  const changeProject = async (projectId: string) => {
    if (activeProject?.id === projectId) return
    const previous = session
    const ticket = beginSelection('')
    try {
      await api.selectProject(projectId)
      const [projectData, sessionData] = await Promise.all([api.projects(), api.sessions()])
      if (selectionRef.current.generation !== ticket.generation) return
      setProjects(projectData.projects)
      setSessions(sessionData.sessions)
      // The project rail must not keep showing a task from another project.
      const existing = sessionData.sessions.find((item) => item.id === previous?.id && item.project_id === projectId)
        ?? sessionData.sessions.find((item) => item.project_id === projectId)
      if (existing) {
        const selected = { ...ticket, sessionId: existing.id }
        selectionRef.current = selected
        await refreshSession(existing.id, selected)
      } else {
        const created = await api.createSession(projectId)
        if (selectionRef.current.generation !== ticket.generation) return
        selectionRef.current = { ...ticket, sessionId: created.id }
        sessionRef.current = created; setSession(created)
        setSessions((current) => [created, ...current])
      }
    } catch (cause) {
      if (selectionRef.current.generation === ticket.generation) setWarning(safeUiError(cause))
    }
  }
  const refreshEnvironment = async () => {
    const [environmentData, providerData, knowledgeData, resourceData, capabilityData] = await Promise.all([
      api.environment(), api.providers(), api.knowledgeStatus(),
      api.systemResources(), api.capabilities(),
    ])
    setEnvironment(environmentData.items)
    setComsolInstallation(environmentData.comsol_java_environment)
    setProviders(providerData.providers)
    setProviderSettings(providerData.settings)
    setKnowledge(knowledgeData)
    setResources(resourceData)
    setCapabilities(capabilityData.capabilities)
    setIndexTask((knowledgeData.index_task as Record<string, unknown>) ?? { state: 'idle' })
    setSemanticIndexTask((knowledgeData.semantic_index_task as Record<string, unknown>) ?? { state: 'idle' })
    await refreshBridge()
  }
  const startBridge = async () => {
    setWarning(''); setInspectorTab('live'); const accepted = await api.startBridge()
    setBridgeView((current) => ({ status: { ...current.status, process: accepted, status: 'Starting' }, readback: null, loading: true }))
    for (let attempt = 0; attempt < 90; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 1000))
      const status = await refreshBridge()
      if (!status) continue
      const process = (status.process ?? {}) as Record<string, unknown>
      if (status?.model_connected) return
      if (['failed', 'crashed'].includes(String(process.state ?? ''))) { setWarning(safeUiError(process.error ?? 'Real COMSOL Java Bridge failed to start.')); return }
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
      setWarning(safeUiError(cause))
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
    } catch (cause) { setWarning(safeUiError(cause)) }
  }
  const pauseLiveRun = async () => {
    if (!session) return
    try {
      await api.pauseRun(session.id)
      setWarning(t('Pause requested; waiting for a verified safe COMSOL boundary.'))
      await refreshSession(session.id)
    } catch (cause) { setWarning(safeUiError(cause)) }
  }
  const correctLiveRun = async (correction: { path: string; value: number; unit: string; reason: string }) => {
    if (!session) return
    try {
      await api.correctRun(session.id, correction)
      setWarning(t('Correction verified by real COMSOL readback. Pre-correction downstream evidence is invalidated.'))
      await refreshSession(session.id)
    } catch (cause) { setCriticalError(safeUiError(cause)) }
  }
  const resumeLiveRun = async () => {
    if (!session) return
    try {
      await api.resumeRun(session.id)
      setWarning(t('Resumed from the earliest invalidated node. Scientific certification remains pending new evidence.'))
      await refreshSession(session.id)
    } catch (cause) { setWarning(safeUiError(cause)) }
  }
  const openHumanModification = () => {
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
      } catch (cause) { setCriticalError(safeUiError(cause)) }
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
    const source = api.streamEvents('/api/knowledge/events?after=0', (payload) => {
      setIndexTask(payload)
      if (payload.type === 'completed' || payload.type === 'error') {
        source.abort()
        runUiAction(refreshEnvironment)
      }
    }, () => {
      source.abort()
      setWarning(t('Knowledge index event stream disconnected. Status remains recoverable from the right sidebar.'))
      runUiAction(refreshEnvironment)
    })
  }
  const startSemanticIndex = async () => {
    setWarning(''); const accepted = await api.startSemanticIndex(); setSemanticIndexTask((accepted.semantic_index_task as Record<string, unknown>) ?? { state: 'running' })
    const source = api.streamEvents('/api/knowledge/semantic/events?after=0', (payload) => {
      setSemanticIndexTask(payload)
      if (payload.type === 'completed' || payload.type === 'error') {
        source.abort()
        runUiAction(refreshEnvironment)
      }
    }, () => {
      source.abort()
      setWarning(t('Semantic index event stream disconnected. Status remains recoverable from the right sidebar.'))
      runUiAction(refreshEnvironment)
    })
  }
  const restartRuntime = async () => {
    setCriticalError(''); setWarning(''); setRuntimeReady(false); setRuntimeDomain(null)
    try { setDesktopBootstrap(await api.restartDesktopRuntime()); await bootstrap() } catch (cause) { setRuntimeUnavailable(true); setCriticalError(safeUiError(cause)) }
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
  // Toolbar actions may reject before they can update their own status.
  // Surface those failures without turning ordinary tool errors into a
  // scientific verdict or leaving an unhandled Promise behind.
  const runUiAction = (action: () => Promise<unknown>) => {
    void action().catch((cause) => setWarning(safeUiError(cause)))
  }
  // The global Bridge feed has no session or attempt ID. Keep it in Live
  // COMSOL; only the session journal may populate this task's evidence view.
  const timelineItems = (session?.timeline ?? []).slice(-250)
  const taskHistory = <div className="task-history-list">
    {session?.messages.map((message, index) => <TaskHistoryMessage locale={locale}
      key={message.created_at + '-' + String(index)} message={message}
      currentPlan={activePlan} onSavePlan={savePlan} />)}
    {streaming && <article className="message assistant streaming-message">
      <div className="message-meta">
        <span>{t('Scientific Agent')}</span><em>ASK · STREAMING</em>
        <small>{streaming.provider} {streaming.model}</small>
      </div>
      <div className="message-content">
        {streaming.text || t('Generating…')}<span className="stream-caret" />
      </div>
    </article>}
  </div>
  // Full plan disclosure precedes the warm interface's explicit RUN consent.
  const planReview = activePlan ? <PlanCard plan={activePlan} editable onSave={savePlan} /> : null
  const intentConfirmation = pendingIntent
    ? <IntentConfirmation locale={locale} intent={pendingIntent}
      onContinue={() => void send()}
      onModify={() => { setPrompt(pendingIntent.original_text); setPendingIntent(null) }}
      onReset={() => { setPrompt(''); setPendingIntent(null) }} />
    : null

  const activeAi = providers.find((provider) => provider.name === providerSettings?.active_provider)
  const displayedModel = activeAi ? activeAi.selected_model : desktopBootstrap?.selectedModel ?? ''
  const displayedAvailability = activeAi ? activeAi.status : desktopBootstrap?.modelState ?? 'Unavailable'
  const showOllamaTier = !providerSettings?.active_provider || providerSettings.active_provider === 'ollama'
  const settingsUnavailable = runtimeUnavailable || (runtimeReady && !providerSettings)
  const handleInstallationScanned = (result: Record<string, unknown>) => {
    const found = result.comsol as { found?: boolean; version?: string } | undefined
    setComsolInstallation({ found: found?.found === true, version: found?.version ?? '' })
  }
  const technicalInspector = <>
    <div className="inspector-tabs-v2">
      {inspectorTabs.map(([key, label]) => <button key={key}
        className={inspectorTab === key ? 'active' : ''}
        onClick={() => setInspectorTab(key)}>{t(label)}</button>)}
    </div>
    <Inspector locale={locale} tab={inspectorTab} session={session} items={environment}
      providers={providers} knowledge={knowledge} indexTask={indexTask}
      semanticIndexTask={semanticIndexTask} resources={resources} capabilities={capabilities}
      bridge={bridge} bridgeReadback={bridgeView.readback} bridgeLoading={bridgeView.loading} bridgeEvents={bridgeEvents}
      onRefresh={() => runUiAction(refreshEnvironment)} onStartIndex={() => runUiAction(startKnowledgeIndex)}
      onRefreshBridge={() => runUiAction(refreshBridge)}
      onStartSemanticIndex={() => runUiAction(startSemanticIndex)}
      onStartBridge={() => runUiAction(startBridge)}
      onStopBridge={() => runUiAction(stopBridge)} onPauseRun={() => void pauseLiveRun()}
      onCorrectRun={(correction) => void correctLiveRun(correction)} onResumeRun={() => void resumeLiveRun()} />
  </>

  return <div className="app-shell warm-app-shell">
    {showOllamaTier && desktopBootstrap?.lowVramMode &&
      <section className="runtime-status model-tier-status" role="status">
        <div>
          <strong>{t(desktopBootstrap.modelState === 'Detected'
            ? 'LOW_VRAM_MODE active' : 'LOW_VRAM_MODE recommended')}</strong>
          <span>{t(desktopBootstrap.modelState === 'Detected'
            ? 'The detected qwen3:4b model matches constrained VRAM mode. No silent fallback occurred.'
            : 'Detected VRAM is below the standard threshold. Install qwen3:4b explicitly; no model is downloaded or substituted silently.')}</span>
          <small>{desktopBootstrap.detectedVramMib} MiB VRAM
            {' · '}{desktopBootstrap.selectedModel} · {t(desktopBootstrap.modelState)}</small>
        </div>
      </section>}
    {String(settingsMigration.status) === 'failed' &&
      <section className="migration-status" role="alert">
        <div>
          <strong>{t('Configuration migration needs attention')}</strong>
          <span>{t('Your previous configuration is preserved. Retry migration or explicitly use safe defaults.')}</span>
          <small>{safeUiError(settingsMigration.detail ?? '')}</small>
        </div>
        <div>
          <button onClick={() => runUiAction(api.openConfigBackup)}>{t('Open backup location')}</button>
          <button onClick={() => runUiAction(retrySettingsMigration)}>{t('Retry')}</button>
          <button onClick={() => runUiAction(useDefaultSettings)}>{t('Use safe defaults')}</button>
        </div>
      </section>}
    {runtimeUnavailable && <section className="runtime-status unavailable">
      <div>
        <strong>{t('Scientific Runtime unavailable')}</strong>
        <span>{activeStates.has(session?.state ?? '')
          ? t('An active RUN was interrupted. The saved plan and session remain preserved; execution is not reported as successful.')
          : t('The UI remains open; restart the owned local sidecar.')}</span>
      </div>
      <div className="runtime-actions">
        <button onClick={() => runUiAction(api.openLogs)}>{t('Open Logs')}</button>
        <button onClick={() => void restartRuntime()}>{t('Restart Runtime')}</button>
        <button onClick={() => setRuntimeUnavailable(false)}>{t('Dismiss')}</button>
      </div>
    </section>}
    {/* The guide renders from the first React frame, including while the
        runtime is still starting, so first launch never skips onboarding. */}
    {firstRunOpen && <WarmOnboarding locale={locale} environment={environment} providers={providers}
      activeProvider={providerSettings?.active_provider ?? ''} desktop={desktopBootstrap}
      runtimeReady={runtimeReady} runtimeUnavailable={runtimeUnavailable}
      onRetryRuntime={() => void restartRuntime()} onOpenLogs={() => runUiAction(api.openLogs)}
      onOpenHelp={() => setHelpOpen(true)} onInstallationScanned={handleInstallationScanned}
      onOpenProviderSettings={() => {
        localStorage.setItem(warmOnboardingStorageKey, 'complete')
        setFirstRunOpen(false)
        setSettingsEntry('providers')
        setSettingsOpen(true)
      }}
      onComplete={() => {
        localStorage.setItem(warmOnboardingStorageKey, 'complete')
        setFirstRunOpen(false)
      }} />}
    <WarmStudio
      locale={locale} projects={projects} activeProject={activeProject} sessions={sessions}
      session={session} plan={activePlan} domain={runtimeReady && !runtimeUnavailable ? selectedDomain : null} bridge={bridge}
      comsolDetected={comsolInstallation.found} comsolVersion={comsolInstallation.version}
      databaseRecovery={databaseRecovery}
      eta={executionEta} selectedModel={displayedModel} modelAvailability={displayedAvailability}
      literatureEnabled={Boolean(providerSettings?.extensions?.literature_modeling?.enabled)}
      mode={mode} prompt={prompt} busy={busy} askStreaming={Boolean(streaming)} warning={warning}
      pendingIntent={Boolean(pendingIntent)} taskHistory={taskHistory} planReview={planReview}
      intentConfirmation={intentConfirmation} timeline={timelineItems}
      technicalInspector={technicalInspector}
      onNewSession={() => newSession()} onSelectSession={(id) => { void selectSession(id) }} onArchiveSession={(id) => void archiveSession(id)}
      onSelectProject={(id) => runUiAction(() => changeProject(id))}
      onPromptChange={(value) => { setPrompt(value); setPendingIntent(null) }}
      onModeChange={(value) => { setMode(value); setPendingIntent(null) }}
      onSend={(value) => void send(value)} onCancelAsk={() => askAbort.current?.abort()} onPause={() => void pauseLiveRun()}
      onResume={() => void resumeLiveRun()} onApprove={() => void approveHumanAction()}
      onReject={() => void rejectHumanAction()} onModify={openHumanModification}
      onCancel={() => void cancelSolve()} onEmergencyStop={() => void emergencyStop()} onOpenSettings={() => { setSettingsEntry('general'); setSettingsOpen(true) }}
      onOpenLiveInspector={() => setInspectorTab('live')}
      onOpenSetup={() => setFirstRunOpen(true)} onOpenAbout={() => setAboutOpen(true)} onOpenHelp={() => setHelpOpen(true)}
      onOpenLogs={() => runUiAction(api.openLogs)}
      onOpenWorkspace={() => runUiAction(api.openWorkspace)} onLocaleChange={setLocale}
    />
    {settingsOpen && (!providerSettings || !runtimeReady || runtimeUnavailable) && <div className="warm-settings-backdrop">
      <section className="warm-settings" role="dialog" aria-modal="true" aria-label={t('Settings')}>
        <header className="warm-settings-header"><BrandIcon size={32} /><h2>{t('Settings')}</h2>
          <button type="button" onClick={() => setSettingsOpen(false)} aria-label={t('Close')}>×</button></header>
        <p className="warm-settings-feedback" role={settingsUnavailable ? 'alert' : 'status'}>
          {settingsUnavailable
            ? locale === 'zh-CN'
              ? 'Runtime 当前不可用，连接设置尚未载入。请查看日志或重启 Runtime。'
              : 'Runtime is unavailable. Connection settings have not loaded; inspect logs or restart Runtime.'
            : locale === 'zh-CN'
              ? '正在读取本机设置。Runtime 就绪后才能修改连接配置。'
              : 'Loading local settings. Connection settings become editable when the Runtime is ready.'}
        </p>
        {settingsUnavailable && <div className="runtime-actions">
          <button type="button" onClick={() => runUiAction(api.openLogs)}>{t('Open Logs')}</button>
          <button type="button" onClick={() => void restartRuntime()}>{t('Restart Runtime')}</button>
        </div>}
        <button type="button" onClick={() => setSettingsOpen(false)}>{t('Close')}</button>
      </section>
    </div>}
    {settingsOpen && providerSettings && runtimeReady && !runtimeUnavailable && <WarmSettings locale={locale} providers={providers}
      settings={providerSettings}
      initialSection={settingsEntry} onLocaleChange={setLocale}
      onOpenSetup={() => { setSettingsOpen(false); setFirstRunOpen(true) }}
      onScanComplete={handleInstallationScanned} onClose={() => setSettingsOpen(false)}
      onSaved={async () => { await refreshEnvironment(); setSettingsOpen(false) }} />}
    {helpOpen && <WarmHelp locale={locale} environment={environment} providers={providers}
      activeProvider={providerSettings?.active_provider ?? ''}
      onClose={() => setHelpOpen(false)} onOpenLogs={() => runUiAction(api.openLogs)} />}
    {aboutOpen && <div className="dialog-backdrop">
      <div className="about-dialog" role="dialog" aria-modal="true" aria-label="About SKY Workbench">
        <BrandIcon size={96} className="about-icon" label="SKY Workbench" />
        <span className="eyebrow">V3.0</span>
        <h2>SKY Workbench</h2>
        <p><b>v3.0.0</b><br />{t('Build signature')}: <b>{t('Unsigned')}</b></p>
        <p>{t('Independent third-party scientific modeling workbench. Not affiliated with, endorsed by, or sponsored by COMSOL AB.')}</p>
        <p>{t('COMSOL® is a trademark of COMSOL AB. Official COMSOL software and a valid license are required for real model execution.')}</p>
        <p>{t('Local and cloud AI are optional. No model is downloaded or substituted silently.')}</p>
        <button className="button primary" onClick={() => setAboutOpen(false)}>{t('Close')}</button>
      </div>
    </div>}
    {criticalError && <div className="dialog-backdrop critical-backdrop">
      <div className="critical-dialog" role="alertdialog" aria-modal="true">
        <span className="eyebrow">{t('BLOCKING FAILURE')}</span>
        <h2>{t('Workbench action stopped')}</h2>
        <p>{safeUiError(criticalError)}</p>
        <div className="critical-actions">
          <button className="button secondary" onClick={() => runUiAction(api.openLogs)}>{t('Open Logs')}</button>
          {runtimeUnavailable && <button className="button primary" onClick={() => void restartRuntime()}>
            {t('Retry Runtime')}
          </button>}
          <button className="button primary" onClick={() => setCriticalError('')}>{t('Acknowledge')}</button>
        </div>
      </div>
    </div>}
  </div>
}

export default App
