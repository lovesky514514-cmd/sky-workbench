import { useEffect, useState } from 'react'
import { translate, type Locale } from '../i18n'
import type { Project, Session, TimelineItem, TreeNode } from '../types'
import { latestVerdict, scientificStageStatuses, statusClass, type ScientificStageStatus } from './workspaceState'
import { WorkspaceIcon, type WorkspaceIconName } from './WorkspaceIcon'

export type ModelTreeNode = 'Geometry' | 'Materials' | 'Physics' | 'Boundary' | 'Mesh' | 'Study / Solver' | 'Results' | 'Evidence'

const railStorageKey = 'comsol-workbench-phase2-rail-expanded'

function countTree(nodes: TreeNode[]): number {
  return nodes.reduce((total, node) => total + 1 + countTree(node.children ?? []), 0)
}

export function ModelTree({ locale, projects, activeProject, session, sessions, timeline, repositoryTree, selected, onSelect, onProjectChange, onSessionSelect, onNewSession, forceExpanded }: {
  locale: Locale; projects: Project[]; activeProject?: Project; session: Session | null; sessions: Session[]
  timeline: TimelineItem[]; repositoryTree: TreeNode[]; selected: ModelTreeNode
  onSelect: (node: ModelTreeNode) => void; onProjectChange: (projectId: string) => void
  onSessionSelect: (sessionId: string) => void; onNewSession: () => void; forceExpanded?: boolean
}) {
  const t = (value: string) => translate(locale, value)
  const [expanded, setExpanded] = useState(() => forceExpanded ?? localStorage.getItem(railStorageKey) === 'true')
  useEffect(() => { if (forceExpanded != null) setExpanded(forceExpanded) }, [forceExpanded])
  useEffect(() => { if (forceExpanded == null) localStorage.setItem(railStorageKey, String(expanded)) }, [expanded, forceExpanded])
  const verdict = latestVerdict(session)
  const verified = Boolean(session?.model && verdict?.model_state_verified)
  const planned = Boolean(session?.plan)
  const source = verified ? t('Verified readback') : planned ? t('Planned · not readback') : t('Unavailable')
  const stages = scientificStageStatuses(session, timeline)
  const stage = (name: string): ScientificStageStatus => stages.find((item) => item.name === name)?.status ?? 'Pending'
  const model = session?.model
  const plan = session?.plan
  const needReview = session?.state === 'VALIDATION_FAILED' || session?.state === 'PAUSED' || (session?.state === 'COMPLETED' && !verdict?.completed)
  const nodes: Array<{ name: ModelTreeNode; icon: WorkspaceIconName; status: ScientificStageStatus; detail: string }> = [
    { name: 'Geometry', icon: 'geometry', status: stage('Geometry'), detail: (verified ? model?.geometry : plan?.geometry)?.[0] ?? t('No verified objects') },
    { name: 'Materials', icon: 'materials', status: stage('Physics'), detail: (verified ? model?.materials : plan?.materials)?.[0] ?? t('No verified objects') },
    { name: 'Physics', icon: 'physics', status: stage('Physics'), detail: (verified ? model?.physics : plan?.physics)?.[0] ?? t('No verified objects') },
    { name: 'Boundary', icon: 'boundary', status: needReview ? 'Need Review' : stage('Boundary'), detail: (verified ? model?.boundary_conditions : plan?.boundary_conditions)?.[0] ?? t('No verified objects') },
    { name: 'Mesh', icon: 'mesh', status: stage('Mesh'), detail: (verified ? model?.mesh : plan?.mesh_strategy)?.[0] ?? t('No verified objects') },
    { name: 'Study / Solver', icon: 'solver', status: stage('Solver'), detail: (verified ? model?.study : plan?.study)?.[0] ?? t('No verified objects') },
    { name: 'Results', icon: 'results', status: session?.state === 'COMPLETED' && verdict?.completed ? 'Verified' : 'Pending', detail: model?.results?.[0] ?? plan?.expected_outputs?.[0] ?? t('No verified objects') },
    { name: 'Evidence', icon: 'evidence', status: stage('Evidence'), detail: verdict ? verdict.run_id : t('No execution evidence yet') },
  ]
  const setRail = (value: boolean) => setExpanded(forceExpanded ?? value)
  return <aside className={'instrument-rail ' + (expanded ? 'expanded' : 'collapsed')} aria-expanded={expanded}>
    <header className="rail-brand"><button className="rail-project-mark" onClick={() => setRail(true)} title={t('Scientific Project')}><WorkspaceIcon name="project" size={19} /></button>{expanded && <div><span>{t('SCIENTIFIC PROJECT')}</span><strong>{activeProject?.name ?? t('No project selected')}</strong></div>}<button className="rail-collapse" onClick={() => setRail(!expanded)} aria-label={t(expanded ? 'Collapse model tree' : 'Expand model tree')}><WorkspaceIcon name="chevron" size={16} /></button></header>
    {expanded && <div className="rail-project-picker"><select aria-label={t('Scientific Project')} value={activeProject?.id ?? ''} onChange={(event) => onProjectChange(event.target.value)}>{projects.map((project) => <option value={project.id} key={project.id}>{project.name}</option>)}</select><button onClick={onNewSession} title={t('New scientific task')}>＋</button></div>}
    <nav className="instrument-tree" aria-label={t('Scientific Model Tree')}>
      {expanded && <div className="tree-context"><span>{session?.plan?.workflow_id ?? t('No scientific model')}</span><small>{source}</small></div>}
      {nodes.map((node) => <button className={(selected === node.name ? 'active ' : '') + statusClass(node.status)} key={node.name} onClick={() => onSelect(node.name)} title={!expanded ? `${t(node.name)} · ${t(node.status)}` : undefined}><span className="rail-node-icon"><WorkspaceIcon name={node.icon} size={18} /><i /></span>{expanded && <span className="rail-node-copy"><b>{t(node.name)}</b><small>{node.detail}</small></span>}{expanded && <em>{t(node.status)}</em>}</button>)}
    </nav>
    {expanded && <details className="rail-repository"><summary>{t('Repository references')}</summary><p>{String(countTree(repositoryTree))} {t('file reference(s) hidden from the primary workspace')}</p></details>}
    <section className="rail-sessions"><header>{expanded && <span>{t('Sessions')}</span>}<button onClick={onNewSession} title={t('New scientific task')}>＋</button></header>{expanded && <div>{sessions.slice(0, 8).map((item) => <button className={session?.id === item.id ? 'active' : ''} key={item.id} onClick={() => onSessionSelect(item.id)}><span>{item.title}</span><small>{t(item.state.replaceAll('_', ' '))}</small></button>)}</div>}</section>
  </aside>
}
