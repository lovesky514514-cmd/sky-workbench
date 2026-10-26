import type { Locale } from '../i18n'
import type { ScientificIntent } from '../types'

type Props = {
  locale: Locale
  intent: ScientificIntent
  onContinue: () => void
  onModify: () => void
  onReset: () => void
}

export function IntentConfirmation({ locale, intent, onContinue, onModify, onReset }: Props) {
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
    <footer>
      <button onClick={onModify}>{zh ? '修改' : 'Modify'}</button>
      <button onClick={onReset}>{zh ? '重新描述' : 'Describe again'}</button>
      <button className="button primary" disabled={blocked} onClick={onContinue}>
        {zh ? '正确，继续' : 'Correct, continue'}
      </button>
    </footer>
  </section>
}
