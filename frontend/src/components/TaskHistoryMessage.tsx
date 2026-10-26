import { useEffect, useState } from 'react'
import { api } from '../api'
import { ExecutionVerdictBadge } from '../ExecutionVerdictBadge'
import { translate, type Locale } from '../i18n'
import { displayPlanForMessage, phase2Plan } from '../planProjection'
import type { Plan, Session } from '../types'
import { CitationList } from './CitationList'
import { PlanCard } from './PlanCard'

type Props = {
  locale: Locale
  message: Session['messages'][number]
  currentPlan: Plan | null
  onSavePlan: (plan: Plan) => Promise<void>
}

export function TaskHistoryMessage({ locale, message, currentPlan, onSavePlan }: Props) {
  const t = (value: string) => translate(locale, value)
  const structured = phase2Plan(message.structured)
  const displayedPlan = displayPlanForMessage(message.structured, currentPlan)
  const editable = displayedPlan === currentPlan

  return <article className={`message ${message.role}`}>
    <div className="message-meta">
      <span>{message.role === 'user' ? t('You') : t('Scientific Agent')}</span>
      <em>{message.mode}</em>
      {message.provider && <small>{message.provider}{message.model ? ` / ${message.model}` : ''}</small>}
    </div>
    <div className="message-content">{message.content}</div>
    {message.visible_reasoning_summary && <div className="reasoning-summary">
      <strong>{t('Visible reasoning summary')}</strong>
      <span>{message.visible_reasoning_summary}</span>
    </div>}
    {displayedPlan && <PlanCard plan={displayedPlan} editable={editable} onSave={onSavePlan} />}
    {message.structured && !structured && <div className="legacy-plan-notice">
      {t('Legacy Phase 1 plan retained for audit. Create a new PLAN to migrate it into ScientificPlan 2.0 before RUN.')}
    </div>}
    {message.citations && message.citations.length > 0 && !message.structured &&
      <details className="citation-details">
        <summary>{message.citations.length} {t('traceable source(s)')}</summary>
        <CitationList locale={locale} citations={message.citations} />
      </details>}
    {message.run_id && message.artifacts?.concentration_image &&
      <ConcentrationResult runId={message.run_id} validation={message.validation ?? {}} />}
    {message.artifacts && <div className="artifacts">
      {Object.entries(message.artifacts).map(([name, path]) => <div key={name}>
        <span>{name}</span><code title={path}>{path}</code>
      </div>)}
    </div>}
    <ExecutionVerdictBadge verdict={message.execution_verdict} t={t} />
  </article>
}

function ConcentrationResult({ runId, validation }: { runId: string; validation: Record<string, unknown> }) {
  const [source, setSource] = useState('')

  useEffect(() => {
    let disposed = false
    let current = ''
    setSource('')
    void api.artifactObjectUrl(runId, 'concentration.png').then((value) => {
      if (disposed) {
        URL.revokeObjectURL(value)
        return
      }
      current = value
      setSource(value)
    }).catch(() => { if (!disposed) setSource('') })
    return () => {
      disposed = true
      if (current) URL.revokeObjectURL(current)
    }
  }, [runId])

  return <figure className="result-viewer concentration-result">
    {source
      ? <img src={source} alt="COMSOL 2D concentration field" />
      : <div className="result-loading">Loading concentration artifact…</div>}
    <figcaption>
      <strong>Concentration · mol/m³</strong>
      <span>{String(validation.concentration_min_mol_per_m3 ?? '—')}
        {' to '}{String(validation.concentration_max_mol_per_m3 ?? '—')}</span>
      <small>RMSE {String(validation.analytical_rmse_mol_per_m3 ?? '—')}
        {' · '}Flux relative error {String(validation.flux_relative_error ?? '—')}
        {' · '}Mass balance {String(validation.mass_balance_relative_error ?? '—')}</small>
    </figcaption>
  </figure>
}
