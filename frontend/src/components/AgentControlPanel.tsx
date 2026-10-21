import { useEffect, useState, type ReactNode } from 'react'
import { translate, type Locale } from '../i18n'
import type { JavaBridgeEvent, Session } from '../types'
import type { ModelTreeNode } from './ModelTree'
import { HumanInterventionPanel } from './HumanInterventionPanel'
import { actionLabel, executionStates, latestVerdict } from './workspaceState'
import { WorkspaceIcon } from './WorkspaceIcon'

type ContextTab = 'Action' | 'Properties' | 'Evidence' | 'Diagnostics'
const contextTabs: ContextTab[] = ['Action', 'Properties', 'Evidence', 'Diagnostics']

function selectedValues(session: Session | null, selected: ModelTreeNode): string[] {
  const model = session?.model
  const plan = session?.plan
  if (selected === 'Geometry') return model?.geometry ?? plan?.geometry ?? []
  if (selected === 'Materials') return model?.materials ?? plan?.materials ?? []
  if (selected === 'Physics') return model?.physics ?? plan?.physics ?? []
  if (selected === 'Boundary') return model?.boundary_conditions ?? plan?.boundary_conditions ?? []
  if (selected === 'Mesh') return model?.mesh ?? plan?.mesh_strategy ?? []
  if (selected === 'Study / Solver') return [...(model?.study ?? plan?.study ?? []), ...(model?.solver ?? plan?.solver ?? [])]
  if (selected === 'Results') return model?.results ?? plan?.expected_outputs ?? []
  return []
}

export function AgentControlPanel({ locale, session, selectedModel, selectedNode = 'Geometry', lowVramMode, warning, blockingError, pendingApproval, inspectorOpen, forceOpen, correctionEvent, inspector, onToggleInspector, onPause, onModify, onApprove, onReject, onResume }: {
  locale: Locale; session: Session | null; selectedModel: string; selectedNode?: ModelTreeNode; lowVramMode: boolean; warning: string
  blockingError: string; pendingApproval: boolean; inspectorOpen: boolean; forceOpen?: boolean; correctionEvent?: JavaBridgeEvent; inspector: ReactNode
  onToggleInspector: () => void; onPause: () => void; onModify: () => void
  onApprove: () => void; onReject: () => void; onResume: () => void
}) {
  const t = (value: string) => translate(locale, value)
  const verdict = latestVerdict(session)
  const planIssue = session?.plan?.missing_information?.[0] || session?.plan?.warnings?.[0] || ''
  const uncertainty = blockingError || warning || planIssue || t('No unresolved uncertainty recorded')
  const needHuman = pendingApproval || session?.state === 'PAUSED' || session?.state === 'VALIDATION_FAILED' || Boolean(blockingError)
  const open = forceOpen ?? (inspectorOpen || needHuman)
  const [tab, setTab] = useState<ContextTab>(needHuman ? 'Action' : 'Properties')
  useEffect(() => { if (needHuman) setTab('Action') }, [needHuman])
  const values = selectedValues(session, selectedNode)
  const planValues = session?.plan ? selectedValues({ ...session, model: null }, selectedNode) : []
  const modelValues = session?.model ? selectedValues({ ...session, plan: null }, selectedNode) : []
  const currentReadback = correctionEvent?.old_value ?? modelValues[0] ?? ''
  const proposedAction = correctionEvent?.new_value ?? planValues.find((value) => !modelValues.includes(value)) ?? ''
  if (!open) return null
  return <aside className="context-inspector open" aria-label={t('Contextual inspector')}>
    <header className="context-inspector-header"><div><span>{t(needHuman ? 'HUMAN REVIEW' : 'INSPECTOR')}</span><strong>{t(needHuman ? 'Decision required' : selectedNode)}</strong></div><button onClick={onToggleInspector} aria-label={t('Close inspector')}><WorkspaceIcon name="close" size={17} /></button></header>
    <nav className="context-tabs">{contextTabs.map((item) => <button className={tab === item ? 'active' : ''} key={item} onClick={() => setTab(item)}>{t(item)}</button>)}</nav>
    <div className="context-inspector-body">
      {tab === 'Action' && <><div className="action-focus"><span>{t('Current Action')}</span><strong>{t(actionLabel(session?.state ?? 'IDLE'))}</strong><small>{executionStates.has(session?.state ?? '') ? t('Workbench is executing the frozen scientific plan.') : t('No active model mutation.')}</small></div><HumanInterventionPanel locale={locale} session={session} pendingApproval={pendingApproval} reason={correctionEvent?.detail || uncertainty} affectedObject={correctionEvent?.target || selectedNode} currentReadback={currentReadback} proposedAction={proposedAction} onPause={onPause} onModify={onModify} onApprove={onApprove} onReject={onReject} onResume={onResume} /></>}
      {tab === 'Properties' && <section className="inspector-properties"><header><WorkspaceIcon name={selectedNode === 'Results' ? 'results' : selectedNode === 'Evidence' ? 'evidence' : selectedNode === 'Boundary' ? 'boundary' : 'geometry'} size={20} /><div><span>{t('Selected scientific object')}</span><strong>{t(selectedNode)}</strong></div></header>{values.length ? <dl>{values.slice(0, 8).map((value, index) => <div key={value + String(index)}><dt>{String(index + 1).padStart(2, '0')}</dt><dd>{value}</dd></div>)}</dl> : <div className="inspector-empty">{t('No verified properties are available.')}</div>}<footer><span>{t(session?.model && verdict?.model_state_verified ? 'Verified readback' : session?.plan ? 'Planned · not readback' : 'Unavailable')}</span></footer></section>}
      {tab === 'Evidence' && <section className="inspector-evidence"><header><span>{t('Execution verdict')}</span><code>{verdict?.run_id ?? t('Unavailable')}</code></header>{[
        ['Command Success', verdict?.command_success],
        ['Model State Verified', verdict?.model_state_verified],
        ['Scientific Validation Passed', verdict?.scientific_validation_success],
        ['Evidence Contract Passed', verdict?.evidence_contract_passed],
      ].map(([label, passed]) => <div className={passed ? 'verified' : 'unavailable'} key={String(label)}><span>{t(String(label))}</span><strong>{passed ? t('Verified') : t('Unavailable')}</strong></div>)}{verdict?.issues.length ? <p>{verdict.issues.join(' · ')}</p> : null}</section>}
      {tab === 'Diagnostics' && <section className="inspector-diagnostics"><div className="diagnostic-summary"><span>{t('Production model')}</span><strong>{selectedModel || t('Unavailable')}</strong><small>{selectedModel ? (lowVramMode ? 'LOW_VRAM_MODE' : 'STANDARD') : t('Provider state pending')}</small></div><details><summary>{t('Agent internal roles')}</summary><p>Planner → Builder → Verifier → Certification</p></details>{inspector}</section>}
    </div>
  </aside>
}
