import { useState } from 'react'
import { AgentControlPanel } from './components/AgentControlPanel'
import { BrandIcon } from './components/BrandIcon'
import { EvidenceTimeline } from './components/EvidenceTimeline'
import { ModelTree, type ModelTreeNode } from './components/ModelTree'
import { ScientificCanvas, type CanvasView, type Phase2VisualState } from './components/ScientificCanvas'
import { TaskDock } from './components/TaskDock'
import type { ExecutionEta, ExecutionVerdict, JavaBridgeEvent, JavaBridgeStatus, Plan, Project, Session, TimelineItem } from './types'

const project: Project = { id: 'visual-project', name: 'Electrochemical Transport Study', path: 'visual-fixture', read_only: true, active: true }
const plan: Plan = {
  schema_version: '2.0', plan_id: 'plan_visual_001', workflow_id: 'heat_stationary_2d', title: '2D stationary heat transfer',
  research_goal: 'Resolve a verified temperature field across an aluminium plate.', physics: ['Heat Transfer in Solids'], geometry: ['Rectangle · 10 mm × 5 mm'], materials: ['Aluminium'],
  parameters: {
    width: { name: 'Width', symbol: 'W', value: 0.01, unit: 'm', source: 'User Provided', assumption: false, verified: true, status: 'User Provided' },
    height: { name: 'Height', symbol: 'H', value: 0.005, unit: 'm', source: 'User Provided', assumption: false, verified: true, status: 'User Provided' },
    left_temperature: { name: 'Left temperature', symbol: 'T_left', value: 373, unit: 'K', source: 'User Provided', assumption: false, verified: true, status: 'User Provided' },
    right_temperature: { name: 'Right temperature', symbol: 'T_right', value: 293, unit: 'K', source: 'User Provided', assumption: false, verified: true, status: 'User Provided' },
  },
  boundary_conditions: ['Left boundary · Temperature 373 K', 'Right boundary · Temperature 293 K', 'Top / bottom · Thermal insulation'],
  mesh_strategy: ['Physics-controlled mesh · Normal'], study: ['Stationary'], solver: ['Stationary fully coupled'], validation: ['Finite temperature', 'Maximum principle', 'Energy balance'], expected_outputs: ['Temperature (K) · Surface'], assumptions: ['Homogeneous isotropic material'], missing_information: [], sources: [],
  steps: [{ phase: 'BUILDING', label: 'Build geometry' }, { phase: 'SOLVING', label: 'Solve stationary study' }], warnings: [], runnable: true, source: 'deterministic', ai_provider: 'Ollama', ai_model: 'qwen3:8b', quality_status: 'VALID', created_at: '2026-09-24T00:00:00Z', updated_at: '2026-09-24T00:00:00Z',
}

const bridge: JavaBridgeStatus = { status: 'Ready', bridge_running: true, comsol_api_loaded: true, model_connected: true, connected: true, real_comsol: true, detail: 'Visual fixture', base_url: '', comsol_version: '6.3', model_tag: 'visual_fixture', bridge_mode: 'fixture', connection_mode: 'fixture', detection_method: 'fixture', poll_interval_ms: 0 }

function verdict(completed: boolean, issue = ''): ExecutionVerdict {
  return { schema_version: 1, run_id: completed ? 'run_verified_8f21' : 'run_review_0c31', workflow_id: 'heat_stationary_2d', command_success: true, model_state_verified: true, scientific_validation_success: completed, evidence_contract_passed: completed, completed, issues: issue ? [issue] : [] }
}

function event(id: string, phase: string, label: string, status: TimelineItem['status'], percent?: number): TimelineItem {
  return { id, phase, label, status, category: phase === 'Validate' ? 'VALIDATION' : 'COMSOL', tool: 'visual_fixture', input_summary: { run_id: 'run_visual_001', attempt_id: 'attempt_001', percent }, output_summary: label, fallback: false, created_at: '2026-09-24T00:00:00Z' }
}

function fixtureSession(state: Phase2VisualState): Session | null {
  if (state === 'idle') return null
  const reviewState = state === 'need-review' || state === 'inspector-object-edit'
  const verifiedState = state === 'verified-result' || state === 'left-rail-expanded'
  const timeline = state === 'plan-review' ? [event('plan', 'Plan', 'Scientific Plan ready for review', 'success')]
    : state === 'running' ? [event('build', 'Build', 'Geometry and selections verified', 'success'), event('solve', 'Solve', 'Assembling stationary solver', 'running', 42)]
      : reviewState ? [event('build', 'Build', 'Model build verified', 'success'), event('readback', 'Readback', 'Boundary selection differs from plan', 'warning')]
        : [event('build', 'Build', 'Model build verified', 'success'), event('solve', 'Solve', 'Stationary solver converged', 'success'), event('validate', 'Validate', 'All required scientific checks passed', 'success')]
  const executionVerdict = verifiedState ? verdict(true) : reviewState ? verdict(false, 'Boundary selection requires human confirmation') : undefined
  return {
    id: `fixture-${state}`, title: 'Aluminium plate · stationary heat transfer', project_id: project.id,
    state: state === 'plan-review' ? 'PLAN_READY' : state === 'running' ? 'SOLVING' : reviewState ? 'PAUSED' : 'COMPLETED',
    mode: state === 'plan-review' ? 'PLAN' : 'RUN', plan,
    model: state === 'plan-review' || state === 'running' ? null : { geometry: plan.geometry, materials: plan.materials, physics: plan.physics, parameters: plan.parameters, boundary_conditions: plan.boundary_conditions, selections: ['Domain 1', 'left_boundary', 'right_boundary', 'insulated_edges'], mesh: ['Physics-controlled · Normal · 1,842 elements'], study: plan.study, solver: plan.solver, results: state === 'verified-result' ? plan.expected_outputs : [] },
    error: null, created_at: '2026-09-24T00:00:00Z', updated_at: '2026-09-24T00:00:00Z', timeline,
    messages: [{ role: 'user', mode: 'PLAN', content: plan.research_goal, created_at: '2026-09-24T00:00:00Z' }, { role: 'assistant', mode: state === 'plan-review' ? 'PLAN' : 'RUN', content: state === 'verified-result' ? 'Verified result is available.' : 'Scientific state projection.', created_at: '2026-09-24T00:00:01Z', structured: plan, execution_verdict: executionVerdict }],
  }
}

export function Phase2VisualFixture({ visualState }: { visualState: Phase2VisualState }) {
  const session = fixtureSession(visualState)
  const reviewState = visualState === 'need-review' || visualState === 'inspector-object-edit'
  const [selected, setSelected] = useState<ModelTreeNode>(visualState === 'verified-result' ? 'Results' : reviewState ? 'Boundary' : 'Geometry')
  const [view, setView] = useState<CanvasView>(visualState === 'verified-result' ? 'results' : 'model')
  const [inspectorOpen, setInspectorOpen] = useState(visualState === 'plan-review' || reviewState)
  const timeline = session?.timeline ?? []
  const eta: ExecutionEta = { schema_version: 1, source: 'execution_ledger', workflow_id: 'heat_stationary_2d', state: session?.state ?? 'IDLE', status: visualState === 'running' ? 'estimated' : visualState === 'verified-result' ? 'complete' : 'unknown', estimated_remaining_seconds: visualState === 'running' ? 80 : null, sample_count: visualState === 'running' ? 7 : 0, basis: 'visual fixture', precision: visualState === 'running' ? 'coarse' : 'unknown' }
  const bridgeEvents: JavaBridgeEvent[] = visualState === 'running' ? [{ sequence: 1, event_type: 'progress', phase: 'Solve', status: 'running', detail: 'Assembling stationary solver', progress: 42, percent: 42, message: 'Assembling stationary solver' }]
    : visualState === 'inspector-object-edit' ? [{ sequence: 2, event_type: 'model_change', phase: 'Readback', status: 'warning', detail: 'User correction awaiting approval', target: 'Left boundary temperature', action: 'correct', old_value: '373 K', new_value: '350 K', verified: true }]
      : []
  const pendingApproval = visualState === 'plan-review' || reviewState
  const selectNode = (node: ModelTreeNode) => { setSelected(node); setView(node === 'Results' ? 'results' : node === 'Evidence' ? 'evidence' : node === 'Mesh' || node === 'Study / Solver' ? 'mesh-solver' : 'model') }
  return <div className="app-shell phase2-fixture-shell" data-visual-state={visualState}>
    <header className="topbar workspace-topbar phase2-global-bar phase21-product-bar"><div className="topbar-product"><BrandIcon size={32} /><div className="brand-copy"><strong>SKY Workbench</strong><span>Electrochemical Transport Study</span></div></div><nav className="global-mode-switch">{([['model', 'Model'], ['parameters', 'Parameters'], ['mesh-solver', 'Mesh/Solver'], ['results', 'Results'], ['evidence', 'Evidence']] as Array<[CanvasView, string]>).map(([value, label]) => <button className={view === value ? 'active' : ''} key={value} onClick={() => setView(value)}>{label}</button>)}</nav><div className="topbar-product-status"><span className="workspace-connection-chip connected"><i />COMSOL attached</span><span className="topbar-model">qwen3:8b</span><div className="language-switch"><button>中</button><span>/</span><button className="active">EN</button></div><button className="topbar-settings">Settings</button><button className="phase2-top-action">•••</button></div></header>
    <span className="fixture-audit-label">VISUAL QA FIXTURE · NOT SCIENTIFIC DATA</span>
    <main className="scientific-agent-workspace instrument-workspace">
      <ModelTree locale="en-US" projects={[project]} activeProject={project} session={session} sessions={session ? [session] : []} timeline={timeline} repositoryTree={[]} selected={selected} forceExpanded={visualState === 'plan-review' || visualState === 'left-rail-expanded'} onSelect={selectNode} onProjectChange={() => undefined} onSessionSelect={() => undefined} onNewSession={() => undefined} />
      <ScientificCanvas locale="en-US" session={session} timeline={timeline} bridge={visualState === 'idle' ? { ...bridge, model_connected: false, connected: false } : bridge} bridgeEvents={bridgeEvents} eta={eta} selectedNode={selected} view={view} visualState={visualState} inspectorOpen={inspectorOpen} onToggleInspector={() => setInspectorOpen((value) => !value)} taskDock={<TaskDock locale="en-US" mode={visualState === 'plan-review' || visualState === 'idle' ? 'PLAN' : 'RUN'} prompt={plan.research_goal} busy={false} activePlan={session?.plan ?? null} hasPendingIntent={pendingApproval} messageCount={session?.messages.length ?? 0} warning="" history={null} confirmation={pendingApproval ? <div className="fixture-confirmation">Review the normalized plan and boundary selections before continuation.</div> : null} sessionState={session?.state ?? 'IDLE'} forceOpen={visualState === 'plan-review'} onModeChange={() => undefined} onPromptChange={() => undefined} onSend={() => undefined} onCancelAsk={() => undefined} onDismissWarning={() => undefined} />} />
      <AgentControlPanel locale="en-US" session={session} selectedNode={selected} selectedModel="qwen3:8b" lowVramMode={false} warning={reviewState ? 'Boundary selection requires confirmation.' : ''} blockingError="" pendingApproval={pendingApproval} inspectorOpen={inspectorOpen} forceOpen={visualState === 'plan-review' || reviewState} correctionEvent={bridgeEvents.find((item) => item.old_value != null || item.new_value != null)} onToggleInspector={() => setInspectorOpen((value) => !value)} onPause={() => undefined} onModify={() => undefined} onApprove={() => undefined} onReject={() => undefined} onResume={() => undefined} inspector={<div className="fixture-diagnostics">Diagnostics remain hidden until explicitly selected.</div>} />
      <EvidenceTimeline locale="en-US" session={session} items={timeline} eta={eta} forceExpanded={false} onSelect={() => undefined} />
    </main>
  </div>
}
