import type { AgentMode, EnvironmentItem, ExecutionEta, ImportedMph, ImportedMphPreview, JavaBridgeStatus, LiteratureModelCandidate, Plan, Project, Provider, ProviderAcceptanceResult, ProviderConfig, ProviderSettings, ProviderTestResult, RetrievalMode, RuntimeDomain, ScientificIntent, Session, TreeNode } from './types'
import { responseError } from './safeUiError'
import { openEventStream, type EventStreamOptions } from './eventStream'
import { readMessageStream } from './messageStream'

export interface DesktopBootstrap {
  desktop: boolean
  version: string
  baseUrl: string
  sessionToken: string
  runtimeState: string
  appDataDir: string
  workspaceDir: string
  logsDir: string
  selectedModel: string
  modelMode: string
  lowVramMode: boolean
  detectedVramMib: number
  ollamaBaseUrl: string
  ollamaState: string
  modelState: string
  modelSetupRequired: boolean
  ollamaModelsRoot: string
  ollamaOwned: boolean
}

export interface LocalScriptRun {
  run_id: string
  engine: string
  state: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELED' | 'TIMED_OUT' | 'OUTPUT_LIMIT' | 'CLEANUP_UNVERIFIED'
  output: string
  elapsed_ms: number
  figures: Array<{ name: string; mime: string; size: number; sha256: string }>
  mathics_result: { status: 'success' | 'failed'; text: string; latex: string; mathml: string; messages: string[]; verification: 'not_requested' } | null
  script_exit_success: boolean
  model_state_verified: false
  scientific_validation_passed: false
}

let desktop: DesktopBootstrap | null = null
let initialization: Promise<DesktopBootstrap | null> | null = null

function endpoint(path: string): string { return desktop ? `${desktop.baseUrl}${path}` : path }
function requestHeaders(init?: RequestInit): HeadersInit {
  return {
    'Content-Type': 'application/json',
    ...(desktop?.sessionToken ? { 'X-Workbench-Token': desktop.sessionToken } : {}),
    ...(init?.headers ?? {}),
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(endpoint(path), { ...init, headers: requestHeaders(init) })
  if (!response.ok) throw new Error(responseError(await response.text(), response.status))
  return response.json() as Promise<T>
}

async function initialize(): Promise<DesktopBootstrap | null> {
  if (initialization) return initialization
  initialization = (async () => {
    if (!('__TAURI_INTERNALS__' in window)) return null
    const { invoke } = await import('@tauri-apps/api/core')
    desktop = await invoke<DesktopBootstrap>('desktop_bootstrap')
    for (let attempt = 0; attempt < 120; attempt += 1) {
      try { await request('/api/health'); return desktop } catch { await new Promise((resolve) => window.setTimeout(resolve, 250)) }
    }
    throw new Error('Scientific Runtime failed to start. Open logs or retry the owned desktop sidecar.')
  })()
  return initialization
}

async function restartDesktopRuntime(): Promise<DesktopBootstrap | null> {
  if (!('__TAURI_INTERNALS__' in window)) return null
  const { invoke } = await import('@tauri-apps/api/core')
  desktop = await invoke<DesktopBootstrap>('restart_runtime')
  initialization = null
  return initialize()
}

async function openLogs(): Promise<void> {
  if (!('__TAURI_INTERNALS__' in window)) return
  const { invoke } = await import('@tauri-apps/api/core')
  await invoke('open_logs')
}

async function openWorkspace(): Promise<void> {
  if (!('__TAURI_INTERNALS__' in window)) return
  const { invoke } = await import('@tauri-apps/api/core')
  await invoke('open_workspace')
}

async function openImportedModels(): Promise<void> {
  if (!('__TAURI_INTERNALS__' in window)) return
  const { invoke } = await import('@tauri-apps/api/core')
  await invoke('open_imported_models')
}

async function openConfigBackup(): Promise<void> {
  if (!('__TAURI_INTERNALS__' in window)) return
  const { invoke } = await import('@tauri-apps/api/core')
  await invoke('open_config_backup')
}

function streamEvents(
  path: string,
  onMessage: (payload: Record<string, unknown>) => void,
  onError: (cause: unknown) => void,
  options: EventStreamOptions = {},
): AbortController {
  return openEventStream(endpoint(path), desktop?.sessionToken ?? '', onMessage, onError, options)
}
async function artifactObjectUrl(runId: string, filename: string): Promise<string> {
  const response = await fetch(endpoint(`/api/artifacts/${encodeURIComponent(runId)}/${encodeURIComponent(filename)}`), {
    headers: desktop?.sessionToken ? { 'X-Workbench-Token': desktop.sessionToken } : {},
  })
  if (!response.ok) throw new Error(responseError(await response.text(), response.status))
  return URL.createObjectURL(await response.blob())
}

async function localScriptFigureUrl(runId: string, filename: string): Promise<string> {
  const response = await fetch(endpoint(`/api/script-runs/${encodeURIComponent(runId)}/figures/${encodeURIComponent(filename)}`), {
    headers: desktop?.sessionToken ? { 'X-Workbench-Token': desktop.sessionToken } : {},
  })
  if (!response.ok) throw new Error(responseError(await response.text(), response.status))
  return URL.createObjectURL(await response.blob())
}

// MPH uploads use a raw stream, not JSON or multipart. The Runtime owns the
// destination path and keeps the original separate from its working copy.
async function importMph(file: File): Promise<ImportedMph> {
  const response = await fetch(endpoint('/api/imported-mph'), {
    method: 'POST', body: file, headers: {
      'Content-Type': 'application/octet-stream',
      'X-Workbench-Filename': encodeURIComponent(file.name),
      ...(desktop?.sessionToken ? { 'X-Workbench-Token': desktop.sessionToken } : {}),
    },
  })
  if (!response.ok) throw new Error(responseError(await response.text(), response.status))
  return response.json() as Promise<ImportedMph>
}

async function extractLiteraturePdf(file: File): Promise<LiteratureModelCandidate> {
  const response = await fetch(endpoint('/api/extensions/literature-modeling/pdf'), {
    method: 'POST', body: file, headers: {
      'Content-Type': 'application/pdf', 'X-Workbench-Filename': encodeURIComponent(file.name),
      ...(desktop?.sessionToken ? { 'X-Workbench-Token': desktop.sessionToken } : {}),
    },
  })
  if (!response.ok) throw new Error(responseError(await response.text(), response.status))
  return response.json() as Promise<LiteratureModelCandidate>
}

export const api = {
  initialize,
  restartDesktopRuntime,
  openLogs,
  openWorkspace,
  openImportedModels,
  openConfigBackup,
  desktop: () => desktop,
  streamEvents,
  artifactObjectUrl,
  importMph,
  importedMph: () => request<{ items: ImportedMph[] }>('/api/imported-mph'),
  startImportedMphPreview: (id: string) => request<{ preview_state: string }>(`/api/imported-mph/${encodeURIComponent(id)}/preview`, { method: 'POST' }),
  importedMphPreview: (id: string) => request<ImportedMphPreview>(`/api/imported-mph/${encodeURIComponent(id)}/preview`),
  literatureModelingStatus: () => request<{ enabled: boolean; deepseek_enabled: boolean; deepseek_available: boolean }>('/api/extensions/literature-modeling'),
  setLiteratureModeling: (enabled: boolean) => request<{ enabled: boolean }>('/api/extensions/literature-modeling', { method: 'PUT', body: JSON.stringify({ enabled }) }),
  setLiteratureDeepSeek: (enabled: boolean) => request<{ enabled: boolean; deepseek_enabled: boolean; deepseek_available: boolean }>('/api/extensions/literature-modeling/deepseek', { method: 'PUT', body: JSON.stringify({ enabled }) }),
  extractLiteratureText: (sourceTitle: string, text: string) => request<LiteratureModelCandidate>('/api/extensions/literature-modeling/text', { method: 'POST', body: JSON.stringify({ source_title: sourceTitle, text }) }),
  extractLiteratureSource: (url: string) => request<LiteratureModelCandidate>('/api/extensions/literature-modeling/source', { method: 'POST', body: JSON.stringify({ url }) }),
  extractLiteraturePdf,
  health: () => request<{ status: string; version: string; settings_migration: Record<string, unknown>; database_recovery?: { status: string; historical_scientific_results?: string } }>('/api/health'),
  settingsMigration: () => request<Record<string, unknown>>('/api/settings/migration'),
  retrySettingsMigration: () => request<Record<string, unknown>>('/api/settings/migration/retry', { method: 'POST' }),
  useDefaultSettings: () => request<Record<string, unknown>>('/api/settings/migration/defaults', { method: 'POST' }),
  clearStorage: (kind: 'temporary-cache' | 'semantic-index') => request<Record<string, unknown>>(`/api/storage/clear/${kind}`, { method: 'POST' }),
  environment: () => request<{ items: EnvironmentItem[]; providers: Provider[]; knowledge: Record<string, unknown>; comsol_java_environment: { found: boolean; version: string } }>('/api/environment'),
  systemResources: () => request<Record<string, unknown>>('/api/system/resources'),
  capabilities: () => request<{ capabilities: Record<string, { status: string; reason: string }> }>('/api/capabilities'),
  probeCapabilities: () => request<Record<string, unknown>>('/api/capabilities/probe', { method: 'POST' }),
  sessionResources: (sessionId: string) => request<Record<string, unknown>>(`/api/sessions/${sessionId}/resources`),
  cancelRun: (sessionId: string, force = false) => request<Record<string, unknown>>(`/api/sessions/${sessionId}/run/cancel`, { method: 'POST', body: JSON.stringify({ force }) }),
  pauseRun: (sessionId: string) => request<Record<string, unknown>>(`/api/sessions/${sessionId}/run/pause`, { method: 'POST' }),
  correctRun: (sessionId: string, correction: { path: string; value: number; unit: string; reason: string }) => request<Record<string, unknown>>(`/api/sessions/${sessionId}/run/correction`, { method: 'POST', body: JSON.stringify(correction) }),
  resumeRun: (sessionId: string) => request<Record<string, unknown>>(`/api/sessions/${sessionId}/run/resume`, { method: 'POST' }),
  // Read the frozen projection; never infer verified model state from a saved plan.
  runtimeDomain: (sessionId: string) => request<RuntimeDomain>(`/api/rc42/sessions/${sessionId}/domain`),
  // A boundary approval applies only a current persisted review; validation still follows resume and solve.
  correctBoundary: (sessionId: string, reviewId: string) => request<Record<string, unknown>>(`/api/sessions/${sessionId}/run/boundary-correction`, { method: 'POST', body: JSON.stringify({ review_id: reviewId }) }),
  projects: () => request<{ projects: Project[] }>('/api/projects'),
  projectTree: (projectId: string) => request<{ tree: TreeNode[] }>(`/api/projects/${projectId}/tree?depth=3`),
  selectProject: (projectId: string) => request<Project>(`/api/projects/${projectId}/select`, { method: 'POST' }),
  sessions: () => request<{ sessions: Session[] }>('/api/sessions'),
  createSession: (projectId: string) => request<Session>('/api/sessions', { method: 'POST', body: JSON.stringify({ project_id: projectId }) }),
  session: (sessionId: string) => request<Session>(`/api/sessions/${sessionId}`),
  archiveSession: (sessionId: string) => request<{ status: string; session_id: string; records_deleted: number }>(`/api/sessions/${sessionId}/archive`, { method: 'POST' }),
  executionEta: (sessionId: string) => request<ExecutionEta>(`/api/sessions/${sessionId}/execution/eta`),
  sendMessage: (sessionId: string, mode: AgentMode, content: string) => request<Session>(`/api/sessions/${sessionId}/messages`, { method: 'POST', body: JSON.stringify({ mode, content }) }),
  compileIntent: (content: string) => request<ScientificIntent>('/api/intents/compile', { method: 'POST', body: JSON.stringify({ content }) }),
  legacyStatus: () => request<Record<string, unknown>>('/api/maintenance/legacy'),
  legacyAction: (action: string, path: string, confirmed = false) => request<Record<string, unknown>>('/api/maintenance/legacy', { method: 'POST', body: JSON.stringify({ action, path, confirmed }) }),
  modelsStatus: () => request<Record<string, unknown>>('/api/models/status'),
  startInstallationScan: () => request<Record<string, unknown>>('/api/environment/installations/scan', { method: 'POST' }),
  installationScanStatus: () => request<Record<string, unknown>>('/api/environment/installations/scan'),
  simulationAdapters: () => request<{ adapters: Array<{ engine: string; installed: boolean; executable: string; readback: string; execute: string; detail: string; connector: string; probe: string }> }>('/api/simulation-adapters'),
  probeSimulationAdapter: (engine: string) => request<{ engine: string; status: string; detail: string; elapsed_ms: number; readback: string; execute: string }>(`/api/simulation-adapters/${encodeURIComponent(engine.toLowerCase().replaceAll(' ', '_'))}/probe`, { method: 'POST' }),
  localScriptAvailability: () => request<{ engines: Record<string, boolean> }>('/api/script-runs/availability'),
  startLocalScript: (engine: string, source: string) => request<LocalScriptRun>('/api/script-runs', { method: 'POST', body: JSON.stringify({ engine, source, confirmed: true }) }),
  localScriptStatus: (runId: string) => request<LocalScriptRun>(`/api/script-runs/${encodeURIComponent(runId)}`),
  cancelLocalScript: (runId: string) => request<LocalScriptRun>(`/api/script-runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' }),
  localScriptFigureUrl,
  startOllama: () => request<Record<string, unknown>>('/api/models/ollama/start', { method: 'POST' }),
  pullOllama: (model: string, confirmed = false) => request<Record<string, unknown>>('/api/models/ollama/pull', { method: 'POST', body: JSON.stringify({ model, confirmed }) }),
  cancelOllamaPull: () => request<Record<string, unknown>>('/api/models/ollama/pull/cancel', { method: 'POST' }),
  removeOllama: (model: string, confirmed = false) => request<Record<string, unknown>>('/api/models/ollama/remove', { method: 'POST', body: JSON.stringify({ model, confirmed }) }),
  updatePlan: (sessionId: string, plan: Plan) => request<Session>(`/api/sessions/${sessionId}/plan`, { method: 'PUT', body: JSON.stringify({ plan }) }),
  providers: () => request<{ providers: Provider[]; settings: ProviderSettings }>('/api/providers'),
  saveProviders: (providers: Record<string, ProviderConfig>, retrievalMode: RetrievalMode, activeProvider: string, comsolRoot?: string) => request<{ providers: Provider[]; settings: ProviderSettings }>('/api/settings/providers', { method: 'PUT', body: JSON.stringify({ providers, retrieval_mode: retrievalMode, active_provider: activeProvider, comsol_root: comsolRoot }) }),
  testProvider: (provider: string) => request<ProviderTestResult>('/api/providers/test', { method: 'POST', body: JSON.stringify({ provider }) }),
  acceptProvider: (provider: string) => request<ProviderAcceptanceResult>('/api/providers/acceptance', { method: 'POST', body: JSON.stringify({ provider }) }),
  knowledgeStatus: () => request<Record<string, unknown>>('/api/knowledge/status'),
  refreshKnowledge: () => request<Record<string, unknown>>('/api/knowledge/refresh', { method: 'POST', body: JSON.stringify({ include_legacy: true }) }),
  startSemanticIndex: () => request<Record<string, unknown>>('/api/knowledge/semantic/index/start', { method: 'POST' }),
  bridgeStatus: () => request<JavaBridgeStatus>('/api/comsol/bridge/status'),
  bridgeTree: () => request<Record<string, unknown>>('/api/comsol/bridge/model-tree'),
  bridgeParameters: () => request<Record<string, unknown>>('/api/comsol/bridge/parameters'),
  startBridge: (modelPath?: string) => request<Record<string, unknown>>('/api/comsol/bridge/start', { method: 'POST', body: JSON.stringify({ model_path: modelPath ?? null }) }),
  stopBridge: () => request<Record<string, unknown>>('/api/comsol/bridge/stop', { method: 'POST' }),
  streamMessage: async (sessionId: string, mode: AgentMode, content: string, onEvent: (event: Record<string, unknown>) => void, signal?: AbortSignal): Promise<Session> => {
    const response = await fetch(endpoint(`/api/sessions/${sessionId}/messages/stream`), { method: 'POST', headers: requestHeaders(), body: JSON.stringify({ mode, content }), signal })
    if (!response.ok || !response.body) throw new Error(responseError(await response.text(), response.status))
    return readMessageStream(response, sessionId, onEvent)
  },
}
