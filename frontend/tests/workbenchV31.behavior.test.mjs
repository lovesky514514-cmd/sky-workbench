import test, { afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { registerHooks } from 'node:module'
import ts from 'typescript'
import { JSDOM } from 'jsdom'

const sourceRoot = new URL('../src/', import.meta.url).href
registerHooks({
  resolve(specifier, context, next) {
    if (specifier.startsWith('.') && context.parentURL?.startsWith(sourceRoot)) {
      for (const extension of ['', '.ts', '.tsx']) {
        const candidate = new URL(specifier + extension, context.parentURL)
        if (existsSync(candidate)) return { url: candidate.href, shortCircuit: true }
      }
    }
    return next(specifier, context)
  },
  load(url, context, next) {
    if (url.startsWith(sourceRoot) && /\.tsx?$/.test(url)) {
      const source = ts.transpileModule(readFileSync(new URL(url), 'utf8'), {
        compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
      }).outputText
      return { format: 'module', source, shortCircuit: true }
    }
    return next(url, context)
  },
})

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' })
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'Node', 'MutationObserver']) {
  Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true })
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
const React = await import('react')
const { render, fireEvent, screen, cleanup, waitFor, act } = await import('@testing-library/react')
const { PlanCompletionReview } = await import('../src/components/PlanCompletionReview.tsx')
const { PlanCard } = await import('../src/components/PlanCard.tsx')
const { WarmPlanOverview } = await import('../src/components/WarmPlanOverview.tsx')
const { ModelingBriefView } = await import('../src/components/ModelingBriefView.tsx')
const { factSourceText } = await import('../src/sourceFacts.ts')
const { EngineTaskWorkspace } = await import('../src/components/EngineTaskWorkspace.tsx')
const { ScientificTaskForm } = await import('../src/components/ScientificTaskForm.tsx')
const { KnowledgeSearchPanel } = await import('../src/components/KnowledgeSearchPanel.tsx')
const { RecoveryPanel } = await import('../src/components/RecoveryPanel.tsx')
const { HeatSimulationPanel } = await import('../src/components/HeatSimulationPanel.tsx')
const { api } = await import('../src/api.ts')
const { ResearchStatus } = await import('../src/components/ResearchStatus.tsx')
const { IntentConfirmation } = await import('../src/components/IntentConfirmation.tsx')
const { ExpertWorkflow } = await import('../src/components/ExpertWorkflow.tsx')
const originalApi = { ...api }
afterEach(() => { cleanup(); Object.assign(api, originalApi) })

test('intent confirmation sends only an explicit study choice and user-supplied conditions', () => {
  const submissions = []
  const intent = { goal: 'Heat transfer', physics: ['Heat Transfer in Solids'], geometry: [], study: [],
    boundary_conditions: [], unknowns: ['Study type'], validation_issues: [], original_text: 'Study a plate' }
  const props = { locale: 'en-US', intent, onContinue: (input) => submissions.push(input),
    onModify: () => {}, onReset: () => {} }
  const view = render(React.createElement(IntentConfirmation, props))
  assert.equal(screen.getAllByRole('radio').some((radio) => radio.checked), false)
  fireEvent.click(screen.getByRole('radio', { name: /Undecided/ }))
  fireEvent.change(screen.getByLabelText('Add known conditions with units'), { target: { value: '2 m x 1 m' } })
  fireEvent.click(screen.getByRole('button', { name: /Continue with draft/ }))
  assert.deepEqual(submissions, [{ study: 'undecided', details: '2 m x 1 m' }])
  view.rerender(React.createElement(IntentConfirmation, { ...props, intent: { ...intent, study: ['Stationary'] } }))
  assert.equal(screen.queryByRole('radio'), null)
})

test('expert workflow loads only a matching checkpoint and shows its dependencies on selection', async () => {
  const scope = { session_id: 's1', project_id: 'p1', case_id: 'c1', plan_revision: 2, run_id: 'r1', attempt_id: 'a1' }
  const budget = { agent_calls: 0, solver_runs: 0 }
  const node = { capability: 'review', objective: 'Check solver evidence', tools: ['evidence.read'],
    inputs: [], outputs: [], budget, state: 'waiting', waiting_for: ['execute'], dependencies: ['execute'] }
  const session = { id: 's1', project_id: 'p1', events: [
    { id: 'e1', sequence: 1, type: 'experts.checkpoint_created', payload: { scope } },
    { id: 'other', sequence: 2, type: 'experts.checkpoint_created', payload: { scope: { ...scope, project_id: 'p2' } } },
  ] }
  const requests = []
  api.expertWorkflow = async (...args) => {
    requests.push(args)
    return { scope, limit: budget, spent: budget, stop_reason: null, status: 'ACTIVE', tasks: [
      { ...node, id: 'audit' }, { ...node, id: 'execute', dependencies: [], waiting_for: [], state: 'running', capability: 'execution' },
    ] }
  }
  render(React.createElement(ExpertWorkflow, { session, locale: 'en-US' }))
  fireEvent.click(await screen.findByRole('button', { name: /audit/ }))
  assert.deepEqual(requests, [['s1', 'c1']])
  assert.equal(screen.getByText('Check solver evidence').tagName, 'DD')
  assert.equal(screen.getByText('evidence.read').tagName, 'DD')
  assert.equal(screen.getByRole('button', { name: /execute/ }).getAttribute('aria-pressed'), 'false')
})

test('research discussion displays actual usage and keeps scientific certification separate', async () => {
  api.researchStudy = async () => ({ status: 'NEEDS_EVIDENCE', calls: [{ task_id: 'physical_modeling', status: 'COMPLETED',
    receipt: { requests: 1, usage_known: true, input_tokens: 100, output_tokens: 50, model: 'fixture-model' } }],
    outcome: { scientific_status: 'NOT_RUN', issues: [{ kind: 'boundary_conflict', required: 'Resolve source applicability' }] } })
  const exports = []
  api.downloadResearchReport = async sid => { exports.push(sid) }
  const view = render(React.createElement(ResearchStatus, { sessionId: 'research-session', eventCount: 1, locale: 'en-US' }))
  await waitFor(() => assert.match(view.container.textContent, /Model requests: 1.*Token: 150/))
  assert.match(view.container.textContent, /does not certify/)
  assert.match(view.container.textContent, /Resolve source applicability/)
  fireEvent.click(screen.getByRole('button', { name: 'Export research report' }))
  assert.deepEqual(exports, ['research-session'])
})

test('completed plan display preserves assumptions and identifies historical execution', () => {
  const plan = { title: 'Reviewed heat', research_goal: 'heat', parameters: {}, missing_information: [],
    assumptions: ['Constant k'], warnings: [], physics: ['Heat'], geometry: [], materials: [],
    boundary_conditions: [], runnable: true }
  const view = render(React.createElement(WarmPlanOverview, { plan, locale: 'zh-CN', historical: true }))
  assert.match(view.container.textContent, /历史计划 · 本次运行已结束/)
  assert.doesNotMatch(view.container.textContent, /等待显式 RUN/)
  assert.match(view.container.textContent, /Constant k/)
  view.rerender(React.createElement(WarmPlanOverview, { plan, locale: 'zh-CN', historical: false }))
  assert.match(view.container.textContent, /等待显式 RUN/)
})

test('VASP WSL submits local potential references only after input review and explicit consent', async () => {
  api.engineConfiguration = async () => ({ executables: {} })
  api.engineRuns = async () => ({ runs: [] })
  api.vaspWslConfiguration = async () => ({ profile: null })
  api.vaspSiliconDraft = async () => ({ files: { INCAR: 'ENCUT=400', POSCAR: 'Si', KPOINTS: 'Gamma' } })
  api.previewEngineTask = async payload => {
    assert.equal(payload.inputs.kind, 'vasp_wsl')
    assert.deepEqual(Object.keys(payload.inputs.files).sort(), ['INCAR', 'KPOINTS', 'POSCAR'])
    assert.equal(payload.review_ticket, undefined)
    return { files: {}, structure: null, review_ticket: 'fixture-review', wsl_review: {
      variant: 'vasp_std', vasp_version: '6.4.3', ranks: 2, potential_sha256: 'a'.repeat(64), input_hashes: {},
    } }
  }
  let submitted = 0
  const saved = { run_id: 'fixture', engine: 'VASP', state: 'COLLECTED', created_at: 1,
    scientific_validation: 'NEEDS_REVIEW', assumptions: [], artifacts: [], request: { inputs: {} }, elapsed_ms: 3 }
  api.startEngineTask = async payload => {
    assert.equal(payload.review_ticket, 'fixture-review')
    assert.equal(payload.confirmed, true)
    submitted++
    return saved
  }
  api.engineRun = async () => saved
  render(React.createElement(EngineTaskWorkspace, { engine: 'VASP', projectId: 'project', locale: 'zh-CN' }))
  assert.equal(screen.getByRole('button', { name: '开始计算' }).disabled, true)
  assert.equal(screen.queryByLabelText(/^POTCAR/), null)
  fireEvent.click(screen.getByRole('button', { name: '生成 Si 静态 SCF 草案' }))
  assert.ok(await screen.findByText('INCAR ✓'))
  fireEvent.click(screen.getByRole('button', { name: '预览结构与检查输入' }))
  const consent = screen.getByRole('checkbox', { name: /授权本机运行/ })
  await waitFor(() => assert.equal(consent.disabled, false))
  assert.equal(submitted, 0)
  fireEvent.click(consent)
  fireEvent.click(screen.getByRole('button', { name: '开始计算' }))
  await waitFor(() => assert.equal(submitted, 1))
})

test('Heat simulation starts explicitly and distinguishes a perfect score from a blocked physical check', async () => {
  let run = { mode: 'simulation', scientific_validation_passed: false, state: 'IDLE', can_start: true }
  api.heatSimulation = async () => run
  api.heatSimulationAction = async (sid, action, payload) => {
    assert.equal(sid, 'empty-session')
    assert.equal(action, 'start')
    assert.deepEqual(payload, { scenario: 'wrong_boundary' })
    run = { ...run, state: 'BLOCKED', version: 2, can_start: false, report: {
      score_total: 100, revision: null,
      evidence: [{ evidence_id: 'readback-id', source_ref: 'heat.model_readback.v1' }],
      claims: [{ claim: { claim_id: 'boundary_selection', evidence_refs: ['readback-id'] }, status: 'FAIL' }],
    } }
    return run
  }
  render(React.createElement(HeatSimulationPanel, { locale: 'zh-CN', sessionId: 'empty-session' }))
  fireEvent.change(await screen.findByLabelText('验收场景'), { target: { value: 'wrong_boundary' } })
  fireEvent.click(screen.getByRole('button', { name: '启动模拟' }))
  assert.ok(await screen.findByText('物理检查失败，交付已阻断'))
  assert.ok(screen.getByText('模拟审核分数 100/100'))
  assert.ok(screen.getByText('边界选择 · FAIL'))
  assert.equal(screen.queryByText('模拟验收通过'), null)
})

test('Heat simulation waits for the matching revision approval before showing a simulated pass', async () => {
  let run = { mode: 'simulation', scientific_validation_passed: false, state: 'REVISION_REQUIRED',
    version: 3, can_start: false, report: { score_total: 94, evidence: [], claims: [],
      revision: { revision_id: 'review-1', issues: [{ issue_id: 'resample', reason: '重新取样' }] } } }
  let approvals = 0
  api.heatSimulation = async () => run
  api.heatSimulationAction = async (_sid, action, payload) => {
    approvals++
    assert.equal(action, 'approve')
    assert.deepEqual(payload, { version: 3, proposal_id: 'review-1' })
    run = { ...run, state: 'PASSED', version: 5, report: { score_total: 100, claims: [], evidence: [], revision: null } }
    return run
  }
  render(React.createElement(HeatSimulationPanel, { locale: 'zh-CN', sessionId: 'revision-session' }))
  assert.ok(await screen.findByText('等待批准模拟返工'))
  assert.equal(approvals, 0)
  fireEvent.click(screen.getByRole('button', { name: '批准模拟返工并复验' }))
  assert.ok(await screen.findByText('模拟验收通过'))
  assert.equal(approvals, 1)
})

test('recovery keeps simulation approval explicit and labels simulated verification', async () => {
  const failure = { event_id: 'case', origin: 'API', route_template: '/api/sessions/{session_id}/plan',
    method: 'PUT', occurred_at_utc: '2026-11-06T01:00:00Z', http_status: 409, error_code: 'plan_revision' }
  const item = { case_id: 'case', state: 'PROPOSED', failure }
  const start = { ...item, report: { rule: { classification: 'PLAN_REVISION_CONFLICT',
    safe_next_probe: '刷新计划', counter_evidence: [] }, missing_evidence: [], evidence_refs: ['case'],
    knowledge_refs: [], candidates: [], model_status: 'NOT_NEEDED' },
  proposal: { action: 'SIMULATE_STATUS_REFRESH', digest: 'a'.repeat(64), approval_required: true,
    verification_plan: [] }, review_decision: null, events: [] }
  let record = start
  const calls = []
  api.recoveryCases = async () => ({ cases: [item], capture_drops: 0 })
  api.recoveryCase = async () => record
  api.recoveryAction = async (session, id, action, useModel, digest) => {
    calls.push({ session, id, action, useModel, digest })
    if (action === 'submit') record = { ...record, state: 'WAITING_APPROVAL' }
    if (action === 'approve') record = { ...record, review_decision: 'approve' }
    if (action === 'simulate') record = { ...record, state: 'RESOLVED' }
    return record
  }
  render(React.createElement(RecoveryPanel, { locale: 'zh-CN', sessionId: 'session', eventCount: 1 }))
  fireEvent.click(await screen.findByRole('button', { name: /409/ }))
  assert.equal(screen.queryByRole('button', { name: '运行模拟并独立复验' }), null)
  fireEvent.click(await screen.findByRole('button', { name: '提交模拟审批' }))
  fireEvent.click(await screen.findByRole('button', { name: '批准这次模拟' }))
  fireEvent.click(await screen.findByRole('button', { name: '运行模拟并独立复验' }))
  assert.ok(await screen.findByText(/模拟复验通过；实际故障仍未确认修复/))
  assert.deepEqual(calls.map(call => call.action), ['submit', 'approve', 'simulate'])
  assert.equal(calls[1].digest, 'a'.repeat(64))
  assert.equal(calls[1].useModel, false)
})

test('recovery cancellation preserves the visible state when a diagnosis responds late', async () => {
  const failure = { event_id: 'case', origin: 'API', occurred_at_utc: '2026-11-06T01:00:00Z',
    http_status: 409, error_code: 'unknown' }
  const start = { case_id: 'case', state: 'DETECTED', failure, report: null, proposal: null, events: [] }
  let current = start
  let finishDiagnosis
  api.recoveryCases = async () => ({ cases: [start], capture_drops: 0 })
  api.recoveryCase = async () => current
  api.recoveryAction = async (_session, _id, action) => {
    if (action === 'diagnose') return new Promise(resolve => { finishDiagnosis = resolve })
    assert.equal(action, 'cancel')
    current = { ...start, state: 'CANCELLED' }
    return current
  }

  render(React.createElement(RecoveryPanel, { locale: 'zh-CN', sessionId: 'session', eventCount: 1 }))
  fireEvent.click(await screen.findByRole('button', { name: /409/ }))
  fireEvent.click(await screen.findByRole('button', { name: '开始只读诊断' }))
  fireEvent.click(await screen.findByRole('button', { name: '停止当前诊断或模拟' }))
  assert.ok(await screen.findByText(/CANCELLED/))
  await act(async () => finishDiagnosis({ ...start, state: 'PROPOSED' }))
  assert.ok(screen.getByText(/CANCELLED/))
  assert.equal(screen.queryByText(/PROPOSED/), null)
})

function plan() {
  return { schema_version: '2.0', plan_id: 'plan-fixture', revision: 3, title: 'Heat draft', research_goal: 'Heat',
    physics: [], geometry: [], materials: [], parameters: {}, boundary_conditions: [], mesh_strategy: [],
    study: [], solver: [], validation: [], expected_outputs: [], assumptions: [], missing_information: [],
    sources: [], steps: [], warnings: [], runnable: false, quality_status: 'INVALID',
    completion: { proposal_id: 'proposal-fixture', reviewed_digest: 'a'.repeat(64), source: 'ai', reason: 'Proposed geometry',
      applicability: 'Exploratory only', remaining_missing: ['Required decision: solver'], decisions: {},
      parameters: { width: { value: 0.1, unit: 'm' }, height: { value: 0.05, unit: 'm' } } } }
}

function review(onComplete, value = plan()) {
  return render(React.createElement(PlanCompletionReview, { key: value.completion.reviewed_digest, plan: value, zh: false, onComplete }))
}

test('fact spans use Unicode code points and reject a changed source binding', () => {
  const text = '🔥铁块 600℃'
  assert.equal(factSourceText(text, { original: '600℃', span: [4, 8] }, text), '600℃')
  assert.equal(factSourceText(text + ' 已修改', { original: '600℃', span: [4, 8] }, text), null)
})

test('model view keeps two bodies and their local conditions separate', () => {
  const value = plan()
  value.modeling_brief = { equations: [], symbolic_inputs: [], engine_candidates: [], exclusions: [],
    model_outline: {}, entities: [{ id: 'a', label: '铁块A', kind: 'body' }, { id: 'b', label: '铝块B', kind: 'body' }],
    relations: [{ kind: 'contact', entity_ids: ['a', 'b'], condition: 'contact_resistance_required' }],
    facts: [{ entity_id: 'a', original: '600℃', role: 'initial_temperature' }, { entity_id: 'b', original: '25℃', role: 'initial_temperature' }] }
  render(React.createElement(ModelingBriefView, { plan: value, zh: true }))
  assert.ok(screen.getByText(/铁块A ↔ 铝块B/))
  assert.ok(screen.getByText(/600℃ · initial_temperature/))
  assert.ok(screen.getByText(/25℃ · initial_temperature/))
})

test('generic model choices are optional, reset consent and are sent with the reviewed digest', async () => {
  const value = plan()
  value.completion.parameters = {}
  value.completion.remaining_missing = []
  value.completion.questions = [{ key: 'parameter_policy', question: 'How should missing inputs be filled?',
    options: [{ id: 'propose', label: 'Ask AI to propose', consequence: 'New suggestions need another review' },
      { id: 'symbolic', label: 'Keep symbols', consequence: 'Build equations before choosing numbers' }] }]
  const calls = []
  review(async (body) => calls.push(body), value)
  const consent = screen.getByRole('checkbox', { name: /I reviewed/ })
  fireEvent.click(consent)
  fireEvent.click(screen.getByRole('radio', { name: /Ask AI to propose/ }))
  assert.equal(consent.checked, false)
  fireEvent.click(consent)
  fireEvent.click(screen.getByRole('button', { name: 'Save choices and revalidate plan' }))
  await waitFor(() => assert.equal(calls.length, 1))
  assert.deepEqual(calls[0].question_answers, { parameter_policy: 'propose' })
  assert.equal(calls[0].reviewed_digest, value.completion.reviewed_digest)
})

test('model outline displays symbols and unavailable-service status without claiming engine readiness', () => {
  const value = plan()
  value.modeling_brief = { equations: ['rho*Cp*dT/dt = div(k*grad(T)) + Q'], facts: [],
    symbolic_inputs: [{ symbol: 'k', unit: 'W/(m*K)', purpose: 'Material relation pending' }],
    engine_candidates: [{ engine: 'COMSOL', role: 'Continuum simulation', execution_status: 'NOT_ASSESSED' }],
    exclusions: ['radiation'], model_outline: { refinement_status: 'NEEDS_MODEL_SERVICE' } }
  render(React.createElement(ModelingBriefView, { plan: value, zh: false }))
  assert.ok(screen.getByText(/No model service is connected/))
  assert.ok(screen.getByText(/k \[W\/\(m\*K\)\]/))
  assert.ok(screen.getByText(/task preflight pending/))
  assert.equal(screen.queryByText('Ready to run'), null)
})

test('iron cooling review retains its own explanation and temperature labels', () => {
  const value = plan()
  value.workflow_id = 'heat_transfer_3d_cooling'
  value.completion.source = 'example'
  value.completion.reason = '空气中三维铁块冷却，初温需要审核'
  value.completion.parameters = { initial_temperature: { value: 1073.15, unit: 'K' } }
  render(React.createElement(PlanCompletionReview, { plan: value, zh: true, onComplete: async () => {} }))
  assert.ok(screen.getByText(value.completion.reason))
  assert.ok(screen.getByRole('checkbox', { name: '初始温度' }))
  assert.equal(screen.queryByText(/二维稳态铝板/), null)
})

test('review is explicit and sends the displayed proposal digest, revision and edited value', async () => {
  const calls = []
  review(async (body) => calls.push(body))
  const button = screen.getByRole('button', { name: 'Save choices and revalidate plan' })
  assert.equal(button.disabled, true)
  fireEvent.change(screen.getByRole('textbox', { name: 'width' }), { target: { value: '0.2' } })
  fireEvent.change(screen.getByRole('textbox', { name: 'solver' }), { target: { value: 'Stationary direct solver' } })
  fireEvent.click(screen.getByRole('checkbox', { name: /I reviewed/ }))
  fireEvent.click(button)
  await waitFor(() => assert.equal(calls.length, 1))
  assert.equal(calls[0].reviewed_digest, 'a'.repeat(64))
  assert.equal(calls[0].expected_revision, 3)
  assert.equal(calls[0].values.width, '0.2')
  assert.deepEqual(calls[0].decisions.solver, ['Stationary direct solver'])
})

test('scope authorization keeps proposed values read-only and has its own explicit acknowledgement', async () => {
  const calls = []
  review(async (body) => calls.push(body))
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'auto' } })
  assert.equal(screen.getByRole('textbox', { name: 'width' }).readOnly, true)
  fireEvent.click(screen.getByRole('checkbox', { name: /I authorize/ }))
  fireEvent.click(screen.getByRole('button', { name: 'Save choices and revalidate plan' }))
  await waitFor(() => assert.equal(calls.length, 1))
  assert.equal(calls[0].mode, 'auto')
  assert.deepEqual(calls[0].values, {})
})

test('manual blank or nonnumeric entries show a useful error without sending a request', async () => {
  const calls = []
  review(async (body) => calls.push(body))
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'manual' } })
  fireEvent.click(screen.getByRole('checkbox', { name: /I reviewed/ }))
  fireEvent.click(screen.getByRole('button', { name: 'Save choices and revalidate plan' }))
  assert.match(screen.getByRole('alert').textContent, /Enter each selected value/)
  fireEvent.change(screen.getByRole('textbox', { name: 'width' }), { target: { value: 'abc' } })
  fireEvent.change(screen.getByRole('textbox', { name: 'height' }), { target: { value: '1' } })
  fireEvent.click(screen.getByRole('checkbox', { name: /I reviewed/ }))
  fireEvent.click(screen.getByRole('button', { name: 'Save choices and revalidate plan' }))
  assert.match(screen.getByRole('alert').textContent, /finite numbers/)
  assert.equal(calls.length, 0)
})

test('partial acceptance submits only selected values and keeps remaining conditions visible', async () => {
  const calls = []
  review(async (body) => calls.push(body))
  fireEvent.click(screen.getByRole('checkbox', { name: 'height' }))
  fireEvent.click(screen.getByRole('checkbox', { name: /I reviewed/ }))
  fireEvent.click(screen.getByRole('button', { name: 'Save choices and revalidate plan' }))
  await waitFor(() => assert.equal(calls.length, 1))
  assert.deepEqual(calls[0].selected_parameters, ['width'])
  assert.ok(screen.getByText('Required decision: solver'))
})

test('a changed proposal clears local approval and stale input', () => {
  const view = review(async () => {})
  fireEvent.change(screen.getByRole('textbox', { name: 'width' }), { target: { value: '3' } })
  fireEvent.click(screen.getByRole('checkbox', { name: /I reviewed/ }))
  const next = plan(); next.completion.reviewed_digest = 'b'.repeat(64); next.completion.parameters.width.value = 4
  view.rerender(React.createElement(PlanCompletionReview, { key: next.completion.reviewed_digest, plan: next, zh: false, onComplete: async () => {} }))
  assert.equal(screen.getByRole('checkbox', { name: /I reviewed/ }).checked, false)
  assert.equal(screen.getByRole('textbox', { name: 'width' }).value, '4')
})

test('full plan disclosure does not duplicate the completion panel', () => {
  const value = plan()
  render(React.createElement(React.Fragment, null,
    React.createElement(PlanCard, { plan: value, locale: 'en-US' }),
    React.createElement(PlanCompletionReview, { plan: value, zh: false, onComplete: async () => {} })))
  assert.equal(screen.getAllByRole('region', { name: 'Complete missing conditions' }).length, 1)
})

function engineApi() {
  api.engineConfiguration = async () => ({ executables: {} })
  api.engineRuns = async () => ({ runs: [] })
}

test('changing engine resets consent before any execution request', async () => {
  engineApi()
  const calls = []
  api.startEngineTask = async (payload) => { calls.push(payload); throw new Error('fixture') }
  const view = render(React.createElement(EngineTaskWorkspace, { engine: 'LAMMPS', projectId: 'workbench', locale: 'en-US' }))
  const consent = screen.getByRole('checkbox', { name: /Accept parameters/ })
  fireEvent.click(consent)
  assert.equal(consent.checked, true)
  view.rerender(React.createElement(EngineTaskWorkspace, { engine: 'VASP', projectId: 'workbench', locale: 'en-US' }))
  await waitFor(() => assert.equal(screen.getByRole('checkbox', { name: /Accept parameters/ }).checked, false))
  fireEvent.click(screen.getByRole('button', { name: 'Start calculation' }))
  assert.equal(calls.length, 0)
})

test('Materials Studio coordinate imports accept blank lines and trailing newlines', async () => {
  engineApi()
  const calls = []
  api.previewEngineTask = async (payload) => { calls.push(payload); return { files: {}, structure: null } }
  render(React.createElement(EngineTaskWorkspace, { engine: 'Materials Studio', projectId: 'workbench', locale: 'en-US' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Atom coordinates' }), { target: { value: 'O 0 0 0\n\nH 1 0 0\n' } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview structure and inputs' }))
  await waitFor(() => assert.equal(calls.length, 1))
  assert.equal(calls[0].inputs.atoms.length, 2)
})

test('MATLAB invalid parameters are visible and the generated template checks boundaries and analytical error', () => {
  const calls = []
  render(React.createElement(ScientificTaskForm, { engine: 'MATLAB', zh: false, onPrepare: (body) => calls.push(body) }))
  const width = screen.getByRole('spinbutton', { name: /width/ })
  fireEvent.change(width, { target: { value: '0' } })
  fireEvent.click(screen.getByRole('button', { name: 'Prepare model' }))
  assert.match(screen.getByRole('alert').textContent, /finite and positive/)
  assert.equal(calls.length, 0)
  fireEvent.change(width, { target: { value: '0.1' } })
  fireEvent.click(screen.getByRole('button', { name: 'Prepare model' }))
  assert.equal(calls.length, 1)
  assert.ok(calls[0].includes('nearestEdge'))
  assert.ok(calls[0].includes('relativeError <= 0.01'))
})

test('knowledge search sends engine and version and explains missing evidence without blocking modeling', async () => {
  const calls=[]
  api.searchKnowledge=async (...args)=>{calls.push(args);return {results:[],evidence_status:'NO_EVIDENCE'}}
  render(React.createElement(KnowledgeSearchPanel,{zh:false}))
  fireEvent.change(screen.getByRole('combobox',{name:'Simulation engine'}),{target:{value:'LAMMPS'}})
  fireEvent.change(screen.getByRole('textbox',{name:'Applicable version (optional)'}),{target:{value:'22Jul2025-update4'}})
  fireEvent.change(screen.getByRole('textbox',{name:'Question or command'}),{target:{value:'compute msd'}})
  fireEvent.click(screen.getByRole('button',{name:'Find sources'}))
  await waitFor(()=>assert.equal(calls.length,1))
  assert.deepEqual(calls[0],['compute msd',{engine:'LAMMPS',engine_version:'22Jul2025-update4'}])
  await waitFor(()=>assert.match(screen.getByRole('status').textContent,/review proposed assumptions/))
})

test('project note import supplies provenance without claiming public rights or verification', async () => {
  const calls=[]
  api.importKnowledgeUnits=async (units)=>{calls.push(units);return {imported_units:1}}
  render(React.createElement(KnowledgeSearchPanel,{zh:false}))
  fireEvent.change(screen.getByRole('combobox',{name:'Simulation engine'}),{target:{value:'VASP'}})
  fireEvent.change(screen.getByRole('textbox',{name:'Title'}),{target:{value:'SCF observation'}})
  fireEvent.change(screen.getByRole('textbox',{name:'Source URL or local location'}),{target:{value:'https://vasp.at/wiki/EDIFF'}})
  fireEvent.change(screen.getByRole('textbox',{name:'Source text'}),{target:{value:'Electronic convergence observation'}})
  fireEvent.click(screen.getByRole('button',{name:'Save project note'}))
  await waitFor(()=>assert.equal(calls.length,1))
  assert.equal(calls[0][0].metadata.engine,'VASP')
  assert.equal(calls[0][0].metadata.verification_status,undefined)
  assert.equal(calls[0][0].metadata.license_class,undefined)
  await waitFor(()=>assert.match(screen.getByRole('status').textContent,/evidence remains unverified/))
})

test('run status shows scientific review separately and offers verified artifact download', async () => {
  engineApi()
  const run={run_id:'fixture-run',engine:'LAMMPS',project_id:'workbench',state:'COLLECTED',created_at:1,
    title:'LJ run',verification_status:'EXERCISED',scientific_validation:'NEEDS_REVIEW',elapsed_ms:10,
    assumptions:[],request:{inputs:{}},output:'',result:null,
    artifacts:[{name:'log.lammps',size_bytes:10,sha256:'a'.repeat(64)}]}
  api.engineRuns=async()=>({runs:[run]})
  api.engineRun=async()=>run
  const calls=[]
  api.downloadEngineArtifact=async(...args)=>calls.push(args)
  render(React.createElement(EngineTaskWorkspace,{engine:'LAMMPS',projectId:'workbench',locale:'en-US'}))
  await waitFor(()=>assert.match(screen.getByRole('status').textContent,/EXERCISED.*NEEDS_REVIEW/))
  fireEvent.click(screen.getByText('Run parameters, assumptions and log'))
  fireEvent.click(screen.getByRole('button',{name:'log.lammps'}))
  await waitFor(()=>assert.deepEqual(calls,[['fixture-run','log.lammps']]))
})
