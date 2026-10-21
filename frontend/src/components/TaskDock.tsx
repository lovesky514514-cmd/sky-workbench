import { useEffect, useState, type ReactNode } from 'react'
import { translate, type Locale } from '../i18n'
import type { AgentMode, Plan } from '../types'
import { WorkspaceIcon } from './WorkspaceIcon'

const runningStates = new Set(['PRECHECK', 'BUILDING', 'BUILD_VALIDATION', 'SOLVING', 'SOLUTION_VALIDATION', 'VALIDATING', 'REPAIRING', 'REVALIDATING', 'EXPORTING', 'REPORTING'])

function description(locale: Locale, mode: AgentMode): string {
  if (locale === 'zh-CN') return mode === 'ASK' ? '询问科学建模问题；不会修改模型。' : mode === 'PLAN' ? '生成并确认结构化 ScientificPlan。' : '只执行已经确认且可运行的 ScientificPlan。'
  return mode === 'ASK' ? 'Ask a scientific modeling question without modifying the model.' : mode === 'PLAN' ? 'Create and confirm a structured ScientificPlan.' : 'Execute only the confirmed runnable ScientificPlan.'
}

function placeholder(locale: Locale, mode: AgentMode): string {
  if (mode === 'RUN') return locale === 'zh-CN' ? '选择已确认的计划' : 'Select a confirmed plan'
  if (mode === 'ASK') return locale === 'zh-CN' ? '询问 COMSOL、物理场、网格或求解器…' : 'Ask about COMSOL, physics, meshing, or solvers…'
  return locale === 'zh-CN' ? '描述科学建模目标、参数和边界条件…' : 'Describe the scientific goal, parameters, and boundary conditions…'
}

export function TaskDock({ locale, mode, prompt, busy, activePlan, hasPendingIntent, messageCount, warning, history, confirmation, sessionState, forceOpen, onModeChange, onPromptChange, onSend, onCancelAsk, onDismissWarning }: {
  locale: Locale; mode: AgentMode; prompt: string; busy: boolean; activePlan: Plan | null
  hasPendingIntent: boolean; messageCount: number; warning: string; history: ReactNode; confirmation: ReactNode
  sessionState: string; forceOpen?: boolean; onModeChange: (mode: AgentMode) => void; onPromptChange: (value: string) => void
  onSend: () => void; onCancelAsk: () => void; onDismissWarning: () => void
}) {
  const t = (value: string) => translate(locale, value)
  const [requestedOpen, setRequestedOpen] = useState(false)
  const running = runningStates.has(sessionState)
  useEffect(() => { if (running) setRequestedOpen(false); else if (hasPendingIntent) setRequestedOpen(true) }, [hasPendingIntent, running])
  const open = forceOpen ?? requestedOpen
  const availableModes: AgentMode[] = hasPendingIntent ? ['PLAN'] : activePlan?.runnable ? ['ASK', 'PLAN', 'RUN'] : ['ASK', 'PLAN']
  if (!open) return <section className="task-dock compact" aria-expanded="false"><button className="task-dock-capsule" onClick={() => setRequestedOpen(true)}><WorkspaceIcon name="task" size={17} /><span><small>{t(running ? 'ACTIVE TASK' : 'TASK DOCK')}</small><strong>{running ? t(sessionState.replaceAll('_', ' ')) : activePlan ? activePlan.title : t('Describe a scientific task')}</strong></span><em>{t(mode)}</em><WorkspaceIcon name="chevron" size={14} /></button></section>
  return <section className="task-dock expanded" aria-expanded="true">
    <header><div><WorkspaceIcon name="task" size={17} /><span><small>{t('TASK DOCK')}</small><strong>{t(mode === 'ASK' ? 'Scientific question' : mode === 'PLAN' ? 'Scientific plan' : 'Verified execution')}</strong></span></div><button onClick={() => setRequestedOpen(false)} aria-label={t('Collapse task dock')}><WorkspaceIcon name="close" size={15} /></button></header>
    <nav className="task-mode-switch" aria-label={t('Available task actions')}>{availableModes.map((item) => <button className={mode === item ? 'active' : ''} key={item} onClick={() => onModeChange(item)}>{t(item)}</button>)}</nav>
    <div className="task-dock-composer"><div><p>{description(locale, mode)}</p><textarea value={mode === 'RUN' ? (activePlan ? t('Confirmed ScientificPlan') + ': ' + activePlan.plan_id : t('Select a confirmed plan')) : prompt} disabled={mode === 'RUN' || hasPendingIntent} onChange={(event) => onPromptChange(event.target.value)} onKeyDown={(event) => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') onSend() }} placeholder={placeholder(locale, mode)} /></div>{busy && mode === 'ASK' ? <button className="cancel" onClick={onCancelAsk}>{t('Cancel ASK')}</button> : <button className={mode === 'RUN' ? 'run' : ''} disabled={busy || hasPendingIntent || (mode === 'RUN' ? !activePlan?.runnable : !prompt.trim())} onClick={onSend}>{busy ? t('Working…') : mode === 'PLAN' ? t('Review intent') : t(mode)}</button>}</div>
    {warning && <div className="task-dock-warning">{warning}<button onClick={onDismissWarning}>×</button></div>}
    {confirmation}
    {messageCount > 0 && <details className="task-history"><summary>{t('Conversation and plan history')} · {String(messageCount)}</summary><div>{history}</div></details>}
  </section>
}
