import { useState } from 'react'
import type { Citation, EnvironmentItem, InspectorTab, JavaBridgeEvent, JavaBridgeStatus, Provider, ScientificParameter, Session, ValidationReport } from '../types'
import { CitationList } from './CitationList'
import { translate, type Locale } from '../i18n'
import { validationDisplay } from '../ExecutionVerdictBadge'
import { LiveComsolView } from './LiveComsolView'
import type { ModelReadback } from '../bridgeReadback'

export const inspectorTabs: Array<[InspectorTab, string]> = [['environment', 'Environment'], ['live', 'Live COMSOL'], ['overview', 'Overview'], ['geometry', 'Geometry'], ['materials', 'Materials'], ['physics', 'Physics'], ['boundaries', 'Boundary Conditions'], ['mesh', 'Mesh'], ['solver', 'Solver'], ['parameters', 'Parameters'], ['validation', 'Validation'], ['sources', 'Sources']]

type InspectorProps = {
  locale: Locale
  tab: InspectorTab
  session: Session | null
  items: EnvironmentItem[]
  providers: Provider[]
  knowledge: Record<string, unknown>
  indexTask: Record<string, unknown>
  semanticIndexTask: Record<string, unknown>
  resources: Record<string, unknown>
  capabilities: Record<string, { status: string; reason: string }>
  bridge: JavaBridgeStatus
  bridgeReadback: ModelReadback | null
  bridgeLoading: boolean
  bridgeEvents: JavaBridgeEvent[]
  onRefresh: () => void
  onRefreshBridge: () => void
  onStartIndex: () => void
  onStartSemanticIndex: () => void
  onStartBridge: () => void
  onStopBridge: () => void
  onPauseRun: () => void
  onCorrectRun: (correction: { path: string; value: number; unit: string; reason: string }) => void
  onResumeRun: () => void
}

export function Inspector({
  locale, tab, session, items, providers, knowledge, indexTask, semanticIndexTask,
  resources, capabilities, bridge, bridgeReadback, bridgeLoading, bridgeEvents,
  onRefresh, onRefreshBridge, onStartIndex, onStartSemanticIndex, onStartBridge,
  onStopBridge, onPauseRun, onCorrectRun, onResumeRun,
}: InspectorProps) {
  const t = (source: string) => translate(locale, source)
  if (tab === 'environment') return <EnvironmentView locale={locale} items={items} providers={providers} knowledge={knowledge} indexTask={indexTask} semanticIndexTask={semanticIndexTask} resources={resources} capabilities={capabilities} onRefresh={onRefresh} onStartIndex={onStartIndex} onStartSemanticIndex={onStartSemanticIndex} />
  if (tab === 'live') return <LiveComsolView locale={locale} bridge={bridge} readback={bridgeReadback} loading={bridgeLoading} events={bridgeEvents} onRefresh={onRefreshBridge} onStart={onStartBridge} onStop={onStopBridge} />
  const model = session?.model
  const plan = session?.plan
  const report = lastValidation(session) ?? model?.validation
  if (tab === 'overview') return <div className="inspector-body"><div className="model-overview"><div className="model-cube">◇</div><div><strong>{model ? t('Agent-read model state') : t('Planned model')}</strong><span>{session?.state.replaceAll('_', ' ') ?? 'IDLE'}</span></div></div><VNextFoundationPanel locale={locale} session={session} capabilities={capabilities} onPause={onPauseRun} onCorrect={onCorrectRun} onResume={onResumeRun} /><Fact title="Plan ID" values={plan ? [plan.plan_id] : []} /><Fact title={t('Physics')} values={model?.physics ?? plan?.physics ?? []} /><Fact title={t('Results')} values={model?.results ?? []} /></div>
  if (tab === 'parameters') return <div className="inspector-body"><ParameterView parameters={plan?.parameters ?? {}} /></div>
  if (tab === 'validation') return <div className="inspector-body"><ValidationView locale={locale} report={report} /></div>
  if (tab === 'sources') return <div className="inspector-body"><CitationList locale={locale} citations={(model?.sources ?? plan?.sources ?? []) as Citation[]} /></div>
  if (tab === 'physics' && plan?.workflow_id === 'tds_2d_stationary_diffusion') return <TdsPhysicsView locale={locale} session={session} />
  const mapping: Record<string, string[]> = {
    geometry: model?.geometry ?? plan?.geometry ?? [], materials: model?.materials ?? plan?.materials ?? [], physics: model?.physics ?? plan?.physics ?? [],
    boundaries: model?.boundary_conditions ?? plan?.boundary_conditions ?? [], mesh: model?.mesh ?? plan?.mesh_strategy ?? [], solver: [...(model?.study ?? plan?.study ?? []), ...(model?.solver ?? plan?.solver ?? [])],
  }
  return <div className="inspector-body"><Fact title={t(inspectorTabs.find(([key]) => key === tab)?.[1] ?? tab)} values={mapping[tab] ?? []} /></div>
}

function TdsPhysicsView({ locale, session }: { locale: Locale; session: Session | null }) {
  const t = (source: string) => translate(locale, source)
  const model = session?.model
  const state = session?.state ?? 'IDLE'
  const physics = (model?.physics ?? []).join(' ').toLowerCase()
  const boundaries = (model?.boundary_conditions ?? []).join(' ').toLowerCase()
  const complete = state === 'COMPLETED'
  const items = [
    [t('Interface'), physics.includes('diluted species')],
    [t('Diffusion'), physics.includes('pure diffusion')],
    [t('Left BC'), boundaries.includes('left concentration')],
    [t('Right BC'), boundaries.includes('right concentration')],
    [t('Mesh'), Boolean(model?.mesh?.length)],
    [t('Solving'), complete || ['SOLVING', 'SOLUTION_VALIDATION', 'EXPORTING', 'REPORTING'].includes(state)],
    [t('Scientific Validation'), Boolean(lastValidation(session)?.execution_verdict?.scientific_validation_success)],
  ] as Array<[string, boolean]>
  return <div className="inspector-body"><div className="inspector-section-title"><span>{t('Transport of Diluted Species')}</span></div><div className="physics-checklist">{items.map(([label, passed]) => <div key={label}><span>{passed ? '✓' : state === 'SOLVING' && label === t('Solving') ? '▶' : '○'}</span><strong>{label}</strong></div>)}</div><Fact title={t('Physics readback')} values={model?.physics ?? session?.plan?.physics ?? []} /></div>
}

function EnvironmentView({ locale, items, providers, knowledge, indexTask, semanticIndexTask, resources, capabilities, onRefresh, onStartIndex, onStartSemanticIndex }: { locale: Locale; items: EnvironmentItem[]; providers: Provider[]; knowledge: Record<string, unknown>; indexTask: Record<string, unknown>; semanticIndexTask: Record<string, unknown>; resources: Record<string, unknown>; capabilities: Record<string, { status: string; reason: string }>; onRefresh: () => void; onStartIndex: () => void; onStartSemanticIndex: () => void }) {
  const t = (source: string) => translate(locale, source)
  const models = (knowledge.models ?? {}) as Record<string, Record<string, unknown>>
  const embedding = models.embedding ?? {}
  const fallback = models.fallback_embedding ?? {}
  const reranker = models.reranker ?? {}
  const taskState = String(indexTask.state ?? indexTask.status ?? 'idle')
  const taskResult = (indexTask.result ?? {}) as Record<string, unknown>
  const semanticTaskState = String(semanticIndexTask.state ?? semanticIndexTask.status ?? 'idle')
  const semanticTaskResult = (semanticIndexTask.result ?? {}) as Record<string, unknown>
  const compiled = (knowledge.compiled_kb ?? {}) as Record<string, unknown>
  const cpu = (resources.cpu ?? {}) as Record<string, unknown>
  const memory = (resources.memory ?? {}) as Record<string, unknown>
  const disk = (resources.disk ?? {}) as Record<string, unknown>
  const gpu = (resources.gpu ?? {}) as Record<string, unknown>
  return <div className="inspector-body">
    <div className="inspector-section-title"><span>{t('Local environment')}</span><button onClick={onRefresh}>{t('Refresh')}</button></div>
    <div className="environment-list">{items.map((entry) => <div className="environment-row" key={entry.name} title={entry.path ?? ''}><span className={`status-dot ${entry.status.toLowerCase()}`} /><div><strong>{t(entry.name)}</strong><small>{entry.detail}</small></div><em>{t(entry.status)}</em></div>)}</div>
    <div className="inspector-section-title"><span>{t('Hardware Resource Guard')}</span></div>
    <div className="rag-backend-list"><StatusFact label="CPU" value={String(cpu.model ?? 'unavailable')} status={cpu.logical_threads ? `${String(cpu.logical_threads)} threads` : 'unavailable'} /><StatusFact label={t('Available RAM')} value={memory.available_gb != null ? `${String(memory.available_gb)} GB / ${String(memory.total_gb)} GB` : 'unavailable'} status={String(memory.status ?? 'unavailable')} /><StatusFact label={t('Workspace disk')} value={disk.free_gb != null ? `${String(disk.free_gb)} GB free` : 'unavailable'} status={String(disk.status ?? 'unavailable')} /><StatusFact label="GPU / VRAM" value={String(gpu.detail ?? 'unavailable')} status={String(gpu.status ?? 'unavailable')} /></div>
    <div className="inspector-section-title"><span>{t('Capability Matrix')}</span></div>
    <div className="rag-backend-list">{Object.entries(capabilities).map(([name, capability]) => <StatusFact key={name} label={name} value={capability.reason} status={capability.status} />)}</div>
    <div className="inspector-section-title"><span>{t('AI Providers')}</span></div><div className="provider-list">{providers.map((provider) => <div key={provider.name}><span>{provider.label}</span><em className={`status-text ${provider.status.toLowerCase().replaceAll(' ', '-')}`}>{t(provider.status)}</em><small>{provider.model || provider.detail}</small></div>)}</div>
    <div className="inspector-section-title rag-index-heading"><span>{t('Scientific RAG')}</span><span className="rag-index-actions"><button onClick={onStartIndex} disabled={taskState === 'running'}>{taskState === 'running' ? t('Indexing…') : t('Build fallback index')}</button><button onClick={onStartSemanticIndex} disabled={semanticTaskState === 'running'}>{semanticTaskState === 'running' ? t('Indexing…') : t('Build semantic index')}</button></span></div>
    <div className="metric-grid rag-metrics"><div><strong>{String(compiled.semantic_embedding_rows ?? 0)} / {String(knowledge.compiled_units ?? 0)}</strong><span>{t('neural Units indexed')}</span></div><div><strong>{String(knowledge.active_mode ?? 'accurate')}</strong><span>{t('retrieval mode')}</span></div></div>
    <div className="rag-backend-list"><StatusFact label={t('Embedding')} value={String(embedding.model ?? 'unavailable')} status={String(embedding.status ?? 'unavailable')} /><StatusFact label={t('Emergency dense')} value={String(fallback.model ?? 'local-hash-384')} status={String(fallback.status ?? 'unavailable')} /><StatusFact label={t('Reranker')} value={String(reranker.model ?? 'unavailable')} status={String(reranker.status ?? 'unavailable')} /><StatusFact label={t('Fallback index')} value={taskResult.indexed != null ? `${String(taskResult.indexed)} indexed · ${String(taskResult.skipped ?? 0)} skipped` : `${String(knowledge.indexed_units ?? 0)} / ${String(knowledge.compiled_units ?? 0)}`} status={taskState} /><StatusFact label={t('Semantic index')} value={semanticTaskResult.indexed != null ? `${String(semanticTaskResult.indexed)} indexed · ${String(semanticTaskResult.skipped ?? 0)} skipped` : `${String(compiled.semantic_embedding_rows ?? 0)} / ${String(knowledge.compiled_units ?? 0)}`} status={semanticTaskState} /></div>
  </div>
}
function StatusFact({ label, value, status }: { label: string; value: string; status: string }) { return <div className="status-fact"><span><strong>{label}</strong><small>{value}</small></span><em className={`status-text ${status.toLowerCase().replaceAll(' ', '-')}`}>{status}</em></div> }
function Fact({ title, values }: { title: string; values: string[] }) { return <section className="inspector-fact"><h3>{title}</h3>{values.length ? <ul>{values.map((value) => <li key={value}>{value}</li>)}</ul> : <p>No agent-read state recorded.</p>}</section> }
function ParameterView({ parameters }: { parameters: Record<string, ScientificParameter> }) { return <div className="parameter-inspector">{Object.entries(parameters).map(([name, value]) => <article key={name}><header><strong>{value.symbol || name}</strong><em className={`parameter-status ${String(value.status).toLowerCase().replaceAll(' ', '-')}`}>{value.status}</em></header><code>{value.value} {value.unit}</code><span>{name}</span><small>{value.source}</small>{value.source_page && <small>Page {value.source_page} · {value.source_chunk}</small>}</article>)}</div> }
function ValidationView({ locale, report }: { locale: Locale; report?: ValidationReport }) { const t = (source: string) => translate(locale, source); if (!report) return <p className="muted-block">{t('No validation report recorded.')}</p>; report = validationDisplay(report); return <div className="validation-view"><div className={`validation-summary ${report.passed ? 'passed' : report.scientific_validation_success ? 'warning' : 'failed'}`}><strong>{report.passed ? t('Scientific validation passed') : report.scientific_validation_success ? t('Evidence incomplete') : t('Validation failed')}</strong><span>{t('Command Success')}: {report.command_success ? '✓' : '✕'} · {t('Model State Verified')}: {report.model_state_verified ? '✓' : '✕'} · {t('Scientific Validation Passed')}: {report.scientific_validation_success ? '✓' : '✕'} · {t('Evidence Contract Passed')}: {report.evidence_contract_passed ? '✓' : '✕'}</span></div>{report.checks.map((check) => <article key={`${check.category}-${check.name}`} className={check.status}><span>{check.status === 'passed' ? '✓' : check.status === 'warning' ? '△' : '!'}</span><div><strong>{check.category} · {check.name}</strong><p>{check.message}</p><small>Observed: {JSON.stringify(check.observed)} · Expected: {JSON.stringify(check.expected)}</small></div></article>)}</div> }

function VNextFoundationPanel({ locale, session, capabilities, onPause, onCorrect, onResume }: { locale: Locale; session: Session | null; capabilities: Record<string, { status: string; reason: string }>; onPause: () => void; onCorrect: (correction: { path: string; value: number; unit: string; reason: string }) => void; onResume: () => void }) {
  const t = (source: string) => translate(locale, source)
  const [correctionValue, setCorrectionValue] = useState('2e-9')
  const [correctionReason, setCorrectionReason] = useState('User-confirmed diffusion coefficient correction')
  const plan = session?.plan; const model = session?.model; const report = lastValidation(session); const verdict = report?.execution_verdict
  const state = session?.state ?? 'IDLE'
  const active: Record<string, string[]> = { build: ['BUILDING', 'BUILD_VALIDATION'], solve: ['SOLVING'], validate: ['SOLUTION_VALIDATION', 'VALIDATING'], export: ['EXPORTING', 'REPORTING'] }
  const finished = (name: string) => name === 'plan' ? Boolean(plan) : name === 'build' ? Boolean(model?.geometry?.length && model?.physics?.length) : name === 'solve' ? Boolean(model?.solver?.length) : name === 'validate' ? Boolean(verdict?.scientific_validation_success) : Boolean(verdict?.evidence_contract_passed)
  const nodes = ['plan', 'build', 'solve', 'validate', 'export'].map((name) => ({ name, status: active[name]?.includes(state) ? 'running' : finished(name) ? 'verified' : ['FAILED', 'VALIDATION_FAILED', 'INTERRUPTED'].includes(state) ? 'needs-review' : 'pending' }))
  const planned = [...(plan?.geometry ?? []), ...(plan?.physics ?? []), ...(plan?.boundary_conditions ?? [])]
  const actual = [...(model?.geometry ?? []), ...(model?.physics ?? []), ...(model?.boundary_conditions ?? [])]
  const verifiedAdapter = capabilities.live_interrupt?.status === 'VERIFIED'
  const supportedWorkflow = session?.plan?.workflow_id === 'tds_2d_stationary_diffusion'
  const pauseRequested = Boolean(session?.events?.some((event) => event.type === 'execution.pause_requested'))
  const correctionVerified = Boolean(session?.events?.some((event) => event.type === 'execution.correction_verified'))
  const canPause = verifiedAdapter && supportedWorkflow && ['BUILDING', 'BUILD_VALIDATION', 'SOLVING', 'SOLUTION_VALIDATION', 'EXPORTING', 'REPORTING'].includes(state) && !pauseRequested
  const canCorrect = verifiedAdapter && supportedWorkflow && state === 'PAUSED' && !correctionVerified
  const canResume = verifiedAdapter && supportedWorkflow && state === 'PAUSED' && correctionVerified
  const submitCorrection = () => {
    const value = Number(correctionValue)
    if (!Number.isFinite(value) || value <= 0 || !correctionReason.trim()) return
    onCorrect({ path: 'parameters.diffusion_coefficient', value, unit: 'm^2/s', reason: correctionReason.trim() })
  }
  const unavailableReason = !verifiedAdapter ? capabilities.live_interrupt?.reason ?? t('Live interrupt acceptance is unavailable') : !supportedWorkflow ? t('Live correction is verified for TDS only') : ''
  return <section className="vnext-foundation"><header><strong>{t('Workflow and evidence')}</strong><em>{t('vNext foundation')}</em></header><div className="execution-mini-graph">{nodes.map((node, index) => <span key={node.name} className={node.status}>{index > 0 && <i>→</i>}<b>{t(node.name[0].toUpperCase() + node.name.slice(1))}</b><small>{t(node.status.replace('-', ' '))}</small></span>)}</div><details><summary>{t('Planned vs actual model state')}</summary><div className="state-compare"><div><b>{t('Planned')}</b>{planned.slice(0, 8).map((value) => <small key={value}>{value}</small>)}</div><div><b>{t('Actual readback')}</b>{actual.slice(0, 8).map((value) => <small key={value}>{value}</small>)}</div></div></details><div className={`evidence-gate ${verdict?.evidence_contract_passed ? 'verified' : 'needs-review'}`}><span>{t('Evidence Contract Passed')}</span><b>{verdict?.evidence_contract_passed ? '✓' : '—'}</b></div>{state === 'PAUSED' && <div className="typed-correction"><label>{t('Diffusion coefficient')}<input value={correctionValue} onChange={(event) => setCorrectionValue(event.target.value)} inputMode="decimal" /></label><code>m²/s</code><label>{t('Reason')}<input value={correctionReason} onChange={(event) => setCorrectionReason(event.target.value)} /></label></div>}<div className="interrupt-controls"><button disabled={!canPause} title={unavailableReason || t('Pause at the next verified safe COMSOL boundary')} onClick={onPause}>{pauseRequested ? t('Pause requested') : t('Pause')}</button><button disabled={!canCorrect} title={unavailableReason || t('Correction requires PAUSED state and real COMSOL readback')} onClick={submitCorrection}>{t('Correct')}</button><button disabled={!canResume} title={unavailableReason || t('Resume from the earliest invalidated node')} onClick={onResume}>{t('Resume')}</button></div></section>
}
function lastValidation(session: Session | null): ValidationReport | undefined { return [...(session?.messages ?? [])].reverse().find((message) => message.validation_report)?.validation_report ?? session?.plan?.precheck_summary ?? undefined }
