import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Locale } from '../i18n'
import type { RecoveryCase, RecoveryCaseSummary } from '../recoveryTypes'

type Action = 'diagnose' | 'submit' | 'approve' | 'reject' | 'simulate' | 'export' | 'cancel' | 'connection-check'
const stoppable = new Set(['SNAPSHOTTING', 'CLASSIFIED', 'DIAGNOSING', 'WAITING_APPROVAL', 'SIMULATING', 'VERIFYING'])

export function RecoveryPanel({ locale, sessionId, eventCount }: {
  locale: Locale; sessionId: string | undefined; eventCount: number
}) {
  const text = (zh: string, en: string) => locale === 'zh-CN' ? zh : en
  const [cases, setCases] = useState<RecoveryCaseSummary[]>([])
  const [selected, setSelected] = useState('')
  const [record, setRecord] = useState<RecoveryCase | null>(null)
  const [busy, setBusy] = useState<Action | null>(null)
  const operation = useRef(0)
  const [error, setError] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [drops, setDrops] = useState(0)
  const [useModel, setUseModel] = useState(false)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    if (!sessionId) return
    const abort = new AbortController()
    void api.recoveryCases(sessionId, abort.signal).then(result => {
      if (abort.signal.aborted) return
      setCases(result.cases)
      setDrops(result.capture_drops)
      setLoaded(true)
    }).catch(() => { if (!abort.signal.aborted) setError(true) })
    return () => abort.abort()
  }, [sessionId, eventCount, refresh])

  useEffect(() => {
    if (!sessionId || !selected) return
    const abort = new AbortController()
    void api.recoveryCase(sessionId, selected, abort.signal).then(value => {
      if (!abort.signal.aborted) setRecord(value)
    }).catch(() => { if (!abort.signal.aborted) setError(true) })
    return () => abort.abort()
  }, [sessionId, selected, refresh])

  async function act(action: Action) {
    if (!sessionId || !record || record.case_id !== selected || busy === 'cancel' || (busy && action !== 'cancel')) return
    const request = ++operation.current
    setBusy(action)
    setError(false)
    try {
      if (action === 'export') await api.downloadRecoveryCase(sessionId, selected)
      else {
        const next = await api.recoveryAction(sessionId, selected, action, useModel, record.proposal?.digest)
        if (request === operation.current) {
          setRecord(next)
          setRefresh(value => value + 1)
        }
      }
    } catch {
      if (request === operation.current) setError(true)
    } finally {
      if (request === operation.current) setBusy(null)
    }
  }

  const current = record?.case_id === selected ? record : null
  return <div className="inspector-body">
    <h3>{text('故障诊断', 'Failure diagnostics')}</h3>
    {!sessionId && <p>{text('选择一个会话以查看故障。', 'Select a session to view failures.')}</p>}
    {sessionId && <>
      <button type="button" disabled={!!busy} onClick={() => { setError(false); setRefresh(value => value + 1) }}>
        {text('刷新记录', 'Refresh records')}
      </button>
      {error && <p role="alert">{text('诊断请求未完成，请刷新后查看当前状态。', 'The diagnostic request did not complete. Refresh for the current state.')}</p>}
      {drops > 0 && <p role="status">{text('部分故障未能写入诊断记录。', 'Some failures could not be recorded.')}</p>}
      {loaded && !cases.length && <p>{text('当前会话没有故障记录。', 'No failures recorded for this session.')}</p>}
      <ul>{cases.map(item => <li key={item.case_id}>
        <button type="button" disabled={!!busy} aria-pressed={selected === item.case_id} onClick={() => {
          setError(false); setSelected(item.case_id)
        }}>
          {item.failure.route_template ?? item.failure.origin} · {item.failure.http_status ?? item.failure.error_code}
          {' · '}{new Date(item.failure.occurred_at_utc).toLocaleString(locale)}
        </button>
      </li>)}</ul>
      {current && <section aria-label={text('故障详情', 'Failure details')}>
        <p>{current.failure.method} {current.failure.route_template ?? current.failure.origin} · {current.failure.error_code}</p>
        <p>{text('状态', 'Status')}: {current.state === 'RESOLVED'
          ? text('模拟复验通过；实际故障仍未确认修复', 'Simulation verified; actual repair unconfirmed') : current.state}</p>
        {current.report && <>
          <h4>{current.report.rule.classification}</h4>
          <p>{current.report.rule.safe_next_probe}</p>
          {current.report.rule.http && <div role="status">
            <p>{text('HTTP 处理建议', 'HTTP recommendation')}: {current.report.rule.http.action}</p>
            {current.report.rule.http.retry_after_seconds != null && <p>
              {text('服务要求等待（秒）', 'Server wait (seconds)')}: {current.report.rule.http.retry_after_seconds}
            </p>}
            {current.report.rule.http.readback_required && <p>
              {text('先核查现有任务；提交状态未知时禁止重复 RUN。', 'Read back the existing task. Never replay a RUN with an unknown submission state.')}
            </p>}
          </div>}
          <p>{text('待核对证据', 'Evidence to check')}: {current.report.missing_evidence.join(' · ')}</p>
          {current.report.knowledge_refs.length > 0 && <p>
            {text('匹配的已验证案例', 'Matching verified cases')}: {current.report.knowledge_refs.join(' · ')}
          </p>}
          {current.report.rule.counter_evidence.length > 0 && <p>
            {text('反证', 'Counterevidence')}: {current.report.rule.counter_evidence.join(' · ')}
          </p>}
          {current.report.candidates.length > 0 && <ul aria-label={text('候选原因', 'Candidate causes')}>
            {current.report.candidates.map((item, index) => <li key={index}>{item.cause_code} · {item.explanation}</li>)}
          </ul>}
          <details><summary>{text('证据与记录摘要', 'Evidence and record digests')}</summary>
            <ul>{current.report.evidence_refs.map(ref => <li key={ref}><code>{ref}</code></li>)}</ul>
            <ul>{current.events.map(event => <li key={event.event_id}>{event.kind} · <code>{event.sha256}</code></li>)}</ul>
          </details>
        </>}
        {current.proposal_status && <p>{text('提案状态', 'Proposal status')}: {current.proposal_status}</p>}
        {['PROPOSED', 'WAITING_APPROVAL', 'UNRESOLVED'].includes(current.state) &&
          ['API', 'JAVA_BRIDGE', 'COMSOL'].includes(current.failure.origin) &&
          <button type="button" disabled={!!busy} onClick={() => void act('connection-check')}>
            {text('只读核查 COMSOL 连接', 'Check COMSOL connection (read only)')}
          </button>}
        {current.events.filter(item => item.kind === 'connection_verification').map(item => <p key={item.event_id} role="status">
          {item.payload_json?.connection_verified
            ? text('连接读回通过；未执行求解或认证科学结果。', 'Connection readback verified. No solve or scientific result certified.')
            : text('连接尚未通过独立核查。', 'Connection has not passed independent verification.')}
        </p>)}
        {current.state === 'DETECTED' && <>
          <label><input type="checkbox" checked={useModel} onChange={event => setUseModel(event.target.checked)} />
            {text('证据不足时使用已配置模型', 'Use the configured model if evidence is inconclusive')}
          </label>
          <button type="button" disabled={!!busy} onClick={() => void act('diagnose')}>{text('开始只读诊断', 'Diagnose')}</button>
        </>}
        {current.proposal?.approval_required && current.state === 'PROPOSED' &&
          <button type="button" disabled={!!busy} onClick={() => void act('submit')}>{text('提交模拟审批', 'Request simulation review')}</button>}
        {current.state === 'WAITING_APPROVAL' && !current.review_decision && <>
          <p>{text('仅模拟状态刷新，无真实求解或模型写入。', 'Simulated status refresh only. No model changes or solve.')}</p>
          <button type="button" disabled={!!busy} onClick={() => void act('approve')}>{text('批准这次模拟', 'Approve this simulation')}</button>
          <button type="button" disabled={!!busy} onClick={() => void act('reject')}>{text('拒绝', 'Reject')}</button>
        </>}
        {current.state === 'WAITING_APPROVAL' && current.review_decision === 'approve' &&
          <button type="button" disabled={!!busy} onClick={() => void act('simulate')}>{text('运行模拟并独立复验', 'Simulate and verify')}</button>}
        {(busy === 'diagnose' || busy === 'simulate' || busy === 'connection-check' || stoppable.has(current.state)) &&
          <button type="button" disabled={busy === 'cancel'} onClick={() => void act('cancel')}>
            {text('停止当前诊断或模拟', 'Stop diagnostics or simulation')}
          </button>}
        <button type="button" disabled={!!busy} onClick={() => void act('export')}>{text('导出脱敏报告', 'Export diagnostic report')}</button>
      </section>}
    </>}
  </div>
}
