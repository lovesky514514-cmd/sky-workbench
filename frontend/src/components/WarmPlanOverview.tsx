import type { Plan } from '../types'
import { translate, type Locale } from '../i18n'
import { ModelingBriefView } from './ModelingBriefView'

// Read-only projection of the saved plan; no warning is promoted to a verdict.
export function WarmPlanOverview({ plan, locale }: { plan: Plan; locale: Locale }) {
  const t = (text: string) => translate(locale, text)
  const parameters = Object.entries(plan.parameters)
  const critical = plan.missing_information
  const review = [...plan.assumptions, ...plan.warnings]

  return <section className="warm-plan-overview" aria-label={t('Scientific plan review')}>
    <div className="warm-plan-overview-title"><div><span>{t('SCIENTIFIC PLAN')}</span><h2>{plan.title}</h2></div><strong className={plan.runnable ? 'is-ready' : 'is-blocked'}>{plan.completion ? (locale === 'zh-CN' ? '草案待确认 · 可继续补全' : 'Draft · review suggestions to continue') : (plan.runnable ? (locale === 'zh-CN' ? '预检通过 · 等待显式 RUN' : 'Preflight passed · awaiting RUN') : t('Missing information · RUN unavailable'))}</strong></div>
    <p className="warm-plan-goal">{plan.research_goal}</p>
    <div className="warm-plan-essentials"><PlanFacts label={t('Physics')} values={plan.physics} /><PlanFacts label={t('Geometry')} values={plan.geometry} /><PlanFacts label={t('Materials')} values={plan.materials} /><PlanFacts label={t('Boundary Conditions')} values={plan.boundary_conditions} /></div>
    <ModelingBriefView plan={plan} zh={locale === 'zh-CN'} />
    {critical.length > 0 && <div className="warm-plan-signal is-critical"><strong>{t('Must resolve before running')}</strong><ul>{critical.map((value, index) => <li key={index}>{value}</li>)}</ul></div>}
    {review.length > 0 && <div className="warm-plan-signal is-review"><strong>{t('Review assumptions and warnings')}</strong><ul>{review.map((value, index) => <li key={index}>{value}</li>)}</ul></div>}
    {parameters.length > 0 && <div className="warm-plan-parameter-summary"><h3>{t('Parameters to verify')}</h3><div>{parameters.map(([name, value]) => <div key={name} className={value.verified === true ? 'is-verified' : 'needs-review'}><span>{value.symbol || name}</span><strong>{String(value.value)} {value.unit}</strong><small>{value.status === 'User Provided' ? (locale === 'zh-CN' ? '用户输入 · 物性未认证' : 'User input · physical validity unverified') : value.verified === true ? t('Verified') : t('Check source and value')}</small></div>)}</div></div>}
  </section>
}

function PlanFacts({ label, values }: { label: string; values: string[] }) {
  return <div className="warm-plan-facts"><h3>{label}</h3>{values.length ? <ul>{values.map((value, index) => <li key={index}>{value}</li>)}</ul> : <p>—</p>}</div>
}
