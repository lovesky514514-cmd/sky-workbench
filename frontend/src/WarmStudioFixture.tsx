import { useState } from 'react'
import { WarmStudio } from './WarmStudio'
import type { AgentMode, JavaBridgeStatus, Plan, Project, RuntimeDomain, Session } from './types'

type FixturePage = 'task' | 'plan' | 'model' | 'results'

// This module is reachable only behind VITE_VISUAL_FIXTURES=1. All identifiers,
// readback values and verdicts below are illustrative, never scientific data.
const project: Project = { id: 'visual-only', name: 'Local workspace', path: 'visual-only', read_only: true, active: true }
const bridge: JavaBridgeStatus = { status: 'Visual fixture', bridge_running: true, comsol_api_loaded: true, model_connected: true, connected: true, real_comsol: true, detail: 'Visual fixture only', base_url: '', comsol_version: '6.3', model_tag: 'visual-only', bridge_mode: 'fixture', connection_mode: 'fixture', detection_method: 'fixture', poll_interval_ms: 0 }
const planFixture: Plan = {
  schema_version:'2.0', plan_id:'visual-plan-only', workflow_id:'heat_transfer_2d_stationary',
  created_at:'visual-only', updated_at:'visual-only', source:'visual-fixture',
  ai_provider:'none', ai_model:'none', quality_status:'VALID_WITH_WARNINGS',
  title:'2D steady-state heat conduction',
  research_goal:'Review the aluminium plate, explicit boundary values and required checks before running.',
  runnable:true, physics:['Heat Transfer in Solids'], geometry:['2D rectangle · 10 mm × 5 mm'],
  materials:['Aluminium · property values require source review'],
  boundary_conditions:['Left: 373 K','Right: 293 K','Top and bottom: thermal insulation'],
  mesh_strategy:[], study:[], solver:[], validation:[], expected_outputs:[], sources:[], steps:[],
  missing_information:[], assumptions:['Thermal conductivity requires a citable source before scientific certification.'], warnings:[],
  parameters:{ conductivity:{ name:'conductivity', symbol:'k', value:238, unit:'W/(m·K)', source:'visual-fixture', assumption:true, verified:false, status:'Assumption' } },
}

function fixtureSession(page: FixturePage): Session {
  return { id: 'visual-only', title: 'Aluminium plate', project_id: project.id,
    state: page === 'model' ? 'PAUSED' : page === 'results' ? 'COMPLETED' : page === 'plan' ? 'PLAN_READY' : 'IDLE',
    created_at: '', updated_at: '', messages: [], timeline: [], events: [] }
}

function fixtureDomain(page: FixturePage): RuntimeDomain | null {
  if (page === 'task' || page === 'plan') return null
  const passed = page === 'results'
  return { contractVersion: 'rc42-v1', studyId: 'visual-only', modelId: 'visual-only',
    modelSnapshot: { name: 'plate_heat.mph / geom1 / rect1', geometry: { kind: 'rectangle', sizeMm: { x: 10, y: 5 } }, material: { name: 'Aluminium' }, physics: { interface: 'Heat Transfer in Solids' }, boundaries: [
      { id: 'left-visual', label: 'Boundary 1 / ht.temp1', location: 'left edge', value: { value: 373, unit: 'K' } },
      { id: 'right-visual', label: 'Boundary 4 / ht.temp2', location: 'right edge', value: { value: 293, unit: 'K' } },
    ], mesh: { readback: 'matched' }, solver: { solver: 'sol1' } },
    objectIds: { geometry: 'geometry-visual', material: 'material-visual', physics: 'physics-visual', mesh: 'mesh-visual', solver: 'solver-visual', boundaries: { left: 'left-visual', right: 'right-visual' } },
    modelComponents: { geometry: { id: 'geometry-visual', verification: 'verified_readback' }, material: { id: 'material-visual', verification: 'verified_readback' }, physics: { id: 'physics-visual', verification: 'verified_readback' }, boundaries: [], mesh: { id: 'mesh-visual', verification: 'solver_result' }, solver: { id: 'solver-visual', verification: 'solver_result' } },
    runState: { runId: 'run-visual-only', attemptId: 'attempt-visual-only', phase: page === 'model' ? 'paused' : 'settled', headline: page === 'model' ? 'Paused at safe point' : 'Run finished', pauseCapability: 'unavailable', safePointState: page === 'model' ? 'paused' : 'none' },
    reviewRequest: page === 'model' ? { id: 'review-visual', reviewId: 'review-visual', runId: 'run-visual-only', attemptId: 'attempt-visual-only', objectId: 'left-visual', objectLabel: 'Boundary 1', current: '373', proposed: '350', unit: 'K', reason: 'Your correction to the left edge.', impact: 'Validation must be rerun.', state: 'OPEN', options: [{ id: 'approve', label: 'Apply proposed value', consequence: 'Invalidates downstream evidence.' }] } : null,
    resultArtifacts: [], evidenceState: passed ? 'verified' : 'pending',
    authoritativeVerdict: passed ? { schema_version: 1, run_id: 'run-visual-only', workflow_id: 'heat_transfer_2d_stationary', command_success: true, model_state_verified: true, scientific_validation_success: true, evidence_contract_passed: true, completed: true, issues: [] } : null,
    contractGaps: [] }
}

// Synthetic pixels live only in this dynamically imported review fixture.
// Production bundles cannot accidentally present them as COMSOL evidence.
function IllustrativeResultGallery() {
  return <div className="verified-artifact-viewer warm-gallery warm-gallery-fixture"><header className="warm-gallery-title"><div><span>DESIGN REVIEW · NOT COMSOL EVIDENCE</span><h2>Temperature field</h2><small>Temperature · K</small></div><button disabled>Enlarge image ↗</button></header><div className="warm-gallery-stage"><button disabled>‹</button><div className="warm-gallery-fixture-field"><span>350 K</span><span>293 K</span></div><button disabled>›</button></div><footer className="warm-gallery-footer"><strong>Temperature field</strong><span>1 / 2 · temperature.png</span><small>Illustrative layout only</small></footer><nav className="warm-gallery-pages"><button className="is-current">1</button><button>2</button></nav></div>
}

export function WarmStudioFixture({ page }: { page: FixturePage }) {
  const [prompt, setPrompt] = useState('Find the steady-state temperature in a 10 × 5 mm aluminium plate.\nSet the left edge to 373 K and the right edge to 293 K.\nInsulate the top and bottom edges.')
  const [mode, setMode] = useState<AgentMode>('PLAN')
  const session = fixtureSession(page)
  return <WarmStudio visualFixture={page === 'plan' ? 'task' : page} illustrativeResult={page === 'results' ? <IllustrativeResultGallery /> : null} locale="en-US" projects={[project]} activeProject={project}
    sessions={[session]} session={session} plan={page === 'plan' ? planFixture : null} domain={fixtureDomain(page)} bridge={bridge}
    eta={null} selectedModel="qwen3:8b" modelAvailability="Detected" literatureEnabled={false} mode={mode} prompt={prompt} busy={false} askStreaming={false} warning=""
    pendingIntent={false} taskHistory={null} planReview={null} intentConfirmation={null} technicalInspector={null} timeline={[]}
    onNewSession={async () => undefined} onSelectSession={() => undefined} onArchiveSession={() => undefined} onSelectProject={() => undefined}
    onPromptChange={setPrompt} onModeChange={setMode} onSend={() => undefined} onCancelAsk={() => undefined}
    onPause={() => undefined} onResume={() => undefined} onApprove={() => undefined}
    onReject={() => undefined} onModify={() => undefined} onCancel={() => undefined} onEmergencyStop={() => undefined}
    onOpenSettings={() => undefined} onOpenSetup={() => undefined} onOpenAbout={() => undefined} onOpenHelp={() => undefined}
    onOpenLogs={() => undefined} onOpenWorkspace={() => undefined} onLocaleChange={() => undefined} />
}
