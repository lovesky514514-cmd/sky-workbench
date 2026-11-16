import { useId, useState } from 'react'
import type { Locale } from '../i18n'
import type { IntentConfirmationInput, ScientificIntent } from '../types'

type Props = {
  locale: Locale
  intent: ScientificIntent
  busy?: boolean
  onContinue: (answers: IntentConfirmationInput) => void
  onModify: () => void
  onReset: () => void
}

export function IntentConfirmation({ locale, intent, busy, onContinue, onModify, onReset }: Props) {
  const name = useId()
  const [study, setStudy] = useState<IntentConfirmationInput['study']>()
  const [details, setDetails] = useState('')
  const zh = locale === 'zh-CN'
  const unresolved = zh ? '未确定' : 'Unresolved'
  const rows = [
    [zh ? '目标' : 'Goal', intent.goal],
    [zh ? '物理场' : 'Physics', intent.physics.join(', ') || unresolved],
    [zh ? '几何' : 'Geometry', intent.geometry.join(', ') || unresolved],
    [zh ? '研究' : 'Study', intent.study.join(', ') || unresolved],
    [zh ? '边界条件' : 'Boundary conditions', intent.boundary_conditions.map((item) =>
      `${item.location}: ${item.condition}${item.value == null ? '' : ` ${item.value} ${item.unit}`}`,
    ).join(' · ') || unresolved],
  ]
  const blocked = intent.unknowns.length > 0 || intent.validation_issues.length > 0
  const needsStudy = !intent.study.length && intent.physics.some((value) =>
    value === 'Heat Transfer in Solids' || value === 'Transport of Diluted Species')
  const options = [
    ['stationary', zh ? '稳态 · 计算稳定后的分布' : 'Stationary · equilibrium distribution'],
    ['transient', zh ? '瞬态 · 计算随时间变化的过程' : 'Transient · evolution over time'],
    ['undecided', zh ? '尚不确定 · 在草案中保留待定' : 'Undecided · leave open in the draft'],
  ] as const

  return <section className="intent-confirmation">
    <header>
      <div>
        <span className="eyebrow">{zh ? '意图确认' : 'INTENT CONFIRMATION'}</span>
        <strong>{zh ? '我理解的是：' : "Here's how I understood your request:"}</strong>
      </div>
      <em className={blocked ? 'warning' : 'ready'}>
        {blocked ? (zh ? '需要补充信息' : 'Missing information') : (zh ? '待人工确认' : 'Ready for review')}
      </em>
    </header>
    <details><summary>{zh ? '原始输入' : 'Original input'}</summary><p>{intent.original_text}</p></details>
    <dl>{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    {intent.unknowns.length > 0 && <div className="intent-missing">
      <strong>{zh ? '需要补充：' : 'Required:'}</strong>
      {intent.unknowns.map((item) => <span key={item}>{item}</span>)}
    </div>}
    {intent.validation_issues.length > 0 && <div className="intent-errors">
      {intent.validation_issues.map((item) => <span key={item}>{item}</span>)}
    </div>}
    {needsStudy && <fieldset className="intent-options" disabled={busy}>
      <legend>{zh ? '选择研究类型' : 'Choose the research type'}</legend>
      {options.map(([value, label]) => <label key={value}>
        <input type="radio" name={name} value={value} checked={study === value}
          onChange={() => setStudy(value)} />{label}
      </label>)}
    </fieldset>}
    {blocked && <label className="intent-details">
      {zh ? '补充已知条件（含单位）' : 'Add known conditions with units'}
      <textarea maxLength={1500} value={details} disabled={busy}
        onChange={(event) => setDetails(event.target.value)} />
    </label>}
    <footer>
      <button onClick={onModify} disabled={busy}>{zh ? '修改' : 'Modify'}</button>
      <button onClick={onReset} disabled={busy}>{zh ? '重新描述' : 'Describe again'}</button>
      <button className="button primary" disabled={busy}
        onClick={() => onContinue({ study: needsStudy ? study : undefined, details })}>
        {blocked ? (zh ? '继续生成草案与补全建议' : 'Continue with draft and suggestions') : (zh ? '正确，继续' : 'Correct, continue')}
      </button>
    </footer>
  </section>
}
