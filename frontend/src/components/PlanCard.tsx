import { useEffect, useState } from 'react'
import type { Plan, ScientificParameter } from '../types'
import { CitationList } from './CitationList'
import { currentLocale, translate, type Locale } from '../i18n'
import { safeUiError } from '../safeUiError'

export function PlanCard({ locale, plan, editable = false, onSave }: { locale?: Locale; plan: Plan; editable?: boolean; onSave?: (plan: Plan) => Promise<void> }) {
  const activeLocale = locale ?? currentLocale()
  const t = (source: string) => translate(activeLocale, source)
  const [draft, setDraft] = useState(plan)
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  useEffect(() => { setDraft(plan); setSaveError('') }, [plan])
  const updateParameter = (name: string, patch: Partial<ScientificParameter>) => setDraft((current) => ({ ...current, parameters: { ...current.parameters, [name]: { ...current.parameters[name], ...patch } } }))
  const save = async () => {
    if (!onSave) return
    setSaveError('')
    setSaving(true)
    try { await onSave(draft); setEditing(false) }
    catch (cause) { setSaveError(safeUiError(cause)) }
    finally { setSaving(false) }
  }
  return <section className="plan-card">
    <header><div><span className="eyebrow">SCIENTIFICPLAN {plan.schema_version}</span><h3>{plan.title}</h3><code>{plan.plan_id}</code></div><div className="plan-status-stack"><div className={`quality-badge ${plan.quality_status?.toLowerCase() ?? 'invalid'}`}>{plan.quality_status ?? 'INVALID'}</div><div className={`plan-readiness ${plan.runnable ? 'ready' : 'warning'}`}>{plan.runnable ? 'READY TO RUN' : 'NEEDS REVISION'}</div></div></header>
    <div className="plan-grid">
      <PlanSection title={t('Research Goal')} values={[plan.research_goal]} />
      <PlanSection title={t('Physics')} values={plan.physics} />
      <PlanSection title={t('Geometry')} values={plan.geometry} />
      <PlanSection title={t('Materials')} values={plan.materials} />
      <PlanSection title={t('Boundary Conditions')} values={plan.boundary_conditions} />
      <PlanSection title={t('Mesh / Study / Solver')} values={[...plan.mesh_strategy, ...plan.study, ...plan.solver]} />
      <PlanSection title={t('Validation')} values={plan.validation} />
      <PlanSection title={t('Expected Outputs')} values={plan.expected_outputs} />
    </div>
    <div className="plan-section-wide"><div className="plan-section-title"><span>{t('Parameters & provenance')}</span>{editable && <button onClick={() => setEditing((value) => !value)}>{editing ? t('Cancel edit') : t('Edit plan')}</button>}</div>
      <div className="plan-parameters"><div className="parameter-header"><span>{t('Name')}</span><span>{t('Value')}</span><span>{t('Unit')}</span><span>{t('Source')}</span><span>{t('Status')}</span></div>{Object.entries(draft.parameters).map(([name, value]) => (
        <div className="parameter-row" key={name}>
          <span><strong>{value.symbol || name}</strong><small>{name}</small></span>
          {editing ? <input value={value.value} onChange={(event) => updateParameter(name, { value: numericOrString(event.target.value) })} /> : <code title={value.original_value != null ? `Original: ${value.original_value} ${value.original_unit ?? ''} · Normalized: ${value.normalized_value ?? value.value} ${value.normalized_unit ?? value.unit}` : `Normalized: ${value.normalized_value ?? value.value} ${value.normalized_unit ?? value.unit}`}>{value.value}</code>}
          {editing ? <input value={value.unit} onChange={(event) => updateParameter(name, { unit: event.target.value })} /> : <span>{value.unit}</span>}
          {editing ? <input value={value.source} onChange={(event) => updateParameter(name, { source: event.target.value })} /> : <span title={value.source}>{value.source}</span>}
          <em className={`parameter-status ${value.status.toLowerCase().replaceAll(' ', '-')}`}>{value.status}</em>
        </div>
      ))}</div>
      {editing && <div className="plan-edit-actions"><span>{t('Edited plans are revalidated before RUN.')}</span><button className="button primary" onClick={() => void save()} disabled={saving}>{saving ? t('Validating…') : t('Save & validate plan')}</button></div>}
      {saveError && <p className="plan-save-error" role="alert">{saveError}</p>}
    </div>
    {plan.quality_report && <details className={`plan-quality ${plan.quality_status.toLowerCase()}`}><summary>Plan Quality <span>{plan.quality_report.checks.filter((item) => item.status === 'passed').length}/{plan.quality_report.checks.length} checks passed</span></summary><div>{plan.quality_report.checks.map((item) => <article className={item.status} key={`${item.category}-${item.name}`}><span>{item.status === 'passed' ? '✓' : item.status === 'warning' ? '△' : '✕'}</span><p><strong>{item.category} · {item.name}</strong><small>{item.message}</small></p></article>)}</div></details>}
    {(plan.assumptions.length > 0 || plan.missing_information.length > 0 || plan.warnings.length > 0) && <div className="plan-alerts"><PlanSection title={t('Assumptions')} values={plan.assumptions} /><PlanSection title={t('Missing Information')} values={plan.missing_information} /><PlanSection title={t('Warnings')} values={plan.warnings} /></div>}
    <details className="plan-sources"><summary>{t('Sources')} <span>{plan.sources.length}</span></summary><CitationList locale={activeLocale} citations={plan.sources} /></details>
    <footer><span>{t('Planner')}: {plan.ai_provider} / {plan.ai_model}</span><span>{t('Workflow')}: {plan.workflow_id || t('planning only')}</span></footer>
  </section>
}

function PlanSection({ title, values }: { title: string; values: string[] }) {
  return <section><h4>{title}</h4>{values.length ? <ul>{values.map((value, index) => <li key={`${title}-${index}`}>{value}</li>)}</ul> : <p>Not specified</p>}</section>
}
function numericOrString(value: string): number | string { const number = Number(value); return value.trim() !== '' && Number.isFinite(number) ? number : value }
