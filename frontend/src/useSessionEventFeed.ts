import { useEffect, type Dispatch, type RefObject, type SetStateAction } from 'react'
import { api } from './api'
import { translate, type Locale } from './i18n'
import { safeUiError } from './safeUiError'
import { isCurrentSelection, reconcileCurrentEvidence, type SelectionTicket } from './scientificView'
import type { RuntimeDomain, Session } from './types'

type FeedOptions = {
  session: Session | null
  runtimeReady: boolean
  locale: Locale
  selectionRef: RefObject<SelectionTicket>
  sessionRef: RefObject<Session | null>
  refreshSession: (id: string, ticket: SelectionTicket) => Promise<Session | null>
  setRuntimeDomain: Dispatch<SetStateAction<RuntimeDomain | null>>
  setWarning: Dispatch<SetStateAction<string>>
}

// One subscription belongs to one selected task. Its abort and selection
// ticket prevent late frames from altering the next task's evidence view.
export function useSessionEventFeed({ session, runtimeReady, locale, selectionRef, sessionRef,
  refreshSession, setRuntimeDomain, setWarning }: FeedOptions): void {
  useEffect(() => {
    if (!session || !runtimeReady) return
    const ticket = selectionRef.current
    const cursor = Math.max(0, ...(session.events ?? []).map((event) => event.sequence))
    const reconnectWarning = translate(locale, 'Live session event stream disconnected; reconnecting from the last event ID.')
    let streamInterrupted = false
    let interruptionEpoch = 0
    let refreshTimer = 0
    const source = api.streamEvents(`/api/sessions/${session.id}/events?after=${cursor}`, (event) => {
      if (!isCurrentSelection(ticket, selectionRef.current)) return
      // Revoke the visible claim before the debounced journal request returns.
      if (['run.started', 'run.attempt', 'evidence.invalidated',
        'validation.invalidated_by_human_correction', 'run.canceled', 'run.failed',
        'run.interrupted', 'artifact.integrity_failed'].includes(String(event.type))) {
        interruptionEpoch += 1
        setRuntimeDomain(null)
      }
      window.clearTimeout(refreshTimer)
      refreshTimer = window.setTimeout(() => { void refreshSession(session.id, ticket).catch((cause) => {
        if (isCurrentSelection(ticket, selectionRef.current)) setWarning(safeUiError(cause))
      }) }, 120)
    }, (cause) => {
      if (!isCurrentSelection(ticket, selectionRef.current)) return
      streamInterrupted = true
      interruptionEpoch += 1
      setRuntimeDomain(null)
      const permanent = cause instanceof SyntaxError || /HTTP (401|403|404)/.test(String(cause))
      setWarning(permanent ? (locale === 'zh-CN'
        ? '任务事件流不可用。请检查 Runtime 状态并打开诊断日志。'
        : 'Task event stream unavailable. Check Runtime status and open diagnostic logs.')
        : reconnectWarning)
    }, {
      reconnect: true,
      onConnected: () => {
        if (!isCurrentSelection(ticket, selectionRef.current)) return
        const connectedEpoch = interruptionEpoch
        // A resumed stream is not evidence. Read the journal and its matching
        // projection before restoring any certification withheld on disconnect.
        void (async () => {
          const projection = await reconcileCurrentEvidence({
            ticket, selection: () => selectionRef.current,
            refresh: () => refreshSession(session.id, ticket),
            projection: () => api.runtimeDomain(session.id),
            session: () => sessionRef.current,
          })
          if (!isCurrentSelection(ticket, selectionRef.current) || connectedEpoch !== interruptionEpoch) return
          if (streamInterrupted) {
            if (projection === undefined) return
            setRuntimeDomain(projection)
            streamInterrupted = false
          }
          setWarning((current) => current === reconnectWarning ? '' : current)
        })().catch((cause) => {
          if (isCurrentSelection(ticket, selectionRef.current)) setWarning(safeUiError(cause))
        })
      },
    })
    return () => { window.clearTimeout(refreshTimer); source.abort() }
  }, [locale, refreshSession, runtimeReady, session?.id, selectionRef, sessionRef, setRuntimeDomain, setWarning])
}
