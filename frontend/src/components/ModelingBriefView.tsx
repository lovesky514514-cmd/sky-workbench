import type { Plan } from '../types'

// Render the shared model structure without treating candidate engines as available.
export function ModelingBriefView({ plan, zh }: { plan: Plan; zh: boolean }) {
  const brief = plan.modeling_brief
  if (!brief) return null
  const status = brief.model_outline.refinement_status
  return <section className="modeling-brief" aria-label={zh ? '参数化模型方案' : 'Parameterized model outline'}>
    <h3>{zh ? '参数化模型方案' : 'Parameterized model outline'}</h3>
    {!!brief.entities?.length && <div><strong>{zh ? '对象与条件作用域' : 'Objects and condition scope'}</strong>
      {brief.entities.map((entity) => <div key={entity.id}><strong>{entity.label} · {entity.material ?? entity.kind}</strong>
        <ul>{brief.facts.filter((fact) => fact.entity_id === entity.id).map((fact, index) => <li key={index}>{fact.original} · {fact.role}</li>)}</ul>
      </div>)}
      {brief.relations?.map((relation, index) => <p key={index}>{relation.entity_ids.map((id) => brief.entities?.find((entity) => entity.id === id)?.label ?? id).join(' ↔ ')} · {relation.condition}</p>)}
    </div>}
    {!!brief.facts.length && <div><strong>{zh ? '已识别的原始输入' : 'Recognized original inputs'}</strong>
      <ul>{brief.facts.map((fact, index) => <li key={index}>{fact.original}{fact.issue ? ` · ${fact.issue}` : ''}</li>)}</ul></div>}
    {status === 'NEEDS_MODEL_SERVICE' && <p role="status">{zh
      ? '已保存补参委托。当前未连接可用语言模型，先保留符号参数。'
      : 'Delegation saved. No model service is connected; unknowns remain symbolic.'}</p>}
    {!!brief.equations.length && <div><strong>{zh ? '候选控制关系 · 适用条件待审核' : 'Candidate governing relations · review applicability'}</strong>
      <ul>{brief.equations.map((equation) => <li key={equation}><code>{equation}</code></li>)}</ul></div>}
    {!!brief.symbolic_inputs?.length && <div><strong>{zh ? '缺失值先保留为符号' : 'Keep missing inputs symbolic'}</strong>
      <ul>{brief.symbolic_inputs.map((item) => <li key={item.symbol}>{item.symbol} [{item.unit}] · {item.purpose}</li>)}</ul></div>}
    {!!brief.engine_candidates.length && <div><strong>{zh ? '候选计算工具 · 尚未完成本次任务预检' : 'Candidate engines · task preflight pending'}</strong>
      <ul>{brief.engine_candidates.map((engine) => <li key={engine.engine}>{engine.engine} · {engine.role}</li>)}</ul></div>}
    {!!brief.exclusions.length && <p>{zh ? '明确排除：' : 'Explicit exclusions: '}{brief.exclusions.join('、')}</p>}
  </section>
}
