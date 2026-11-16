import { useEffect, useState } from 'react'
import { api } from '../api'
import type { HeatScenario, HeatSimulation } from '../heatSimulationTypes'
import type { Locale } from '../i18n'

const scenarios: Array<[HeatScenario, string, string]> = [
  ['normal', '正常基准', 'Normal benchmark'],
  ['wrong_boundary', '求解返回成功但边界错误', 'Successful return with a wrong boundary'],
  ['missing_evidence', '缺少场读回', 'Missing field readback'],
  ['interrupted', '可恢复中断', 'Recoverable interruption'],
  ['revision', '返工与复验', 'Revision and verification'],
  ['unknown_submission', '求解回执未知', 'Unknown solver submission'],
]

const states: Record<HeatSimulation['state'], [string, string]> = {
  IDLE: ['准备模拟', 'Ready to simulate'],
  RUNNING: ['模拟进行中', 'Simulation in progress'],
  PAUSED: ['已到达安全检查点', 'At a safe checkpoint'],
  INTERRUPTED: ['模拟中断，等待恢复', 'Simulation interrupted'],
  PASSED: ['模拟验收通过', 'Simulation checks passed'],
  BLOCKED: ['物理检查失败，交付已阻断', 'Delivery blocked by a physical check'],
  UNVERIFIED: ['证据不足，无法验证', 'Insufficient evidence'],
  REVISION_REQUIRED: ['等待批准模拟返工', 'Simulation revision awaits approval'],
  CANCELLED: ['模拟已取消', 'Simulation cancelled'],
  STOPPED: ['模拟已停止', 'Simulation stopped'],
}

const tasks: Record<string, [string, string]> = {
  research: ['资料与基准', 'Reference and benchmark'], build: ['建模', 'Build'],
  solve: ['模拟求解', 'Simulate solve'], validate: ['独立审核', 'Independent review'],
  export: ['报告整理', 'Prepare report'],
}

const taskStates: Record<NonNullable<HeatSimulation['tasks']>[number]['state'], [string, string]> = {
  VERIFIED: ['已记录', 'Recorded'], IN_FLIGHT: ['已派发', 'Dispatched'], PENDING: ['待派发', 'Pending'],
  NOT_DISPATCHED: ['未派发', 'Not dispatched'],
}

const checks: Record<string, [string, string]> = {
  model_geometry: ['矩形几何', 'Rectangular geometry'], material_and_source: ['导热率与热源', 'Conductivity and heat source'],
  boundary_selection: ['边界选择', 'Boundary selection'], temperature: ['九点温度', 'Nine-point temperature'],
  heat_flux: ['热流', 'Heat flux'], execution: ['模拟执行判定', 'Simulated execution verdict'],
}

export function HeatSimulationPanel({ locale, sessionId }: { locale: Locale; sessionId?: string }) {
  const text = (zh: string, en: string) => locale === 'zh-CN' ? zh : en
  const [scenario, setScenario] = useState<HeatScenario>('normal')
  const [run, setRun] = useState<HeatSimulation | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)
  const [refresh, setRefresh] = useState(0)

  useEffect(() => {
    if (!sessionId || busy) return
    const id = sessionId
    const controller = new AbortController()
    let timer: number | undefined
    async function load() {
      try {
        const next = await api.heatSimulation(id, controller.signal)
        if (controller.signal.aborted) return
        setRun(current => (current?.version ?? 0) > (next.version ?? 0) ? current : next)
        if (next.state === 'RUNNING') timer = window.setTimeout(() => void load(), 300)
      } catch {
        if (!controller.signal.aborted) setError(true)
      }
    }
    void load()
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [sessionId, busy, refresh])

  async function act(action: 'start' | 'cancel' | 'resume' | 'approve') {
    if (!sessionId || !run || busy) return
    if (action !== 'start' && run.version === undefined) return
    setBusy(true)
    setError(false)
    try {
      const payload = action === 'start' ? { scenario }
        : action === 'approve' ? { version: run.version, proposal_id: run.report?.revision?.revision_id }
          : { version: run.version }
      setRun(await api.heatSimulationAction(sessionId, action, payload))
    } catch {
      setError(true)
    } finally {
      setBusy(false)
    }
  }

  const report = run && ['PASSED', 'BLOCKED', 'UNVERIFIED', 'REVISION_REQUIRED'].includes(run.state) ? run.report : null
  return <section className="inspector-body" aria-label={text('模拟 Heat', 'Heat simulation')}>
    <h3>{text('模拟 Heat', 'Heat simulation')}</h3>
    <p>{text('二维稳态基准。所有求解、证据和审核均来自本地模拟。', '2D steady-state benchmark. All solver output, evidence and review come from a local simulation.')}</p>
    {!sessionId && <p>{text('先在当前项目新建任务。', 'Create a task in the current project first.')}</p>}
    {error && <p role="alert">{text('请求未完成，请刷新查看当前状态。', 'Request did not complete. Refresh for the current state.')}</p>}
    {sessionId && <button type="button" disabled={busy} onClick={() => { setError(false); setRefresh(value => value + 1) }}>
      {text('刷新模拟状态', 'Refresh simulation status')}
    </button>}
    {run?.state === 'IDLE' && (run.can_start
      ? <>
        <p>{text('启动后，此任务将专用于模拟。', 'Starting a simulation makes this a dedicated simulation task.')}</p>
        <label>{text('验收场景', 'Test scenario')} <select value={scenario} disabled={busy}
          onChange={event => setScenario(event.target.value as HeatScenario)}>
          {scenarios.map(([id, zh, en]) => <option key={id} value={id}>{text(zh, en)}</option>)}
        </select></label>
        <button type="button" disabled={busy} onClick={() => void act('start')}>{text('启动模拟', 'Start simulation')}</button>
      </>
      : <p>{text('请新建空白任务来运行模拟。', 'Create an empty task to run the simulation.')}</p>)}
    {run && run.state !== 'IDLE' && <>
      <p role="status">{text(...states[run.state])}</p>
      {run.reason === 'submission_unknown' && <p>{text('求解回执未知，已停止并保留检查点。', 'Solver submission is unknown. The checkpoint has been preserved.')}</p>}
      {run.tasks?.length ? <ol aria-label={text('模拟阶段', 'Simulation stages')}>
        {run.tasks.map(item => <li key={item.id}>{tasks[item.id] ? text(...tasks[item.id]) : item.id}
          {' · '}{text(...taskStates[item.state])}</li>)}
      </ol> : null}
      {(run.state === 'PAUSED' || run.state === 'INTERRUPTED') &&
        <button type="button" disabled={busy} onClick={() => void act('resume')}>{text('恢复模拟', 'Resume simulation')}</button>}
      {['RUNNING', 'PAUSED', 'INTERRUPTED', 'REVISION_REQUIRED'].includes(run.state) &&
        <button type="button" disabled={busy} onClick={() => void act('cancel')}>{text('取消模拟', 'Cancel simulation')}</button>}
    </>}
    {report && <div>
      {report.score_total !== null && <p>{text('模拟审核分数', 'Simulated review score')} {report.score_total}/100</p>}
      <ul>{report.claims.map(item => <li key={item.claim.claim_id}>
        {checks[item.claim.claim_id] ? text(...checks[item.claim.claim_id]) : item.claim.statement} · {item.status}
        {item.claim.evidence_refs.length > 0 && <details><summary>{text('证据出处', 'Evidence references')}</summary>
          <ul>{item.claim.evidence_refs.map(ref => {
            const entry = report.evidence.find(record => record.evidence_id === ref)
            return <li key={ref}><code>{ref}</code>
              {entry && <div>{entry.source_ref}{entry.artifact && <> · <code>{entry.artifact.sha256}</code></>}</div>}
            </li>
          })}</ul>
        </details>}
      </li>)}</ul>
      {run?.state === 'REVISION_REQUIRED' && report.revision && <>
        <ul>{report.revision.issues.map(item => <li key={item.issue_id}>{item.reason}</li>)}</ul>
        <button type="button" disabled={busy} onClick={() => void act('approve')}>
          {text('批准模拟返工并复验', 'Approve simulation revision and verify')}
        </button>
      </>}
    </div>}
  </section>
}
