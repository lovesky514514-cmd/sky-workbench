import { useState } from 'react'
import type { Plan } from '../types'
import type { CompletePlan } from '../planCompletion'
import { safeUiError } from '../safeUiError'

const labels: Record<string, string> = {
  width: '宽度', height: '高度', length: '长度', left_temperature: '左侧温度', right_temperature: '右侧温度',
  thermal_conductivity: '导热系数', density: '密度', heat_capacity: '比热容',
  initial_temperature: '初始温度', ambient_temperature: '环境温度', heat_transfer_coefficient: '对流换热系数',
  emissivity: '表面发射率', duration: '观察时长', output_interval: '输出时间间隔',
  geometry: '几何', boundary_conditions: '边界条件', mesh_strategy: '网格策略', study: '研究类型',
  solver: '求解器', expected_outputs: '预期输出', physics: '物理场', materials: '材料', validation: '检查方法',
}

export function PlanCompletionReview({ plan, zh, onComplete }: { plan: Plan; zh: boolean; onComplete?: CompletePlan }) {
  const proposal = plan.completion
  const [mode, setMode] = useState('review')
  const [values, setValues] = useState<Record<string, string>>({})
  const [excluded, setExcluded] = useState<string[]>([])
  const [decisions, setDecisions] = useState<Record<string, string>>({})
  const [questionAnswers, setQuestionAnswers] = useState<Record<string, string>>({})
  const [acknowledged, setAcknowledged] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  if (!proposal) return null
  const selected = Object.keys(proposal.parameters).filter((name) => !excluded.includes(name))
  const missingDecisions = proposal.remaining_missing.flatMap((label) => label.startsWith('Required decision: ') ? [label.slice(19)] : [])
  const submit = async () => {
    if (!onComplete || !acknowledged) return
    if (selected.some((name) => !String(values[name] ?? (mode === 'manual' ? '' : proposal.parameters[name].value)).trim())) {
      setError(zh ? '请填写已选择参数的数值，或取消勾选以稍后补全。' : 'Enter each selected value, or deselect it to complete it later.')
      return
    }
    if (selected.some((name) => !Number.isFinite(Number(values[name] ?? proposal.parameters[name].value)))) {
      setError(zh ? '参数值必须是有限数值；单位使用右侧标注。' : 'Values must be finite numbers in the displayed units.')
      return
    }
    setBusy(true); setError('')
    try {
      await onComplete({
        proposal_id: proposal.proposal_id, reviewed_digest: proposal.reviewed_digest,
        expected_revision: plan.revision ?? 1, mode, selected_parameters: selected,
        values: mode === 'auto' ? {} : Object.fromEntries(selected.filter((name) => name in values).map((name) => [name, values[name]])),
        decisions: Object.fromEntries(Object.entries(decisions).filter(([, value]) => value.trim()).map(([name, value]) => [name, [value.trim()]])),
        question_answers: questionAnswers,
      })
    } catch (cause) { setError(safeUiError(cause instanceof Error ? cause.message : cause)) }
    finally { setBusy(false) }
  }
  return <section className="plan-completion" aria-label={zh ? '补全缺失条件' : 'Complete missing conditions'}>
    <h4>{zh ? '草案已生成 · 补全缺失条件后继续' : 'Draft created · complete missing conditions to continue'}</h4>
    <p>{zh && proposal.source === 'example' && plan.workflow_id === 'heat_transfer_2d_stationary'
      ? '以下是二维稳态铝板传热的探索示例，上下绝热、材料参数为常数。请确认这些条件适合你的问题。'
      : proposal.reason}</p>
    <p>{proposal.applicability}</p>
    <label>{zh ? '补全方式' : 'Completion mode'} <select value={mode} disabled={busy} onChange={(event) => { setMode(event.target.value); setValues({}); setAcknowledged(false) }}>
      <option value="review">{zh ? '逐项查看并确认建议' : 'Review the listed suggestions'}</option>
      <option value="manual">{zh ? '我自己填写数值' : 'Enter values myself'}</option>
      <option value="auto">{zh ? '授权使用本次选中的建议值' : 'Authorize these selected suggested values'}</option>
    </select></label>
    {Object.entries(proposal.parameters).map(([name, item]) => <div className="completion-value" key={name}>
      <label><input type="checkbox" checked={!excluded.includes(name)} disabled={busy}
        onChange={(event) => { setExcluded(event.target.checked ? excluded.filter((n) => n !== name) : [...excluded, name]); setAcknowledged(false) }} />
        <strong>{zh ? labels[name] ?? name : name}</strong></label>
      <input aria-label={name} disabled={busy || excluded.includes(name)} readOnly={mode === 'auto'}
        value={values[name] ?? (mode === 'manual' ? '' : item.value)}
        onChange={(event) => { setValues({ ...values, [name]: event.target.value }); setAcknowledged(false) }} />
      <span>{item.unit}</span><small>{item.rationale} {item.applicability}</small>
    </div>)}
    {Object.entries(proposal.decisions).map(([name, choices]) => <div key={name}><strong>{zh ? labels[name] ?? name : name}</strong><p>{choices.join(' · ')}</p></div>)}
    {(proposal.questions ?? []).map((question) => <fieldset key={question.key} disabled={busy}>
      <legend>{question.question}</legend>
      {question.options.map((option) => <label key={option.id}>
        <input type="radio" name={question.key} value={option.id} checked={questionAnswers[question.key] === option.id}
          onChange={() => { setQuestionAnswers({ ...questionAnswers, [question.key]: option.id }); setAcknowledged(false) }} />
        {option.label}<small>{option.consequence}</small>
      </label>)}
    </fieldset>)}
    {!!proposal.remaining_missing.length && <div role="status"><strong>{zh ? '仍需补充的条件' : 'Remaining conditions'}</strong>
      <ul>{proposal.remaining_missing.map((item) => <li key={item}>{item}</li>)}</ul>
      {missingDecisions.map((name) => <label key={name}>{zh ? labels[name] ?? name : name}
        <textarea aria-label={name} value={decisions[name] ?? ''} onChange={(event) => { setDecisions({ ...decisions, [name]: event.target.value }); setAcknowledged(false) }} />
      </label>)}
      <p>{zh ? '可先接受部分参数；其他条件可在本面板或完整计划编辑器中继续补充。' : 'Accept selected parameters now and continue here or in the full plan editor.'}</p>
    </div>}
    <label><input type="checkbox" checked={acknowledged} disabled={busy} onChange={(event) => setAcknowledged(event.target.checked)} />
      {mode === 'auto' ? (zh ? '我授权使用以上选中的建议；这些值仍属于未验证假设。' : 'I authorize the selected suggestions as unverified assumptions.')
        : (zh ? '我已查看以上选中的数值和条件，同意用于本次模型。' : 'I reviewed the selected values and conditions for this model.')}</label>
    <p>{zh ? '授权使用与科学验证分别记录；未选中的项目会保留。' : 'Authorization and scientific validation are recorded separately. Unselected items remain pending.'}</p>
    {onComplete && <button type="button" disabled={busy || !acknowledged} onClick={() => void submit()}>
      {busy ? (zh ? '正在校验…' : 'Validating…') : (zh ? '保存选择并重新校验计划' : 'Save choices and revalidate plan')}</button>}
    {error && <p role="alert">{error}</p>}
  </section>
}
