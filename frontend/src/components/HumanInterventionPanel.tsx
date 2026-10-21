import { translate, type Locale } from '../i18n'
import type { Session } from '../types'
import { WorkspaceIcon } from './WorkspaceIcon'

export function HumanInterventionPanel({ locale, session, pendingApproval, reason, affectedObject = 'Boundary', currentReadback, proposedAction, onPause, onModify, onApprove, onReject, onResume }: {
  locale: Locale; session: Session | null; pendingApproval: boolean; reason: string; affectedObject?: string
  currentReadback?: string; proposedAction?: string
  onPause: () => void; onModify: () => void; onApprove: () => void; onReject: () => void; onResume: () => void
}) {
  const t = (value: string) => translate(locale, value)
  const active = ['PRECHECK', 'BUILDING', 'BUILD_VALIDATION', 'SOLVING', 'SOLUTION_VALIDATION', 'VALIDATING', 'REPAIRING', 'REVALIDATING', 'EXPORTING', 'REPORTING'].includes(session?.state ?? '')
  const paused = session?.state === 'PAUSED'
  const needReview = pendingApproval || session?.state === 'VALIDATION_FAILED'
  const interventionActions = needReview
    ? ['Approve', 'Modify', 'Reject'] as const
    : active ? ['Pause'] as const
      : paused ? ['Modify', 'Resume'] as const
        : [] as const
  const actionHandlers = { Pause: onPause, Modify: onModify, Approve: onApprove, Reject: onReject, Resume: onResume }
  return <section className="intervention-workflow">
    <div className="intervention-status"><WorkspaceIcon name={needReview || paused ? 'warning' : active ? 'play' : 'check'} size={17} /><div><span>{t(needReview || paused ? 'Decision required' : active ? 'Execution in progress' : 'No action required')}</span><strong>{t(affectedObject)}</strong></div></div>
    {(needReview || paused) && <dl className="intervention-diff">
      <div><dt>{t('Current')}</dt><dd>{currentReadback || (session?.model ? t('Verified readback') : t('Unavailable'))}</dd></div>
      <div><dt>{t('Proposed')}</dt><dd>{proposedAction || t('Not specified')}</dd></div>
      <div><dt>{t('Reason')}</dt><dd>{reason || t('No unresolved uncertainty recorded')}</dd></div>
      <div><dt>{t('Safe boundary')}</dt><dd>{paused ? t('Execution is paused at a verified safe boundary.') : t('Pause waits for a verified safe boundary before correction.')}</dd></div>
    </dl>}
    {interventionActions.length > 0 && <div className={'intervention-actions count-' + String(interventionActions.length)}>{interventionActions.map((action) => <button className={action.toLowerCase()} key={action} onClick={actionHandlers[action]}>{action === 'Pause' && <WorkspaceIcon name="pause" size={14} />}{action === 'Approve' && <WorkspaceIcon name="check" size={14} />}{action === 'Resume' && <WorkspaceIcon name="play" size={14} />}{t(action)}</button>)}</div>}
    {needReview && <p className="intervention-contract">{t('Approval authorizes continuation; it does not certify scientific success.')}</p>}
  </section>
}
