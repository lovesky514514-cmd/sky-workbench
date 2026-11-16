import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Locale } from '../i18n'
import type { ResearchStudy } from '../researchTypes'

export function ResearchStatus({ sessionId, eventCount, locale }: {
  sessionId: string | undefined; eventCount: number; locale: Locale
}) {
  const [study, setStudy] = useState<ResearchStudy | null>(null)
  const [error, setError] = useState(false)
  const zh = locale === 'zh-CN'
  useEffect(() => {
    if (!sessionId) return
    const abort = new AbortController()
    let timer: number | undefined
    const refresh = async () => {
      try {
        const value = await api.researchStudy(sessionId, abort.signal)
        if (abort.signal.aborted) return
        setStudy(value); setError(false)
        if (value.status === 'ACTIVE') timer = window.setTimeout(() => void refresh(), 1500)
      } catch { if (!abort.signal.aborted) setError(true) }
    }
    void refresh()
    return () => { abort.abort(); window.clearTimeout(timer) }
  }, [sessionId, eventCount])
  if (!study || study.status === 'NOT_STARTED') return error ? <p role="status">{zh ? '研究状态暂不可用' : 'Research status unavailable'}</p> : null
  const calls = study.calls ?? []
  const knownRequests = calls.every(call => typeof call.receipt.requests === 'number')
  const knownTokens = calls.length > 0 && calls.every(call => call.receipt.usage_known)
  const requests = knownRequests ? calls.reduce((total, call) => total + (call.receipt.requests ?? 0), 0) : 'UNKNOWN'
  const tokens = knownTokens ? calls.reduce((total, call) => total + (call.receipt.input_tokens ?? 0) + (call.receipt.output_tokens ?? 0), 0) : 'UNKNOWN'
  return <section className="inspector-section" aria-label={zh ? '科研专家讨论' : 'Research discussion'}>
    <h3>{zh ? '科研专家讨论' : 'Research discussion'}</h3>
    <p role="status">{study.status}</p>
    <p>{zh ? '模型请求' : 'Model requests'}: {requests} · Token: {tokens}</p>
    <p>{zh ? '讨论意见不代表真实求解或科学通过。' : 'Discussion does not certify a calculation or scientific PASS.'}</p>
    <ul>{calls.map(call => <li key={call.task_id}>{call.task_id} · {call.status} · {call.receipt.model ?? 'UNKNOWN'}</li>)}</ul>
    {study.outcome?.issues?.map((issue, index) => <p key={index}>{issue.required}</p>)}
    {sessionId && (study.status === 'ACTIVE'
      ? <button type="button" onClick={() => void api.cancelResearch(sessionId).then(setStudy).catch(() => setError(true))}>{zh ? '取消讨论' : 'Cancel discussion'}</button>
      : <button type="button" onClick={() => void api.downloadResearchReport(sessionId).catch(() => setError(true))}>{zh ? '导出科研讨论报告' : 'Export research report'}</button>)}
    {error && <p role="alert">{zh ? '请求未完成，请刷新查看当前状态。' : 'Request did not complete; refresh for the current state.'}</p>}
  </section>
}
