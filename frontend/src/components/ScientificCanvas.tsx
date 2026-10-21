import type { ReactNode } from 'react'
import { translate, type Locale } from '../i18n'
import type { ExecutionEta, JavaBridgeEvent, JavaBridgeStatus, Session, TimelineItem } from '../types'
import type { ModelTreeNode } from './ModelTree'
import { actionLabel, executionStates, formatEta, latestVerdict, modelValue, scientificStageStatuses } from './workspaceState'
import { WorkspaceIcon, type WorkspaceIconName } from './WorkspaceIcon'
import { VerifiedArtifactViewer } from './VerifiedArtifactViewer'

export type CanvasView = 'model' | 'parameters' | 'mesh-solver' | 'results' | 'evidence'
export type Phase2VisualState = 'idle' | 'plan-review' | 'running' | 'need-review' | 'verified-result' | 'left-rail-expanded' | 'inspector-object-edit'

function stateList(values: unknown, empty: string): string[] {
  if (Array.isArray(values) && values.length) return values.map(String)
  return [empty]
}

function parameterByRole(session: Session | null, role: 'width' | 'height') {
  const entries = Object.entries(session?.model?.parameters ?? {})
  const pattern = role === 'width' ? /(^|[\s_])(width|w|宽度|宽)($|[\s_])/i : /(^|[\s_])(height|h|高度|高)($|[\s_])/i
  return entries.find(([key, value]) => pattern.test(`${key} ${value.name} ${value.symbol}`))?.[1]
}

function parameterText(parameter: ReturnType<typeof parameterByRole>, unavailable: string): string {
  return parameter ? `${String(parameter.value)} ${parameter.unit}`.trim() : unavailable
}

function nodeReadback(session: Session | null, selected: ModelTreeNode, unavailable: string): string[] {
  const model = session?.model
  if (selected === 'Geometry') return stateList(model?.geometry, unavailable)
  if (selected === 'Materials') return stateList(model?.materials, unavailable)
  if (selected === 'Physics') return stateList(model?.physics, unavailable)
  if (selected === 'Boundary') return stateList(model?.boundary_conditions, unavailable)
  if (selected === 'Mesh') return stateList(model?.mesh, unavailable)
  if (selected === 'Study / Solver') return [...stateList(model?.study, unavailable), ...stateList(model?.solver, unavailable)]
  if (selected === 'Results') return stateList(model?.results, unavailable)
  return [unavailable]
}

function openTaskDock() {
  const button = document.querySelector<HTMLButtonElement>('.task-dock-capsule')
  button?.click()
}

export function ScientificCanvas({ locale, session, timeline, bridge, bridgeEvents, eta, selectedNode, view, inspectorOpen, visualState, onToggleInspector, taskDock }: {
  locale: Locale; session: Session | null; timeline: TimelineItem[]; bridge: JavaBridgeStatus
  bridgeEvents: JavaBridgeEvent[]; eta: ExecutionEta | null; selectedNode: ModelTreeNode
  view: CanvasView; inspectorOpen?: boolean; visualState?: Phase2VisualState
  onToggleInspector?: () => void; taskDock: ReactNode
}) {
  const t = (value: string) => translate(locale, value)
  const verdict = latestVerdict(session)
  const verified = Boolean(session?.model && verdict?.model_state_verified)
  const model = session?.model
  const plan = session?.plan
  const stages = scientificStageStatuses(session, timeline)
  const actualProgress = [...bridgeEvents].reverse().find((event) => typeof (event.percent ?? event.progress) === 'number')
  const actualPercent = actualProgress ? Number(actualProgress.percent ?? actualProgress.progress) : null
  const verifiedCount = stages.filter((stage) => stage.status === 'Verified').length
  const progress = actualPercent == null ? Math.round((verifiedCount / stages.length) * 100) : Math.max(0, Math.min(100, actualPercent))
  const parameters = Object.values(verified ? model?.parameters ?? {} : plan?.parameters ?? {})
  const sourceLabel = verified ? t('Verified readback') : plan ? t('Planned · not readback') : t('Unavailable')
  const state = session?.state ?? 'IDLE'
  const running = executionStates.has(state)
  const needReview = state === 'PAUSED' || state === 'VALIDATION_FAILED' || visualState === 'need-review'
  const settled = state === 'COMPLETED' && Boolean(verdict?.completed)
  const verifiedRectangle = verified && Boolean(model?.geometry?.some((item) => /rectangle|矩形/i.test(item)))
  const readbackValues = nodeReadback(session, selectedNode, t('Unavailable'))
  const width = parameterText(parameterByRole(session, 'width'), t('Unavailable'))
  const height = parameterText(parameterByRole(session, 'height'), t('Unavailable'))
  const domainIdentity = model?.selections?.find((item) => /domain|域/i.test(item)) ?? t('Not verified')
  const boundaryAt = (pattern: RegExp) => model?.boundary_conditions?.find((item) => pattern.test(item)) ?? t('Unavailable')
  const planeTone = needReview ? 'need-review' : running ? 'running' : settled ? 'verified' : 'idle'
  const unavailable = <div className="canvas-empty-state"><span className="empty-orbit"><i /><i /><i /></span><small>{t('NO VERIFIED MODEL ATTACHED')}</small><h2>{t('Begin with the scientific problem')}</h2><p>{t('Create a Scientific Plan, review its assumptions, then execute only after confirmation.')}</p><button onClick={openTaskDock}>{t('Create Scientific Plan')}</button><em>{t('No 3D representation is generated without verified COMSOL readback.')}</em></div>
  const blueprintNodes: Array<{ name: string; icon: WorkspaceIconName; values: string[] }> = [
    { name: 'Geometry', icon: 'geometry', values: stateList(verified ? model?.geometry : plan?.geometry, t('Unavailable')) },
    { name: 'Physics', icon: 'physics', values: stateList(verified ? model?.physics : plan?.physics, t('Unavailable')) },
    { name: 'Boundary', icon: 'boundary', values: stateList(verified ? model?.boundary_conditions : plan?.boundary_conditions, t('Unavailable')) },
    { name: 'Mesh', icon: 'mesh', values: stateList(verified ? model?.mesh : plan?.mesh_strategy, t('Unavailable')) },
    { name: 'Solver', icon: 'solver', values: stateList(verified ? model?.solver : plan?.solver, t('Unavailable')) },
  ]
  return <section className={'instrument-canvas ' + planeTone}>
    <header className="canvas-command-layer">
      <div className="canvas-object-title"><small>{t(verified ? 'VERIFIED MODEL' : plan ? 'SCIENTIFIC PLAN' : 'SCIENTIFIC CANVAS')}</small><h1>{session?.title ?? t('New scientific task')}</h1><span>{t(selectedNode)} · {sourceLabel}</span></div>
      <div className="canvas-live-context"><button className={inspectorOpen ? 'active' : ''} onClick={onToggleInspector}><WorkspaceIcon name="inspect" size={16} />{t('Inspect')}</button></div>
    </header>
    <div className="scientific-plane">
      <div className="plane-coordinate plane-x">x</div><div className="plane-coordinate plane-y">y</div>
      {view === 'model' && (!plan && !model ? unavailable : verifiedRectangle ? <div className={'verified-2d-viewport focus-' + selectedNode.toLowerCase().replaceAll(' ', '-').replace('/', '-')}>
        <header><div><span>{t('Verified 2D readback')}</span><strong>{model?.geometry?.[0]}</strong></div><small>{t('Only verified model-state values are shown')}</small></header>
        <div className="readback-viewport-layout">
          <section className="schematic-stage" aria-label={t('Verified rectangle domain')}>
            <span className="schematic-axis axis-x">x</span><span className="schematic-axis axis-y">y</span>
            <div className="dimension-readback dimension-width"><i /><span>{t('Width')} · {width}</span><i /></div>
            <div className="dimension-readback dimension-height"><i /><span>{t('Height')} · {height}</span><i /></div>
            <div className="rectangle-readback">
              <button className="boundary-edge boundary-top" title={boundaryAt(/top|上/i)} onClick={onToggleInspector}><span>{boundaryAt(/top|上/i)}</span></button>
              <button className="boundary-edge boundary-right" title={boundaryAt(/right|右/i)} onClick={onToggleInspector}><span>{boundaryAt(/right|右/i)}</span></button>
              <button className="boundary-edge boundary-bottom" title={boundaryAt(/bottom|下/i)} onClick={onToggleInspector}><span>{boundaryAt(/bottom|下/i)}</span></button>
              <button className="boundary-edge boundary-left" title={boundaryAt(/left|左/i)} onClick={onToggleInspector}><span>{boundaryAt(/left|左/i)}</span></button>
              <div className="rectangle-domain"><small>{t('Domain identity')}</small><strong>{domainIdentity}</strong></div>
            </div>
          </section>
          <aside className="object-readback"><small>{t('Selected scientific object')}</small><h2>{t(selectedNode)}</h2><ul>{readbackValues.slice(0, 6).map((item, index) => <li key={item + String(index)}><i />{item}</li>)}</ul><footer><WorkspaceIcon name="check" size={15} />{t('Verified readback')}</footer></aside>
        </div>
      </div> : <div className="model-blueprint">
        <header><span>{t(verified ? 'Verified scientific structure' : 'Proposed scientific structure')}</span><strong>{t(actionLabel(state))}</strong><small>{verified ? t('Read from COMSOL model state') : t('Awaiting COMSOL build and readback')}</small></header>
        <div className="blueprint-path">{blueprintNodes.map((node, index) => <div className={(selectedNode === node.name || (selectedNode === 'Study / Solver' && node.name === 'Solver') ? 'selected ' : '') + (needReview && node.name === 'Boundary' ? 'review ' : '')} key={node.name}><span className="blueprint-index">0{index + 1}</span><span className="blueprint-icon"><WorkspaceIcon name={node.icon} size={22} /></span><strong>{t(node.name)}</strong><small>{node.values[0]}</small>{index < blueprintNodes.length - 1 && <i className="blueprint-connector" />}</div>)}</div>
        {running && <div className="active-operation"><span className="operation-ring"><i style={{ '--progress': String(progress * 3.6) + 'deg' } as React.CSSProperties} /></span><div><small>{t('CURRENT OPERATION')}</small><strong>{t(actionLabel(state))}</strong><p>{actualProgress?.message || t('Following verified execution state; no activity is simulated.')}</p></div></div>}
        {needReview && <div className="review-callout"><WorkspaceIcon name="warning" size={18} /><div><small>{t('HUMAN REVIEW REQUIRED')}</small><strong>{t('Boundary selection needs confirmation')}</strong><span>{t('The affected object is highlighted. Open Action to compare the proposed change with current readback.')}</span></div><button onClick={onToggleInspector}>{t('Review change')}</button></div>}
      </div>)}
      {view === 'parameters' && (parameters.length ? <div className="parameter-work-surface"><header><span>{t('Scientific parameters')}</span><strong>{sourceLabel}</strong></header><div>{parameters.map((parameter) => <article key={parameter.name}><span><b>{parameter.name}</b><small>{parameter.source}</small></span><strong>{String(parameter.value)} <em>{parameter.unit}</em></strong><i>{t(parameter.status)}</i></article>)}</div></div> : unavailable)}
      {view === 'mesh-solver' && (plan || model ? <div className="solver-work-surface"><div className="solver-flow"><span><WorkspaceIcon name="mesh" size={25} /><small>{t('Mesh')}</small><strong>{modelValue(verified ? model?.mesh : plan?.mesh_strategy, t('Pending'))}</strong></span><i /><span><WorkspaceIcon name="solver" size={25} /><small>{t('Study')}</small><strong>{modelValue(verified ? model?.study : plan?.study, t('Pending'))}</strong></span><i /><span><WorkspaceIcon name="results" size={25} /><small>{t('Solver')}</small><strong>{modelValue(verified ? model?.solver : plan?.solver, t('Pending'))}</strong></span></div><p>{t(verified ? 'Mesh and solver state are verified from model readback.' : 'Mesh and solver values are planned and not yet read back from COMSOL.')}</p></div> : unavailable)}
      {view === 'results' && (visualState ? (settled && model?.results?.length ? <div className="verified-result-surface fixture-field"><header><span><WorkspaceIcon name="check" size={17} />{t('VERIFIED RESULT')}</span><strong>{model.results[0]}</strong><small>{t('Scientific certification is derived from the current ExecutionVerdict.')}</small></header><div className="result-focus"><div className="result-field"><span>{t('VISUAL QA FIXTURE · NOT SCIENTIFIC DATA')}</span></div><aside><span>{t('Result state')}</span><strong>{t('Verified')}</strong><span>{t('Evidence')}</span><strong>{String(verdict?.issues.length ? verdict.issues.length : 4)} {t('checks')}</strong><span>{t('Run ID')}</span><code>{verdict?.run_id}</code></aside></div></div> : unavailable) : <VerifiedArtifactViewer locale={locale} session={session} />)}
      {view === 'evidence' && (verdict ? <div className="verdict-work-surface"><header><span>{t('Execution verdict')}</span><code>{verdict.run_id}</code></header><div>{[
        ['Command Success', verdict.command_success],
        ['Model State Verified', verdict.model_state_verified],
        ['Scientific Validation Passed', verdict.scientific_validation_success],
        ['Evidence Contract Passed', verdict.evidence_contract_passed],
      ].map(([label, passed]) => <article className={passed ? 'verified' : 'unavailable'} key={String(label)}><i>{passed ? <WorkspaceIcon name="check" size={15} /> : '—'}</i><span>{t(String(label))}</span><strong>{passed ? t('Verified') : t('Unavailable')}</strong></article>)}</div>{verdict.issues.length > 0 && <p>{verdict.issues.join(' · ')}</p>}</div> : unavailable)}
      <div className="canvas-status-line"><span><i className={planeTone} />{t(actionLabel(state))}</span><span>{formatEta(locale, eta)}</span><span>{String(verifiedCount)} / {String(stages.length)} {t('verified stages')}</span></div>
    </div>
    {taskDock}
  </section>
}
