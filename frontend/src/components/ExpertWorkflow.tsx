import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Locale } from '../i18n'
import { safeUiError } from '../safeUiError'
import type { Session } from '../types'

type Budget = { agent_calls: number; solver_runs: number }
type Task = {
  id: string; capability: string; objective: string; state: string; dependencies: string[]
  waiting_for: string[]; tools: string[]; inputs: string[]; outputs: string[]; budget: Budget
}
export type Workflow = {
  scope: { project_id: string; session_id: string; case_id: string; plan_revision: number; run_id: string; attempt_id: string }
  status: string; stop_reason: string | null; limit: Budget; spent: Budget; tasks: Task[]
}

export function expertCases(session: Session) {
  const cases = new Map<string, { id: string; revision: number; sequence: number }>()
  for (const event of session.events ?? []) {
    if (!event.type.startsWith('experts.')) continue
    const scope = event.payload.scope as Workflow['scope'] | undefined
    if (scope?.session_id !== session.id || scope.project_id !== session.project_id ||
        typeof scope.case_id !== 'string' || !Number.isInteger(scope.plan_revision)) continue
    const previous = cases.get(scope.case_id)
    if (!previous || event.sequence > previous.sequence) {
      cases.set(scope.case_id, { id: scope.case_id, revision: scope.plan_revision, sequence: event.sequence })
    }
  }
  return [...cases.values()].sort((a, b) => b.sequence - a.sequence)
}

function placeTasks(tasks: Task[]) {
  const positions = new Map<string, { task: Task; depth: number; x: number; y: number }>()
  const nextRows = new Map<number, number>()
  let remaining = tasks
  while (remaining.length) {
    const available = remaining.filter((task) => task.dependencies.every((dependency) => positions.has(dependency)))
    if (!available.length) return null
    for (const task of available) {
      const depth = Math.max(0, ...task.dependencies.map((dependency) => positions.get(dependency)!.depth + 1))
      const row = nextRows.get(depth) ?? 0
      positions.set(task.id, { task, depth, x: 16 + 212 * depth, y: 14 + 81 * row })
      nextRows.set(depth, row + 1)
    }
    const placed = new Set(available.map((task) => task.id))
    remaining = remaining.filter((task) => !placed.has(task.id))
  }
  return positions
}

export function ExpertWorkflow({ session, locale }: { session: Session; locale: Locale }) {
  const zh = locale === 'zh-CN'
  const cases = expertCases(session)
  const [selected, select] = useState('')
  const [node, setNode] = useState('')
  const [zoom, setZoom] = useState(1)
  const [result, setResult] = useState<Workflow | null>(null)
  const [error, setError] = useState('')
  const selectedCase = cases.find((item) => item.id === selected) ?? cases[0]
  const caseId = selectedCase?.id
  const sequence = selectedCase?.sequence
  useEffect(() => {
    if (!caseId) return
    let active = true
    setError('')
    void api.expertWorkflow(session.id, caseId).then((workflow) => {
      if (active && workflow.scope.project_id === session.project_id && workflow.scope.session_id === session.id &&
          workflow.scope.case_id === caseId) setResult(workflow)
    }).catch((cause) => { if (active) setError(safeUiError(cause)) })
    return () => { active = false }
  }, [session.project_id, session.id, caseId, sequence])
  const workflow = result?.scope.case_id === caseId ? result : null
  const positions = workflow && placeTasks(workflow.tasks)
  const locations = positions ? [...positions.values()] : []
  const detail = positions ? positions.get(node)?.task : undefined
  const labels: Record<string, string> = zh ? {
    verified: '已收齐证据', stopped: '已停止', running: '执行中', ready: '可调度', waiting: '等待依赖',
    execution: '真实执行', acquisition: '证据读回', review: '独立审核', challenge: '独立质疑', inspection: '检查',
  } : {}
  const changes = (session.events ?? []).filter((event) => event.type === 'experts.graph_revised' &&
    (event.payload.scope as Workflow['scope'] | undefined)?.case_id === caseId)
  if (!caseId) return null
  return <section className="warm-workflow" aria-label={zh ? '专家工作流' : 'Expert workflow'}>
    <div className="warm-workflow-controls">
      <label>{zh ? '专家工作流' : 'Expert workflow'} <select value={caseId}
        onChange={(event) => { select(event.target.value); setNode('') }}>
        {cases.map((item, index) => <option key={item.id} value={item.id}>
          {zh ? '计划修订' : 'Plan revision'} {item.revision} · {index === 0 ? (zh ? '最近' : 'Latest') : item.id.slice(0, 8)}
        </option>)}
      </select></label>
      <label>{zh ? '缩放' : 'Zoom'} <input type="range" min="0.65" max="1.4" step="0.05"
        value={zoom} onChange={(event) => setZoom(Number(event.target.value))} /></label>
    </div>
    {error ? <p role="alert">{error}</p> : !workflow ? <p role="status">{zh ? '正在读取任务图…' : 'Loading workflow…'}</p>
      : !positions ? <p role="status">{zh ? '任务依赖无法显示。' : 'Task dependencies are unavailable.'}</p>
      : <div className="warm-workflow-viewport" tabIndex={0}>
        <div className="warm-workflow-map" style={{ zoom, width: Math.max(250, ...locations.map((item) => item.x + 202)),
          height: Math.max(90, ...locations.map((item) => item.y + 67)) }}>
          <svg width="100%" height="100%" aria-hidden="true">{locations.flatMap((item) => item.task.dependencies.map((dependency) => {
            const source = positions.get(dependency)!
            const end = item.x
            const start = source.x + 184
            const mid = (start + end) / 2
            return <path key={`${dependency}:${item.task.id}`}
              d={`M ${start} ${source.y + 27} H ${mid} V ${item.y + 27} H ${end}`} />
          }))}</svg>
          {locations.map(({ task, x, y }) => <button className="warm-workflow-node" key={task.id} type="button"
            style={{ left: x, top: y }} aria-pressed={node === task.id} onClick={() => setNode(task.id)}>
            <strong>{task.id}</strong><span>{labels[task.capability] ?? task.capability} · {labels[task.state] ?? task.state}</span>
          </button>)}
        </div>
      </div>}
    {workflow?.stop_reason && <p role="status">{zh ? '停止原因' : 'Stop reason'} · {workflow.stop_reason}</p>}
    {detail && <dl className="warm-fact-list">
      <div><dt>{zh ? '职责' : 'Objective'}</dt><dd>{detail.objective}</dd></div>
      <div><dt>{zh ? '依赖' : 'Dependencies'}</dt><dd>{detail.dependencies.join(' · ') || '—'}</dd></div>
      <div><dt>{zh ? '等待' : 'Waiting for'}</dt><dd>{detail.waiting_for.join(' · ') || '—'}</dd></div>
      <div><dt>{zh ? '允许工具' : 'Allowed tools'}</dt><dd>{detail.tools.join(' · ') || '—'}</dd></div>
      <div><dt>{zh ? '调用与求解预算' : 'Call and solve budget'}</dt><dd>{detail.budget.agent_calls} / {detail.budget.solver_runs}</dd></div>
      <div><dt>{zh ? '输入证据' : 'Input evidence'}</dt><dd>{detail.inputs.join(' · ') || '—'}</dd></div>
      <div><dt>{zh ? '输出证据' : 'Output evidence'}</dt><dd>{detail.outputs.join(' · ') || '—'}</dd></div>
    </dl>}
    {!!changes.length && <details className="warm-history-detail"><summary>{zh ? '任务图修订' : 'Graph revisions'}</summary>
      <ul>{changes.map((change) => <li key={change.id}>{String(change.payload.reason ?? '')}
        {Array.isArray(change.payload.cancelled_tasks) && change.payload.cancelled_tasks.length > 0 &&
          <small> · {zh ? '取消' : 'Cancelled'} {change.payload.cancelled_tasks.join(', ')}</small>}</li>)}</ul>
    </details>}
  </section>
}
